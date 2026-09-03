"""A bounded pool of live cloud browsers.

The pool starts browsers, keeps their number inside the plan's parallel limit,
gives each a fresh fingerprint and stops every paid session on the way out.
``lease()`` hands out a live browser and takes it back::

    async with client.browsers() as browsers:
        async with browsers.lease() as browser:
            await browser.goto(url)

``map()`` is a thin wrapper over the lease for the common case.
"""

import copy
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, Unpack

import anyio

from ..client import stop_session
from ..errors import RateLimitError
from ..proxy import validate_proxy
from ..resources.profiles import SessionOptions
from ..transport import PLAN_FULL
from ..types import OneTimeStartRequest
from .browser import Browser, normalize_blocked, normalize_urls

if TYPE_CHECKING:
    from ..client import AsyncSurfsky

logger = logging.getLogger("surfsky")

type PoolHandler[Item, Res] = Callable[[Browser, Item], Awaitable[Res]]


def plan_is_full(exc: BaseException) -> bool:
    return isinstance(exc, RateLimitError) and exc.code == PLAN_FULL


class PoolOptions(SessionOptions, total=False):
    concurrency: int | Literal["auto"]
    block_resources: frozenset[str] | set[str] | None
    block_urls: Sequence[str] | None


class StopRun(Exception): ...


@dataclass
class PoolOutcome[I, R]:
    item: I
    index: int
    value: R | None = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class BrowserPool:
    def __init__(self, client: "AsyncSurfsky", **options: Unpack[PoolOptions]) -> None:
        self._client = client
        self._concurrency = options.pop("concurrency", "auto")
        self.blocked_resources = normalize_blocked(options.pop("block_resources", None))
        self.blocked_urls = normalize_urls(options.pop("block_urls", None))
        probe: dict[str, Any] = {**options, "proxy": None}
        OneTimeStartRequest(**probe)
        validate_proxy(options.get("proxy"))
        self.session_options: SessionOptions = options
        self._idle: list[Browser] = []
        self._owned = 0
        self._plan_full = False
        self._plan_full_error: Exception | None = None
        self._cond = anyio.Condition()
        self._capacity = 0
        self._slots: anyio.Semaphore | None = None

    @property
    def capacity(self) -> int:
        self._require_open()
        return self._capacity

    def _require_open(self) -> anyio.Semaphore:
        if self._slots is None:
            raise RuntimeError("the pool is not open: use it in an `async with` block")
        return self._slots

    async def __aenter__(self) -> "BrowserPool":
        if self._concurrency == "auto":
            total = await self._client.account.max_browsers()
        else:
            total = self._concurrency
        self._capacity = max(1, total)
        self._slots = anyio.Semaphore(self._capacity)
        self._plan_full = False
        self._plan_full_error = None
        self._owned = 0
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._slots = None
        idle, self._idle = self._idle, []
        self._owned -= len(idle)
        async with anyio.create_task_group() as tg:
            for browser in idle:
                tg.start_soon(self._teardown, browser)

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Browser]:
        """Take a live browser, give it back on exit."""
        async with self._require_open():
            browser = await self._acquire()
            browser._use_count += 1
            try:
                yield browser
            finally:
                await self._release(browser)

    async def _acquire(self) -> Browser:
        while True:
            async with self._cond:
                dead = [b for b in self._idle if b.should_recycle]
                if dead:
                    self._idle = [b for b in self._idle if not b.should_recycle]
                    self._owned -= len(dead)
                    self._plan_full = False
                    self._cond.notify_all()
                elif self._idle:
                    return self._idle.pop()
                elif self._plan_full:
                    if self._owned == 0:
                        error = self._plan_full_error or RateLimitError(
                            "parallel browser limit reached", status_code=429
                        )
                        raise copy.copy(error)
                    await self._cond.wait()  # 1 refusal is enough, do not retry
                    continue
                else:
                    self._owned += (
                        1  # before the await: a start in flight is capacity too
                    )
            if dead:
                async with anyio.create_task_group() as tg:
                    for browser in dead:
                        tg.start_soon(self._teardown, browser)
                continue
            try:
                browser = await self._start_browser()
            except BaseException as exc:
                full = isinstance(exc, Exception) and plan_is_full(exc)
                with anyio.CancelScope(shield=True):
                    async with self._cond:
                        self._owned -= 1
                        if full:
                            self._plan_full = True
                            self._plan_full_error = exc
                        nothing_left = self._owned == 0
                        self._cond.notify_all()
                if not full or nothing_left:
                    raise
                logger.info("plan is full, waiting for a browser of ours")
                continue
            with anyio.CancelScope(shield=True):
                async with self._cond:
                    self._plan_full = False
            return browser

    async def _release(self, browser: Browser) -> None:
        if not browser.should_recycle:
            with anyio.CancelScope(shield=True):
                try:
                    with anyio.fail_after(5):
                        await browser._end_lease()
                except Exception as exc:
                    logger.warning(f"could not clean up {browser.internal_uuid}: {exc}")
                    browser.retire()
        recycle = browser.should_recycle
        if recycle:
            await self._teardown(browser)
        with anyio.CancelScope(shield=True):
            async with self._cond:
                if recycle:
                    self._owned -= 1
                    self._plan_full = False
                else:
                    self._idle.append(browser)
                self._cond.notify_all()

    async def map[Item, Res](
        self, handler: PoolHandler[Item, Res], items: Iterable[Item]
    ) -> list[PoolOutcome[Item, Res]]:
        """Run every item through ``handler`` on a leased browser."""
        work = enumerate(items)  # shared: next() never yields, so no item is taken twice
        outcomes: list[PoolOutcome[Item, Res]] = []
        stopped = False

        async def worker() -> None:
            nonlocal stopped
            while not stopped and (pulled := next(work, None)) is not None:
                index, item = pulled
                try:
                    async with self.lease() as browser:
                        value = await handler(browser, item)
                except StopRun as exc:
                    stopped = True
                    outcomes.append(PoolOutcome(item, index, error=exc))
                    return
                except Exception as exc:
                    outcomes.append(PoolOutcome(item, index, error=exc))
                else:
                    outcomes.append(PoolOutcome(item, index, value=value))

        async with anyio.create_task_group() as tg:
            for _ in range(self.capacity):
                tg.start_soon(worker)
        return sorted(outcomes, key=lambda outcome: outcome.index)

    async def _start_browser(self) -> Browser:
        session = await self._client.profiles.start_one_time(**self.session_options)
        try:
            browser = Browser(
                session,
                block_resources=self.blocked_resources,
                block_urls=self.blocked_urls,
            )
            await browser.connect()
        except BaseException:
            await stop_session(self._client, session.internal_uuid)
            raise
        logger.info(f"pool browser {browser.internal_uuid} started")
        return browser

    async def _teardown(self, browser: Browser) -> None:
        with anyio.CancelScope(shield=True):
            await browser.close()
            await stop_session(self._client, browser.internal_uuid)
