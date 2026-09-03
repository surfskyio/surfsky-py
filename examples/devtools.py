"""Open Chrome DevTools on a running cloud browser.

Paste the printed URL into Chrome and you get the real DevTools. Use it for your
own page logic only: the DevTools front-end enables Runtime, Console and Overlay
on the target, all of which page script can see, so it is the wrong tool for
chasing a detection problem. The session bills until this script exits.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/devtools.py
"""

import asyncio

from surfsky import AsyncSurfsky

ATTACH_SECONDS = 20
WATCH_SECONDS = 60


async def main() -> None:
    async with AsyncSurfsky() as client, client.browser() as browser:
        inspector = browser.session.inspector
        if inspector is None or not inspector.pages:
            raise SystemExit("no inspector in the start response")
        page = inspector.pages[0]
        if page.devtools_url is None:
            raise SystemExit("the inspector returned no devtools_url")

        print("devtools :", page.devtools_url)
        print("page list:", inspector.list_url)

        # navigate only once someone can be attached, or the panels open empty
        print(f"attach now, navigating in {ATTACH_SECONDS}s")
        await asyncio.sleep(ATTACH_SECONDS)
        await browser.goto("https://example.com", wait_until="domcontentloaded")

        print(f"holding the session for {WATCH_SECONDS}s")
        await asyncio.sleep(WATCH_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
