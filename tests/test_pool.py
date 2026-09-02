import json
from unittest.mock import AsyncMock

import anyio
import pytest

from surfsky.browser import Browser, CapturedResponse, Page
from surfsky.browser.pool import BrowserPool, StopRun
from surfsky.types import Session


class FakeBrowser(Browser):
    """A Browser that never connects, so the pool logic can be tested offline."""

    connected = True  # shadows the property; tests flip it per instance

    def __init__(self) -> None:
        super().__init__(Session(internal_uuid="fake", ws_url="wss://fake"))


def _offline_pool(monkeypatch, **kwargs) -> BrowserPool:
    pool = BrowserPool(client=None, **kwargs)  # type: ignore[arg-type]
    monkeypatch.setattr(pool, "_start_browser", AsyncMock(side_effect=FakeBrowser))
    monkeypatch.setattr(pool, "_teardown", AsyncMock())
    return pool


@pytest.mark.anyio
async def test_lease_reuses_the_same_browser(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=2)
    async with pool:
        async with pool.lease() as first:
            pass
        async with pool.lease() as second:
            pass
    assert first is second
    assert pool._start_browser.await_count == 1


@pytest.mark.anyio
async def test_lease_blocks_once_every_browser_is_out(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=1)
    async with pool, pool.lease():
        with anyio.move_on_after(0.05) as scope:
            async with pool.lease():  # pragma: no cover - must not be reached
                pass
    assert scope.cancelled_caught  # capacity is the backpressure


@pytest.mark.anyio
async def test_retired_browser_is_not_handed_out_again(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=1)
    async with pool:
        async with pool.lease() as first:
            first.retire()
        async with pool.lease() as second:
            pass
    assert second is not first
    assert pool._teardown.await_args_list[0].args == (first,)


@pytest.mark.anyio
async def test_dead_browser_is_not_handed_out_again(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=1)
    async with pool:
        async with pool.lease() as first:
            first.connected = False  # the CDP socket dropped mid-item
        async with pool.lease() as second:
            pass
    assert second is not first


@pytest.mark.anyio
async def test_browser_is_returned_even_when_the_body_raises(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=1)
    async with pool:
        with pytest.raises(ValueError):
            async with pool.lease():
                raise ValueError("handler blew up")
        async with pool.lease():
            pass
    assert pool._start_browser.await_count == 1  # a failure alone doesn't burn identity


@pytest.mark.anyio
async def test_leaving_the_pool_stops_every_browser(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=2)
    async with pool:
        async with pool.lease() as a, pool.lease() as b:
            pass
    assert {call.args[0] for call in pool._teardown.await_args_list} == {a, b}


@pytest.mark.anyio
async def test_lease_use_count_and_data_are_per_identity(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=1)
    async with pool:
        async with pool.lease() as browser:
            assert browser.use_count == 1
            browser.data["logged_in"] = True
        async with pool.lease() as browser:
            assert browser.use_count == 2
            assert browser.data["logged_in"] is True
            browser.retire()
        async with pool.lease() as browser:
            assert browser.use_count == 1
            assert browser.data == {}


@pytest.mark.anyio
async def test_lease_raises_the_start_failure_to_the_caller(monkeypatch):
    from surfsky import ServerError

    pool = _offline_pool(monkeypatch, concurrency=1)
    monkeypatch.setattr(
        pool,
        "_start_browser",
        AsyncMock(side_effect=ServerError("no pods", status_code=503)),
    )
    async with pool:
        with pytest.raises(ServerError):
            async with pool.lease():  # pragma: no cover - never entered
                pass
        # the slot was released, so the next attempt is not deadlocked
        with pytest.raises(ServerError):
            async with pool.lease():  # pragma: no cover - never entered
                pass


@pytest.mark.anyio
async def test_lease_before_entering_the_pool_is_a_clear_error(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=1)
    with pytest.raises(RuntimeError, match="async with"):
        async with pool.lease():  # pragma: no cover - never entered
            pass


