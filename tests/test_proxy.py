import json

import pytest

from surfsky import (
    AsyncSurfsky,
    MonthlySessionLimitError,
    PremiumProxy,
    PremiumTrafficLimitError,
    ProxyCycle,
    ProxyRandom,
    ProxySource,
    ProxyTemplate,
    RateLimitError,
    SharedProxy,
    SharedTrafficLimitError,
)
from surfsky.proxy import aresolve_proxy, resolve_proxy
from surfsky.types import ProxyGeo

_ONE_TIME = "https://api.test/profiles/one_time"


def _session_json() -> dict:
    return {"internal_uuid": "u1", "ws_url": "wss://x/proxy/u1", "success": True}


def _sent_proxy(httpx_mock) -> object:
    return json.loads(httpx_mock.get_requests()[0].content)["proxy"]


def test_shared_proxy_on_the_wire(httpx_mock, client):
    httpx_mock.add_response(method="POST", url=_ONE_TIME, json=_session_json())
    client.profiles.start_one_time(proxy=SharedProxy(country="us"))
    assert _sent_proxy(httpx_mock) == {"tier": "shared", "country": "us"}


def test_shared_proxy_any_country(httpx_mock, client):
    httpx_mock.add_response(method="POST", url=_ONE_TIME, json=_session_json())
    client.profiles.start_one_time(proxy=SharedProxy())
    assert _sent_proxy(httpx_mock) == {"tier": "shared"}


def test_proxy_geo_stays_pool_free(httpx_mock, client):
    httpx_mock.add_response(method="POST", url=_ONE_TIME, json=_session_json())
    client.profiles.start_one_time(proxy=ProxyGeo(country="de"))
    assert _sent_proxy(httpx_mock) == {"country": "de"}


def test_cycle_round_robins():
    source = ProxyCycle(["http://a:1", "http://b:2"])
    assert [source.pick() for _ in range(3)] == ["http://a:1", "http://b:2", "http://a:1"]


def test_cycle_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        ProxyCycle([])


def test_sources_reject_a_bare_url():
    # a str is iterable too; it must not be split into one-character "proxies"
    with pytest.raises(TypeError, match="single URL"):
        ProxyCycle("socks5://u:p@h:1080")
    with pytest.raises(TypeError, match="single URL"):
        ProxyRandom("socks5://u:p@h:1080")


def test_random_picks_members():
    source = ProxyRandom(["http://a:1", "http://b:2"])
    assert {source.pick() for _ in range(50)} == {"http://a:1", "http://b:2"}


def test_random_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        ProxyRandom([])


def test_template_generates_fresh_session_ids():
    source = ProxyTemplate("http://u-{session}:pw@gate:7000/{n}")
    first, second = source.pick(), source.pick()
    assert first != second
    assert first.endswith("/0")
    assert second.endswith("/1")


def test_template_rejects_unknown_placeholder():
    with pytest.raises(ValueError, match="placeholder"):
        ProxyTemplate("http://u-{sesion}:pw@gate:7000")


def test_template_allows_escaped_braces():
    assert ProxyTemplate("http://u:p{{w}}@gate:7000").pick() == "http://u:p{w}@gate:7000"


def test_resolve_passes_values_through():
    assert resolve_proxy("http://x:1") == "http://x:1"
    assert resolve_proxy(None) is None
    shared = SharedProxy()
    assert resolve_proxy(shared) is shared


def test_resolve_picks_from_source():
    assert resolve_proxy(ProxyCycle(["http://a:1"])) == "http://a:1"


def test_resolve_calls_sync_factory():
    assert resolve_proxy(lambda: "http://f:1") == "http://f:1"


def test_resolve_rejects_unsupported_values():
    with pytest.raises(TypeError, match="proxy must be"):
        resolve_proxy({"country": "us"})  # type: ignore[arg-type]


def test_resolve_rejects_async_factory_on_sync_client():
    async def factory():
        return "http://f:1"

    with pytest.raises(TypeError, match="AsyncSurfsky"):
        resolve_proxy(factory)


@pytest.mark.anyio
async def test_aresolve_awaits_async_factory():
    async def factory():
        return SharedProxy(country="de")

    picked = await aresolve_proxy(factory)
    assert isinstance(picked, SharedProxy)


