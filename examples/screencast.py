"""Watch a cloud browser live: the screencast stream, view-only.

The session bills until this script exits, so WATCH_SECONDS is billed time.
See https://docs.surfsky.io/screencast for the viewer.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/screencast.py
"""

import asyncio
from urllib.parse import urlencode, urlsplit

import anyio

from surfsky import AsyncSurfsky

WATCH_SECONDS = 60


async def main() -> None:
    async with AsyncSurfsky() as client, client.browser() as browser:
        inspector = browser.session.inspector
        if inspector is None or not inspector.screencast:
            raise SystemExit("no screencast in the start response")

        stream = inspector.screencast
        # the stream URL carries its own query, so it has to be encoded
        query = urlencode({"ws": stream})
        print(f"viewer: https://{urlsplit(stream).netloc}/screencast?{query}")

        await browser.goto("https://example.com")
        print(f"watching for {WATCH_SECONDS}s")
        await anyio.sleep(WATCH_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
