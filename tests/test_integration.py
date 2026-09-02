import json

import pytest

from conftest import PostsViaSend
from surfsky import AsyncSurfsky, StopRun


class ScriptedCDP(PostsViaSend):
    def __init__(self) -> None:
        self.connected = True
        self.commands: list[str] = []
        self.handlers: dict = {}

    def on(self, event, handler) -> None:
        self.handlers[event] = handler

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.connected = False

    async def send(self, method, params=None, session_id=None):
        self.commands.append(method)
        match method:
            case "Target.setAutoAttach":
                self.handlers["Target.attachedToTarget"](
                    {"sessionId": "S", "targetInfo": {"type": "page", "targetId": "T"}},
                    None,
                )
            case "Target.getTargetInfo":
                return {"targetInfo": {"title": "Example Domain"}}
            case "Page.navigate":
                # the milestone lands while navigate is still in flight, as it can live
                self.handlers["Page.lifecycleEvent"](
                    {"name": "load", "frameId": "T", "loaderId": "L"}, "S"
                )
                return {"loaderId": "L"}
        return {}


class CDPRegistry(list[ScriptedCDP]):
    def __call__(self, ws, **kwargs) -> ScriptedCDP:
        client = ScriptedCDP()
        self.append(client)
        return client

    @property
    def commands(self) -> list[str]:
        return [command for client in self for command in client.commands]


@pytest.fixture
def cdp(monkeypatch) -> CDPRegistry:
    registry = CDPRegistry()
    monkeypatch.setattr("surfsky.browser.browser.CDPClient", registry)
    return registry


def _session_endpoints(httpx_mock, count: int) -> None:
    for i in range(count):
        httpx_mock.add_response(
            method="POST", url="https://api.test/profiles/one_time",
            json={
                "success": True,
                "data": {"internal_uuid": f"sess-{i}", "ws_url": f"wss://fake/{i}"},
            },
            is_optional=True,  # the last one is spare: exactly how many start is
        )                      # what the test asserts, not what it dictates
    for i in range(count):
        httpx_mock.add_response(
            method="POST", url=f"https://api.test/profiles/sess-{i}/stop",
            json={"success": True, "msg": "Profile stopped", "data": None},
            is_optional=True,
        )


@pytest.mark.anyio
async def test_map_drives_real_browsers_end_to_end(httpx_mock, cdp):
    httpx_mock.add_response(
        method="GET", url="https://api.test/users/browser-limits",
        json={
            "success": True,
            "data": {"has_browser_limits": True, "parallel_browsers": 2, "running": 0},
        },
    )
    _session_endpoints(httpx_mock, count=2)

    async def scrape(browser, url: str) -> str:
        await browser.goto(url, wait_until="load", timeout=2)
        return await browser.title()

    client = AsyncSurfsky(api_token="t", base_url="https://api.test")
    outcomes = await client.map(scrape, ["https://a.test", "https://b.test"])
    await client.aclose()

    assert [o.value for o in outcomes] == ["Example Domain"] * 2
    assert cdp.commands.count("Page.navigate") == 2
    assert "Runtime.enable" not in cdp.commands  # the design forbids it
    assert all(not client.connected for client in cdp)

    stops = [r for r in httpx_mock.get_requests() if r.url.path.endswith("/stop")]
    starts = [r for r in httpx_mock.get_requests() if r.url.path.endswith("/one_time")]
    assert len(stops) == len(starts)  # every paid session was stopped


@pytest.mark.anyio
async def test_retire_and_stop_run_travel_the_whole_stack(httpx_mock, cdp):
    httpx_mock.add_response(
        method="GET", url="https://api.test/users/browser-limits",
        json={
            "success": True,
            "data": {"has_browser_limits": True, "parallel_browsers": 1, "running": 0},
        },
    )
    _session_endpoints(httpx_mock, count=3)

    async def scrape(browser, url: str) -> str:
        await browser.goto(url, wait_until="load", timeout=2)
        if url.endswith("burn"):
            browser.retire()
        if url.endswith("stop"):
            raise StopRun("done here")
        return browser.internal_uuid

    client = AsyncSurfsky(api_token="t", base_url="https://api.test")
    outcomes = await client.map(
        scrape, ["https://a.test/burn", "https://b.test/next", "https://c.test/stop"]
    )
    await client.aclose()

    assert outcomes[0].value == "sess-0"
    assert outcomes[1].value == "sess-1"  # retire() really provisioned a new one
    assert isinstance(outcomes[2].error, StopRun)
    starts = [r for r in httpx_mock.get_requests() if r.url.path.endswith("/one_time")]
    stops = [r for r in httpx_mock.get_requests() if r.url.path.endswith("/stop")]
    assert len(starts) == len(stops) == 2  # nothing started after the stop


@pytest.mark.anyio
async def test_session_options_reach_the_wire_through_the_pool(httpx_mock, cdp):
    from surfsky import SharedProxy

    _session_endpoints(httpx_mock, count=1)

    async def noop(browser, item):
        return item

    client = AsyncSurfsky(api_token="t", base_url="https://api.test")
    await client.map(noop, ["x"], concurrency=1, proxy=SharedProxy(country="us"))
    await client.aclose()

    body = json.loads(
        next(r for r in httpx_mock.get_requests() if r.url.path.endswith("/one_time")).content
    )
    assert body["proxy"] == {"tier": "shared", "country": "us"}