@pytest.mark.anyio
async def test_start_browser_stops_the_session_when_connect_fails(monkeypatch):
    stopped: list[str] = []

    class FakeProfiles:
        async def start_one_time(self, **kwargs):
            return Session(internal_uuid="sess-x", ws_url="wss://fake")

        async def stop(self, uuid: str):
            stopped.append(uuid)

    class FakeClient:
        profiles = FakeProfiles()

        def with_options(self, **kwargs):
            return self

    async def failing_connect(self):
        raise RuntimeError("ws attach failed")

    monkeypatch.setattr(Browser, "connect", failing_connect)
    pool = BrowserPool(client=FakeClient(), concurrency=1)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="ws attach failed"):
        await pool._start_browser()
    assert stopped == ["sess-x"]  # the paid session must not leak


@pytest.mark.anyio
async def test_teardown_closes_the_socket_before_stopping_the_session():
    # the stop kills the browser and its socket; closing first keeps the receive
    # loop from logging that as a lost connection on every pool exit
    events: list[str] = []

    class FakeProfiles:
        async def stop(self, uuid: str) -> None:
            events.append("stop")

    class FakeClient:
        profiles = FakeProfiles()

        def with_options(self, **kwargs):
            return self

    class Closing(FakeBrowser):
        async def close(self) -> None:
            events.append("close")

    pool = BrowserPool(client=FakeClient(), concurrency=1)  # type: ignore[arg-type]
    await pool._teardown(Closing())
    assert events == ["close", "stop"]


@pytest.mark.anyio
async def test_each_browser_takes_a_fresh_pick_from_a_proxy_source(monkeypatch):
    from surfsky import ProxyCycle
    from surfsky.proxy import aresolve_proxy

    seen: list[object] = []

    class FakeProfiles:
        async def start_one_time(self, **kwargs):
            # the real start_one_time resolves sources itself
            seen.append(await aresolve_proxy(kwargs["proxy"]))
            return Session(internal_uuid=f"sess-{len(seen)}", ws_url="wss://fake")

    class FakeClient:
        profiles = FakeProfiles()

    async def ok_connect(self):
        return None

    monkeypatch.setattr(Browser, "connect", ok_connect)
    pool = BrowserPool(
        client=FakeClient(),  # type: ignore[arg-type]
        concurrency=1,
        proxy=ProxyCycle(["http://a:1", "http://b:2"]),
    )
    await pool._start_browser()
    await pool._start_browser()
    assert seen == ["http://a:1", "http://b:2"]


@pytest.mark.anyio
async def test_auto_concurrency_is_resolved_once_on_entry():
    calls = {"n": 0}

    class FakeAccount:
        async def max_browsers(self) -> int:
            calls["n"] += 1
            return 7

    class FakeClient:
        account = FakeAccount()

    pool = BrowserPool(client=FakeClient(), concurrency="auto")  # type: ignore[arg-type]
    async with pool:
        assert pool.capacity == 7
    assert calls["n"] == 1


@pytest.mark.anyio
async def test_auto_concurrency_is_the_plans_cap_not_todays_free_slots(httpx_mock):
    """Slots held elsewhere must not cap the pool for its whole life: they come
    back, and the server refuses anything over the cap anyway."""
    from surfsky import AsyncSurfsky

    httpx_mock.add_response(
        method="GET", url="https://api.test/users/browser-limits",
        json={
            "success": True,
            "data": {
                "has_browser_limits": True,
                "parallel_browsers": 10,
                "running": 8,
                "available": 2,
            },
        },
    )
    client = AsyncSurfsky(api_token="t", base_url="https://api.test")
    async with client.browsers() as browsers:
        assert browsers.capacity == 10
    await client.aclose()


def test_pool_rejects_unknown_blocked_resources():
    # must fail at construction, not after a billable session already started
    with pytest.raises(ValueError, match="unknown resource types"):
        BrowserPool(client=None, block_resources={"scripts"})  # type: ignore[arg-type]


