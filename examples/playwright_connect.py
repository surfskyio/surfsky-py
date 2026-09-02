"""A simple example of working with Playwright.

    pip install playwright
    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/playwright_connect.py
"""

import asyncio

from playwright.async_api import async_playwright  # ty: ignore[unresolved-import]

from surfsky import AsyncSurfsky


async def main() -> None:
    async with (
        AsyncSurfsky() as client,
        client.session() as session,
        async_playwright() as pw,
    ):
        browser = await pw.chromium.connect_over_cdp(session.connect_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        response = await page.goto("https://google.com")
        print("status:", response.status if response else None)
        print("title :", await page.title())


if __name__ == "__main__":
    asyncio.run(main())
