"""Click a Turnstile checkbox that lives inside a closed shadow root.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/shadow_root.py
"""

import asyncio

from surfsky import AsyncSurfsky

PAGE = "https://www.scrapingcourse.com/login/cf-turnstile"
WIDGET = 'iframe[src*="challenges.cloudflare.com"]'
TOKEN = 'input[name="cf-turnstile-response"]'


async def main() -> None:
    async with AsyncSurfsky() as client, client.browser() as browser:
        inspector = browser.session.inspector
        if inspector is not None and inspector.pages:
            print("devtools:",inspector.pages[0].devtools_url)
        await browser.goto(PAGE)
        await browser.wait_for_selector(WIDGET)
        await asyncio.sleep(2)
        box = await browser.bounding_box(WIDGET)
        assert box is not None
        await browser.mouse.click(box["x"] + 28, box["y"] + box["height"] / 2)
        token = await browser.wait_for_function(
            "s => document.querySelector(s).value", TOKEN, timeout=30
        )
        print("token:", f"{token[:24]}...")


if __name__ == "__main__":
    asyncio.run(main())
