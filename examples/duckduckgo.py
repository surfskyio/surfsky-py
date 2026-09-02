"""Search DuckDuckGo on every browser your plan allows and read the results.

One query per browser.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/duckduckgo.py
"""

import asyncio

from surfsky import AsyncSurfsky, Browser

QUERIES = [
    "surfsky",
    "cloud browser",
    "headless browser",
]


async def search(browser: Browser, query: str) -> list[dict[str, str]]:
    tag = f"[{browser.internal_uuid[:8]}] {query}"
    print(f"{tag}: opening duckduckgo")
    await browser.goto("https://duckduckgo.com", wait_until="domcontentloaded")
    print(f"{tag}: searching")
    await browser.type('[name="q"]', query)
    # the button: keyboard.press("Enter") does not submit a form
    await browser.click("#searchbox_homepage button[type=submit]")
    print(f"{tag}: waiting for results")
    await browser.wait_for_selector('[data-testid="result-title-a"]', timeout=30)
    hits = await browser.evaluate("""
        [...document.querySelectorAll('[data-testid="result-title-a"]')]
            .slice(0, 5)
            .map(a => ({title: a.innerText, url: a.href}))
    """)
    print(f"{tag}: {len(hits)} results")
    return hits


async def main() -> None:
    async with AsyncSurfsky() as client:
        outcomes = await client.map(search, QUERIES)

    for outcome in outcomes:
        print(f"\n{outcome.item}")
        if not outcome.ok:
            print(f"  FAILED: {outcome.error!r}")
            continue
        for hit in outcome.value or []:
            print(f"  {hit['title']}\n    {hit['url']}")


if __name__ == "__main__":
    asyncio.run(main())