def test_pool_rejects_unusable_proxy_input():
    with pytest.raises(TypeError, match="proxy must be"):
        BrowserPool(client=None, proxy={"country": "us"})  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_pool_forwards_session_options_to_the_start_request(httpx_mock, monkeypatch):
    from surfsky import AsyncSurfsky

    httpx_mock.add_response(
        method="POST",
        url="https://api.test/profiles/one_time",
        json={"internal_uuid": "s1", "ws_url": "wss://fake", "success": True},
    )

    async def ok_connect(self):
        return None

    monkeypatch.setattr(Browser, "connect", ok_connect)
    client = AsyncSurfsky(api_token="t", base_url="https://api.test")
    pool = client.browsers(
        concurrency=1, extensions=["ext-1"], proxy_blacklist=["ads.example"]
    )
    await pool._start_browser()
    await client.aclose()
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["extensions"] == ["ext-1"]
    assert body["proxy_blacklist"] == ["ads.example"]


# map(): the thin sugar over lease()


@pytest.mark.anyio
async def test_map_returns_outcomes_in_input_order(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=3)

    async def handle(_browser, item: int) -> int:
        await anyio.sleep(0.05 if item == 0 else 0)  # the first item finishes last
        return item * 2

    async with pool:
        outcomes = await pool.map(handle, [0, 1, 2])
    assert [o.index for o in outcomes] == [0, 1, 2]
    assert [o.value for o in outcomes] == [0, 2, 4]
    assert all(o.ok for o in outcomes)


@pytest.mark.anyio
async def test_map_empty(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=3)

    async def handle(_browser, item):  # pragma: no cover - never called
        return item

    async with pool:
        assert await pool.map(handle, []) == []
    assert pool._start_browser.await_count == 0


@pytest.mark.anyio
async def test_map_reports_a_failed_item_instead_of_raising(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=1)

    async def handle(_browser, item: str) -> str:
        if item == "bad":
            raise RuntimeError("boom")
        return item

    async with pool:
        outcomes = await pool.map(handle, ["ok", "bad"])
    assert outcomes[0].value == "ok"
    assert isinstance(outcomes[1].error, RuntimeError)
    assert not outcomes[1].ok


@pytest.mark.anyio
async def test_map_spreads_work_over_the_pool(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=3)
    live = 0
    peak = 0

    async def handle(_browser, item: int) -> int:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await anyio.sleep(0.01)
        live -= 1
        return item

    async with pool:
        await pool.map(handle, list(range(9)))
    assert peak == 3  # capacity, not one at a time and not all nine


@pytest.mark.anyio
async def test_stop_run_from_a_handler_ends_the_map(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=1)
    seen: list[int] = []

    async def handle(_browser, item: int) -> int:
        seen.append(item)
        if item == 2:
            raise StopRun("account suspended")
        return item

    async with pool:
        outcomes = await pool.map(handle, [1, 2, 3, 4, 5])
    assert seen == [1, 2]
    assert outcomes[0].value == 1  # work already paid for is kept
    assert isinstance(outcomes[1].error, StopRun)
    assert len(outcomes) == 2  # a partial list, not one outcome per input


@pytest.mark.anyio
async def test_client_map_enters_and_leaves_the_pool(monkeypatch):
    from surfsky import AsyncSurfsky

    monkeypatch.setattr(BrowserPool, "_start_browser", AsyncMock(side_effect=FakeBrowser))
    monkeypatch.setattr(BrowserPool, "_teardown", AsyncMock())

    async def handle(_browser, item: int) -> int:
        return item + 1

    client = AsyncSurfsky(api_token="t", base_url="https://api.test")
    outcomes = await client.map(handle, [1, 2, 3], concurrency=2)
    await client.aclose()
    assert [o.value for o in outcomes] == [2, 3, 4]
    # whatever the pool started, it stopped: no session outlives the block
    assert BrowserPool._teardown.await_count == BrowserPool._start_browser.await_count


