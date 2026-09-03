"""Scrape page titles and HTML across all your Surfsky browsers.

Both settings come from the environment, or from
``AsyncSurfsky(api_token=..., base_url=...)``.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/basic_scrape.py
"""

import asyncio

from surfsky import AsyncSurfsky, Browser

URLS = [
    "https://example.com",
    "https://surfsky.io",
]


async def scrape(browser: Browser, url: str) -> tuple[str, str]:
    await browser.goto(url, wait_until="domcontentloaded")
    return await browser.title(), await browser.content()


async def main() -> None:
    async with AsyncSurfsky() as client:
        outcomes = await client.map(scrape, URLS)  # concurrency="auto"

    for outcome in outcomes:
        if outcome.value is None:
            print(f"FAILED {outcome.item}: {outcome.error!r}")
            continue
        title, html = outcome.value
        print(f"\n{outcome.item} -> {title}")
        print(f"{html[:200]}...")


# The same list through ``pool.lease()`` instead of ``map``::
#
#     async with client.browsers() as pool:
#         for url in URLS:
#             async with pool.lease() as browser:
#                 await browser.goto(url)


if __name__ == "__main__":
    asyncio.run(main())
