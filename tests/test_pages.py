import asyncio

import pytest

from conftest import PostsViaSend
from surfsky.browser import browser as mod
from surfsky.browser import page as page_mod
from surfsky.browser.browser import Browser
from surfsky.browser.cdp import CDPError
from surfsky.browser.page import Page
from surfsky.errors import BrowserTimeoutError, PageClosedError
from surfsky.types import Session

MAIN = {"type": "page", "targetId": "T", "url": "about:blank"}
POPUP = {"type": "page", "targetId": "P", "url": ""}  # a fresh popup has no URL yet


class AttachingCDP(PostsViaSend):
    """Speaks the auto-attach handshake: ``Target.setAutoAttach`` reports every
    scripted target, ``attach()`` reports one that opened later, as a popup
    would, and ``detach()`` takes one away. Records every command with the
    session it went out on."""

    def __init__(self, targets: list[dict] | None = None) -> None:
        self.targets = targets if targets is not None else [MAIN]
        self.calls: list[tuple[str, dict, str | None]] = []
        self.handlers: dict = {}
        self.connected = True
        self.delay: dict[str, float] = {}  # method -> seconds before it answers
        self.rejected: dict[str, str] = {}  # method -> session it fails on
        self.loads = True  # whether a navigation reaches load on its own
        self._sessions = 0
        self._targets: dict[str, str] = {}  # session -> target, for events

    def on(self, event, handler) -> None:
        self.handlers[event] = handler

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.connected = False

    def attach(self, info: dict, *, waiting: bool = False) -> str:
        self._sessions += 1
        session = f"S{self._sessions}"
        self._targets[session] = info["targetId"]
        self.handlers["Target.attachedToTarget"](
            {"sessionId": session, "targetInfo": info, "waitingForDebugger": waiting},
            None,
        )
        return session

    def detach(self, session: str, target: str) -> None:
        self.handlers["Target.detachedFromTarget"](
            {"sessionId": session, "targetId": target}, None
        )

    def sent(self, method: str) -> list[tuple[dict, str | None]]:
        return [(params, session) for m, params, session in self.calls if m == method]

    async def send(self, method, params=None, session_id=None):
        self.calls.append((method, params or {}, session_id))
        if seconds := self.delay.get(method):
            await asyncio.sleep(seconds)
        if method in self.rejected and self.rejected[method] == session_id:
            raise RuntimeError(f"{method} rejected")
        match method:
            case "Target.setAutoAttach":
                for info in self.targets:
                    self.attach(info)
            case "Target.createTarget":
                target = f"T{self._sessions + 1}"
                page = {"type": "page", "targetId": target, "url": "about:blank"}
                self.attach(page, waiting=True)  # Chrome reports it before it replies
                return {"targetId": target}
            case "Target.getTargetInfo":
                target = params["targetId"]
                return {"targetInfo": {"url": f"https://{target}.test/", "title": target}}
            case "Page.navigate":
                if self.loads:
                    self.handlers["Page.lifecycleEvent"](
                        {"name": "load", "frameId": self._targets[session_id], "loaderId": "L"},
                        session_id,
                    )
                return {"loaderId": "L"}
        return {}


class PausedCDP(AttachingCDP):
    """A target paused on start, as Chrome really behaves: commands on its
    session are received in order but answered only once
    ``Runtime.runIfWaitingForDebugger`` reaches it."""

    def __init__(self) -> None:
        super().__init__()
        self.paused_session: str | None = None
        self._held: list[tuple[asyncio.Future, object]] = []

    async def post(self, method, params=None, session_id=None):
        result = await super().send(method, params, session_id)  # records, in wire order
        reply = asyncio.get_running_loop().create_future()
        # None is the browser session, not "nothing is paused": guard it
        paused = self.paused_session is not None and session_id == self.paused_session
        if paused and method != "Runtime.runIfWaitingForDebugger":
            self._held.append((reply, result))
            return 0, reply
        reply.set_result(result)
        if paused:  # the resume lets everything it was holding answer, and after
            self.paused_session = None  # it the target behaves like any other
            for held, value in self._held:
                held.set_result(value)
            self._held.clear()
        return 0, reply

    async def send(self, method, params=None, session_id=None):
        # the pause has to bite here too, or a setup that awaits each command in
        # turn would sail through the fake and the deadlock would go unnoticed
        _, reply = await self.post(method, params, session_id)
        return await reply