def _plan_full() -> Exception:
    from surfsky import RateLimitError

    # what the server sends once parallel_browsers_limit is reached
    return RateLimitError(
        "Maximum parallel browsers",
        status_code=429,
        body={"success": False, "code": "parallel_browsers_limit_reached"},
    )


@pytest.mark.anyio
async def test_lease_waits_for_a_free_browser_when_the_plan_is_full(monkeypatch):
    """Someone else (another script, a bare Playwright run) took the slots the
    snapshot promised us: reuse our own browser instead of failing."""
    pool = _offline_pool(monkeypatch, concurrency=2)
    first = FakeBrowser()
    monkeypatch.setattr(
        pool, "_start_browser", AsyncMock(side_effect=[first, _plan_full()])
    )
    leased: list[Browser] = []

    async def worker() -> None:
        async with pool.lease() as browser:
            leased.append(browser)
            await anyio.sleep(0.05)

    async with pool, anyio.create_task_group() as tg:
        tg.start_soon(worker)
        await anyio.sleep(0.01)  # the first worker is holding the only browser
        tg.start_soon(worker)

    assert leased == [first, first]


@pytest.mark.anyio
async def test_lease_raises_when_the_plan_is_full_and_we_hold_nothing(monkeypatch):
    from surfsky import RateLimitError

    pool = _offline_pool(monkeypatch, concurrency=2)
    monkeypatch.setattr(pool, "_start_browser", AsyncMock(side_effect=_plan_full()))
    async with pool:
        with pytest.raises(RateLimitError):  # nothing to wait for
            async with pool.lease():  # pragma: no cover - never entered
                pass


@pytest.mark.anyio
async def test_tearing_down_the_last_browser_wakes_a_waiter(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=2)
    first, second = FakeBrowser(), FakeBrowser()
    monkeypatch.setattr(
        pool, "_start_browser", AsyncMock(side_effect=[first, _plan_full(), second])
    )
    leased: list[Browser] = []

    async def hold_and_retire() -> None:
        async with pool.lease() as browser:
            leased.append(browser)
            await anyio.sleep(0.05)
            browser.retire()  # torn down instead of returned: a slot frees up

    async def wait_for_one() -> None:
        async with pool.lease() as browser:
            leased.append(browser)

    async with pool, anyio.create_task_group() as tg:
        tg.start_soon(hold_and_retire)
        await anyio.sleep(0.01)
        tg.start_soon(wait_for_one)

    assert leased == [first, second]


@pytest.mark.anyio
async def test_a_quota_429_still_raises_instead_of_waiting(monkeypatch):
    from surfsky import RateLimitError

    pool = _offline_pool(monkeypatch, concurrency=2)
    quota = RateLimitError(
        "monthly session limit",
        status_code=429,
        body={"code": "monthly_session_limit_reached"},
    )
    monkeypatch.setattr(
        pool, "_start_browser", AsyncMock(side_effect=[FakeBrowser(), quota])
    )
    async with pool:
        async with pool.lease():
            pass
        pool._idle.clear()  # force the next lease to start a browser
        with pytest.raises(RateLimitError):
            async with pool.lease():  # pragma: no cover - never entered
                pass


@pytest.mark.anyio
async def test_a_browser_returned_during_a_failed_start_is_not_missed(monkeypatch):
    """The classic lost wakeup: a browser comes back while our start attempt is
    still in flight. Parking after that must not miss it."""
    pool = _offline_pool(monkeypatch, concurrency=2)
    held, returned = anyio.Event(), anyio.Event()
    first = FakeBrowser()
    starts = 0

    async def start_browser() -> Browser:
        nonlocal starts
        starts += 1
        if starts == 1:
            return first
        await returned.wait()  # the other lease gives its browser back meanwhile
        raise _plan_full()

    monkeypatch.setattr(pool, "_start_browser", start_browser)
    took: list[Browser] = []

    async def holder() -> None:
        async with pool.lease():
            held.set()
            await anyio.sleep(0.05)
        returned.set()

    async def latecomer() -> None:
        async with pool.lease() as browser:
            took.append(browser)

    with anyio.fail_after(2):  # a lost wakeup would hang here
        async with pool, anyio.create_task_group() as tg:
            tg.start_soon(holder)
            await held.wait()
            tg.start_soon(latecomer)

    assert took == [first]


