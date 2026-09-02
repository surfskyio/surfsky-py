"""Several pages open in 1 browser, driven in turn.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/tabs.py
"""

import asyncio

from surfsky import AsyncSurfsky

URLS = [
    "https://example.com",
    "https://quotes.toscrape.com",
    "https://the-internet.herokuapp.com",
]


async def main() -> None:
    async with AsyncSurfsky() as client, client.browser() as browser:
        tabs = [browser, *[await browser.new_page() for _ in URLS[1:]]]
        print(f"opened {len(browser.pages)} pages")

        for i, (tab, url) in enumerate(zip(tabs, URLS, strict=True)):
            await tab.goto(url, wait_until="domcontentloaded")
            print(f"  page {i} loaded {url}")

        print("\neach page keeps its own document:")
        for i, tab in enumerate(tabs):
            print(f"  page {i}: {await tab.title()} - {await tab.url()}")

        print("\nreading two of them, in any order, no switching:")
        print("  page 0 h1   :", await tabs[0].inner_text("h1"))
        print("  page 1 quote:", await tabs[1].inner_text(".quote .text"))

        await tabs[2].bring_to_front()
        png = await tabs[2].screenshot()
        print(f"\npage 2 brought to the front, screenshot: {len(png)} bytes")

        for tab in tabs[1:]:
            await tab.close()
        print(
            f"closed the rest: {len(browser.pages)} page left, on {await browser.url()}"
        )


if __name__ == "__main__":
    asyncio.run(main())
