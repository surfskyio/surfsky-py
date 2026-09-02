import asyncio
import base64

import anyio
import pytest

from conftest import PostsViaSend
from surfsky.browser import CapturedResponse
from surfsky.browser import browser as mod
from surfsky.browser import page as page_mod
from surfsky.browser.browser import Browser
from surfsky.browser.cdp import CDPError
from surfsky.client import stop_session
from surfsky.errors import BrowserTimeoutError, SurfskyError
from surfsky.types import Cookie, Session

HEAVY = {"image", "media", "font", "stylesheet"}


def make_browser(**kwargs) -> Browser:
    return Browser(Session(internal_uuid="s1", ws_url="wss://fake/proxy/s1"), **kwargs)


class FakeNavigateCDP:
    """Just enough of the CDP client for goto(): a Page.navigate that can fire
    lifecycle events before its result is returned."""

    def __init__(self, navigate_result: dict, before_return=None) -> None:
        self._result = navigate_result
        self._before_return = before_return

    async def send(self, method, params=None, session_id=None):
        assert method == "Page.navigate"
        if self._before_return is not None:
            self._before_return()
        return self._result


def _goto_browser(navigate_result: dict, before_return=None) -> Browser:
    browser = make_browser()
    browser._client = FakeNavigateCDP(navigate_result, before_return)  # type: ignore[assignment]
    browser._session_id = "sid"
    browser._frame_id = "F"
    browser._pages = {"sid": browser}
    return browser


@pytest.mark.anyio
async def test_goto_ignores_stale_lifecycle_event():
    def fire_stale():
        browser._on_lifecycle({"name": "load", "frameId": "F", "loaderId": "OLD"})

    browser = _goto_browser({"loaderId": "NEW"}, before_return=fire_stale)
    fired: list[str] = []

    async def fire_new_later():
        await asyncio.sleep(0.01)
        fired.append("new")
        browser._on_lifecycle({"name": "load", "frameId": "F", "loaderId": "NEW"})

    task = asyncio.create_task(fire_new_later())
    await browser.goto("https://x", wait_until="load", timeout=2)
    assert fired == ["new"]
    await task


@pytest.mark.anyio
async def test_goto_accepts_event_seen_before_navigate_returns():
    def fire_new_early():
        browser._on_lifecycle({"name": "load", "frameId": "F", "loaderId": "NEW"})

    browser = _goto_browser({"loaderId": "NEW"}, before_return=fire_new_early)
    await browser.goto("https://x", wait_until="load", timeout=1)


@pytest.mark.anyio
async def test_goto_follows_a_navigation_that_replaced_the_document():
    # a head script redirect: document A never reaches load, B does
    browser = _goto_browser({"loaderId": "A"})

    async def redirect_later():
        await asyncio.sleep(0.01)
        browser._on_lifecycle({"name": "DOMContentLoaded", "frameId": "F", "loaderId": "A"})
        browser._on_lifecycle({"name": "init", "frameId": "F", "loaderId": "B"})
        browser._on_lifecycle({"name": "load", "frameId": "F", "loaderId": "B"})

    task = asyncio.create_task(redirect_later())
    await browser.goto("https://x", wait_until="load", timeout=1)
    await task


@pytest.mark.anyio
async def test_goto_follows_a_replacement_seen_before_navigate_returns():
    def replace_early():
        browser._on_lifecycle({"name": "init", "frameId": "F", "loaderId": "A"})
        browser._on_lifecycle({"name": "init", "frameId": "F", "loaderId": "B"})
        browser._on_lifecycle({"name": "load", "frameId": "F", "loaderId": "B"})

    browser = _goto_browser({"loaderId": "A"}, before_return=replace_early)
    await browser.goto("https://x", wait_until="load", timeout=1)


@pytest.mark.anyio
async def test_goto_fails_fast_when_the_connection_drops():
    browser = _goto_browser({"loaderId": "NEW"})

    async def drop_later():
        await asyncio.sleep(0.01)
        browser._on_disconnect()

    task = asyncio.create_task(drop_later())
    with pytest.raises(CDPError, match="connection closed"):
        await browser.goto("https://x", wait_until="load", timeout=5)
    await task


@pytest.mark.anyio
async def test_goto_same_document_navigation_returns_immediately():
    # fragment navigations return no loaderId and emit no lifecycle events
    browser = _goto_browser({})
    await browser.goto("https://x#anchor", wait_until="networkidle", timeout=1)


@pytest.mark.anyio
async def test_goto_timeout_covers_the_wait():
    browser = _goto_browser({"loaderId": "NEW"})
    with pytest.raises(TimeoutError, match="did not reach 'load'"):
        await browser.goto("https://x", wait_until="load", timeout=0.05)


@pytest.mark.anyio
async def test_goto_rejects_concurrent_navigation():
    browser = _goto_browser({"loaderId": "NEW"})
    first = asyncio.create_task(browser.goto("https://a", wait_until="load", timeout=1))
    await asyncio.sleep(0.01)  # let the first goto install its waiter
    with pytest.raises(RuntimeError, match="navigation already in progress"):
        await browser.goto("https://b")
    browser._on_lifecycle({"name": "load", "frameId": "F", "loaderId": "NEW"})
    await first


@pytest.mark.anyio
async def test_goto_navigation_error_is_a_cdp_error():
    browser = _goto_browser({"errorText": "net::ERR_NAME_NOT_RESOLVED"})
    with pytest.raises(CDPError, match="ERR_NAME_NOT_RESOLVED"):
        await browser.goto("https://nope.test", timeout=1)


def test_blocking_the_document_is_refused():
    # it would answer every navigation with ERR_BLOCKED_BY_CLIENT
    with pytest.raises(ValueError, match="blocks the page itself"):
        make_browser(block_resources={"document", "image"})


def test_unknown_blocked_resource_raises():
    with pytest.raises(ValueError, match="unknown resource types"):
        make_browser(block_resources={"imgae"})


