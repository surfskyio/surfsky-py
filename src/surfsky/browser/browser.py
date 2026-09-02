import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from contextlib import asynccontextmanager, suppress
from types import TracebackType
from typing import TYPE_CHECKING, Any, Unpack

import anyio

from ..resources.profiles import SessionOptions
from ..types import Session
from .cdp import CDPClient, EventHandler
from .page import POLL_INTERVAL, Page, deadline

if TYPE_CHECKING:
    from ..client import AsyncSurfsky

logger = logging.getLogger("surfsky")

# The cloud stops a session after inactive_kill_timeout (30s default) without
# CDP traffic from the user
KEEPALIVE_INTERVAL = 10.0
KEEPALIVE_TIMEOUT = 10.0

# Pauses the document response so its HTTP status can be read, and nothing else
STATUS_PATTERN = {
    "urlPattern": "*",
    "resourceType": "Document",
    "requestStage": "Response",
}

# Every page target, present and future, attached on this socket and paused
# until its setup is done
AUTO_ATTACH = {
    "autoAttach": True,
    "waitForDebuggerOnStart": True,
    "flatten": True,
    "filter": [{"type": "page"}],
}

NON_WEB_SCHEMES = ("chrome-extension://", "devtools://", "chrome://")

# CDP Network.ResourceType values
RESOURCE_TYPES: dict[str, str] = {
    name.lower(): name
    for name in (
        "Stylesheet",
        "Image",
        "Media",
        "Font",
        "Script",
        "TextTrack",
        "XHR",
        "Fetch",
        "Prefetch",
        "EventSource",
        "WebSocket",
        "Manifest",
        "SignedExchange",
        "Ping",
        "CSPViolationReport",
        "Preflight",
        "FedCM",
        "Other",
    )
}


def normalize_blocked(resources: frozenset[str] | set[str] | None) -> frozenset[str]:
    blocked = frozenset(name.lower() for name in resources or ())
    if "document" in blocked:
        raise ValueError("blocking 'document' blocks the page itself")
    if unknown := blocked - RESOURCE_TYPES.keys():
        raise ValueError(
            f"unknown resource types {sorted(unknown)}; valid: {sorted(RESOURCE_TYPES)}"
        )
    return blocked


def normalize_urls(patterns: Sequence[str] | None) -> tuple[str, ...]:
    # a str is a Sequence too: "*.png" would become "*", ".", "p"... and "*" blocks all
    if isinstance(patterns, str):
        raise TypeError("block_urls takes a list of patterns, not 1 string")
    return tuple(patterns or ())


