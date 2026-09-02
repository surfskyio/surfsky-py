"""Premium proxies: 100M+ residential and mobile IPs, checked before use.

Targeting is by country, region, city or ASN, by coordinates, or a regional
pool. ``session_minutes``, ``keep_ip``, ``unique_ip`` and ``keep_asn`` set
how long the IP lasts and when it may change.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/premium_proxy.py
"""

import asyncio

from surfsky import AsyncSurfsky, PremiumProxy

COUNTRY, REGION = "us", "california"


async def main() -> None:
    async with AsyncSurfsky() as client:
        # countries = await client.proxies.countries()
        # regions = await client.proxies.regions(COUNTRY)
        cities = await client.proxies.cities(COUNTRY, REGION)

        proxy = PremiumProxy(
            country=COUNTRY,
            region=REGION,
            city=cities[0].code if cities else None,
            type="residential",  # residential or mobile
            session_minutes=10,
            keep_ip=True,
        )
        async with client.browser(proxy=proxy) as browser:
            await browser.goto("https://google.com", wait_until="domcontentloaded")
            print(await browser.title())


if __name__ == "__main__":
    asyncio.run(main())
