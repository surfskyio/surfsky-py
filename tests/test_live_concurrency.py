import os

import anyio
import pytest
from anyio import create_task_group

from surfsky import AsyncSurfsky, Browser

LIVE_ENV_READY = bool(
    os.environ.get("SURFSKY_LIVE_TESTS")
    and os.environ.get("SURFSKY_API_TOKEN")
    and os.environ.get("SURFSKY_API_BASE_URL")
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not LIVE_ENV_READY,
        reason="live tests need SURFSKY_LIVE_TESTS=1, SURFSKY_API_TOKEN and SURFSKY_API_BASE_URL",
    ),
]

POOL_WORKERS = 5
CHURN_SESSIONS = 8
TEST_DEADLINE = 600.0
RECORDS_DRAIN_DEADLINE = 120.0

URLS = ["https://example.com", "https://example.org", "https://example.net"] * 5


async def _active_uuids(client: AsyncSurfsky) -> set[str]:
    return {p.internal_uuid for p in await client.profiles.list_active()}


async def _wait_records_drained(client: AsyncSurfsky, ours: set[str]) -> set[str]:
    leftover = ours
    with anyio.move_on_after(RECORDS_DRAIN_DEADLINE):
        while True:
            leftover = await _active_uuids(client) & ours
            if not leftover:
                return set()
            await anyio.sleep(5)
    return leftover


async def test_concurrent_scraping_pool():
    """N workers scrape M urls concurrently; every task completes, no records leak."""
    used_browsers: set[str] = set()

    async def handle(browser: Browser, url: str) -> dict:
        used_browsers.add(browser.internal_uuid)
        await browser.goto(url, wait_until="domcontentloaded")
        return {"url": url, "title": await browser.title()}

    async with AsyncSurfsky() as client:
        with anyio.fail_after(TEST_DEADLINE):
            async with client.browsers(concurrency=POOL_WORKERS) as browsers:
                outcomes = await browsers.map(handle, URLS)

        assert all(o.ok for o in outcomes), [o.error for o in outcomes if not o.ok]
        scraped = [o.value for o in outcomes if o.value is not None]
        assert {page["url"] for page in scraped} == set(URLS)
        assert len(used_browsers) == POOL_WORKERS

        leftover = await _wait_records_drained(client, used_browsers)
        assert not leftover, f"zombie session records left: {leftover}"


async def test_concurrent_session_churn():
    """A parallel burst of short start->navigate->close sessions; all clean up."""
    session_uuids: list[str] = []

    async with AsyncSurfsky() as client:

        async def one_session() -> None:
            async with client.browser() as browser:
                session_uuids.append(browser.internal_uuid)
                await browser.goto("https://example.com", wait_until="domcontentloaded")
                assert "Example" in await browser.title()

        with anyio.fail_after(TEST_DEADLINE):
            async with create_task_group() as tg:
                for _ in range(CHURN_SESSIONS):
                    tg.start_soon(one_session)

        assert len(session_uuids) == CHURN_SESSIONS
        leftover = await _wait_records_drained(client, set(session_uuids))
        assert not leftover, f"zombie session records left: {leftover}"