@pytest.mark.anyio
async def test_a_paused_popup_does_not_deadlock_on_its_own_pause(monkeypatch):
    # a window the page opens attaches paused and answers nothing until it is
    # resumed; a setup that awaited Page.enable first would hang until the
    # command timeout and leave the popup unusable
    fake = PausedCDP()
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)
    async with make_browser(command_timeout=1) as browser:
        fake.paused_session = "S2"
        fake.attach(POPUP, waiting=True)
        popup = browser.pages[-1]
        # the frame every event is keyed by is known before the setup answers
        assert popup._frame_id == "P"
        assert await popup.title() == "P"  # setup finished, so the page is usable
        # the arming commands reached Chrome before the resume let the page run
        on_popup = [m for m, _, s in fake.calls if s == "S2"]
        assert on_popup.index("Fetch.enable") < on_popup.index("Runtime.runIfWaitingForDebugger")


@pytest.fixture
def cdp(monkeypatch) -> AttachingCDP:
    fake = AttachingCDP()
    monkeypatch.setattr(mod, "CDPClient", lambda ws, **kw: fake)
    return fake


def make_browser(**kwargs) -> Browser:
    return Browser(Session(internal_uuid="s1", ws_url="wss://fake/proxy/s1"), **kwargs)


async def settle() -> None:
    await asyncio.sleep(0.01)  # let spawned handler tasks run


@pytest.mark.anyio
async def test_connect_takes_the_first_web_page_through_auto_attach(cdp):
    async with make_browser() as browser:
        [(attach, on_browser)] = cdp.sent("Target.setAutoAttach")
        assert on_browser is None
        assert attach["autoAttach"] and attach["waitForDebuggerOnStart"] and attach["flatten"]
        assert browser.target_id == "T"
        assert browser.pages == [browser]
        assert cdp.sent("Fetch.enable") == [({"patterns": [mod.STATUS_PATTERN]}, "S1")]
        assert browser._frame_id == "T"
        assert "Target.attachToTarget" not in [m for m, _, _ in cdp.calls]


@pytest.mark.anyio
async def test_connect_creates_a_page_when_the_session_has_none(cdp):
    cdp.targets = []
    async with make_browser() as browser:
        assert cdp.sent("Target.createTarget") == [({"url": "about:blank"}, None)]
        assert browser.target_id == "T1"
        assert browser.pages == [browser]


@pytest.mark.anyio
async def test_targets_that_are_not_web_pages_are_let_go(cdp):
    cdp.targets = [
        {"type": "page", "targetId": "EXT", "url": "chrome-extension://abc/options.html"},
        {"type": "service_worker", "targetId": "SW", "url": "https://a.test/sw.js"},
        MAIN,
    ]
    async with make_browser() as browser:
        await settle()
        assert browser.target_id == "T"
        assert browser.pages == [browser]
        assert cdp.sent("Target.detachFromTarget") == [
            ({"sessionId": "S1"}, None),
            ({"sessionId": "S2"}, None),
        ]


@pytest.mark.anyio
async def test_the_startup_tab_is_reused_not_replaced(cdp):
    cdp.targets = [{"type": "page", "targetId": "T", "url": "chrome://newtab/"}]
    async with make_browser() as browser:
        await settle()
        assert browser.target_id == "T"
        assert browser.pages == [browser]
        assert "Target.createTarget" not in [method for method, _, _ in cdp.calls]


@pytest.mark.anyio
async def test_a_decorated_startup_tab_is_still_the_startup_tab(cdp):
    cdp.targets = [{"type": "page", "targetId": "T", "url": "chrome://new-tab-page/?x=1"}]
    async with make_browser() as browser:
        await settle()
        assert browser.target_id == "T"
        assert "Target.createTarget" not in [method for method, _, _ in cdp.calls]


@pytest.mark.anyio
async def test_a_start_page_opened_after_connect_is_let_go(cdp):
    async with make_browser() as browser:
        cdp.attach({"type": "page", "targetId": "N", "url": "chrome://newtab/"})
        await settle()
        assert browser.pages == [browser]
        assert cdp.sent("Target.detachFromTarget") == [({"sessionId": "S2"}, None)]


