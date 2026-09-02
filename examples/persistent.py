"""A persistent profile: 1 identity that keeps its state between runs.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/persistent.py
"""

import asyncio

from surfsky import AsyncSurfsky, Fingerprint, StorageOptions


async def main() -> None:
    async with AsyncSurfsky() as client:
        profile = await client.profiles.create(
            title="demo-persistent",
            fingerprint=Fingerprint(os="mac", os_arch="arm", os_version="15"),
            storage_options=StorageOptions(cookies=True, localstorage=True),
        )
        print("created", profile.uuid)
        try:
            async with client.browser(profile_uuid=profile.uuid) as browser:
                await browser.goto("https://google.com", wait_until="domcontentloaded")
                print("session 1 completed")

            async with client.browser(profile_uuid=profile.uuid) as browser:
                await browser.goto("https://google.com", wait_until="domcontentloaded")
                print("session 2 completed")
        finally:
            await client.profiles.delete(profile.uuid)
            print("deleted", profile.uuid)


if __name__ == "__main__":
    asyncio.run(main())