class RecordingCDP:
    """Returns canned results per method and records what was sent. Script runs
    in an isolated world, so that one is always on offer."""

    def __init__(self, results: dict) -> None:
        self.results = {"Page.createIsolatedWorld": {"executionContextId": 7}, **results}
        self.calls: list[tuple[str, dict]] = []

    async def send(self, method, params=None, session_id=None):
        self.calls.append((method, params or {}))
        return self.results[method]

    def sent(self, method: str) -> list[dict]:
        return [params for m, params in self.calls if m == method]


@pytest.mark.anyio
async def test_send_before_connect_is_a_clear_error():
    with pytest.raises(RuntimeError, match="not connected"):
        await make_browser().click("#x")


@pytest.mark.anyio
async def test_screenshot_passes_quality_for_webp():
    browser = make_browser()
    browser._client = RecordingCDP({  # type: ignore[assignment]
        "Page.captureScreenshot": {"data": base64.b64encode(b"img").decode()},
    })
    assert await browser.screenshot(format="webp", quality=30) == b"img"
    method, params = browser._client.calls[0]
    assert method == "Page.captureScreenshot"
    assert params["quality"] == 30


@pytest.mark.anyio
async def test_content_serializes_the_dom_without_running_script():
    browser = make_browser()
    browser._client = RecordingCDP({  # type: ignore[assignment]
        "DOM.getDocument": {"root": {"nodeId": 1}},
        "DOM.getOuterHTML": {"outerHTML": "<!DOCTYPE html><html></html>"},
    })
    assert await browser.content() == "<!DOCTYPE html><html></html>"
    assert "Runtime.evaluate" not in [m for m, _ in browser._client.calls]


@pytest.mark.anyio
async def test_title_reads_the_target_without_running_script():
    browser = make_browser()
    browser._target_id = "T"
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {"Target.getTargetInfo": {"targetInfo": {"title": "Hi"}}}
    )
    assert await browser.title() == "Hi"
    assert [m for m, _ in browser._client.calls] == ["Target.getTargetInfo"]


@pytest.mark.anyio
async def test_url_reads_the_target_without_running_script():
    browser = make_browser()
    browser._target_id = "T"
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {"Target.getTargetInfo": {"targetInfo": {"url": "https://example.com/"}}}
    )
    assert await browser.url() == "https://example.com/"
    assert [m for m, _ in browser._client.calls] == ["Target.getTargetInfo"]


@pytest.mark.anyio
async def test_cookies_parse_into_the_import_format():
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {
            "Network.getCookies": {
                "cookies": [
                    {
                        "name": "sess",
                        "value": "abc",
                        "domain": "example.com",
                        "path": "/",
                        "expires": 1800000000,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                ]
            }
        }
    )
    cookies = await browser.cookies()
    assert [c.name for c in cookies] == ["sess"]
    assert cookies[0].http_only is True
    # the wire name the import endpoint expects, not the one Network.getCookies sent
    assert cookies[0].model_dump(by_alias=True)["expirationDate"] == 1800000000


@pytest.mark.anyio
async def test_cookies_on_a_page_without_any():
    browser = make_browser()
    browser._client = RecordingCDP({"Network.getCookies": {}})  # type: ignore[assignment]
    assert await browser.cookies() == []


@pytest.mark.anyio
async def test_a_failed_command_names_itself():
    class FailingCDP:
        async def send(self, method, params=None, session_id=None):
            raise CDPError("Element not found in the main document within 30s: #nope")

    browser = make_browser()
    browser._client = FailingCDP()  # type: ignore[assignment]
    with pytest.raises(CDPError, match=r"Human.click '#nope': Element not found"):
        await browser.click("#nope")


@pytest.mark.anyio
async def test_a_stale_node_is_reported_as_the_navigation_race_it_is():
    class RacingCDP:
        async def send(self, method, params=None, session_id=None):
            raise CDPError(
                "CDP Error: {'code': -32000, 'message': "
                "'Could not find node with given id'}",
                code=-32000,
            )

    browser = make_browser()
    browser._client = RacingCDP()  # type: ignore[assignment]
    with pytest.raises(
        CDPError, match="the page navigated while the command ran"
    ) as caught:
        await browser.hover("#flash")
    assert "Human.moveTo '#flash'" in str(caught.value)
    assert caught.value.code == -32000


DOCUMENT = {"root": {"nodeId": 1}}


@pytest.mark.anyio
async def test_wait_for_returns_once_the_element_has_a_box():
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {
            "DOM.getDocument": DOCUMENT,
            "DOM.querySelector": {"nodeId": 2},
            "DOM.getBoxModel": {"model": {"width": 80, "height": 20}},
        }
    )
    await browser.wait_for_selector("#ready", timeout=1)
    assert "Runtime.evaluate" not in [m for m, _ in browser._client.calls]


@pytest.mark.anyio
async def test_wait_for_ignores_a_box_of_no_size():
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {
            "DOM.getDocument": DOCUMENT,
            "DOM.querySelector": {"nodeId": 2},
            "DOM.getBoxModel": {"model": {"width": 0, "height": 0}},
        }
    )
    with pytest.raises(TimeoutError, match="was not visible"):
        await browser.wait_for_selector("#collapsed", timeout=0.2)


@pytest.mark.anyio
async def test_wait_for_attached_skips_the_box_lookup():
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {"DOM.getDocument": DOCUMENT, "DOM.querySelector": {"nodeId": 2}}
    )
    await browser.wait_for_selector("#hidden", visible=False, timeout=1)
    assert "DOM.getBoxModel" not in [m for m, _ in browser._client.calls]


