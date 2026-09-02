import pytest

from surfsky import AsyncSurfsky, ConfigurationError, Surfsky


def test_requires_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SURFSKY_API_TOKEN", raising=False)
    with pytest.raises(ConfigurationError, match="token"):
        Surfsky()


def test_explicit_token_and_url():
    client = Surfsky(api_token="tok-123", base_url="https://api.example.com/")
    assert client.http.headers["X-Cloud-Api-Token"] == "tok-123"
    assert "surfsky-py/" in client.http.headers["User-Agent"]
    assert client.base_url == "https://api.example.com"


def test_requires_base_url(monkeypatch: pytest.MonkeyPatch):
    # the endpoint comes with the account: no public default to fall back to
    monkeypatch.setenv("SURFSKY_API_TOKEN", "env-tok")
    monkeypatch.delenv("SURFSKY_API_BASE_URL", raising=False)
    with pytest.raises(ConfigurationError, match="base_url"):
        Surfsky()


def test_base_url_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SURFSKY_API_TOKEN", "env-tok")
    monkeypatch.setenv("SURFSKY_API_BASE_URL", "https://custom.example.com")
    assert Surfsky().base_url == "https://custom.example.com"


def test_with_options_clone_does_not_close_parent(httpx_mock, client: Surfsky):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active",
        json={"success": True, "msg": "", "data": []},
    )
    with client.with_options(timeout=5):
        pass
    assert client.profiles.list_active() == []


@pytest.mark.anyio
async def test_async_with_options_clone_does_not_close_parent(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active",
        json={"success": True, "msg": "", "data": []},
    )
    async with AsyncSurfsky(api_token="t", base_url="https://api.test") as client:
        async with client.with_options(timeout=5):
            pass
        assert await client.profiles.list_active() == []


def test_session_rejects_fingerprint_for_a_persistent_profile(
    httpx_mock, client: Surfsky
):
    from surfsky import Fingerprint

    with pytest.raises(ValueError, match="one-time"):
        with client.session(profile_uuid="p1", fingerprint=Fingerprint(os="mac")):
            pass  # pragma: no cover
    assert not httpx_mock.get_requests()


def test_max_browsers_is_the_plans_cap_not_the_free_slots(
    monkeypatch: pytest.MonkeyPatch, httpx_mock, client: Surfsky
):
    # what a pool may grow to once the slots taken elsewhere are given back
    monkeypatch.delenv("SURFSKY_MAX_BROWSERS", raising=False)
    httpx_mock.add_response(
        method="GET", url="https://api.test/users/browser-limits",
        json={
            "success": True,
            "data": {
                "has_browser_limits": True,
                "parallel_browsers": 25,
                "running": 20,
                "available": 5,
            },
        },
    )
    assert client.account.max_browsers() == 25


def test_max_browsers_env_override_skips_api(
    monkeypatch: pytest.MonkeyPatch, httpx_mock, client: Surfsky
):
    monkeypatch.setenv("SURFSKY_MAX_BROWSERS", "3")  # no response registered
    assert client.account.max_browsers() == 3


def test_max_browsers_uncapped_plan_uses_default(
    monkeypatch: pytest.MonkeyPatch, httpx_mock, client: Surfsky
):
    from surfsky.resources.account import DEFAULT_MAX_BROWSERS

    monkeypatch.delenv("SURFSKY_MAX_BROWSERS", raising=False)
    httpx_mock.add_response(
        method="GET", url="https://api.test/users/browser-limits",
        json={"success": True, "data": {"has_browser_limits": False, "running": 1}},
    )
    assert client.account.max_browsers() == DEFAULT_MAX_BROWSERS


def test_max_browsers_missing_endpoint_uses_default(
    monkeypatch: pytest.MonkeyPatch, httpx_mock, client: Surfsky
):
    from surfsky.resources.account import DEFAULT_MAX_BROWSERS

    monkeypatch.delenv("SURFSKY_MAX_BROWSERS", raising=False)
    httpx_mock.add_response(
        method="GET", url="https://api.test/users/browser-limits", status_code=404,
        json={"success": False, "msg": "Not found"},
    )
    assert client.account.max_browsers() == DEFAULT_MAX_BROWSERS


