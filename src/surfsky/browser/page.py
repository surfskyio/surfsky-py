import base64
import json
import logging
import random
import re
from collections.abc import Callable, Coroutine, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import anyio

from ..errors import BrowserTimeoutError, PageClosedError
from ..types import Cookie, WaitUntil
from .actions import Actions
from .cdp import CDPClient, CDPError

if TYPE_CHECKING:
    from .browser import Browser

logger = logging.getLogger("surfsky")

POLL_INTERVAL = 0.1

LIFECYCLE_EVENT = {
    "commit": "commit",
    "domcontentloaded": "DOMContentLoaded",
    "load": "load",
    "networkidle": "networkIdle",
}

WORLD_NAME = "utility"

DIALOG_DELAY = (0.6, 1.4)

FUNCTION = re.compile(r"^\s*(async\s+)?(function\b|\([^()]*\)\s*=>|[\w$]+\s*=>)")

SELECT = """(selector, value, label) => {
  const el = document.querySelector(selector);
  if (!el) return null;
  const option = Array.from(el.options).find(
    o => label === null ? o.value === value : o.label === label || o.text === label
  );
  if (!option) return false;
  el.value = option.value;
  el.dispatchEvent(new Event("input", {bubbles: true}));
  el.dispatchEvent(new Event("change", {bubbles: true}));
  return option.value;
}"""

DialogHandler = Callable[[str, str], bool | str | None]


def cookie_param(cookie: Cookie | dict[str, Any]) -> dict[str, Any]:
    if isinstance(cookie, Cookie):
        cookie = cookie.model_dump(by_alias=True, exclude_none=True)
    if (expires := cookie.get("expirationDate")) is not None:
        cookie = {"expires": expires, **cookie}
    return cookie


def command_failed(method: str, params: dict[str, Any] | None, exc: CDPError) -> CDPError:
    where = method
    if selector := (params or {}).get("selector"):
        where = f"{method} {selector!r}"
    if "Could not find node with given id" in str(exc):
        return CDPError(
            f"{where}: the page navigated while the command ran.",
            code=exc.code,
        )
    return CDPError(f"{where}: {exc}", code=exc.code)


@contextmanager
def deadline(timeout: float | None, message: str) -> Iterator[None]:
    try:
        with anyio.fail_after(timeout):
            yield
    except TimeoutError as exc:
        if isinstance(exc, BrowserTimeoutError):
            raise
        raise BrowserTimeoutError(f"{message} within {timeout}s") from exc


@dataclass
class CapturedResponse:
    url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)


class NavWaiter:
    """The lifecycle wait of one ``goto``"""

    def __init__(self, milestone: str) -> None:
        self.milestone = milestone
        self.done = anyio.Event()
        self.error: str | None = None
        self.loader_id: str | None = None
        self.created: list[str] = []
        self.reached: set[str] = set()

    def follow(self, loader_id: str) -> bool:
        if loader_id in self.created:
            loader_id = self.created[-1]  # already replaced by a newer document
        self.loader_id = loader_id
        return loader_id in self.reached

    def follow_newest(self) -> bool:
        if self.created:
            return self.follow(self.created[-1])
        self.loader_id = ""  # any init from here on is ours
        return False

    def observe(self, name: Any, loader_id: Any) -> None:
        if self.done.is_set() or not loader_id:
            return
        if name == "init":
            if self.loader_id is None:
                self.created.append(loader_id)
            elif loader_id != self.loader_id:
                self.loader_id = loader_id  # a newer document replaced ours
        elif name == self.milestone:
            if self.loader_id is None:
                self.reached.add(loader_id)
            elif loader_id == self.loader_id:
                self.done.set()

    def fail(self, reason: str) -> None:
        self.error = reason
        self.done.set()