@pytest.mark.anyio
async def test_wait_for_polls_through_a_document_replaced_under_it():
    class RacingCDP(RecordingCDP):
        def __init__(self, results, swaps):
            super().__init__(results)
            self.swaps = swaps

        async def send(self, method, params=None, session_id=None):
            self.calls.append((method, params or {}))
            if method == "DOM.querySelector" and self.swaps > 0:
                self.swaps -= 1
                raise CDPError("Could not find node with given id", code=-32000)
            return self.results[method]

    browser = make_browser()
    browser._client = RacingCDP(  # type: ignore[assignment]
        {
            "DOM.getDocument": DOCUMENT,
            "DOM.querySelector": {"nodeId": 2},
            "DOM.getBoxModel": {"model": {"width": 80, "height": 20}},
        },
        swaps=2,
    )
    await browser.wait_for_selector("#late", timeout=5)
    queries = [m for m, _ in browser._client.calls if m == "DOM.querySelector"]
    assert len(queries) == 3


@pytest.mark.anyio
async def test_wait_for_names_what_never_showed_up():
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {"DOM.getDocument": DOCUMENT, "DOM.querySelector": {"nodeId": 0}}
    )
    with pytest.raises(TimeoutError, match=r"'#nope' was not visible within 0.2s"):
        await browser.wait_for_selector("#nope", timeout=0.2)


@pytest.mark.anyio
async def test_is_visible_answers_without_waiting():
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {"DOM.getDocument": DOCUMENT, "DOM.querySelector": {"nodeId": 0}}
    )
    assert await browser.is_visible("#nope") is False


@pytest.mark.anyio
async def test_html_returns_one_element_and_none_for_a_miss():
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {
            "DOM.getDocument": DOCUMENT,
            "DOM.querySelector": {"nodeId": 2},
            "DOM.getOuterHTML": {"outerHTML": "<h1>Hi</h1>"},
        }
    )
    assert await browser.outer_html("h1") == "<h1>Hi</h1>"

    browser._client = RecordingCDP(  # type: ignore[assignment]
        {"DOM.getDocument": DOCUMENT, "DOM.querySelector": {"nodeId": 0}}
    )
    assert await browser.outer_html("h1") is None


@pytest.mark.anyio
async def test_set_cookies_speaks_cdp_whatever_it_is_given():
    browser = make_browser()
    browser._client = RecordingCDP({"Network.setCookies": {}})  # type: ignore[assignment]
    await browser.set_cookies(
        [
            Cookie(
                domain="example.com", name="model", value="1", expiration_date=1800000000
            ),
            {
                "name": "read-back",
                "value": "2",
                "domain": "example.com",
                "expires": 42,
                "size": 9,
                "session": False,
            },
            {"name": "plain", "value": "3", "domain": "example.com"},
        ]
    )
    sent = browser._client.calls[0][1]["cookies"]
    # the export calls the expiry expirationDate, CDP calls it expires
    assert sent[0] == {
        "expires": 1800000000,
        "domain": "example.com",
        "name": "model",
        "value": "1",
        "expirationDate": 1800000000,
    }
    # size and session come back from a read; Chrome ignores what it does not know
    assert sent[1] == {
        "name": "read-back",
        "value": "2",
        "domain": "example.com",
        "expires": 42,
        "size": 9,
        "session": False,
    }
    assert sent[2] == {"name": "plain", "value": "3", "domain": "example.com"}


@pytest.mark.anyio
async def test_wait_for_url_polls_until_the_navigation_lands():
    class MovingCDP(RecordingCDP):
        def __init__(self):
            super().__init__({})
            self.urls = iter(
                ["https://a.test/", "https://a.test/", "https://a.test/done"]
            )

        async def send(self, method, params=None, session_id=None):
            self.calls.append((method, params or {}))
            return {"targetInfo": {"url": next(self.urls)}}

    browser = make_browser()
    browser._client = MovingCDP()  # type: ignore[assignment]
    assert await browser.wait_for_url("/done", timeout=5) == "https://a.test/done"


@pytest.mark.anyio
async def test_wait_for_url_names_where_it_never_got():
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {"Target.getTargetInfo": {"targetInfo": {"url": "https://a.test/"}}}
    )
    with pytest.raises(TimeoutError, match=r"did not reach '/secure' within 0.2s"):
        await browser.wait_for_url("/secure", timeout=0.2)


@pytest.mark.anyio
async def test_an_element_screenshot_clips_to_its_box_in_page_coordinates():
    # the box is viewport-relative and the clip page-relative: an element that
    # had to be scrolled into view came back blank without the offset
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {
            "DOM.getDocument": DOCUMENT,
            "DOM.querySelector": {"nodeId": 2},
            "DOM.scrollIntoViewIfNeeded": {},
            "DOM.getBoxModel": {"model": {"content": [10, 20, 90, 20, 90, 60, 10, 60]}},
            "Page.getLayoutMetrics": {"cssVisualViewport": {"pageX": 0, "pageY": 3000}},
            "Page.captureScreenshot": {"data": "aW1n"},
        }
    )
    assert await browser.screenshot(selector="#card") == b"img"
    params = dict(browser._client.calls)["Page.captureScreenshot"]
    assert params["clip"] == {"x": 10, "y": 3020, "width": 80, "height": 40, "scale": 1}


@pytest.mark.anyio
async def test_fill_selects_what_is_there_before_typing():
    browser = make_browser()
    browser._client = RecordingCDP({"Human.click": {}, "Human.type": {}})  # type: ignore[assignment]
    await browser.fill("#q", "hello")
    assert browser._client.calls == [
        ("Human.click", {"selector": "#q", "clickCount": 3}),
        ("Human.type", {"text": "hello"}),
    ]


@pytest.mark.anyio
async def test_reload_uses_page_reload_and_waits_for_the_new_document():
    # not a navigation to the same URL: with a fragment that is a same-document no-op
    class ReloadingCDP(RecordingCDP):
        async def send(self, method, params=None, session_id=None):
            self.calls.append((method, params or {}))
            if method == "Page.reload":  # Chrome can report the document before it answers
                browser._on_lifecycle({"name": "init", "frameId": "F", "loaderId": "R"})
                browser._on_lifecycle({"name": "load", "frameId": "F", "loaderId": "R"})
            return {}

    browser = _goto_browser({})
    browser._client = ReloadingCDP({})  # type: ignore[assignment]
    await browser.reload(timeout=1)
    assert browser._client.sent("Page.reload") == [{}]
    assert browser._client.sent("Page.navigate") == []


