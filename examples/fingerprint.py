"""Set a fingerprint, then read it back from browserleaks.

Surfsky gives every session a fingerprint out of the box, and the default is
fine almost every time. Pass your own when you want to pin some of the values.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/fingerprint.py
"""

import asyncio

from surfsky import AsyncSurfsky, Fingerprint


async def main() -> None:
    async with AsyncSurfsky() as client:
        screens = await client.fingerprints.screens(os="win", os_arch="x86")
        fingerprint = Fingerprint(
            os="win",
            os_arch="x86",
            os_version="11",
            cpu=8,
            ram=8,
            screen=screens[0].value,
            languages=["de-DE", "de", "en"],
            timezone="Europe/Berlin",
        )
        print("fingerprint:", fingerprint.model_dump(exclude_none=True))

        async with client.browser(fingerprint=fingerprint) as browser:
            await browser.goto("https://browserleaks.com/javascript", wait_until="load")
            await browser.hover("#js-userAgent")
            fields = [
                "userAgent",
                "platform",
                "languages",
                "timeZone",
                "hardwareConcurrency",
                "deviceMemory",
                "width",
                "height",
            ]
            seen = await browser.evaluate(f"""
                Object.fromEntries({fields!r}.map(
                    id => [id, document.querySelector('#js-' + id)?.textContent.trim()]))
            """)

    for name, value in seen.items():
        print(f"{name:20} {value}")


if __name__ == "__main__":
    asyncio.run(main())