@pytest.mark.anyio
async def test_another_chrome_page_is_still_let_go(cdp):
    cdp.targets = [{"type": "page", "targetId": "SET", "url": "chrome://settings/"}, MAIN]
    async with make_browser() as browser:
        await settle()
        assert browser.target_id == "T"
        assert cdp.sent("Target.detachFromTarget") == [({"sessionId": "S1"}, None)]


@pytest.mark.anyio
async def test_a_let_go_that_never_answers_gives_up(cdp):
    cdp.delay["Target.detachFromTarget"] = 5
    async with make_browser(command_timeout=0.05) as browser:
        running = set(browser._pending)  # the keepalive loop, which never finishes
        cdp.attach({"type": "service_worker", "targetId": "SW", "url": ""})
        [let_go] = set(browser._pending) - running
        with pytest.raises(BrowserTimeoutError):
            await asyncio.wait_for(let_go, 1)


@pytest.mark.anyio
async def test_a_paused_target_that_is_not_ours_is_resumed_before_it_is_let_go(cdp):
    async with make_browser():
        cdp.attach({"type": "service_worker", "targetId": "SW", "url": ""}, waiting=True)
        await settle()
        assert [m for m, _, s in cdp.calls if s == "S2"] == ["Runtime.runIfWaitingForDebugger"]
        assert cdp.sent("Target.detachFromTarget") == [({"sessionId": "S2"}, None)]


@pytest.mark.anyio
async def test_a_popup_is_listed_and_driven_on_its_own_session(cdp):
    async with make_browser() as browser:
        cdp.attach(POPUP, waiting=True)
        popup = browser.pages[-1]
        assert isinstance(popup, Page) and popup is not browser
        assert popup.target_id == "P" and not popup.closed
        assert await popup.url() == "https://P.test/"
        assert cdp.sent("Target.getTargetInfo")[-1] == ({"targetId": "P"}, "S2")
        await popup.click("#go")
        assert cdp.sent("Human.click") == [({"selector": "#go"}, "S2")]


@pytest.mark.anyio
async def test_a_paused_popup_is_set_up_before_it_runs_and_before_it_is_driven(cdp):
    async with make_browser() as browser:
        cdp.delay["Fetch.enable"] = 0.02  # the setup is still in flight when asked
        cdp.attach(POPUP, waiting=True)
        await browser.pages[-1].title()
        assert [m for m, _, s in cdp.calls if s == "S2"] == [
            "Page.enable",
            "Page.setLifecycleEventsEnabled",
            "Fetch.enable",
            "Runtime.runIfWaitingForDebugger",  # last: it lets the document run
            "Target.getTargetInfo",
        ]


@pytest.mark.anyio
async def test_a_popup_whose_setup_failed_says_so_when_used(cdp):
    async with make_browser() as browser:
        cdp.rejected["Fetch.enable"] = "S2"
        cdp.attach(POPUP, waiting=True)
        with pytest.raises(RuntimeError, match="Fetch.enable rejected"):
            await browser.pages[-1].title()
        assert browser.connected  # the main page is unaffected


@pytest.mark.anyio
async def test_new_page_opens_a_window_and_returns_it_ready(cdp):
    async with make_browser() as browser:
        page = await browser.new_page()
        assert cdp.sent("Target.createTarget") == [
            ({"url": "about:blank", "newWindow": True}, None)
        ]
        assert browser.pages == [browser, page]
        assert page.target_id == "T2"
        assert ("Runtime.runIfWaitingForDebugger", {}, "S2") in cdp.calls


@pytest.mark.anyio
async def test_goto_on_a_popup_waits_for_its_own_document(cdp):
    async with make_browser() as browser:
        cdp.attach(POPUP)
        popup = browser.pages[-1]
        await popup.goto("https://x.test", timeout=1)
        assert cdp.sent("Page.navigate") == [({"url": "https://x.test"}, "S2")]
        assert browser._waiter is None and popup._waiter is None


@pytest.mark.anyio
async def test_a_blocked_request_in_a_popup_is_answered_on_the_popup_session(cdp):
    async with make_browser(block_resources={"image"}) as browser:
        cdp.attach(POPUP)
        popup = browser.pages[-1]
        await popup.url()  # set up
        paused = cdp.handlers["Fetch.requestPaused"]
        paused({"requestId": "r1"}, "S2")
        paused({"requestId": "r2", "responseStatusCode": 503, "frameId": "P"}, "S2")
        await settle()
        assert cdp.sent("Fetch.failRequest") == [
            ({"requestId": "r1", "errorReason": "BlockedByClient"}, "S2")
        ]
        assert cdp.sent("Fetch.continueResponse") == [({"requestId": "r2"}, "S2")]
        assert (popup.status, browser.status) == (503, None)


