"""Scrape a list of URLs across every browser your plan allows.

The SDK hands out live browsers and stops them; the loop, the deadlines and the
identity rotation are yours (``client.map`` is this loop, packaged).

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/parallel_urls.py
"""

import asyncio

from surfsky import AsyncSurfsky, Browser

URLS = [
    "https://google.com",
    "https://bing.com",
    "https://amazon.com",
    "https://surfsky.io",
]


async def scrape(browser: Browser, url: str) -> str:
    await browser.goto(url, wait_until="domcontentloaded")  # its own 30s deadline
    title = await browser.title()
    if browser.use_count >= 20:
        browser.retire()  # a fresh identity every 20 leases
    return title


async def main() -> None:
    urls = iter(URLS)
    async with (
        AsyncSurfsky() as client,
        client.browsers() as browsers,  # concurrency="auto": the plan's cap
        asyncio.TaskGroup() as tg,
    ):

        async def worker() -> None:
            for url in urls:
                try:
                    async with browsers.lease() as browser:
                        print(f"{url} -> {await scrape(browser, url)}")
                except Exception as exc:
                    print(f"FAILED {url}: {exc!r}")

        for _ in range(browsers.capacity):
            tg.create_task(worker())


if __name__ == "__main__":
    asyncio.run(main())
