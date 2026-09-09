# API reference

Every browser method is async. `Browser` is a `Page` plus the connection: page
methods on it act on the session's first tab. Waits take `timeout` in seconds,
default 30, and raise `BrowserTimeoutError`.

## Client

`AsyncSurfsky(api_token=None, base_url=None, timeout=30, max_retries=3, backoff_factor=0.5)`.
`Surfsky` is the sync client, REST only.

| Method | Description |
| --- | --- |
| `session(profile_uuid=None, **options)` | Start a session, stop it on exit. Yields `Session` with `internal_uuid` and `connect_url`. |
| `browser(profile_uuid=None, block_resources=None, block_urls=None, **options)` | Start a session and connect a `Browser`. Stops on exit. |
| `browsers(concurrency="auto", block_resources=None, block_urls=None, **options)` | A `BrowserPool`. Exiting it stops every browser. |
| `map(handler, items, **pool_options)` | `browsers()` and `pool.map()` in one call. |
| `with_options(timeout=None, max_retries=None, headers=None)` | Copy with overrides. Same connection pool. |
| `request(method, path, json=None, params=None, ...)` | Raw call. Returns `httpx.Response`, never raises on status. |

Session options: `fingerprint`, `proxy`, `browser_settings`
(`inactive_kill_timeout`, `cache_enabled`, `cache_key`), `enable_chromedriver`,
`extensions` (up to 5 uuids), `proxy_blacklist`, `domain_routes`, `cookies`.
`fingerprint` and `cookies` apply to one-time sessions only.

## Pool

| Member | Description |
| --- | --- |
| `pool.lease()` | Async context manager. Yields a live browser and hands it back on exit. Waits while all are busy. |
| `pool.map(handler, items)` | `handler(browser, item)` per item, `capacity` at a time. Returns `PoolOutcome` list in input order: `item`, `index`, `value`, `error`, `ok`. Raise `StopRun` to end early. |
| `pool.capacity` | Max live browsers. `"auto"` is the plan's limit, `SURFSKY_MAX_BROWSERS` overrides it. |
| `browser.data` | Per-browser dict. Survives leases. |
| `browser.use_count` | Leases so far, current included. |
| `browser.retire()` | Replace this browser with a fresh identity after the lease. |
| `browser.internal_uuid` | Session id. |
| `browser.connected` | Socket is up. |

The plan limit counts browsers started elsewhere with the same token. `lease()`
waits for one of its own and raises `RateLimitError` only if it has none.

## Navigation

| Method | Description |
| --- | --- |
| `goto(url, wait_until="load", timeout=30)` | Navigate. `wait_until`: `commit`, `domcontentloaded`, `load`, `networkidle`. Follows redirects. |
| `reload(wait_until="load", timeout=30)` | Reload. |
| `go_back(timeout=30)`, `go_forward(timeout=30)` | Returns the new URL, `None` at the end of history. |
| `wait_for_load_state(state="load", timeout=30)` | Wait for the current document to reach `state`. |
| `wait_for_url(fragment, timeout=30)` | Wait until the URL contains `fragment`. Returns the URL. |
| `status` | HTTP status of the current document. Set even when `goto` raises. |

## Reading

`selector` is CSS, or XPath when it starts with `//`, `..` or `xpath=`. XPath
covers the DOM reads below and `screenshot(selector=)`; `inner_text`,
`all_inner_texts`, `select_option` and the input methods take CSS only.

| Method | Description |
| --- | --- |
| `url()`, `title()` | Current URL and title. |
| `content()` | Full HTML. |
| `outer_html(selector)` | HTML of the first match, `None` if none. |
| `inner_text(selector)`, `all_inner_texts(selector)` | Rendered text of the first match, or of every match. Runs script in the isolated world. |
| `get_attribute(selector, name)` | `None` if missing. |
| `count(selector)` | Number of matches. |
| `is_visible(selector)` | First match has a bounding box. |
| `wait_for_selector(selector, visible=True, timeout=30)` | Wait for the element, visible by default. |
| `screenshot(selector=None, full_page=False, format="png", quality=None)` | Bytes. Viewport, one element or the full page. `format`: `png`, `jpeg`, `webp`. |

## Input

Server-side human emulation. The first CSS match is used. `click`, `dblclick`
and `hover` also take `wait_for_visible`, `scroll_into_view`, `pre_delay`,
`post_delay`, `timeout`.