@pytest.mark.anyio
async def test_reload_waits_for_a_document_that_inits_after_the_reply():
    browser = _goto_browser({})
    browser._client = RecordingCDP({"Page.reload": {}})  # type: ignore[assignment]

    async def load_later():
        await asyncio.sleep(0.01)
        browser._on_lifecycle({"name": "init", "frameId": "F", "loaderId": "R"})
        browser._on_lifecycle({"name": "load", "frameId": "F", "loaderId": "R"})

    task = asyncio.create_task(load_later())
    await browser.reload(timeout=1)
    await task


@pytest.mark.anyio
async def test_a_commands_own_timeout_keeps_its_message_inside_a_longer_wait():
    # BrowserTimeoutError is a TimeoutError: the navigation's deadline must not
    # relabel the command's, which fired first
    async def hang(method, params=None, session_id=None):
        await asyncio.sleep(10)

    browser = _goto_browser({})
    browser._client.send = hang  # type: ignore[method-assign]
    browser.command_timeout = 0.05
    with pytest.raises(BrowserTimeoutError, match=r"Page.navigate did not answer within 0.05s"):
        await browser.goto("https://x", timeout=5)


@pytest.mark.anyio
async def test_evaluate_does_not_call_a_function_it_was_only_asked_to_read():
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {"Runtime.evaluate": {"result": {"type": "function", "className": "Function"}}}
    )
    assert await browser.evaluate("window.fetch") is None
    assert len(browser._client.sent("Runtime.evaluate")) == 1


class HistoryCDP(RecordingCDP):
    """A navigation history that actually moves when it is told to."""

    def __init__(self, urls: list[str], index: int) -> None:
        super().__init__({})
        self.entries = [{"id": n, "url": url} for n, url in enumerate(urls)]
        self.index = index

    async def send(self, method, params=None, session_id=None):
        self.calls.append((method, params or {}))
        if method == "Page.getNavigationHistory":
            return {"currentIndex": self.index, "entries": self.entries}
        if method == "Page.navigateToHistoryEntry":
            self.index = params["entryId"]
        if method == "Target.getTargetInfo":
            return {"targetInfo": {"url": self.entries[self.index]["url"]}}
        return {}


@pytest.mark.anyio
async def test_back_and_forward_walk_the_history():
    browser = make_browser()
    browser._client = HistoryCDP(["https://a.test/", "https://a.test/?p=2"], index=1)  # type: ignore[assignment]
    # the URL left behind contains the one we are going to, so only the index tells
    assert await browser.go_back() == "https://a.test/"
    assert await browser.go_forward() == "https://a.test/?p=2"


@pytest.mark.anyio
async def test_history_ends_are_none_not_errors():
    browser = make_browser()
    browser._client = HistoryCDP(["https://a.test/"], index=0)  # type: ignore[assignment]
    assert await browser.go_back() is None
    assert await browser.go_forward() is None
    assert "Page.navigateToHistoryEntry" not in [m for m, _ in browser._client.calls]


@pytest.mark.anyio
async def test_local_storage_reads_and_writes_the_frame_origin():
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {
            "DOMStorage.enable": {},
            "Page.getFrameTree": {"frameTree": {"frame": {"id": "F", "storageKey": "K"}}},
            "DOMStorage.getDOMStorageItems": {"entries": [["token", "abc"]]},
            "DOMStorage.setDOMStorageItem": {},
        }
    )
    await browser.set_local_storage({"token": "abc"})
    assert await browser.local_storage() == {"token": "abc"}
    written = dict(browser._client.calls)["DOMStorage.setDOMStorageItem"]
    assert written["storageId"] == {"isLocalStorage": True, "storageKey": "K"}
    assert [m for m, _ in browser._client.calls].count("DOMStorage.enable") == 1


@pytest.mark.anyio
async def test_capture_responses_needs_a_fragment_and_arms_the_network_domain(fake_cdp):
    browser = make_browser()
    await browser.connect()
    with pytest.raises(ValueError, match="at least one URL fragment"):
        await browser.capture_responses()
    assert "Network.enable" not in fake_cdp.calls

    await browser.capture_responses("/api/")
    await browser.capture_responses("/graphql")
    assert fake_cdp.calls.count("Network.enable") == 1


@pytest.mark.anyio
async def test_wait_for_response_says_when_nothing_was_armed():
    browser = make_browser()
    with pytest.raises(RuntimeError, match="capture_responses"):
        await browser.wait_for_response("/api/")


@pytest.mark.anyio
async def test_a_captured_response_carries_its_body(monkeypatch):
    class BodyCDP(FakeConnectCDP):
        async def send(self, method, params=None, session_id=None):
            if method == "Network.getResponseBody":
                return {"body": '{"ok": true}', "base64Encoded": False}
            return await super().send(method, params, session_id)

    fake = BodyCDP()
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)
    browser = make_browser()
    await browser.connect()
    await browser.capture_responses("/api/")

    on_response = fake.handlers["Network.responseReceived"]
    on_finished = fake.handlers["Network.loadingFinished"]
    on_response(
        {
            "requestId": "R1",
            "response": {
                "url": "https://a.test/api/search",
                "status": 200,
                "headers": {"content-type": "application/json"},
            },
        },
        "S",
    )
    on_response({"requestId": "R2", "response": {"url": "https://a.test/logo.png"}}, "S")
    running = set(browser._pending)  # the keepalive loop, which never finishes
    on_finished({"requestId": "R2"}, "S")  # never matched, so nothing to fetch
    on_finished({"requestId": "R1"}, "S")
    await asyncio.gather(*(set(browser._pending) - running))

    captured = await browser.wait_for_response("/api/", timeout=1)
    assert (captured.url, captured.status) == ("https://a.test/api/search", 200)
    assert captured.json() == {"ok": True}
    assert [r.url for r in browser.responses] == ["https://a.test/api/search"]