class Browser(Page):
    """A connected cloud browser, and the page its session started with."""

    def __init__(
        self,
        session: Session,
        *,
        block_resources: frozenset[str] | set[str] | None = None,
        block_urls: Sequence[str] | None = None,
        connect_timeout: float = 30.0,
        command_timeout: float | None = 60.0,
    ) -> None:
        super().__init__(self, "", "")
        self.session = session
        self.data: dict[str, Any] = {}
        self.blocked_resources = normalize_blocked(block_resources)
        self.blocked_urls = normalize_urls(block_urls)
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self._client: CDPClient | None = None
        self._pages: dict[str, Page] = {}
        self._pending: set[asyncio.Task[None]] = set()
        self._retired = False
        self._use_count = 0

    @property
    def internal_uuid(self) -> str:
        return self.session.internal_uuid

    @property
    def cdp(self) -> CDPClient:
        if self._client is None:
            raise RuntimeError("browser is not connected; call connect() first")
        return self._client

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected

    @property
    def should_recycle(self) -> bool:
        return self._retired or not self.connected

    @property
    def use_count(self) -> int:
        """Leases so far, the current one included.

        Rotate identity from your own loop::

            if browser.use_count >= 20:
                browser.retire()
        """
        return self._use_count

    @property
    def pages(self) -> list[Page]:
        return list(self._pages.values())

    @property
    def _fetch_patterns(self) -> list[dict[str, Any]]:
        # what a page's Fetch.enable intercepts
        patterns: list[dict[str, Any]] = [
            {"urlPattern": "*", "resourceType": RESOURCE_TYPES[name]}
            for name in sorted(self.blocked_resources)
        ]
        patterns.extend({"urlPattern": pattern} for pattern in self.blocked_urls)
        return [*patterns, STATUS_PATTERN]

    def retire(self) -> None:
        self._retired = True

    async def new_page(self) -> Page:
        with deadline(self.command_timeout, "the new page did not open"):
            created = await self.cdp.send(
                "Target.createTarget", {"url": "about:blank", "newWindow": True}
            )
            return await self._page_ready(created["targetId"])

    async def wait_for_page(
        self, action: Awaitable[Any], *, timeout: float = 30.0
    ) -> Page:
        """Run ``action``, the click that opens a window, and return the page it
        opened::

            popup = await browser.wait_for_page(browser.click("a[target=_blank]"))

        A site can open the window after an async step, so ``pages[-1]`` right
        after the click may still be this page.
        """
        before = set(self._pages)
        with deadline(timeout, "no page opened"):
            await action
            while not (opened := [p for s, p in self._pages.items() if s not in before]):
                self._require_open()
                await anyio.sleep(POLL_INTERVAL)
        return opened[-1]

    async def connect(self) -> None:
        if self._client is not None:
            raise RuntimeError("browser is already connected")
        handler = self.on_dialog  # survives a reconnect
        Page.__init__(self, self, "", "")
        self.on_dialog = handler
        self._pages = {}
        try:
            with deadline(self.connect_timeout, "could not connect"):
                self._client = client = CDPClient(
                    self.session.ws_url, on_close=self._on_disconnect
                )
                await client.start()
                client.on("Target.attachedToTarget", self._on_attached)
                client.on("Target.detachedFromTarget", self._on_detached)
                client.on("Inspector.targetCrashed", self._to_page(self._on_crashed))
                client.on("Page.lifecycleEvent", self._to_page(Page._on_lifecycle))
                client.on("Fetch.requestPaused", self._to_page(Page._on_request_paused))
                client.on("Network.responseReceived", self._to_page(Page._on_response))
                client.on("Network.loadingFinished", self._to_page(Page._on_body_ready))
                client.on("Page.javascriptDialogOpening", self._to_page(Page._on_dialog))
                await client.send("Target.setAutoAttach", AUTO_ATTACH)
                await client.send("Target.getTargets")
                if not self._session_id:  # nothing to reuse
                    created = await client.send(
                        "Target.createTarget", {"url": "about:blank"}
                    )
                    await self._page_ready(created["targetId"])
                await self._wait_ready()
                self._spawn(self._keepalive(client))
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        with anyio.move_on_after(5, shield=True):
            pending, self._pending = list(self._pending), set()
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            client, self._client = self._client, None
            if client is not None:
                with suppress(Exception):
                    await client.stop()
        self._closed = True

    async def __aenter__(self) -> "Browser":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def _end_lease(self) -> None:
        for page in self.pages:
            if page is not self:
                await page.close()
        await self.stop_capturing()
        self.on_dialog = None

    async def _page_ready(self, target_id: str) -> Page:
        while (page := self._page_for(target_id)) is None:
            await anyio.sleep(POLL_INTERVAL)
        await page._wait_ready()
        return page

    def _page_for(self, target_id: str) -> Page | None:
        return next((p for p in self._pages.values() if p._target_id == target_id), None)

    async def _keepalive(self, client: CDPClient) -> None:
        while True:
            await anyio.sleep(KEEPALIVE_INTERVAL)
            try:
                with anyio.fail_after(KEEPALIVE_TIMEOUT):
                    await client.send("Browser.getVersion")
            except Exception as exc:
                logger.warning(f"keepalive failed for {self.internal_uuid}: {exc}")
                self.retire()
                return

    def _on_attached(self, event: dict[str, Any], session_id: str | None) -> None:
        info, session = event["targetInfo"], event["sessionId"]
        waiting = bool(event.get("waitingForDebugger"))
        if info.get("type") != "page" or info.get("url", "").startswith(NON_WEB_SCHEMES):
            self._spawn(self._let_go(session, waiting))
            return
        if self._session_id:
            page = Page(self, info["targetId"], session)
        else:  # the 1 web page is the browser itself
            page = self
            self._target_id, self._session_id = info["targetId"], session
        page._frame_id = page._target_id  # the page target's main frame
        page._ready = anyio.Event()  # commands on it wait for the setup
        self._pages[session] = page
        self._spawn(page._setup(waiting))

    async def _let_go(self, session: str, waiting: bool) -> None:
        if waiting:
            await self.cdp.send("Runtime.runIfWaitingForDebugger", session_id=session)
        await self.cdp.send("Target.detachFromTarget", {"sessionId": session})

    def _on_detached(self, event: dict[str, Any], session_id: str | None) -> None:
        if (page := self._pages.get(event.get("sessionId", ""))) is not None:
            self._drop(page)

    def _on_crashed(self, page: Page, event: dict[str, Any]) -> None:
        self._drop(page)

    def _drop(self, page: Page) -> None:
        self._pages.pop(page._session_id, None)
        page._closed = True
        if page._waiter is not None:
            page._waiter.fail("page closed")
        if page is self:
            logger.warning(f"page target gone for {self.internal_uuid}")
            self.retire()

    def _to_page(self, handler: Callable[[Page, dict[str, Any]], None]) -> EventHandler:
        def route(event: dict[str, Any], session_id: str | None) -> None:
            if (page := self._pages.get(session_id or "")) is not None:
                handler(page, event)

        return route

    def _on_disconnect(self) -> None:
        for page in self._pages.values():
            page._closed = True
            if page._waiter is not None:
                page._waiter.fail("CDP connection closed")

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._reap)

    def _reap(self, task: asyncio.Task[Any]) -> None:
        self._pending.discard(task)
        if not task.cancelled():
            task.exception()


@asynccontextmanager
async def start_session(
    client: "AsyncSurfsky",
    *,
    profile_uuid: str | None = None,
    block_resources: frozenset[str] | set[str] | None = None,
    block_urls: Sequence[str] | None = None,
    **options: Unpack[SessionOptions],
) -> AsyncIterator[Browser]:
    blocked = normalize_blocked(block_resources)
    urls = normalize_urls(block_urls)
    async with (
        client.session(profile_uuid=profile_uuid, **options) as session,
        Browser(session, block_resources=blocked, block_urls=urls) as browser,
    ):
        yield browser