def test_custom_source_subclass():
    class Fixed(ProxySource):
        def pick(self):
            return PremiumProxy(country="us")

    assert isinstance(resolve_proxy(Fixed()), PremiumProxy)


def test_start_one_time_accepts_source(httpx_mock, client):
    httpx_mock.add_response(method="POST", url=_ONE_TIME, json=_session_json())
    client.profiles.start_one_time(proxy=ProxyCycle(["socks5://u:p@h:1080"]))
    assert _sent_proxy(httpx_mock) == "socks5://u:p@h:1080"


def test_profile_start_accepts_proxy(httpx_mock, client):
    uuid = "a" * 32
    httpx_mock.add_response(
        method="POST", url=f"https://api.test/profiles/{uuid}/start", json=_session_json()
    )
    client.profiles.start(uuid, proxy=SharedProxy(country="us"))
    assert _sent_proxy(httpx_mock) == {"tier": "shared", "country": "us"}


@pytest.mark.anyio
async def test_async_start_accepts_async_factory(httpx_mock):
    async def factory():
        return SharedProxy()

    httpx_mock.add_response(method="POST", url=_ONE_TIME, json=_session_json())
    async with AsyncSurfsky(api_token="t", base_url="https://api.test") as aclient:
        await aclient.profiles.start_one_time(proxy=factory)
    assert _sent_proxy(httpx_mock) == {"tier": "shared"}


def test_shared_countries(httpx_mock, client):
    httpx_mock.add_response(
        method="GET",
        url="https://api.test/proxies/shared/countries",
        json={"success": True, "data": ["de", "us"]},
    )
    assert client.proxies.shared_countries() == ["de", "us"]


def test_shared_quota(httpx_mock, client):
    httpx_mock.add_response(
        method="GET",
        url="https://api.test/proxies/shared/quota",
        json={
            "success": True,
            "data": {
                "limit_gb": 10,
                "limit_bytes": 10_000_000_000,
                "used_bytes": 3_000_000_000,
                "remaining_bytes": 7_000_000_000,
                "remaining_gb": 7.0,
                "reset_time": 1721822400,
            },
        },
    )
    quota = client.proxies.shared_quota()
    assert quota.remaining_bytes == 7_000_000_000
    assert quota.reset_time == 1721822400


def test_traffic_stats_rename_the_wire_names(httpx_mock, client):
    httpx_mock.add_response(
        method="GET",
        url="https://api.test/proxies/premium/stats",
        json={
            "success": True,
            "data": {
                "24h": {"bytes": 406891290, "gb": 0.4069},
                "7d": {"bytes": 421342044, "gb": 0.4213},
                "30d": {"bytes": 534680504, "gb": 0.5347},
            },
        },
    )
    stats = client.proxies.premium_stats()
    assert stats.last_24h is not None and stats.last_24h.size_bytes == 406891290
    assert stats.last_7d is not None and stats.last_7d.gb == 0.4213
    assert stats.last_30d is not None and stats.last_30d.size_bytes == 534680504


def test_shared_stats_reads_the_same_shape(httpx_mock, client):
    httpx_mock.add_response(
        method="GET",
        url="https://api.test/proxies/shared/stats",
        json={"success": True, "data": {"24h": {"bytes": 1024, "gb": 0.0}}},
    )
    stats = client.proxies.shared_stats()
    assert stats.last_24h is not None and stats.last_24h.size_bytes == 1024
    assert stats.last_30d is None  # a window the server left out


@pytest.mark.parametrize(
    ("code", "exc", "proxy"),
    [
        ("shared_traffic_limit_reached", SharedTrafficLimitError, SharedProxy()),
        (
            "premium_traffic_limit_reached",
            PremiumTrafficLimitError,
            PremiumProxy(country="us"),
        ),
        ("monthly_session_limit_reached", MonthlySessionLimitError, None),
        ("parallel_browsers_limit_reached", RateLimitError, None),  # the pool's backpressure
    ],
)
def test_quota_429_is_typed_and_not_retried(httpx_mock, client, code, exc, proxy):
    httpx_mock.add_response(
        method="POST",
        url=_ONE_TIME,
        status_code=429,
        json={"success": False, "msg": "Quota reached.", "data": None, "code": code},
    )
    with pytest.raises(exc) as info:
        client.profiles.start_one_time(proxy=proxy)
    assert info.value.code == code
    assert len(httpx_mock.get_requests()) == 1