@pytest.mark.anyio
async def test_a_body_still_being_fetched_at_stop_capturing_is_dropped(monkeypatch):
    # the lease ended while the body was in flight: it must not surface in the next one
    release = asyncio.Event()

    class SlowBodyCDP(FakeConnectCDP):
        async def send(self, method, params=None, session_id=None):
            if method == "Network.getResponseBody":
                await release.wait()
                return {"body": "late", "base64Encoded": False}
            return await super().send(method, params, session_id)

    fake = SlowBodyCDP()
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)
    async with make_browser() as browser:
        await browser.capture_responses("/api/")
        fake.handlers["Network.responseReceived"](
            {"requestId": "R1", "response": {"url": "https://a.test/api/x", "status": 200}}, "S"
        )
        running = set(browser._pending)
        fake.handlers["Network.loadingFinished"]({"requestId": "R1"}, "S")
        await browser.stop_capturing()
        release.set()
        await asyncio.gather(*(set(browser._pending) - running))
        assert browser.responses == []


@pytest.mark.anyio
async def test_send_applies_the_command_timeout():
    class WedgedCDP:
        async def send(self, method, params=None, session_id=None):
            await asyncio.sleep(10)

    browser = make_browser(command_timeout=0.05)
    browser._client = WedgedCDP()  # type: ignore[assignment]
    with pytest.raises(BrowserTimeoutError, match="Human.click"):
        await browser.send("Human.click", {})


@pytest.mark.anyio
async def test_stop_session_survives_cancellation():
    # unshielded awaits die inside a cancelled scope; the stop must still run
    stopped: list[str] = []

    class FakeProfiles:
        async def stop(self, uuid: str) -> None:
            await anyio.sleep(0.01)  # a checkpoint, like a real HTTP call
            stopped.append(uuid)

    class FakeClient:
        profiles = FakeProfiles()

        def with_options(self, **kwargs):
            return self

    with anyio.CancelScope() as scope:
        scope.cancel()
        await stop_session(FakeClient(), "sess-1")  # type: ignore[arg-type]
    assert stopped == ["sess-1"]


class FakeConnectCDP(PostsViaSend):
    """Fakes the full connect() handshake and records what was sent."""

    def __init__(self, start_delay: float = 0.0, targets: list[dict] | None = None) -> None:
        self.targets = targets or [{"type": "page", "targetId": "T"}]
        self.fetch_enable_params: dict | None = None
        self.calls: list[str] = []
        self.handlers: dict = {}
        self.connected = True
        self._start_delay = start_delay

    def on(self, event, handler) -> None:
        self.handlers[event] = handler

    async def start(self):
        if self._start_delay:
            await asyncio.sleep(self._start_delay)

    async def stop(self):
        self.connected = False

    async def send(self, method, params=None, session_id=None):
        self.calls.append(method)
        match method:
            case "Target.setAutoAttach":
                for index, info in enumerate(self.targets):
                    self.handlers["Target.attachedToTarget"]({
                        "sessionId": "S" + (str(index) if index else ""),
                        "targetInfo": {"url": "about:blank", **info},
                        "waitingForDebugger": False,
                    }, None)
            case "Fetch.enable":
                self.fetch_enable_params = params
        return {}


@pytest.fixture
def fake_cdp(monkeypatch) -> FakeConnectCDP:
    fake = FakeConnectCDP()
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)
    return fake


@pytest.mark.anyio
async def test_connect_enables_fetch_with_resource_type_patterns(fake_cdp):
    browser = make_browser(block_resources=HEAVY)
    await browser.connect()
    assert fake_cdp.fetch_enable_params == {
        "patterns": [
            {"urlPattern": "*", "resourceType": "Font"},
            {"urlPattern": "*", "resourceType": "Image"},
            {"urlPattern": "*", "resourceType": "Media"},
            {"urlPattern": "*", "resourceType": "Stylesheet"},
            mod.STATUS_PATTERN,
        ]
    }


@pytest.mark.anyio
async def test_a_paused_document_reports_its_status_and_is_resumed(fake_cdp):
    browser = make_browser(block_resources={"image"})
    await browser.connect()
    paused = fake_cdp.handlers["Fetch.requestPaused"]

    running = set(browser._pending)  # the keepalive loop, which never finishes
    paused({"requestId": "R1", "responseStatusCode": 404, "frameId": "T"}, "S")
    paused({"requestId": "R2"}, "S")  # a blocked image, no status on the event
    await asyncio.gather(*(set(browser._pending) - running))

    assert browser.status == 404
    assert "Fetch.continueResponse" in fake_cdp.calls  # the document must not hang
    assert "Fetch.failRequest" in fake_cdp.calls


@pytest.mark.anyio
async def test_an_iframe_document_does_not_set_the_page_status(fake_cdp):
    # the Document pattern matches every frame's document, so a 404 ad frame
    # must not overwrite the page's 200
    async with make_browser() as browser:
        paused = fake_cdp.handlers["Fetch.requestPaused"]
        running = set(browser._pending)
        paused({"requestId": "R1", "responseStatusCode": 200, "frameId": "T"}, "S")
        paused({"requestId": "R2", "responseStatusCode": 404, "frameId": "child"}, "S")
        await asyncio.gather(*(set(browser._pending) - running))
        assert browser.status == 200
        assert fake_cdp.calls.count("Fetch.continueResponse") == 2


@pytest.mark.anyio
async def test_a_document_that_failed_to_load_keeps_its_own_error(fake_cdp):
    async with make_browser() as browser:
        running = set(browser._pending)
        fake_cdp.handlers["Fetch.requestPaused"](
            {"requestId": "R1", "responseErrorReason": "NameNotResolved", "frameId": "T"}, "S"
        )
        await asyncio.gather(*(set(browser._pending) - running))
        assert "Fetch.continueRequest" in fake_cdp.calls
        assert "Fetch.failRequest" not in fake_cdp.calls
        assert browser.status is None