@pytest.mark.anyio
async def test_waiters_do_not_all_retry_the_start_while_the_plan_is_full(monkeypatch):
    """One 429 is enough: parked leases wait for a browser of ours rather than
    each hammering the API every time one comes back."""
    pool = _offline_pool(monkeypatch, concurrency=4)
    starts = 0
    first = FakeBrowser()

    async def start_browser() -> Browser:
        nonlocal starts
        starts += 1
        if starts == 1:
            return first
        raise _plan_full()

    monkeypatch.setattr(pool, "_start_browser", start_browser)
    took: list[Browser] = []

    async def worker() -> None:
        for _ in range(3):
            async with pool.lease() as browser:
                took.append(browser)
                await anyio.sleep(0.01)

    with anyio.fail_after(2):
        async with pool, anyio.create_task_group() as tg:
            for _ in range(4):
                tg.start_soon(worker)

    assert took == [first] * 12
    # at most one attempt per lease while warming up, then nobody retries; the
    # broadcast version tried again on each of the 11 handovers
    assert starts <= 4


@pytest.mark.anyio
async def test_a_cancelled_lease_still_gives_its_browser_back(monkeypatch):
    """Returning a browser takes a lock now, so the release has to survive the
    cancellation that interrupted the lease: otherwise the browser (and its
    billing session) is simply dropped."""
    pool = _offline_pool(monkeypatch, concurrency=1)
    async with pool:
        with anyio.move_on_after(0.01):
            async with pool.lease():
                await anyio.sleep(5)

        with anyio.fail_after(1):
            async with pool.lease():
                pass
    assert pool._start_browser.await_count == 1
    assert pool._teardown.await_count == 1  # only the pool's own exit stopped it


@pytest.mark.anyio
async def test_a_reused_pool_starts_from_a_clean_slate(monkeypatch):
    """Leaving the pool ends the run; entering it again must not inherit the
    plan-full verdict from last time."""
    from surfsky import RateLimitError

    pool = _offline_pool(monkeypatch, concurrency=1)
    monkeypatch.setattr(pool, "_start_browser", AsyncMock(side_effect=_plan_full()))
    async with pool:
        with pytest.raises(RateLimitError):
            async with pool.lease():  # pragma: no cover - never entered
                pass

    monkeypatch.setattr(pool, "_start_browser", AsyncMock(side_effect=FakeBrowser))
    async with pool:  # the plan has room again
        with anyio.fail_after(1):
            async with pool.lease() as browser:
                assert browser.use_count == 1


@pytest.mark.anyio
async def test_a_browser_started_as_the_lease_is_cancelled_is_still_stopped(monkeypatch):
    """The session start is shielded, so a cancellation can land just after the
    browser is up. It must not be dropped on the floor: it bills, and its own
    keepalive keeps the cloud from reaping it."""
    pool = _offline_pool(monkeypatch, concurrency=1)
    started = FakeBrowser()

    async def slow_shielded_start() -> Browser:
        with anyio.CancelScope(shield=True):
            await anyio.sleep(0.05)  # the caller's deadline fires in here
        return started

    monkeypatch.setattr(pool, "_start_browser", slow_shielded_start)

    async with pool:
        with anyio.move_on_after(0.01):
            async with pool.lease():  # pragma: no cover - never entered
                pass
    assert pool._teardown.await_args_list
    assert pool._teardown.await_args_list[0].args == (started,)


