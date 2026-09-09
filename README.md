# surfsky

Python SDK for [Surfsky](https://surfsky.io), a cloud-based antidetect browser.

## Install

```sh
uv add surfsky
```

Or `pip install surfsky`. Requires Python 3.12+.

## Quick start

Get your API token and base URL from the [dashboard](https://app.surfsky.io):

```sh
export SURFSKY_API_TOKEN='your-token'
export SURFSKY_API_BASE_URL='your-base-url'
```

```python
import asyncio

from surfsky import AsyncSurfsky, PremiumProxy


async def main():
    async with AsyncSurfsky() as client:
        async with client.browser(proxy=PremiumProxy(country="us")) as browser:
            await browser.goto("https://example.com")
            print(await browser.title())


asyncio.run(main())
```

The browser context stops the session on exit. Sessions are billed until
stopped, including idle time. You can also pass `api_token` and `base_url`
directly to the client.

Browser methods are async; timeouts are in seconds. `Surfsky` provides
synchronous REST calls. Browser input uses Surfsky's
[human emulation](https://docs.surfsky.io/human_emulation).

## Profiles and proxies

A profile preserves its fingerprint, proxy and cookies across sessions.
Inside the client context:

```python
from surfsky import Fingerprint

profile = await client.profiles.create(
    title="account-1",
    fingerprint=Fingerprint(os="win", os_arch="x86", os_version="11"),
    proxy=PremiumProxy(country="us"),
)
async with client.browser(profile_uuid=profile.uuid) as browser:
    await browser.goto("https://example.com/login")
```

Reuse the profile ID for subsequent sessions. `proxy` accepts `PremiumProxy`
for residential or mobile IPs, `SharedProxy` for testing, or your own proxy URL.
`client.proxies` lists locations and quota.

## Parallel browsers

`client.map` distributes items across a browser pool. Inside the client context:

```python
async def title(browser, url):
    await browser.goto(url)
    return await browser.title()


urls = ["https://example.com", "https://example.org"]
for result in await client.map(title, urls, concurrency=2):
    print(result.item, result.value if result.ok else result.error)
```

Without a concurrency limit, the pool uses your plan's maximum. Results include
per-item errors.

To use a browser from the pool:

```python
async with client.browsers() as pool:
    async with pool.lease() as browser:
        await browser.goto("https://example.com")
        print(await browser.title())
```

`lease()` waits for a free browser and returns it to the pool on exit.
Cookies and browser state persist between leases.

## Reference and examples

- [SDK API reference](https://github.com/surfskyio/surfsky-py/blob/main/docs/api.md)
- [REST API](https://docs.surfsky.io/api-reference)
- [Examples](https://github.com/surfskyio/surfsky-py/tree/main/examples): forms, tabs, retries, profiles and CDP connections.

`client.session()` exposes a CDP URL for external browser clients. See the
[Playwright](https://github.com/surfskyio/surfsky-py/blob/main/examples/playwright_connect.py)
and [Selenium](https://github.com/surfskyio/surfsky-py/blob/main/examples/selenium_connect.py) examples.
Install example dependencies with `uv sync --group examples`.

## Development

```sh
uv sync --all-extras
uv run ruff check . && uv run ty check && uv run pytest
```

Live tests require credentials and bill your account:

```sh
SURFSKY_LIVE_TESTS=1 uv run pytest tests/test_live_concurrency.py
```

## License

MIT