class ReplayingCDP(FakeConnectCDP):
    async def send(self, method, params=None, session_id=None):
        if method == "Page.setLifecycleEventsEnabled":
            for name in ("commit", "DOMContentLoaded", "load"):
                self.handlers["Page.lifecycleEvent"](
                    {"name": name, "frameId": "T", "loaderId": "L0"}, session_id
                )
        return await super().send(method, params, session_id)


@pytest.mark.anyio
async def test_wait_for_load_before_any_goto_sees_the_replayed_state(monkeypatch):
    fake = ReplayingCDP()
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)
    async with make_browser() as browser:
        await browser.wait_for_load_state(timeout=0.2)


@pytest.mark.anyio
async def test_connect_timeout(monkeypatch):
    fake = FakeConnectCDP(start_delay=10)
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)
    browser = make_browser(connect_timeout=0.05)
    with pytest.raises(BrowserTimeoutError):
        await browser.connect()
    assert browser._client is None


class FailingFetchCDP(FakeConnectCDP):
    async def send(self, method, params=None, session_id=None):
        if method == "Fetch.enable":
            raise RuntimeError("Fetch.enable rejected")
        return await super().send(method, params, session_id)


@pytest.mark.anyio
async def test_connect_raises_a_failed_setup_command(monkeypatch):
    fake = FailingFetchCDP()
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)
    browser = make_browser(block_resources=HEAVY)
    with pytest.raises(RuntimeError, match="Fetch.enable rejected"):
        await browser.connect()
    assert not fake.connected


@pytest.mark.anyio
async def test_async_context_manager_connects_and_closes(fake_cdp):
    async with make_browser() as browser:
        assert browser._client is fake_cdp
        assert browser.cdp is fake_cdp
    assert browser._client is None


def test_public_exports_resolve():
    import surfsky

    for name in surfsky.__all__:
        assert hasattr(surfsky, name), name


@pytest.mark.anyio
async def test_connect_twice_raises(fake_cdp):
    async with make_browser() as browser:
        with pytest.raises(RuntimeError, match="already connected"):
            await browser.connect()  # would orphan the first socket


@pytest.mark.anyio
async def test_keepalive_pings_the_browser(monkeypatch, fake_cdp):
    monkeypatch.setattr(mod, "KEEPALIVE_INTERVAL", 0.01)
    async with make_browser():
        await asyncio.sleep(0.05)
    assert "Browser.getVersion" in fake_cdp.calls  # counts as activity server-side


def test_disconnected_browser_should_recycle():
    browser = make_browser()
    assert not browser.connected
    assert browser.should_recycle  # the pool must not retry items on a dead socket


@pytest.mark.anyio
async def test_start_session_stops_after_close(fake_cdp):
    from contextlib import asynccontextmanager

    from surfsky.browser.browser import start_session

    events: list[str] = []

    class FakeClient:
        @asynccontextmanager
        async def session(self, **kwargs):
            events.append("start")
            try:
                yield Session(internal_uuid="s1", ws_url="wss://fake")
            finally:
                events.append("stop")

    async with start_session(FakeClient()) as browser:  # type: ignore[arg-type]
        events.append("body")
        assert browser.connected
    assert events == ["start", "body", "stop"]
    assert not fake_cdp.connected


class HangingPingCDP(FakeConnectCDP):
    """Connects fine, then the socket half-opens: the ping is written but no
    reply ever comes back."""

    def __init__(self) -> None:
        super().__init__()
        self.pings = 0

    async def send(self, method, params=None, session_id=None):
        if method == "Browser.getVersion":
            self.pings += 1
            await asyncio.sleep(60)
        return await super().send(method, params, session_id)


@pytest.mark.anyio
async def test_a_wedged_keepalive_marks_the_browser_for_recycling(monkeypatch):
    monkeypatch.setattr(mod, "KEEPALIVE_INTERVAL", 0.01)
    monkeypatch.setattr(mod, "KEEPALIVE_TIMEOUT", 0.02)
    fake = HangingPingCDP()
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)

    browser = make_browser()
    await browser.connect()
    try:
        with anyio.fail_after(2):
            while not browser.should_recycle:
                await anyio.sleep(0.01)
    finally:
        await browser.close()
    assert fake.pings >= 1


class FailingPingCDP(FakeConnectCDP):
    async def send(self, method, params=None, session_id=None):
        if method == "Browser.getVersion":
            raise RuntimeError("browser is not answering")
        return await super().send(method, params, session_id)


@pytest.mark.anyio
async def test_a_failed_keepalive_marks_the_browser_for_recycling(monkeypatch):
    monkeypatch.setattr(mod, "KEEPALIVE_INTERVAL", 0.01)
    fake = FailingPingCDP()
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)

    browser = make_browser()
    await browser.connect()
    try:
        with anyio.fail_after(2):
            while not browser.should_recycle:
                await anyio.sleep(0.01)
    finally:
        await browser.close()


@pytest.mark.anyio
async def test_a_crashed_renderer_marks_the_browser_for_recycling(fake_cdp):
    async with make_browser() as browser:
        # Inspector.targetCrashed comes on the page's session; Target.targetCrashed
        # only comes with setDiscoverTargets, which nothing here turns on
        fake_cdp.handlers["Inspector.targetCrashed"]({}, "S")
        assert browser.should_recycle and browser.closed


class WedgedSendCDP(FakeConnectCDP):
    """Connects fine, then never answers a blocked request's failRequest."""

    async def send(self, method, params=None, session_id=None):
        if method in ("Fetch.failRequest", "Browser.getVersion"):
            self.calls.append(method)
            await asyncio.sleep(60)
        return await super().send(method, params, session_id)