@pytest.mark.anyio
async def test_async_max_browsers_uses_endpoint(
    monkeypatch: pytest.MonkeyPatch, httpx_mock
):
    monkeypatch.delenv("SURFSKY_MAX_BROWSERS", raising=False)
    httpx_mock.add_response(
        method="GET", url="https://api.test/users/browser-limits",
        json={
            "success": True,
            "data": {"has_browser_limits": True, "parallel_browsers": 4, "running": 4},
        },
    )
    aclient = AsyncSurfsky(api_token="t", base_url="https://api.test")
    assert await aclient.account.max_browsers() == 4
    await aclient.aclose()


def test_session_start_rejects_an_unknown_keyword(client: Surfsky):
    # the request model forbids extras, so a typo cannot ship silently
    with pytest.raises(ValueError, match="fingerpint"):
        client.profiles.start_one_time(fingerpint=None)  # type: ignore[call-arg]


def test_pool_rejects_an_unknown_keyword():
    from surfsky import AsyncSurfsky

    aclient = AsyncSurfsky(api_token="t", base_url="https://api.test")
    with pytest.raises(ValueError, match="concurency"):
        aclient.browsers(concurency=4)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "module",
    ["account", "extensions", "fingerprints", "profiles", "proxies"],
)
def test_every_async_resource_mirrors_its_sync_twin(module: str):
    # the two classes are hand-written mirrors: an endpoint added to one and
    # forgotten on the other only shows up when someone reaches for it
    import importlib
    import inspect

    mod = importlib.import_module(f"surfsky.resources.{module}")
    name = module.capitalize()
    sync, asyncish = getattr(mod, name), getattr(mod, "Async" + name)

    def methods(cls) -> dict[str, inspect.Signature]:
        return {
            n: inspect.signature(f)
            for n, f in vars(cls).items()
            if not n.startswith("_") and callable(f)
        }

    on_sync, on_async = methods(sync), methods(asyncish)
    assert on_sync.keys() == on_async.keys()
    for method, signature in on_sync.items():
        # the return annotation differs (a coroutine), the arguments must not
        assert signature.parameters == on_async[method].parameters, method


# The async classes are hand-written mirrors, so each one is driven once here:
# a twin wired to the wrong Spec builder passes the parity check above but hits
# the wrong endpoint. (method, args, kwargs, "VERB /path", the data it parses)
_ASYNC_CALLS: list[tuple[str, tuple, dict, str, object]] = [
    ("account.session_limits", (), {}, "GET /users/session-limits", {}),
    ("account.browser_limits", (), {}, "GET /users/browser-limits", {}),
    ("fingerprints.renderers", ("win", "x86"), {}, "GET /fingerprint/renderers", []),
    ("fingerprints.screens", ("win", "x86"), {}, "GET /fingerprint/screens", []),
    ("fingerprints.device_models", (), {}, "GET /fingerprint/device_models", []),
    ("extensions.upload", (b"PK", "ublock"), {}, "POST /extensions", {"uuid": "e1"}),
    ("extensions.list_all", (), {}, "GET /extensions", {"extensions": []}),
    ("extensions.get", ("e1",), {}, "GET /extensions/e1", {"uuid": "e1"}),
    ("extensions.update", ("e1",), {"name": "x"}, "PATCH /extensions/e1", {"uuid": "e1"}),
    ("extensions.delete", ("e1",), {}, "DELETE /extensions/e1", None),
    ("proxies.countries", (), {}, "GET /proxies/countries", []),
    ("proxies.regions", ("us",), {}, "GET /proxies/regions/us", []),
    ("proxies.cities", ("us", "il"), {}, "GET /proxies/cities/us/il", []),
    ("proxies.quota", (), {}, "GET /proxies/quota", {}),
    ("proxies.premium_stats", (), {}, "GET /proxies/premium/stats", {}),
    ("proxies.shared_countries", (), {}, "GET /proxies/shared/countries", []),
    ("proxies.shared_quota", (), {}, "GET /proxies/shared/quota", {}),
    ("proxies.shared_stats", (), {}, "GET /proxies/shared/stats", {}),
    ("profiles.start", ("p1",), {}, "POST /profiles/p1/start",
     {"internal_uuid": "s1", "ws_url": "wss://x"}),
    ("profiles.stop", ("s1",), {}, "POST /profiles/s1/stop", None),
    ("profiles.stop_all", (), {}, "POST /profiles/stop", {}),  # no id: stops them all
    ("profiles.list_active", (), {}, "GET /profiles/active", []),
    ("profiles.get", ("p1",), {}, "GET /profiles/p1", {"uuid": "p1", "title": "t"}),
    ("profiles.update", ("p1",), {"proxy": None}, "PATCH /profiles/p1",
     {"uuid": "p1", "title": "t"}),
    ("profiles.update", ("p1",), {"title": "t"}, "PATCH /profiles/p1",
     {"uuid": "p1", "title": "t"}),
    ("profiles.delete", ("p1",), {}, "DELETE /profiles/p1", {"uuid": "p1"}),
    ("profiles.delete_many", (["p1"],), {}, "DELETE /profiles", {}),
    ("profiles.list_page", (), {}, "GET /profiles", []),
    ("profiles.export_cookies", ("p1",), {}, "GET /profiles/p1/cookies", {"cookies": []}),
    ("profiles.import_cookies", ("p1", []), {}, "POST /profiles/p1/cookies", None),
    ("profiles.scrape", ("s1", "https://x"), {}, "POST /profiles/s1/scrape",
     {"url": "https://x/", "content": ""}),
]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("dotted", "args", "kwargs", "expected", "data"),
    _ASYNC_CALLS,
    ids=[row[0] for row in _ASYNC_CALLS],
)
async def test_async_resource_methods_reach_their_own_endpoint(
    httpx_mock, dotted, args, kwargs, expected, data
):
    verb, path = expected.split(" ")
    httpx_mock.add_response(json={"success": True, "data": data})

    namespace, method = dotted.split(".")
    async with AsyncSurfsky(api_token="t", base_url="https://api.test") as aclient:
        await getattr(getattr(aclient, namespace), method)(*args, **kwargs)

    [request] = httpx_mock.get_requests()
    assert (request.method, request.url.path) == (verb, path)


