"""Log in once per browser, then reuse that identity for many pages.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/login.py
"""

import asyncio

from surfsky import AsyncSurfsky, Browser


async def log_in(browser: Browser) -> None:
    if browser.data.get("logged_in"):
        return
    await browser.goto(
        "https://the-internet.herokuapp.com/login", wait_until="domcontentloaded"
    )
    await browser.type("#username", "tomsmith")
    await browser.type("#password", "SuperSecretPassword!")
    await browser.click('button[type="submit"]')
    await browser.wait_for_url("/secure", timeout=15)
    browser.data["logged_in"] = True


async def job(browser: Browser, n: int) -> str:
    await log_in(browser)
    await browser.goto(
        "https://the-internet.herokuapp.com/secure", wait_until="domcontentloaded"
    )
    state = "login ok" if (await browser.url()).endswith("/secure") else "login failed"
    if browser.use_count >= 3:
        browser.retire()  # the next lease gets a fresh browser
    return f"job {n} on {browser.internal_uuid[:8]} lease {browser.use_count}: {state}"


async def main() -> None:
    async with AsyncSurfsky() as client:
        outcomes = await client.map(job, list(range(6)), concurrency=2)
    for outcome in outcomes:
        print(outcome.value if outcome.ok else f"FAILED: {outcome.error!r}")


if __name__ == "__main__":
    asyncio.run(main())