@pytest.mark.anyio
async def test_blocking_a_request_does_not_park_a_task_forever(monkeypatch):
    fake = WedgedSendCDP()
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)
    browser = make_browser(block_resources=HEAVY, command_timeout=0.05)
    await browser.connect()
    try:
        before = set(browser._pending)  # the keepalive is in here and never ends
        browser._on_request_paused({"requestId": "r1"})
        blocked = (set(browser._pending) - before).pop()
        with anyio.fail_after(2):
            while not blocked.done():
                await anyio.sleep(0.01)  # command_timeout reaps it
    finally:
        await browser.close()


@pytest.mark.anyio
async def test_close_does_not_abandon_spawned_tasks(monkeypatch):
    fake = WedgedSendCDP()
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)
    browser = make_browser(block_resources=HEAVY)
    await browser.connect()
    browser._on_request_paused({"requestId": "r1"})
    spawned = list(browser._pending)
    await browser.close()
    assert spawned and all(task.done() for task in spawned)


def test_browser_errors_are_surfsky_errors():
    # `except SurfskyError` must catch a failed command and a wait that ran out
    assert issubclass(CDPError, SurfskyError)
    assert issubclass(BrowserTimeoutError, SurfskyError)
    assert issubclass(BrowserTimeoutError, TimeoutError)  # and the old catch still works


@pytest.mark.anyio
async def test_evaluate_calls_a_function_with_json_safe_arguments():
    browser = make_browser()
    browser._client = RecordingCDP({"Runtime.evaluate": {"result": {"value": 3}}})  # type: ignore[assignment]
    assert await browser.evaluate("(a, b) => a + b", 1, {"x": 'q"uote'}) == 3
    [params] = browser._client.sent("Runtime.evaluate")
    assert params["expression"] == '((a, b) => a + b)(...[1, {"x": "q\\"uote"}])'
    assert params["awaitPromise"] is True


@pytest.mark.anyio
async def test_evaluate_calls_a_bare_function_and_leaves_an_expression_alone():
    browser = make_browser()
    browser._client = RecordingCDP({"Runtime.evaluate": {"result": {"value": None}}})  # type: ignore[assignment]
    for expression in [
        "() => document.title",
        "async x => x",
        "function () { return 1 }",
        "(1 + 2) * 3",
        "document.title",
    ]:
        await browser.evaluate(expression)
    assert [p["expression"] for p in browser._client.sent("Runtime.evaluate")] == [
        "(() => document.title)()",
        "(async x => x)()",
        "(function () { return 1 })()",
        "(1 + 2) * 3",
        "document.title",
    ]


@pytest.mark.anyio
async def test_evaluate_calls_a_function_the_regex_did_not_spot():
    class FunctionCDP(RecordingCDP):
        async def send(self, method, params=None, session_id=None):
            self.calls.append((method, params or {}))
            if method != "Runtime.evaluate":
                return self.results[method]
            if params["expression"].endswith(")()"):
                return {"result": {"type": "number", "value": 2}}
            return {"result": {"type": "function", "value": {}}}  # by value, Chrome gives {}

    browser = make_browser()
    browser._client = FunctionCDP({})  # type: ignore[assignment]
    assert await browser.evaluate("(a = Math.max(1, 2)) => a") == 2
    assert [p["expression"] for p in browser._client.sent("Runtime.evaluate")] == [
        "(a = Math.max(1, 2)) => a",
        "((a = Math.max(1, 2)) => a)()",
    ]


@pytest.mark.anyio
async def test_all_inner_texts_read_through_one_evaluate():
    browser = make_browser()
    browser._client = RecordingCDP({"Runtime.evaluate": {"result": {"value": ["a", "b"]}}})  # type: ignore[assignment]
    assert await browser.all_inner_texts("li") == ["a", "b"]
    [params] = browser._client.sent("Runtime.evaluate")
    assert '"li"' in params["expression"] and "innerText" in params["expression"]


@pytest.mark.anyio
async def test_text_is_none_for_a_miss():
    browser = make_browser()
    browser._client = RecordingCDP({"Runtime.evaluate": {"result": {"value": None}}})  # type: ignore[assignment]
    assert await browser.inner_text("h1") is None


@pytest.mark.anyio
async def test_attribute_reads_the_node_without_running_script():
    browser = make_browser()
    browser._client = RecordingCDP({  # type: ignore[assignment]
        "DOM.getDocument": {"root": {"nodeId": 1}},
        "DOM.querySelector": {"nodeId": 7},
        "DOM.getAttributes": {"attributes": ["href", "/x", "class", "a b"]},
    })
    assert await browser.get_attribute("a", "href") == "/x"
    assert await browser.get_attribute("a", "id") is None  # there, but without one
    assert "Runtime.evaluate" not in [m for m, _ in browser._client.calls]


@pytest.mark.anyio
async def test_attribute_is_none_for_a_miss():
    browser = make_browser()
    browser._client = RecordingCDP({  # type: ignore[assignment]
        "DOM.getDocument": {"root": {"nodeId": 1}},
        "DOM.querySelector": {"nodeId": 0},
    })
    assert await browser.get_attribute("a", "href") is None


@pytest.mark.anyio
async def test_count_counts_matches_without_running_script():
    browser = make_browser()
    browser._client = RecordingCDP({  # type: ignore[assignment]
        "DOM.getDocument": {"root": {"nodeId": 1}},
        "DOM.querySelectorAll": {"nodeIds": [3, 4, 5]},
    })
    assert await browser.count("li") == 3


@pytest.mark.anyio
async def test_select_sets_the_option_and_fires_change():
    browser = make_browser()
    browser._client = RecordingCDP({"Runtime.evaluate": {"result": {"value": "eu"}}})  # type: ignore[assignment]
    await browser.select_option("#region", "eu")
    [params] = browser._client.sent("Runtime.evaluate")
    expression = params["expression"]
    assert '"#region"' in expression and '"eu"' in expression and "change" in expression


