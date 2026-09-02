"""A one-time session: an identity that exists for this run and no longer.

Nothing is stored server-side - the profile is gone when the session ends.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/one_time.py
"""

import asyncio

from surfsky import AsyncSurfsky


async def main() -> None:
    async with AsyncSurfsky() as client, client.browser() as browser:
        await browser.goto("https://example.com", wait_until="domcontentloaded")
        print("status :", browser.status)
        print("title  :", await browser.title())
        print(await browser.content())


if __name__ == "__main__":
    asyncio.run(main())