@pytest.mark.anyio
async def test_async_create_and_iter_all_reach_their_own_endpoints(httpx_mock):
    # the two that need a body of their own, kept out of the table above
    from surfsky import Fingerprint

    httpx_mock.add_response(json={"success": True, "data": {"uuid": "p1"}})
    async with AsyncSurfsky(api_token="t", base_url="https://api.test") as aclient:
        created = await aclient.profiles.create(
            title="demo", fingerprint=Fingerprint(os="win")
        )
    assert created.uuid == "p1"
    request = httpx_mock.get_requests()[0]
    assert (request.method, request.url.path) == ("POST", "/profiles")


@pytest.mark.anyio
async def test_async_max_browsers_takes_the_same_two_shortcuts_as_the_sync_one(
    monkeypatch: pytest.MonkeyPatch, httpx_mock
):
    from surfsky.resources.account import DEFAULT_MAX_BROWSERS

    aclient = AsyncSurfsky(api_token="t", base_url="https://api.test")
    monkeypatch.setenv("SURFSKY_MAX_BROWSERS", "3")  # no response registered
    assert await aclient.account.max_browsers() == 3

    monkeypatch.delenv("SURFSKY_MAX_BROWSERS")
    httpx_mock.add_response(
        method="GET", url="https://api.test/users/browser-limits", status_code=404,
        json={"success": False, "msg": "Not found"},
    )
    assert await aclient.account.max_browsers() == DEFAULT_MAX_BROWSERS
    await aclient.aclose()


def test_session_limits_report_the_monthly_plan(httpx_mock, client: Surfsky):
    httpx_mock.add_response(
        method="GET", url="https://api.test/users/session-limits",
        json={"success": True, "data": {
            "has_session_limits": True, "spm": 500, "used": 120, "remaining": 380,
        }},
    )
    limits = client.account.session_limits()
    assert (limits.spm, limits.remaining) == (500, 380)


def test_stop_all_stops_every_running_session(httpx_mock, client: Surfsky):
    # no id in the path: /profiles/stop is the fleet-wide one
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/stop",
        json={"success": True, "data": {"stopped": ["s1", "s2"], "failed": []}},
    )
    assert client.profiles.stop_all().stopped == ["s1", "s2"]
    assert httpx_mock.get_requests()[0].url.path == "/profiles/stop"