@pytest.mark.anyio
async def test_a_popup_the_site_closes_drops_out_and_refuses_commands(cdp):
    async with make_browser() as browser:
        session = cdp.attach(POPUP)
        popup = browser.pages[-1]
        cdp.detach(session, "P")
        assert popup.closed
        assert browser.pages == [browser]
        assert not browser.should_recycle  # only the main page going away retires it
        with pytest.raises(RuntimeError, match="page is closed"):
            await popup.title()


@pytest.mark.anyio
async def test_a_crashed_popup_counts_as_closed(cdp):
    async with make_browser() as browser:
        cdp.attach(POPUP)
        popup = browser.pages[-1]
        cdp.handlers["Inspector.targetCrashed"]({}, "S2")
        assert popup.closed
        assert browser.pages == [browser]


@pytest.mark.anyio
async def test_the_main_page_going_away_still_retires_the_browser(cdp):
    async with make_browser() as browser:
        cdp.attach(POPUP)
        cdp.detach("S1", "T")
        assert browser.closed and browser.should_recycle
        assert browser.pages == [browser.pages[0]] and browser.pages[0].target_id == "P"


@pytest.mark.anyio
async def test_close_closes_the_target(cdp):
    async with make_browser() as browser:
        cdp.attach(POPUP)
        popup = browser.pages[-1]
        await popup.close()
        assert cdp.sent("Target.closeTarget") == [({"targetId": "P"}, None)]
        assert popup.closed
        assert browser.pages == [browser]
        await popup.close()
        assert len(cdp.sent("Target.closeTarget")) == 1


@pytest.mark.anyio
async def test_close_and_new_page_time_out_as_browser_timeouts(cdp):
    async with make_browser(command_timeout=0.05) as browser:
        cdp.attach(POPUP)
        cdp.delay["Target.closeTarget"] = 1
        with pytest.raises(BrowserTimeoutError):
            await browser.pages[-1].close()
        cdp.delay["Target.createTarget"] = 1
        with pytest.raises(BrowserTimeoutError):
            await browser.new_page()


@pytest.mark.anyio
async def test_wait_for_page_returns_what_the_action_opened(cdp):
    async with make_browser() as browser:
        cdp.attach(POPUP)  # already open: not the one wanted
        later = {"type": "page", "targetId": "P2", "url": ""}

        async def click_that_opens_after_an_async_step():
            asyncio.get_running_loop().call_later(0.02, cdp.attach, later)

        popup = await browser.wait_for_page(click_that_opens_after_an_async_step(), timeout=1)
        assert popup.target_id == "P2" and browser.pages[-1] is popup


@pytest.mark.anyio
async def test_wait_for_page_takes_a_page_that_attached_during_the_action(cdp):
    async with make_browser() as browser:

        async def click():
            cdp.attach(POPUP, waiting=True)  # Human.click returns after the window opened

        popup = await browser.wait_for_page(click(), timeout=1)
        assert popup.target_id == "P" and popup is not browser


@pytest.mark.anyio
async def test_wait_for_page_times_out_as_a_browser_timeout(cdp):
    async with make_browser() as browser:

        async def nothing():
            return None

        with pytest.raises(BrowserTimeoutError, match="no page opened"):
            await browser.wait_for_page(nothing(), timeout=0.05)


@pytest.mark.anyio
async def test_bring_to_front_activates_the_page(cdp):
    async with make_browser() as browser:
        cdp.attach(POPUP)
        await browser.pages[-1].bring_to_front()
        assert cdp.sent("Page.bringToFront") == [({}, "S2")]


@pytest.mark.anyio
async def test_a_dropped_connection_fails_every_page_navigation(cdp):
    async with make_browser() as browser:
        cdp.attach(POPUP)
        popup = browser.pages[-1]
        cdp.loads = False
        goto = asyncio.create_task(popup.goto("https://x.test", timeout=5))
        await settle()
        browser._on_disconnect()
        with pytest.raises(CDPError, match="connection closed"):
            await goto


@pytest.fixture
def no_dialog_pause(monkeypatch) -> None:
    monkeypatch.setattr(page_mod, "DIALOG_DELAY", (0.0, 0.0))