class Page(Actions):
    def __init__(self, browser: "Browser", target_id: str, session_id: str) -> None:
        self._browser = browser
        self._target_id = target_id
        self._session_id = session_id
        self._frame_id = ""
        self._closed = False
        self.on_dialog: DialogHandler | None = None
        self._loader_id: str | None = None  # the main frame's newest document
        self._milestones: set[str] = set()  # the lifecycle events it reached
        self._world_id: int | None = None  # the isolated world's context
        self._ready = anyio.Event()
        self._ready.set()
        self._setup_error: Exception | None = None
        self._waiter: NavWaiter | None = None
        self._status: int | None = None
        self._storage_ready = False
        self._captures: list[str] = []
        self._responses: list[CapturedResponse] = []
        self._in_flight: dict[str, dict[str, Any]] = {}

    @property
    def cdp(self) -> CDPClient:
        return self._browser.cdp

    @property
    def target_id(self) -> str:
        return self._target_id

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def status(self) -> int | None:
        return self._status

    async def send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._require_open()
        await self._wait_ready()
        return await self._send(method, params)

    def _require_open(self) -> None:
        if self._closed:
            raise PageClosedError("page is closed")

    async def _send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with deadline(self._browser.command_timeout, f"{method} did not answer"):
            try:
                return await self.cdp.send(method, params, session_id=self._session_id)
            except CDPError as exc:
                raise command_failed(method, params, exc) from exc

    async def _wait_ready(self) -> None:
        await self._ready.wait()
        if self._setup_error is not None:
            raise self._setup_error

    async def _setup(self, waiting: bool) -> None:
        commands: list[tuple[str, dict[str, Any] | None]] = [
            ("Page.enable", None),
            ("Page.setLifecycleEventsEnabled", {"enabled": True}),
            ("Fetch.enable", {"patterns": self._browser._fetch_patterns}),
        ]
        if waiting:
            commands.append(("Runtime.runIfWaitingForDebugger", None))
        posted: list[tuple[int, Any]] = []
        try:
            with deadline(self._browser.command_timeout, "the page was not ready"):
                posted = [await self.cdp.post(*c, self._session_id) for c in commands]
                for (method, params), (_, reply) in zip(commands, posted, strict=True):
                    try:
                        await reply
                    except CDPError as exc:
                        raise command_failed(method, params, exc) from exc
        except Exception as exc:
            self._setup_error = exc
        finally:
            for _, reply in posted:
                reply.cancel()
            self._ready.set()

    async def close(self) -> None:
        if self._closed:
            return
        with deadline(self._browser.command_timeout, "the page did not close"):
            await self.cdp.send("Target.closeTarget", {"targetId": self._target_id})
        self._browser._drop(self)

    async def bring_to_front(self) -> None:
        await self.send("Page.bringToFront")

    async def wait_for_load_state(
        self, state: WaitUntil = "load", *, timeout: float = 30.0
    ) -> None:
        milestone = LIFECYCLE_EVENT[state]
        with deadline(timeout, f"the page did not reach {state!r}"):
            while milestone not in self._milestones:
                self._require_open()
                await anyio.sleep(POLL_INTERVAL)

    async def goto(
        self, url: str, *, wait_until: WaitUntil = "load", timeout: float = 30.0
    ) -> None:
        if self._waiter is not None:
            raise RuntimeError("navigation already in progress")
        self._waiter = waiter = NavWaiter(LIFECYCLE_EVENT[wait_until])
        self._status = None  # a failed navigation must not keep the last one's
        try:
            with deadline(timeout, f"navigation to {url} did not reach {wait_until!r}"):
                result = await self.send("Page.navigate", {"url": url})
                if result.get("errorText"):
                    raise CDPError(f"navigation to {url} failed: {result['errorText']}")
                loader_id = result.get("loaderId")
                if wait_until == "commit" or loader_id is None:
                    return
                if not waiter.follow(loader_id):
                    await waiter.done.wait()
                if waiter.error is not None:
                    raise CDPError(waiter.error)
        finally:
            if self._waiter is waiter:
                self._waiter = None

    async def reload(
        self, *, wait_until: WaitUntil = "load", timeout: float = 30.0
    ) -> None:
        if self._waiter is not None:
            raise RuntimeError("navigation already in progress")
        self._waiter = waiter = NavWaiter(LIFECYCLE_EVENT[wait_until])
        self._status = None
        try:
            with deadline(timeout, f"reload did not reach {wait_until!r}"):
                await self.send("Page.reload")
                if not waiter.follow_newest():
                    await waiter.done.wait()
                if waiter.error is not None:
                    raise CDPError(waiter.error)
        finally:
            if self._waiter is waiter:
                self._waiter = None

    async def go_back(self, *, timeout: float = 30.0) -> str | None:
        return await self._history_step(-1, timeout)

    async def go_forward(self, *, timeout: float = 30.0) -> str | None:
        return await self._history_step(1, timeout)

    async def _history_step(self, delta: int, timeout: float) -> str | None:
        history = await self.send("Page.getNavigationHistory")
        entries = history["entries"]
        index = history["currentIndex"] + delta
        if not 0 <= index < len(entries):
            return None
        await self.send("Page.navigateToHistoryEntry", {"entryId": entries[index]["id"]})
        with deadline(timeout, "history did not move"):
            while True:
                moved = await self.send("Page.getNavigationHistory")
                if moved["currentIndex"] == index:
                    break
                await anyio.sleep(POLL_INTERVAL)
        return await self.url()

    @property
    def responses(self) -> list[CapturedResponse]:
        return list(self._responses)

    async def capture_responses(self, *fragments: str) -> None:
        if not fragments:
            raise ValueError("capture_responses needs at least one URL fragment")
        if not self._captures:
            await self.send("Network.enable")
        self._captures.extend(fragments)

    async def stop_capturing(self) -> None:
        if not self._captures:
            return
        self._captures.clear()
        self._responses = []
        self._in_flight.clear()
        await self.send("Network.disable")

    async def wait_for_response(
        self, fragment: str, *, timeout: float = 30.0
    ) -> CapturedResponse:
        if not self._captures:
            raise RuntimeError("call capture_responses() before the navigation")
        with deadline(timeout, f"no response matching {fragment!r}"):
            while True:
                for response in self._responses:
                    if fragment in response.url:
                        return response
                self._require_open()
                await anyio.sleep(POLL_INTERVAL)

    def _on_response(self, event: dict[str, Any]) -> None:
        response = event.get("response") or {}
        url = response.get("url", "")
        if any(fragment in url for fragment in self._captures):
            self._in_flight[event["requestId"]] = response

    def _on_body_ready(self, event: dict[str, Any]) -> None:
        response = self._in_flight.pop(event.get("requestId", ""), None)
        if response is not None:
            self._spawn(self._collect(event["requestId"], response, self._responses))

    async def _collect(
        self, request_id: str, response: dict[str, Any], into: list[CapturedResponse]
    ) -> None:
        body = b""
        try:
            result = await self.send("Network.getResponseBody", {"requestId": request_id})
            raw = result.get("body") or ""
            body = base64.b64decode(raw) if result.get("base64Encoded") else raw.encode()
        except Exception as exc:
            logger.debug("no body for %s: %s", response.get("url"), exc)
        into.append(
            CapturedResponse(
                url=response.get("url", ""),
                status=response.get("status", 0),
                headers=response.get("headers") or {},
                body=body,
            )
        )

    async def local_storage(self) -> dict[str, str]:
        return await self._storage(local=True)

    async def set_local_storage(self, values: Mapping[str, str]) -> None:
        await self._set_storage(values, local=True)

    async def session_storage(self) -> dict[str, str]:
        return await self._storage(local=False)

    async def set_session_storage(self, values: Mapping[str, str]) -> None:
        await self._set_storage(values, local=False)

    async def clear_cookies(self) -> None:
        await self.send("Network.clearBrowserCookies")

    async def _storage(self, *, local: bool) -> dict[str, str]:
        items = await self.send(
            "DOMStorage.getDOMStorageItems", {"storageId": await self._storage_id(local)}
        )
        return dict(items.get("entries") or [])

    async def _set_storage(self, values: Mapping[str, str], *, local: bool) -> None:
        storage_id = await self._storage_id(local)
        for key, value in values.items():
            await self.send(
                "DOMStorage.setDOMStorageItem",
                {"storageId": storage_id, "key": key, "value": value},
            )

    async def _storage_id(self, local: bool) -> dict[str, Any]:
        if not self._storage_ready:
            await self.send("DOMStorage.enable")
            self._storage_ready = True
        tree = await self.send("Page.getFrameTree")
        frame = tree["frameTree"]["frame"]
        storage_id: dict[str, Any] = {"isLocalStorage": local}
        if key := frame.get("storageKey"):
            storage_id["storageKey"] = key
        else:
            storage_id["securityOrigin"] = frame["securityOrigin"]
        return storage_id

    async def evaluate(
        self,
        expression: str,
        *args: Any,
        isolated: bool = True,
        await_promise: bool = True,
    ) -> Any:
        """Run script in the page.
        It runs in an isolated world: the page's DOM, but not its globals, so
        the page's own script cannot see the call or hook it. Page variables
        (``window.__data``) need ``isolated=False``, the main world, where a
        site that hooks the natives can notice.
        """
        if args:
            expression = f"({expression})(...{json.dumps(list(args))})"
        elif FUNCTION.match(expression):
            expression = f"({expression})()"
        remote = await self._evaluate(expression, isolated, await_promise)
        if remote.get("type") == "function" and "=>" in expression:
            remote = await self._evaluate(f"({expression})()", isolated, await_promise)
        return remote.get("value")

    async def _evaluate(
        self, expression: str, isolated: bool, await_promise: bool
    ) -> dict[str, Any]:
        params = {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        }
        if isolated:
            params["contextId"] = await self._world()
        try:
            result = await self.send("Runtime.evaluate", params)
        except CDPError as exc:
            if not isolated or "Cannot find context" not in str(exc):
                raise
            # the document changed under us before its event came: once more
            self._world_id = None
            params["contextId"] = await self._world()
            result = await self.send("Runtime.evaluate", params)
        if details := result.get("exceptionDetails"):
            exception = details.get("exception") or {}
            raise CDPError(
                f"evaluate failed: {exception.get('description') or details.get('text')}"
            )
        return result["result"]

    async def _world(self) -> int:
        if self._world_id is None:
            created = await self.send(
                "Page.createIsolatedWorld",
                {"frameId": self._frame_id, "worldName": WORLD_NAME},
            )
            self._world_id = created["executionContextId"]
        return self._world_id

    async def wait_for_function(
        self, expression: str, *args: Any, isolated: bool = True, timeout: float = 30.0
    ) -> Any:
        with deadline(timeout, f"{expression!r} was not truthy"):
            while not (
                value := await self.evaluate(expression, *args, isolated=isolated)
            ):
                await anyio.sleep(POLL_INTERVAL)
        return value

    async def inner_text(self, selector: str) -> str | None:
        return await self.evaluate(
            "s => document.querySelector(s)?.innerText ?? null", selector
        )

    async def all_inner_texts(self, selector: str) -> list[str]:
        return await self.evaluate(
            "s => Array.from(document.querySelectorAll(s), e => e.innerText)", selector
        )

    async def get_attribute(self, selector: str, name: str) -> str | None:
        if (node_id := await self._node_id(selector)) is None:
            return None
        found = await self.send("DOM.getAttributes", {"nodeId": node_id})
        pairs = found.get("attributes") or []
        return dict(zip(pairs[::2], pairs[1::2], strict=True)).get(name)

    async def count(self, selector: str) -> int:
        document = await self.send("DOM.getDocument", {"depth": 0})
        found = await self.send(
            "DOM.querySelectorAll",
            {"nodeId": document["root"]["nodeId"], "selector": selector},
        )
        return len(found.get("nodeIds") or ())

    async def select_option(
        self, selector: str, value: str | None = None, *, label: str | None = None
    ) -> str:
        if (value is None) == (label is None):
            raise ValueError("select_option takes either a value or a label")
        picked = await self.evaluate(SELECT, selector, value, label)
        if picked is None:
            raise ValueError(f"nothing matches {selector!r}")
        if picked is False:
            raise ValueError(f"{selector!r} has no option {label or value!r}")
        return picked

    async def wait_for_selector(
        self, selector: str, *, visible: bool = True, timeout: float = 30.0
    ) -> None:
        state = "visible" if visible else "in the document"
        with deadline(timeout, f"{selector!r} was not {state}"):
            while not await self._matches(selector, visible):
                await anyio.sleep(POLL_INTERVAL)

    async def _matches(self, selector: str, visible: bool) -> bool:
        # Node ids belong to one document, so a navigation between the lookups
        # invalidates them. The poll is here for that, and for a node that does
        # not have a box yet.
        try:
            if (node_id := await self._node_id(selector)) is None:
                return False
            if not visible:
                return True
            box = await self.send("DOM.getBoxModel", {"nodeId": node_id})
        except CDPError:
            return False
        model = box.get("model") or {}
        return bool(model.get("width")) and bool(model.get("height"))

    async def _node_id(self, selector: str) -> int | None:
        document = await self.send("DOM.getDocument", {"depth": 0})
        found = await self.send(
            "DOM.querySelector",
            {"nodeId": document["root"]["nodeId"], "selector": selector},
        )
        return found.get("nodeId") or None

    async def wait_for_url(self, fragment: str, *, timeout: float = 30.0) -> str:
        with deadline(timeout, f"the page did not reach {fragment!r}"):
            while fragment not in (url := await self.url()):
                await anyio.sleep(POLL_INTERVAL)
        return url

    async def is_visible(self, selector: str) -> bool:
        return await self._matches(selector, True)

    async def outer_html(self, selector: str) -> str | None:
        if (node_id := await self._node_id(selector)) is None:
            return None
        html = await self.send("DOM.getOuterHTML", {"nodeId": node_id})
        return html["outerHTML"]

    async def set_cookies(self, cookies: Sequence[Cookie | dict[str, Any]]) -> None:
        params = [cookie_param(cookie) for cookie in cookies]
        await self.send("Network.setCookies", {"cookies": params})

    async def content(self) -> str:
        document = await self.send("DOM.getDocument", {"depth": 0})
        html = await self.send("DOM.getOuterHTML", {"nodeId": document["root"]["nodeId"]})
        return html["outerHTML"]

    async def title(self) -> str:
        info = await self.send("Target.getTargetInfo", {"targetId": self._target_id})
        return info["targetInfo"]["title"]

    async def url(self) -> str:
        info = await self.send("Target.getTargetInfo", {"targetId": self._target_id})
        return info["targetInfo"]["url"]

    async def cookies(self) -> list[Cookie]:
        result = await self.send("Network.getCookies")
        return [Cookie.model_validate(c) for c in result.get("cookies") or ()]

    async def screenshot(
        self,
        *,
        selector: str | None = None,
        full_page: bool = False,
        format: Literal["png", "jpeg", "webp"] = "png",
        quality: int | None = None,
    ) -> bytes:
        params: dict[str, Any] = {"format": format, "captureBeyondViewport": full_page}
        if quality is not None and format != "png":
            params["quality"] = quality
        if selector is not None:
            params["clip"] = await self._clip(selector)
        elif full_page:
            metrics = await self.send("Page.getLayoutMetrics")
            size = metrics.get("cssContentSize") or metrics["contentSize"]
            params["clip"] = {"x": 0, "y": 0, "scale": 1, **size}
        shot = await self.send("Page.captureScreenshot", params)
        return base64.b64decode(shot["data"])

    async def _clip(self, selector: str) -> dict[str, float]:
        if (node_id := await self._node_id(selector)) is None:
            raise ValueError(f"nothing matches {selector!r}")
        await self.send("DOM.scrollIntoViewIfNeeded", {"nodeId": node_id})
        box = await self.send("DOM.getBoxModel", {"nodeId": node_id})
        metrics = await self.send("Page.getLayoutMetrics")
        viewport = metrics.get("cssVisualViewport") or metrics.get("visualViewport") or {}
        quad = box["model"]["content"]
        return {
            "x": quad[0] + viewport.get("pageX", 0),
            "y": quad[1] + viewport.get("pageY", 0),
            "width": quad[2] - quad[0],
            "height": quad[5] - quad[1],
            "scale": 1,
        }

    def _on_lifecycle(self, event: dict[str, Any]) -> None:
        if event.get("frameId") != self._frame_id:
            return
        name, loader_id = event.get("name", ""), event.get("loaderId")
        if name == "init":
            self._loader_id, self._milestones = loader_id, set()
            self._world_id = None  # worlds belong to a document
        elif loader_id == self._loader_id:
            self._milestones.add(name)
        elif self._loader_id is None:  # replayed on attach: the document found there
            self._loader_id, self._milestones = loader_id, {name}
        if self._waiter is not None:
            self._waiter.observe(name, loader_id)

    def _on_dialog(self, event: dict[str, Any]) -> None:
        # an open dialog blocks the page, and nobody in the cloud will click it
        kind, message = event.get("type", ""), event.get("message", "")
        handler = self.on_dialog or self._browser.on_dialog
        try:
            verdict = handler(kind, message) if handler is not None else None
        except Exception:
            logger.exception("on_dialog failed, falling back to the default")
            verdict = None
        if verdict is None:
            verdict = kind == "beforeunload"  # leave the page; dismiss the rest
        params: dict[str, Any] = {"accept": verdict is not False}
        if isinstance(verdict, str):
            params["promptText"] = verdict
        logger.info(
            "%s dialog %r %s",
            kind,
            message,
            "accepted" if params["accept"] else "dismissed",
        )
        self._spawn(self._answer_dialog(params))

    async def _answer_dialog(self, params: dict[str, Any]) -> None:
        await anyio.sleep(random.uniform(*DIALOG_DELAY))  # as a person would
        await self._send("Page.handleJavaScriptDialog", params)

    def _on_request_paused(self, event: dict[str, Any]) -> None:
        request_id = event.get("requestId")
        if request_id is None:
            logger.warning("Fetch.requestPaused without a requestId")
            return
        if (status := event.get("responseStatusCode")) is not None:
            if event.get("frameId") == self._frame_id:
                self._status = status
            self._spawn(self._send("Fetch.continueResponse", {"requestId": request_id}))
        elif event.get("responseErrorReason"):
            self._spawn(self._send("Fetch.continueRequest", {"requestId": request_id}))
        else:
            params = {"requestId": request_id, "errorReason": "BlockedByClient"}
            self._spawn(self._send("Fetch.failRequest", params))

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        self._browser._spawn(coro)