def test_city_without_region_is_rejected_client_side():
    # the server raises "'city' requires 'region'"; catch it before the round trip
    from surfsky import PremiumProxy

    with pytest.raises(ValueError, match="region"):
        PremiumProxy(country="us", city="chicago")
    assert PremiumProxy(country="us", region="il", city="chicago").city == "chicago"


def test_more_than_five_extensions_is_rejected_client_side():
    from surfsky.types import OneTimeStartRequest

    with pytest.raises(ValueError):
        OneTimeStartRequest(extensions=[f"ext-{i}" for i in range(6)])


def test_premium_proxy_carries_the_full_selector():
    from surfsky import PremiumProxy

    proxy = PremiumProxy(
        country="us", region="il", city="chicago", type="mobile",
        asn=7018, session_minutes=30, unique_ip=True, keep_ip=True,
    )
    assert proxy.model_dump(exclude_none=True) == {
        "tier": "premium", "country": "us", "region": "il", "city": "chicago",
        "type": "mobile", "asn": 7018, "session_minutes": 30,
        "unique_ip": True, "keep_ip": True,
    }


def test_regional_pool_and_gps_targeting():
    from surfsky import ProxyGeo

    assert ProxyGeo(pool="westeurope").pool == "westeurope"
    assert ProxyGeo(lat=48.85, lon=2.35).lat == 48.85


def test_the_three_targeting_modes_are_exclusive():
    from surfsky import ProxyGeo

    with pytest.raises(ValueError, match="targeting modes"):
        ProxyGeo(pool="asia", country="jp")
    with pytest.raises(ValueError, match="targeting modes"):
        ProxyGeo(lat=1.0, lon=2.0, country="jp")


def test_partial_targeting_is_rejected():
    from surfsky import PremiumProxy, ProxyGeo

    with pytest.raises(ValueError, match="lat.*lon|lon.*lat"):
        ProxyGeo(lat=48.85)
    with pytest.raises(ValueError, match="country"):
        PremiumProxy(region="il")
    with pytest.raises(ValueError, match="country"):
        ProxyGeo(asn=7018)


def test_the_geo_catalogue_narrows_country_to_region_to_city(httpx_mock, client):
    httpx_mock.add_response(
        method="GET", url="https://api.test/proxies/countries",
        json={"success": True, "data": [{"code": "us", "name": "United States"}]},
    )
    httpx_mock.add_response(
        method="GET", url="https://api.test/proxies/regions/us",
        json={"success": True, "data": [
            {"code": "il", "name": "Illinois", "country_code": "us"}
        ]},
    )
    httpx_mock.add_response(
        method="GET", url="https://api.test/proxies/cities/us/il",
        json={"success": True, "data": [
            {"code": "chicago", "name": "Chicago", "region_code": "il",
             "country_code": "us"}
        ]},
    )
    [country] = client.proxies.countries()
    [region] = client.proxies.regions(country.code)
    [city] = client.proxies.cities(country.code, region.code)
    assert (country.code, region.code, city.code) == ("us", "il", "chicago")
    assert city.region_code == "il"


def test_the_catalogue_ids_stay_one_path_segment_each(httpx_mock, client):
    # a region carrying a slash must not walk up into /proxies/countries
    httpx_mock.add_response(
        method="GET", url="https://api.test/proxies/cities/us/il%2F..",
        json={"success": True, "data": []},
    )
    assert client.proxies.cities("us", "il/..") == []
    with pytest.raises(ValueError, match="empty"):
        client.proxies.regions("")


def test_premium_quota_parses(httpx_mock, client):
    httpx_mock.add_response(
        method="GET", url="https://api.test/proxies/quota",
        json={"success": True, "data": {"remaining_bytes": 5_000_000, "remaining_gb": 0.005}},
    )
    quota = client.proxies.quota()
    assert (quota.remaining_bytes, quota.remaining_gb) == (5_000_000, 0.005)
