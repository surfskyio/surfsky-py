"""Log several accounts in, each on its own persistent profile.

A profile keeps its fingerprint, proxy country and cookies between sessions:
one profile, one identity. Profiles are found again by title, so a run that
died midway can just be repeated. They are deleted once every account is done.

    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/multi_account.py
"""

import asyncio

from surfsky import AsyncSurfsky, Browser, Fingerprint, ProxyGeo, StorageOptions

PREFIX = "demo-account-"
LOGIN = "https://the-internet.herokuapp.com/login"
SECURE = "https://the-internet.herokuapp.com/secure"
ACCOUNTS = {
    "account1": (Fingerprint(os="win", os_arch="x86", os_version="11"), "us"),
    "account2": (Fingerprint(os="mac", os_arch="arm", os_version="15"), "de"),
    "account3": (Fingerprint(os="win", os_arch="x86", os_version="10"), "fr"),
}


async def our_profiles(client: AsyncSurfsky) -> dict[str, str]:
    return {
        profile.title.removeprefix(PREFIX): profile.uuid
        async for profile in client.profiles.iter_all()
        if profile.title and profile.title.startswith(PREFIX)
    }


async def ensure_profiles(client: AsyncSurfsky) -> dict[str, str]:
    profiles = await our_profiles(client)
    for name, (fingerprint, country) in ACCOUNTS.items():
        if name not in profiles:
            created = await client.profiles.create(
                title=PREFIX + name,
                fingerprint=fingerprint,
                proxy=ProxyGeo(country=country),  # premium if the account has it, else shared
                storage_options=StorageOptions(cookies=True, localstorage=True),
            )
            profiles[name] = created.uuid
            print(f"{name}: created profile {created.uuid}")
    return profiles


async def logged_in(browser: Browser) -> bool:
    await browser.goto(SECURE, wait_until="domcontentloaded")
    return await browser.is_visible('a[href="/logout"]')


async def log_in(browser: Browser) -> None:
    await browser.goto(LOGIN, wait_until="domcontentloaded")
    await browser.fill("#username", "tomsmith")
    await browser.fill("#password", "SuperSecretPassword!")
    await browser.click("button[type=submit]")
    await browser.wait_for_url("/secure")


async def run_account(
    client: AsyncSurfsky, name: str, uuid: str, slots: asyncio.Semaphore
) -> None:
    try:
        async with slots, client.browser(profile_uuid=uuid) as browser:
            if await logged_in(browser):
                print(f"{name}: still logged in from an earlier run")
                return
            await log_in(browser)
            print(f"{name}: logged in, {'ok' if await logged_in(browser) else 'FAILED'}")
    except Exception as exc:
        print(f"{name}: FAILED {exc!r}")


async def main() -> None:
    async with AsyncSurfsky() as client:
        profiles = await ensure_profiles(client)
        try:
            # no more starts at once than the plan allows; one past the cap is a 429
            slots = asyncio.Semaphore(await client.account.max_browsers())
            async with asyncio.TaskGroup() as tg:
                for name, uuid in profiles.items():
                    tg.create_task(run_account(client, name, uuid, slots))
        finally:
            await client.profiles.delete_many(list(profiles.values()))


if __name__ == "__main__":
    asyncio.run(main())