| Method | Description |
| --- | --- |
| `click(selector, button=None, click_count=None, modifiers=None)` | `button`: `left`, `right`, `middle`. `modifiers`: `Alt`, `Control`, `Meta`, `Shift`. Waits up to 30s for the element. |
| `dblclick(selector, ...)` | Double-click. |
| `hover(selector)` | Move the mouse over it. |
| `type(selector, text)` | Click, then type after the existing text. |
| `fill(selector, text)` | Select the existing text, then type over it. |
| `select_option(selector, value=None, label=None)` | Pick an `<option>` by value or label. Returns the value. |
| `scroll(delta_x=None, delta_y=None, duration=None)` | Animated scroll. |
| `scroll_into_view(selector, behavior=None)`, `scroll_to(x=None, y=None, behavior=None)` | `behavior`: `smooth`, `instant`. |
| `keyboard.type(text)`, `keyboard.press(key, modifiers=None, delay=None)` | Keys to the focused element. `press("Enter")` doesn't submit forms, click the button. |
| `mouse.move(x, y)`, `mouse.click(x, y)`, `mouse.down(x, y)`, `mouse.up(x, y)`, `mouse.wheel(delta_x, delta_y)`, `mouse.drag(start_x=, start_y=, end_x=, end_y=)` | Viewport coordinates. |

## Script

| Method | Description |
| --- | --- |
| `evaluate(expression, *args, isolated=True, await_promise=True)` | Run JS. A function is called with `args` as JSON, anything else is an expression. Isolated world by default. |
| `wait_for_function(expression, *args, isolated=True, timeout=30)` | Poll until truthy. Returns the value. |
| `send(method, params=None)` | Raw page-level CDP command. |
| `browser.cdp` | Raw browser-level client: `send`, `post`, `on`. |

## Cookies and storage

| Method | Description |
| --- | --- |
| `cookies()` | All cookies, `httpOnly` included, as `Cookie` models. |
| `set_cookies(cookies)` | `Cookie` models or dicts. |
| `clear_cookies()` | Remove every cookie. |
| `local_storage()`, `set_local_storage(values)` | Current origin, as a dict. |
| `session_storage()`, `set_session_storage(values)` | Same for sessionStorage. |

## Network

| Method | Description |
| --- | --- |
| `capture_responses(*fragments)` | Record responses whose URL contains a fragment. Call before navigating. |
| `wait_for_response(fragment, timeout=30)` | First captured match. `CapturedResponse`: `url`, `status`, `headers`, `body`, `text`, `json()`. |
| `responses` | Everything captured, oldest first. |
| `stop_capturing()` | Drop captures, stop recording. |

## Dialogs

`page.on_dialog = handler(kind, message)`. `kind`: `alert`, `confirm`, `prompt`,
`beforeunload`. Return `True` to accept, `False` to dismiss, a string to answer
a prompt, `None` for the default. Default: dismiss, except `beforeunload` is
accepted.

## Pages

| Member | Description |
| --- | --- |
| `browser.pages` | Every open page. The browser's own first, newest last. |
| `browser.new_page()` | Blank page in a new window. |
| `browser.wait_for_page(action, timeout=30)` | Await `action` (a click) and return the page it opened. |
| `page.close()` | Close the tab. On the browser itself: close the connection. |
| `page.closed` | `True` once gone. Commands then raise `PageClosedError`. |
| `page.bring_to_front()` | Make it the visible tab. Screenshots of hidden tabs hang. |
| `page.target_id` | CDP target id. |

## REST

Same on both clients.

| Namespace | Methods |
| --- | --- |
| `client.profiles` | `start_one_time(**options)`, `start(uuid, **options)`, `stop(session)`, `stop_all()`, `list_active()`, `create(title=, fingerprint=, description=, proxy=, cookies=, storage_options=)`, `get(uuid)`, `update(uuid, **fields)`, `delete(uuid)`, `delete_many(uuids)`, `list_page(page=, page_len=, ordering=)`, `iter_all(page_len=100, ordering="created")`, `export_cookies(uuid, export_format="json")`, `import_cookies(uuid, cookies)`, `scrape(session, url, screenshot=, wait=, wait_until=, wait_for=, human_actions=)` |
| `client.proxies` | `countries()`, `regions(country)`, `cities(country, region)`, `quota()`, `premium_stats()`, `shared_countries()`, `shared_quota()`, `shared_stats()`. The first four need a premium provider on the account. |
| `client.fingerprints` | `renderers(os, os_arch)`, `screens(os, os_arch)`, `device_models(os=, os_arch=, os_version=, device_type=)` |
| `client.extensions` | `upload(file, name)` (path, bytes or stream, zip up to 100 MB), `list_all()`, `get(uuid)`, `update(uuid, name=)`, `delete(uuid)` |
| `client.account` | `session_limits()`, `browser_limits()`, `max_browsers()` |

## Errors

All subclasses of `SurfskyError`. HTTP: `APIError` subclasses named after the
status (`NotFoundError`, `RateLimitError`, ...). Browser: `CDPError`,
`BrowserTimeoutError`, `PageClosedError`. Idempotent requests retry on 429, 5xx
and connection errors. POST and PATCH retry on 429 only, so a lost reply can't
start a second billed session.
