"""Retry a failed item, taking a fresh browser only when the browser is at fault.

export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
uv run python examples/retry.py
"""

import asyncio
import re

from surfsky import (
    AsyncSurfsky,
    Browser,
    BrowserPool,
    BrowserTimeoutError,
    PoolOutcome,
)

URLS = [
    "https://example.com",
    "https://surfsky.io",
    "https://httpstat.us/503",
]
ATTEMPTS = 3
# errors worth another attempt at all
RETRY_ERRORS = re.compile(r"blocked|captcha|HTTP 5\d\d", re.IGNORECASE)
RETIRE_ERRORS = re.compile(r"blocked|captcha", re.IGNORECASE)


async def scrape(browser: Browser, url: str) -> str:
    await browser.goto(url, wait_until="domcontentloaded")
    if browser.status is not None and browser.status >= 500:
        raise RuntimeError(f"HTTP {browser.status}")
    return await browser.title()


def should_retry(exc: Exception) -> bool:
    return isinstance(exc, BrowserTimeoutError) or bool(RETRY_ERRORS.search(str(exc)))


def should_retire(exc: Exception) -> bool:
    return isinstance(exc, BrowserTimeoutError) or bool(RETIRE_ERRORS.search(str(exc)))


async def with_retry(pool: BrowserPool, url: str) -> PoolOutcome[str, str]:
    attempt = 1
    while True:
        try:
            async with pool.lease() as browser:
                try:
                    title = await scrape(browser, url)
                except Exception as exc:
                    if should_retire(exc):
                        browser.retire()  # the next lease pays for a fresh browser
                    raise
                return PoolOutcome(item=url, index=0, value=title)
        except Exception as exc:
            if attempt >= ATTEMPTS or not should_retry(exc):
                return PoolOutcome(item=url, index=0, error=exc)
            print(f"{url}: attempt {attempt} failed ({exc!r}), retrying")
            await asyncio.sleep(
                attempt
            )  # outside the lease: hold no browser while waiting
            attempt += 1


async def main() -> None:
    async with (
        AsyncSurfsky() as client,
        client.browsers() as pool,
        asyncio.TaskGroup() as tg,
    ):
        tasks = [tg.create_task(with_retry(pool, url)) for url in URLS]

    for task in tasks:
        outcome = task.result()
        print(outcome.item, outcome.value if outcome.ok else f"FAILED: {outcome.error!r}")


if __name__ == "__main__":
    asyncio.run(main())
