# surfsky

Python SDK for [Surfsky](https://surfsky.io), a cloud-based antidetect browser.

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

- Raw CDP, without the automation traces stock Playwright, Puppeteer or
  Selenium leave in the page.
- Input goes through our
  [human-emulation](https://docs.surfsky.io/human_emulation) framework: real
  timing, typing, cursor movement.
- Residential and mobile proxies, 100M+ IPs, with geo, ASN and sticky-session
  targeting. Or use your own.
- Real browser fingerprints from a pool of 2.5M+ devices, not synthetic ones.
- Browsers run in the cloud. Playwright-style API, sync and async, fully typed.

REST API docs for the service: https://docs.surfsky.io/api-reference

## Installation

```sh
uv add surfsky
```

or, with pip:

```sh
pip install surfsky
```

Requires Python 3.12 or newer.

## Quick start

Sign up at [surfsky.io](https://surfsky.io). Once you're logged in, the
[dashboard](https://app.surfsky.io) shows your API token and base URL. Put them
in the environment:

```sh
export SURFSKY_API_TOKEN=...
export SURFSKY_API_BASE_URL=...
```

Then start a browser:

```python
import asyncio

from surfsky import AsyncSurfsky, PremiumProxy


async def main():
    # or AsyncSurfsky(api_token="...", base_url="...") instead of env vars
    async with AsyncSurfsky() as client:
        # starts a session, stops it on exit so it doesn't keep billing
        async with client.browser(proxy=PremiumProxy(country="us")) as browser:
            await browser.goto("https://duckduckgo.com", wait_until="domcontentloaded")
            await browser.type('[name="q"]', "surfsky cloud browser")
            await browser.click("#searchbox_homepage button[type=submit]")
            print(await browser.wait_for_url("?q="))


asyncio.run(main())
```

More examples in [`examples/`](https://github.com/surfskyio/surfsky-py/tree/main/examples).

## Browser automation

The API sticks to Playwright's names and semantics where it can: `click`,
`fill`, `hover`, `wait_for_selector`, `inner_text`, `select_option`,
`keyboard.press`, `mouse.move` and so on. Input goes through Surfsky's
[human emulation](https://docs.surfsky.io/human_emulation). Reads use plain CDP
and run no JavaScript in the page.

When you need JavaScript:

```python
await browser.evaluate("document.title")
await browser.evaluate("(a, b) => a + b", 1, 2)
```

Scripts run in an isolated world the page can't see. Pass `isolated=False` to
use the page's own context.

Also useful:

- `client.browser(block_resources={"image", "font", "media"})` skips those
  downloads and saves proxy traffic.
- Grab the JSON a page fetches instead of parsing its HTML:

  ```python
  await browser.capture_responses("/api/search")
  await browser.goto(url)
  data = (await browser.wait_for_response("/api/search")).json()
  ```

- Work with several pages at once. `browser.pages` has every open tab, popups
  included, and `new_page()` adds one:

  ```python
  await browser.new_page()
  await browser.pages[1].goto("https://example.com")
  print(await browser.pages[0].title(), await browser.pages[1].title())
  ```

Every method is listed in the [API reference](#api-reference).

## Running multiple browsers

`client.map` runs a function over a list of items in parallel, one browser per
item, and collects the results:

```python
async def title(browser, url):
    await browser.goto(url)
    return await browser.title()

for o in await client.map(title, urls):
    print(o.item, o.value if o.ok else o.error)
```

Errors land in `o.error` instead of raising, so one bad page doesn't stop the
run. By default the pool uses every browser your plan allows
(`concurrency="auto"`). Pass `concurrency=5` to cap it.

If you need more control, write your own loop on top of the pool:

```python
async with client.browsers() as pool:
    async with pool.lease() as browser:      # waits for a free browser
        await browser.goto("https://example.com")
        print(await browser.title())
```

A browser keeps its fingerprint, proxy and cookies between leases.
`browser.data` carries your own state across them, `browser.retire()` swaps in
a fresh identity.

## Using Playwright or Puppeteer

You don't have to use the SDK's browser API. `client.session` starts a browser
and gives you its WebSocket URL, so Playwright, Puppeteer or any other CDP
client can connect to it. The SDK still handles profiles, proxies and the
session lifecycle:

```python
from playwright.sync_api import sync_playwright
from surfsky import Surfsky

with Surfsky() as client, client.session() as session, sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp(session.connect_url)
    page = browser.contexts[0].pages[0]
    page.goto("https://example.com")
```

Don't use stock Playwright or Puppeteer: both put back the traces this SDK
avoids, `Runtime.enable` above all. The patched forks strip them and are
drop-in replacements.

Selenium works too (`enable_chromedriver=True`, see
[`examples/selenium_connect.py`](https://github.com/surfskyio/surfsky-py/blob/main/examples/selenium_connect.py)) but isn't
recommended: chromedriver is easy to detect.

## Proxies

Every session start accepts `proxy=`. Use your own, or one of Surfsky's
built-in pools:

- Premium: clean residential and mobile IPs, targeted by country, region,
  city, ASN or coordinates, with sticky sessions. Use it for production.
- Shared: a pool for testing. Don't rely on it against sites that matter.

```python
from surfsky import PremiumProxy, ProxyCycle, ProxyGeo, ProxyTemplate, SharedProxy

proxy = PremiumProxy(country="us", region="ny", type="mobile")  # Surfsky premium
proxy = SharedProxy(country="us")                              # Surfsky shared, for tests
proxy = ProxyGeo(country="de")                                 # premium if set up, else shared
proxy = "socks5://user:pass@host:1080"                         # your own
proxy = ProxyCycle(my_proxies)                                 # round-robin over your own list
proxy = ProxyTemplate("http://user-sessid-{session}:pw@gate.example.com:7000")
```

`client.proxies` lists available countries, regions and cities, plus your quota.

## Profiles and the REST API

Profiles, proxies, fingerprints, extensions and account limits are all typed
calls on the client. You'll mostly use profiles: a profile is a saved identity,
so every session started on it gets the same fingerprint, proxy and cookies.

```python
from surfsky import AsyncSurfsky, Fingerprint, PremiumProxy

async with AsyncSurfsky() as client:
    profile = await client.profiles.create(
        title="account-1",
        fingerprint=Fingerprint(os="win", os_arch="x86", os_version="11"),
        proxy=PremiumProxy(country="us"),
    )
    async with client.browser(profile_uuid=profile.uuid) as browser:
        await browser.goto("https://example.com/login")
        ...  # log in once, the cookies stay with the profile
```

Next time, `client.browser(profile_uuid=...)` brings back the same identity,
still logged in. `client.profiles.iter_all()` lists your profiles and
`delete(uuid)` removes one.

The sync client, `Surfsky`, has the same REST calls without the browser API.
For endpoints the SDK doesn't cover, `client.request(method, path, ...)` sends
the request and returns the raw `httpx.Response`.

## API reference

Every browser method is async. `Browser` is a `Page` plus the connection: page
methods on it act on the session's first tab. Waits take `timeout` in seconds,
default 30, and raise `BrowserTimeoutError`.

### Client

`AsyncSurfsky(api_token=None, base_url=None, timeout=30, max_retries=3, backoff_factor=0.5)`.
`Surfsky` is the sync client, REST only.

| Method                                                                           | Description                                                                                |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `session(profile_uuid=None, **options)`                                          | Start a session, stop it on exit. Yields `Session` with `internal_uuid` and `connect_url`. |
| `browser(profile_uuid=None, block_resources=None, block_urls=None, **options)`   | Start a session and connect a `Browser`. Stops on exit.                                    |
| `browsers(concurrency="auto", block_resources=None, block_urls=None, **options)` | A `BrowserPool`. Exiting it stops every browser.                                           |
| `map(handler, items, **pool_options)`                                            | `browsers()` and `pool.map()` in one call.                                                 |
| `with_options(timeout=None, max_retries=None, headers=None)`                     | Copy with overrides. Same connection pool.                                                 |
| `request(method, path, json=None, params=None, ...)`                             | Raw call. Returns `httpx.Response`, never raises on status.                                |

Session options: `fingerprint`, `proxy`, `browser_settings`
(`inactive_kill_timeout`, `cache_enabled`, `cache_key`), `enable_chromedriver`,
`extensions` (up to 5 uuids), `proxy_blacklist`, `domain_routes`, `cookies`.
`fingerprint` and `cookies` apply to one-time sessions only.

### Pool

| Member                     | Description                                                                                                                                                                |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pool.lease()`             | Async context manager. Yields a live browser and hands it back on exit. Waits while all are busy.                                                                          |
| `pool.map(handler, items)` | `handler(browser, item)` per item, `capacity` at a time. Returns `PoolOutcome` list in input order: `item`, `index`, `value`, `error`, `ok`. Raise `StopRun` to end early. |
| `pool.capacity`            | Max live browsers. `"auto"` is the plan's limit, `SURFSKY_MAX_BROWSERS` overrides it.                                                                                      |
| `browser.data`             | Per-browser dict. Survives leases.                                                                                                                                         |
| `browser.use_count`        | Leases so far, current included.                                                                                                                                           |
| `browser.retire()`         | Replace this browser with a fresh identity after the lease.                                                                                                                |
| `browser.internal_uuid`    | Session id.                                                                                                                                                                |
| `browser.connected`        | Socket is up.                                                                                                                                                              |

The plan limit counts browsers started elsewhere with the same token. `lease()`
waits for one of its own and raises `RateLimitError` only if it has none.

### Navigation

| Method                                          | Description                                                                                     |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `goto(url, wait_until="load", timeout=30)`      | Navigate. `wait_until`: `commit`, `domcontentloaded`, `load`, `networkidle`. Follows redirects. |
| `reload(wait_until="load", timeout=30)`         | Reload.                                                                                         |
| `go_back(timeout=30)`, `go_forward(timeout=30)` | Returns the new URL, `None` at the end of history.                                              |
| `wait_for_load_state(state="load", timeout=30)` | Wait for the current document to reach `state`.                                                 |
| `wait_for_url(fragment, timeout=30)`            | Wait until the URL contains `fragment`. Returns the URL.                                        |
| `status`                                        | HTTP status of the current document. Set even when `goto` raises.                               |

### Reading

| Method                                                                   | Description                                                                             |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| `url()`, `title()`                                                       | Current URL and title.                                                                  |
| `content()`                                                              | Full HTML.                                                                              |
| `outer_html(selector)`                                                   | HTML of the first match, `None` if none.                                                |
| `inner_text(selector)`, `all_inner_texts(selector)`                      | Rendered text of the first match, or of every match. Runs script in the isolated world. |
| `get_attribute(selector, name)`                                          | `None` if missing.                                                                      |
| `count(selector)`                                                        | Number of matches.                                                                      |
| `is_visible(selector)`                                                   | First match has a bounding box.                                                         |
| `wait_for_selector(selector, visible=True, timeout=30)`                  | Wait for the element, visible by default.                                               |
| `screenshot(selector=None, full_page=False, format="png", quality=None)` | Bytes. Viewport, one element or the full page. `format`: `png`, `jpeg`, `webp`.         |

### Input

Server-side human emulation. The first CSS match is used. `click`, `dblclick`
and `hover` also take `wait_for_visible`, `scroll_into_view`, `pre_delay`,
`post_delay`, `timeout`.

| Method                                                                                                                                                           | Description                                                                                                           |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `click(selector, button=None, click_count=None, modifiers=None)`                                                                                                 | `button`: `left`, `right`, `middle`. `modifiers`: `Alt`, `Control`, `Meta`, `Shift`. Waits up to 30s for the element. |
| `dblclick(selector, ...)`                                                                                                                                        | Double-click.                                                                                                         |
| `hover(selector)`                                                                                                                                                | Move the mouse over it.                                                                                               |
| `type(selector, text)`                                                                                                                                           | Click, then type after the existing text.                                                                             |
| `fill(selector, text)`                                                                                                                                           | Select the existing text, then type over it.                                                                          |
| `select_option(selector, value=None, label=None)`                                                                                                                | Pick an `<option>` by value or label. Returns the value.                                                              |
| `scroll(delta_x=None, delta_y=None, duration=None)`                                                                                                              | Animated scroll.                                                                                                      |
| `scroll_into_view(selector, behavior=None)`, `scroll_to(x=None, y=None, behavior=None)`                                                                          | `behavior`: `smooth`, `instant`.                                                                                      |
| `keyboard.type(text)`, `keyboard.press(key, modifiers=None, delay=None)`                                                                                         | Keys to the focused element. `press("Enter")` doesn't submit forms, click the button.                                 |
| `mouse.move(x, y)`, `mouse.click(x, y)`, `mouse.down(x, y)`, `mouse.up(x, y)`, `mouse.wheel(delta_x, delta_y)`, `mouse.drag(start_x=, start_y=, end_x=, end_y=)` | Viewport coordinates.                                                                                                 |

### Script

| Method                                                            | Description                                                                                                  |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `evaluate(expression, *args, isolated=True, await_promise=True)`  | Run JS. A function is called with `args` as JSON, anything else is an expression. Isolated world by default. |
| `wait_for_function(expression, *args, isolated=True, timeout=30)` | Poll until truthy. Returns the value.                                                                        |
| `send(method, params=None)`                                       | Raw page-level CDP command.                                                                                  |
| `browser.cdp`                                                     | Raw browser-level client: `send`, `post`, `on`.                                                              |

### Cookies and storage

| Method                                             | Description                                           |
| -------------------------------------------------- | ----------------------------------------------------- |
| `cookies()`                                        | All cookies, `httpOnly` included, as `Cookie` models. |
| `set_cookies(cookies)`                             | `Cookie` models or dicts.                             |
| `clear_cookies()`                                  | Remove every cookie.                                  |
| `local_storage()`, `set_local_storage(values)`     | Current origin, as a dict.                            |
| `session_storage()`, `set_session_storage(values)` | Same for sessionStorage.                              |

### Network

| Method                                    | Description                                                                                     |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `capture_responses(*fragments)`           | Record responses whose URL contains a fragment. Call before navigating.                         |
| `wait_for_response(fragment, timeout=30)` | First captured match. `CapturedResponse`: `url`, `status`, `headers`, `body`, `text`, `json()`. |
| `responses`                               | Everything captured, oldest first.                                                              |
| `stop_capturing()`                        | Drop captures, stop recording.                                                                  |

### Dialogs

`page.on_dialog = handler(kind, message)`. `kind`: `alert`, `confirm`, `prompt`,
`beforeunload`. Return `True` to accept, `False` to dismiss, a string to answer
a prompt, `None` for the default. Default: dismiss, except `beforeunload` is
accepted.

### Pages

| Member                                      | Description                                                 |
| ------------------------------------------- | ----------------------------------------------------------- |
| `browser.pages`                             | Every open page. The browser's own first, newest last.      |
| `browser.new_page()`                        | Blank page in a new window.                                 |
| `browser.wait_for_page(action, timeout=30)` | Await `action` (a click) and return the page it opened.     |
| `page.close()`                              | Close the tab. On the browser itself: close the connection. |
| `page.closed`                               | `True` once gone. Commands then raise `PageClosedError`.    |
| `page.bring_to_front()`                     | Make it the visible tab. Screenshots of hidden tabs hang.   |
| `page.target_id`                            | CDP target id.                                              |

### REST

Same on both clients.

| Namespace             | Methods                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `client.profiles`     | `start_one_time(**options)`, `start(uuid, **options)`, `stop(session)`, `stop_all()`, `list_active()`, `create(title=, fingerprint=, description=, proxy=, cookies=, storage_options=)`, `get(uuid)`, `update(uuid, **fields)`, `delete(uuid)`, `delete_many(uuids)`, `list_page(page=, page_len=, ordering=)`, `iter_all(page_len=100, ordering="created")`, `export_cookies(uuid, export_format="json")`, `import_cookies(uuid, cookies)`, `scrape(session, url, screenshot=, wait=, wait_until=, wait_for=, human_actions=)` |
| `client.proxies`      | `countries()`, `regions(country)`, `cities(country, region)`, `quota()`, `premium_stats()`, `shared_countries()`, `shared_quota()`, `shared_stats()`. The first four need a premium provider on the account.                                                                                                                                                                                                                                                                                                                    |
| `client.fingerprints` | `renderers(os, os_arch)`, `screens(os, os_arch)`, `device_models(os=, os_arch=, os_version=, device_type=)`                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `client.extensions`   | `upload(file, name)` (path, bytes or stream, zip up to 100 MB), `list_all()`, `get(uuid)`, `update(uuid, name=)`, `delete(uuid)`                                                                                                                                                                                                                                                                                                                                                                                                |
| `client.account`      | `session_limits()`, `browser_limits()`, `max_browsers()`                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |

### Errors

All subclasses of `SurfskyError`. HTTP: `APIError` subclasses named after the
status (`NotFoundError`, `RateLimitError`, ...). Browser: `CDPError`,
`BrowserTimeoutError`, `PageClosedError`. Idempotent requests retry on 429, 5xx
and connection errors. POST and PATCH retry on 429 only, so a lost reply can't
start a second billed session.

## Examples

More examples in [`examples/`](https://github.com/surfskyio/surfsky-py/tree/main/examples). Install the extras first:

```sh
uv sync --group examples
```

## Development

```sh
uv sync --all-extras
uv run ruff check . && uv run ty check && uv run pytest
```

Live tests start real sessions and bill your account:

```sh
SURFSKY_LIVE_TESTS=1 SURFSKY_API_TOKEN=... uv run pytest tests/test_live_concurrency.py
```

## License

MIT