@pytest.mark.anyio
async def test_select_by_label_and_a_missing_option_complain():
    browser = make_browser()
    browser._client = RecordingCDP({"Runtime.evaluate": {"result": {"value": False}}})  # type: ignore[assignment]
    with pytest.raises(ValueError, match="no option"):
        await browser.select_option("#region", label="Europe")
    with pytest.raises(ValueError, match="either"):
        await browser.select_option("#region")


@pytest.mark.anyio
async def test_wait_for_function_polls_until_truthy_and_returns_the_value():
    class Later(RecordingCDP):
        polls = 0

        async def send(self, method, params=None, session_id=None):
            if method != "Runtime.evaluate":
                return await super().send(method, params, session_id)
            self.polls += 1
            return {"result": {"value": None if self.polls < 3 else "ready"}}

    browser = make_browser()
    browser._client = Later({})  # type: ignore[assignment]
    assert await browser.wait_for_function("() => window.ready", timeout=1) == "ready"
    assert browser._client.polls == 3


@pytest.mark.anyio
async def test_wait_for_function_names_what_stayed_false():
    browser = make_browser()
    browser._client = RecordingCDP({"Runtime.evaluate": {"result": {"value": 0}}})  # type: ignore[assignment]
    with pytest.raises(BrowserTimeoutError, match="window.ready.*within 0.05s"):
        await browser.wait_for_function("window.ready", timeout=0.05)


@pytest.mark.anyio
async def test_session_storage_uses_the_session_bucket():
    browser = make_browser()
    browser._client = RecordingCDP(  # type: ignore[assignment]
        {
            "DOMStorage.enable": {},
            "Page.getFrameTree": {"frameTree": {"frame": {"id": "F", "storageKey": "K"}}},
            "DOMStorage.getDOMStorageItems": {"entries": [["step", "2"]]},
            "DOMStorage.setDOMStorageItem": {},
        }
    )
    await browser.set_session_storage({"step": "2"})
    assert await browser.session_storage() == {"step": "2"}
    written = dict(browser._client.calls)["DOMStorage.setDOMStorageItem"]
    assert written["storageId"] == {"isLocalStorage": False, "storageKey": "K"}


@pytest.mark.anyio
async def test_clear_cookies_clears_the_browser():
    browser = make_browser()
    browser._client = RecordingCDP({"Network.clearBrowserCookies": {}})  # type: ignore[assignment]
    await browser.clear_cookies()
    assert [m for m, _ in browser._client.calls] == ["Network.clearBrowserCookies"]


@pytest.mark.anyio
async def test_stop_capturing_forgets_and_turns_the_network_domain_off(fake_cdp):
    browser = make_browser()
    await browser.connect()
    await browser.capture_responses("/api/")
    browser._responses.append(CapturedResponse(url="https://a.test/api/x", status=200))
    await browser.stop_capturing()
    assert browser.responses == []
    assert "Network.disable" in fake_cdp.calls
    with pytest.raises(RuntimeError, match="capture_responses"):
        await browser.wait_for_response("/api/")
    await browser.stop_capturing()
    assert fake_cdp.calls.count("Network.disable") == 1


@pytest.mark.anyio
async def test_connect_blocks_url_patterns_too(fake_cdp):
    browser = make_browser(block_resources={"image"}, block_urls=["*analytics*", "*.woff2"])
    await browser.connect()
    assert fake_cdp.fetch_enable_params == {
        "patterns": [
            {"urlPattern": "*", "resourceType": "Image"},
            {"urlPattern": "*analytics*"},
            {"urlPattern": "*.woff2"},
            mod.STATUS_PATTERN,
        ]
    }


@pytest.mark.anyio
async def test_evaluate_runs_in_an_isolated_world_the_page_cannot_see():
    browser = make_browser()
    browser._frame_id = "F"
    browser._client = RecordingCDP({"Runtime.evaluate": {"result": {"value": 1}}})  # type: ignore[assignment]
    await browser.evaluate("1")
    await browser.inner_text("h1")
    assert browser._client.sent("Page.createIsolatedWorld") == [{"frameId": "F", "worldName": page_mod.WORLD_NAME}]
    assert [p["contextId"] for p in browser._client.sent("Runtime.evaluate")] == [7, 7]


@pytest.mark.anyio
async def test_evaluate_in_the_main_world_only_on_request():
    browser = make_browser()
    browser._client = RecordingCDP({"Runtime.evaluate": {"result": {"value": 1}}})  # type: ignore[assignment]
    await browser.evaluate("window.__data", isolated=False)
    await browser.wait_for_function("window.__data", isolated=False, timeout=1)
    assert not browser._client.sent("Page.createIsolatedWorld")
    assert all("contextId" not in p for p in browser._client.sent("Runtime.evaluate"))


@pytest.mark.anyio
async def test_the_isolated_world_is_made_again_after_a_navigation():
    class Stale(RecordingCDP):
        worlds = 0

        async def send(self, method, params=None, session_id=None):
            if method == "Page.createIsolatedWorld":
                self.worlds += 1
                return {"executionContextId": self.worlds}
            if method == "Runtime.evaluate" and params["contextId"] < self.worlds:
                raise CDPError("Cannot find context with specified id")
            return await super().send(method, params, session_id)

    browser = make_browser()
    browser._frame_id = "F"
    browser._client = Stale({"Runtime.evaluate": {"result": {"value": 1}}})  # type: ignore[assignment]
    await browser.evaluate("1")
    browser._on_lifecycle({"name": "init", "frameId": "F", "loaderId": "L2"})  # a new document
    await browser.evaluate("1")
    assert browser._client.worlds == 2
    # or the document changed under us before the event arrived: once more, then
    browser._client.worlds = 3  # the world we hold, 2, is gone
    await browser.evaluate("1")
    assert browser._client.worlds == 4


def test_a_bare_string_of_url_patterns_is_refused():
    # a str is a Sequence too: "*.png" would become "*", ".", "p"... and "*" blocks everything
    with pytest.raises(TypeError, match="list of patterns"):
        make_browser(block_urls="*.png")