@pytest.mark.anyio
async def test_a_dialog_is_answered_after_a_human_pause(cdp, monkeypatch):
    # alert() returning the instant it opened is a tell
    monkeypatch.setattr(page_mod, "DIALOG_DELAY", (0.05, 0.05))
    async with make_browser():
        cdp.handlers["Page.javascriptDialogOpening"]({"type": "alert", "message": "hi"}, "S1")
        await settle()
        assert not cdp.sent("Page.handleJavaScriptDialog")
        await asyncio.sleep(0.1)
        assert cdp.sent("Page.handleJavaScriptDialog") == [({"accept": False}, "S1")]


@pytest.mark.anyio
async def test_dialogs_are_dismissed_and_beforeunload_accepted(cdp, no_dialog_pause):
    # an open dialog would otherwise hang every command until the timeout
    async with make_browser():
        cdp.attach(POPUP)
        dialog = cdp.handlers["Page.javascriptDialogOpening"]
        dialog({"type": "alert", "message": "hi"}, "S2")
        dialog({"type": "beforeunload", "message": ""}, "S1")
        await settle()
        assert cdp.sent("Page.handleJavaScriptDialog") == [
            ({"accept": False}, "S2"),
            ({"accept": True}, "S1"),
        ]


@pytest.mark.anyio
async def test_on_dialog_decides_and_can_answer_a_prompt(cdp, no_dialog_pause):
    async with make_browser() as browser:
        seen: list[tuple[str, str]] = []

        def decide(kind: str, message: str) -> bool | str:
            seen.append((kind, message))
            return "42" if kind == "prompt" else True

        browser.on_dialog = decide
        cdp.attach(POPUP)  # popups follow the browser's handler
        dialog = cdp.handlers["Page.javascriptDialogOpening"]
        dialog({"type": "confirm", "message": "sure?"}, "S1")
        dialog({"type": "prompt", "message": "age?"}, "S2")
        await settle()
        assert seen == [("confirm", "sure?"), ("prompt", "age?")]
        assert cdp.sent("Page.handleJavaScriptDialog") == [
            ({"accept": True}, "S1"),
            ({"accept": True, "promptText": "42"}, "S2"),
        ]


@pytest.mark.anyio
async def test_wait_for_load_knows_the_state_chrome_replays_on_attach(cdp):
    # enabling lifecycle events replays the current document's milestones
    async with make_browser() as browser:
        lifecycle = cdp.handlers["Page.lifecycleEvent"]
        lifecycle({"name": "load", "frameId": "T", "loaderId": "L0"}, "S1")
        await browser.wait_for_load_state(timeout=0.5)


@pytest.mark.anyio
async def test_wait_for_load_waits_for_the_newest_document(cdp):
    async with make_browser() as browser:
        lifecycle = cdp.handlers["Page.lifecycleEvent"]
        lifecycle({"name": "load", "frameId": "T", "loaderId": "L0"}, "S1")
        lifecycle({"name": "init", "frameId": "T", "loaderId": "L1"}, "S1")  # a click navigated
        lifecycle({"name": "load", "frameId": "child", "loaderId": "L9"}, "S1")  # an iframe, not it
        with pytest.raises(BrowserTimeoutError, match="did not reach 'load'"):
            await browser.wait_for_load_state(timeout=0.05)
        lifecycle({"name": "DOMContentLoaded", "frameId": "T", "loaderId": "L1"}, "S1")
        await browser.wait_for_load_state(state="domcontentloaded", timeout=0.5)
        with pytest.raises(BrowserTimeoutError):
            await browser.wait_for_load_state(timeout=0.05)
        lifecycle({"name": "load", "frameId": "T", "loaderId": "L1"}, "S1")
        await browser.wait_for_load_state(timeout=0.5)


@pytest.mark.anyio
async def test_a_wait_on_a_page_the_site_closes_ends_at_once(cdp):
    # wait_for_load_state only watches local state: without the check it ran to
    # its deadline after the popup was gone
    async with make_browser() as browser:
        session = cdp.attach(POPUP)
        popup = browser.pages[-1]
        wait = asyncio.create_task(popup.wait_for_load_state(timeout=5))
        await settle()
        cdp.detach(session, "P")
        with pytest.raises(PageClosedError):
            await wait
