from surfsky import (
    BrowserSettings,
    Cookie,
    Fingerprint,
    OneTimeStartRequest,
    ProxyGeo,
    Session,
)
from surfsky.transport import dump


def test_fingerprint_serializes_snake_case_dropping_none():
    fp = Fingerprint(os="win", os_arch="x86", os_version="11", cpu=8, noise={"canvas": True})
    body = dump(fp)
    assert body == {
        "os": "win",
        "os_arch": "x86",
        "os_version": "11",
        "cpu": 8,
        "noise": {"canvas": True},
    }


def test_one_time_request_with_geo_proxy():
    req = OneTimeStartRequest(
        fingerprint=Fingerprint(os="win"),
        proxy=ProxyGeo(country="us", region="california"),
        browser_settings=BrowserSettings(inactive_kill_timeout=60),
    )
    body = dump(req)
    assert body["proxy"] == {"country": "us", "region": "california"}
    assert body["browser_settings"] == {"inactive_kill_timeout": 60}


def test_string_proxy_passthrough():
    req = OneTimeStartRequest(proxy="socks5://user:pass@host:1080")
    assert dump(req) == {"proxy": "socks5://user:pass@host:1080"}


def test_cookie_camelcase_roundtrip():
    raw = {
        "domain": ".x.com", "name": "sid", "value": "abc", "path": "/",
        "expirationDate": 123.5, "hostOnly": False, "httpOnly": True,
        "sameSite": "lax", "secure": True,
    }
    cookie = Cookie.model_validate(raw)
    assert cookie.expiration_date == 123.5
    assert cookie.http_only is True
    assert cookie.same_site == "lax"
    assert cookie.model_dump(by_alias=True, exclude_none=True)["expirationDate"] == 123.5


def test_session_parses_inspector_list_alias():
    session = Session.model_validate({
        "internal_uuid": "u", "ws_url": "wss://x",
        "inspector": {"list": "http://insp", "pages": [], "screencast": "wss://sc"},
        "success": True,
    })
    assert session.inspector.list_url == "http://insp"
    assert session.inspector.screencast == "wss://sc"


def test_geolocation_accuracy_is_an_int_on_the_wire():
    from surfsky import Geolocation

    # the server validates accuracy as a strict int; a float would 422
    geo = Geolocation(latitude=48.8, longitude=2.3, accuracy=50.0)
    assert dump(Fingerprint(geolocation=geo)) == {
        "geolocation": {"latitude": 48.8, "longitude": 2.3, "accuracy": 50}
    }
