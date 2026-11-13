# surfsky

Python SDK for [Surfsky](https://surfsky.io), an antidetect cloud browser: scrape
a page in one call, or start a browser session and control it over CDP.

## Install

```sh
uv add surfsky
```

Needs Python 3.12+. `pip install surfsky` works too.

## Credentials

Token and base URL come from the [dashboard](https://app.surfsky.io).

```sh
export SURFSKY_API_TOKEN='your-token'
export SURFSKY_API_BASE_URL='your-base-url'
```

`Surfsky(api_token=..., base_url=...)` overrides the environment.

## Scrape

```python
from surfsky import Surfsky

with Surfsky() as client:
    page = client.scrape("https://example.com", formats=["markdown", "links"])
    print(page.status, page.title)
    print(page.markdown)
```

The page comes from a pre-started browser, so there is no session to stop.
Formats: `html` (the default, cleaned, returned as `content`), `raw_html`,
`markdown`, `links`, `screenshot` (base64) and `cookies`. `timeout` is in
milliseconds here. The other options (`wait_for`, `actions`, `cookies`,
`auto_captcha_solve`) are in the
[API reference](https://github.com/surfskyio/surfsky-py/blob/main/docs/api.md#client).

`Surfsky` is the sync client: REST, `scrape` and `session`. `AsyncSurfsky` has
the same methods as coroutines, plus the browser layer below.

## Browser

```python
import asyncio

from surfsky import AsyncSurfsky, PremiumProxy


async def main():
    async with AsyncSurfsky() as client:
        async with client.browser(proxy=PremiumProxy(country="us")) as browser:
            await browser.goto("https://example.com")
            await browser.click("a")
            print(await browser.title())


asyncio.run(main())
```

`Browser` is the session's first tab plus the connection; `browser.pages` and
`browser.new_page()` give the rest. Method names follow Playwright (`goto`,
`click`, `fill`, `wait_for_selector`, `evaluate`, `screenshot`), timeouts are in
seconds. Input goes through Surfsky's
[human emulation](https://docs.surfsky.io/human_emulation), and scripts run in
an isolated world unless you pass `isolated=False`.

## Playwright and other CDP clients

`client.session()` starts a session without connecting to it. `connect_url` is
its CDP WebSocket URL.

```python
import asyncio

from playwright.async_api import async_playwright
from surfsky import AsyncSurfsky


async def main():
    async with (
        AsyncSurfsky() as client,
        client.session() as session,
        async_playwright() as pw,
    ):
        browser = await pw.chromium.connect_over_cdp(session.connect_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://example.com")
        print(await page.title())


asyncio.run(main())
```

Selenium needs `enable_chromedriver=True`, see
[selenium_connect.py](https://github.com/surfskyio/surfsky-py/blob/main/examples/selenium_connect.py).

## Stopping sessions

A session is billed until it stops. `client.browser()`, `client.session()` and
the pool stop theirs on exit, on errors and cancellation too. A session started
through `client.profiles` needs an explicit stop (sync client shown):

```python
session = client.profiles.start_one_time()
try:
    ...
finally:
    client.profiles.stop(session)
```

`client.profiles.list_active()` lists what is running, `stop_all()` stops it.

The cloud also stops a session after 30 seconds without CDP traffic. Raise that
with `browser_settings=BrowserSettings(inactive_kill_timeout=300)`, up to 3600.

## Profiles and proxies

A profile keeps its fingerprint, proxy and cookies between sessions. Inside
the client context:

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

`proxy` takes `PremiumProxy` (residential or mobile IPs, targeted by `country`,
`region`, `city`), `SharedProxy` for testing, or your own proxy URL.
`client.proxies` lists locations and quota. `fingerprint` and `cookies` are
session options for one-time sessions only; a profile has its own.

## Parallel browsers

`client.map` runs a handler over a pool of browsers. Inside the client context:

```python
async def title(browser, url):
    await browser.goto(url)
    return await browser.title()


urls = ["https://example.com", "https://example.org"]
for result in await client.map(title, urls, concurrency=2):
    print(result.item, result.value if result.ok else result.error)
```

Results come back in input order; a handler's exception lands in `error`
and does not stop the run. Without `concurrency` the pool takes your plan's
limit. The pool does not retry or time out a handler.

To lease browsers yourself:

```python
async with client.browsers() as pool:
    async with pool.lease() as browser:
        await browser.goto("https://example.com")
```

`lease()` waits for a free browser and returns it on exit. Cookies and browser
state stay between leases; `browser.retire()` replaces the browser after the
lease.

## Errors

API and browser errors derive from `SurfskyError`.

```python
from surfsky import APIError, RateLimitError

try:
    page = client.scrape("https://example.com")
except RateLimitError as exc:
    print(exc.retry_after)
except APIError as exc:
    print(exc.status_code, exc.code, exc.message)
```

| Error                                                 | When                                 |
| ----------------------------------------------------- | ------------------------------------ |
| `ConfigurationError`                                  | no token or base URL                 |
| `BadRequestError`                                     | 400, 422, a failed scrape (`code`)   |
| `AuthenticationError`, `PaymentRequiredError`         | 401, 402                             |
| `ForbiddenError`, `NotFoundError`, `ConflictError`    | 403, 404, 409                        |
| `RateLimitError`                                      | 429                                  |
| `SharedTrafficLimitError`, `PremiumTrafficLimitError` | 429, proxy traffic quota used up     |
| `ServerError`                                         | 5xx                                  |
| `APIConnectionError`, `APITimeoutError`               | no response                          |
| `BrowserTimeoutError`, `PageClosedError`, `CDPError`  | browser wait, closed page, CDP reply |

The quota errors are not subclasses of `RateLimitError` and are never retried.
Idempotent requests retry on 429, 5xx and connection errors, `max_retries=3` by
default. POST and PATCH retry only on 429 or a failed connect, so a lost reply
cannot start a second billed session.

## Reference and examples

- [SDK API reference](https://github.com/surfskyio/surfsky-py/blob/main/docs/api.md)
- [REST API](https://docs.surfsky.io/api-reference)
- [Examples](https://github.com/surfskyio/surfsky-py/tree/main/examples): forms,
  tabs, retries, profiles, a closed shadow root. `uv sync --group examples`
  installs Playwright and Selenium for them.

## Development

```sh
uv sync
uv run ruff check . && uv run ty check && uv run pytest
```

Live tests need credentials and bill your account:

```sh
SURFSKY_LIVE_TESTS=1 uv run pytest tests/test_live_concurrency.py
```

## License

MIT
