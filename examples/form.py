"""A list of queries through a site's search form, on every browser you have.

Shows how to work a form: type, submit, wait for the page it loads, read the result.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/form.py
"""

import asyncio

from surfsky import AsyncSurfsky, Browser

URL = "https://www.scrapethissite.com/pages/forms/"
QUERIES = ["Boston", "New York", "Detroit", "Chicago"]
ROWS = """
    [...document.querySelectorAll("tr.team")].map(tr => ({
        name: tr.querySelector("td.name").innerText.trim(),
        year: tr.querySelector("td.year").innerText.trim(),
        wins: tr.querySelector("td.wins").innerText.trim(),
        losses: tr.querySelector("td.losses").innerText.trim(),
    }))
"""


async def seasons(browser: Browser, query: str) -> list[dict[str, str]]:
    await browser.goto(URL, wait_until="domcontentloaded")
    # the form: type into its field, click its button, wait for the page it loads
    await browser.type("#q", query)
    await browser.click("input[type=submit]")
    await browser.wait_for_url("q=")
    await browser.wait_for_load_state()
    # a dropdown whose change handler reloads the page: pick, then wait again
    await browser.select_option("#per_page", "100")
    await browser.wait_for_url("per_page=100")
    await browser.wait_for_load_state()
    return await browser.evaluate(ROWS)


async def main() -> None:
    async with AsyncSurfsky() as client:
        outcomes = await client.map(seasons, QUERIES, block_resources={"image", "font"})
    for outcome in outcomes:
        rows = outcome.value or []
        if not rows:
            print(f"{outcome.item}: {outcome.error!r}" if outcome.error else f"{outcome.item}: no rows")
            continue
        teams = ", ".join(sorted({row["name"] for row in rows}))
        best = max(rows, key=lambda row: int(row["wins"]))
        print(f"{outcome.item}: {len(rows)} seasons ({teams})")
        print(f"  best: {best['name']} {best['year']}, {best['wins']}-{best['losses']}")


if __name__ == "__main__":
    asyncio.run(main())