@pytest.mark.anyio
async def test_a_cancelled_start_does_not_leave_phantom_capacity(monkeypatch):
    """A cancelled start must give its slot back. Counting it as a live browser
    makes the plan-full branch wait for something that will never come back."""
    from surfsky import RateLimitError

    pool = _offline_pool(monkeypatch, concurrency=2)

    async def cancelled_start() -> Browser:
        with anyio.CancelScope(shield=True):
            await anyio.sleep(0.05)  # the caller's deadline fires in here
        await anyio.sleep(0)  # ... and is delivered at this checkpoint
        raise AssertionError("unreachable")  # pragma: no cover

    monkeypatch.setattr(pool, "_start_browser", cancelled_start)
    async with pool:
        with anyio.move_on_after(0.01):
            async with pool.lease():  # pragma: no cover - never entered
                pass
        assert pool._owned == 0

        # with phantom capacity this would park forever instead of reporting
        monkeypatch.setattr(pool, "_start_browser", AsyncMock(side_effect=_plan_full()))
        with anyio.fail_after(1):
            with pytest.raises(RateLimitError):
                async with pool.lease():  # pragma: no cover - never entered
                    pass


class LeftOpen(Page):
    """A popup a handler opened and walked away from; closing needs no socket here."""

    async def close(self) -> None:
        self._browser._drop(self)


@pytest.mark.anyio
async def test_release_closes_the_pages_a_handler_left_open(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=1)
    async with pool:
        async with pool.lease() as browser:
            popup = LeftOpen(browser, "P", "S2")
            browser._pages = {"S1": browser, "S2": popup}
            assert browser.pages == [browser, popup]
        assert popup.closed  # gone before the next lease can see it
        assert browser.pages == [browser]
        assert not browser.should_recycle


@pytest.mark.anyio
async def test_a_page_that_will_not_close_retires_the_browser(monkeypatch):
    class Stuck(Page):
        async def close(self) -> None:
            raise RuntimeError("Target.closeTarget: no such target")

    pool = _offline_pool(monkeypatch, concurrency=1)
    async with pool:
        async with pool.lease() as browser:
            browser._pages = {"S1": browser, "S2": Stuck(browser, "P", "S2")}
        assert browser.should_recycle
        assert pool._teardown.await_args_list[0].args == (browser,)


@pytest.mark.anyio
async def test_release_forgets_captures_and_the_dialog_handler(monkeypatch):
    class Quiet:
        connected = True

        async def send(self, method, params=None, session_id=None):
            return {}

    pool = _offline_pool(monkeypatch, concurrency=1)
    async with pool:
        async with pool.lease() as browser:
            browser._client = Quiet()  # type: ignore[assignment]
            await browser.capture_responses("/api/")
            browser._responses.append(CapturedResponse(url="https://a.test/api/x", status=200))
            browser.on_dialog = lambda kind, message: True
        assert browser.responses == []  # lease N+1 must not read lease N's answers
        assert browser.on_dialog is None
        assert not browser.should_recycle


def test_the_pool_refuses_a_bare_string_of_url_patterns():
    with pytest.raises(TypeError, match="list of patterns"):
        BrowserPool(client=None, block_urls="*.png")  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_a_browser_that_died_while_parked_is_replaced(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=1)
    async with pool:
        async with pool.lease() as first:
            pass
        first.retire()  # the keepalive gave up while it sat idle
        async with pool.lease() as second:
            pass
        assert second is not first
        assert pool._teardown.await_args_list[0].args == (first,)
        assert pool._owned == 1


@pytest.mark.anyio
async def test_stop_run_pulls_nothing_more_from_the_callers_iterator(monkeypatch):
    pool = _offline_pool(monkeypatch, concurrency=2)
    items = iter([0, 1, 2, 3])
    started = anyio.Event()

    async def handle(_browser, item: int) -> int:
        if item == 0:
            await started.wait()  # still running when the other worker stops the run
            return item
        started.set()
        raise StopRun("stop")

    async with pool:
        outcomes = await pool.map(handle, items)
    assert [o.index for o in outcomes] == [0, 1]
    assert [o.ok for o in outcomes] == [True, False]  # item 0 was let finish
    assert next(items) == 2  # not pulled and dropped by the worker that finished 0
