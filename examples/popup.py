"""Follow a ``target="_blank"`` link into the window it opens.

``wait_for_page`` runs the click and returns the new ``Page``, with the same
methods as the browser. Do not use ``browser.pages[-1]`` right after the
click: the window may not be open yet.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/popup.py
"""

import asyncio

from surfsky import AsyncSurfsky


async def main() -> None:
    async with AsyncSurfsky() as client, client.browser() as browser:
        await browser.goto(
            "https://the-internet.herokuapp.com/windows", wait_until="domcontentloaded"
        )
        popup = await browser.wait_for_page(browser.click('a[href="/windows/new"]'))
        await popup.wait_for_selector("h3")
        print("opened :", await popup.url())
        print("heading:", await popup.outer_html("h3"))

        await popup.close()
        print("back on:", await browser.url())
        print("pages  :", len(browser.pages))


if __name__ == "__main__":
    asyncio.run(main())
