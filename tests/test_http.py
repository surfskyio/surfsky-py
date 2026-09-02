from datetime import UTC

import httpx
import pytest

from surfsky import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
    ServerError,
    Surfsky,
)
from surfsky.transport import retry_after_seconds


def _make(**kwargs) -> Surfsky:
    kwargs.setdefault("backoff_factor", 0.0)  # no real sleeping in tests
    return Surfsky(api_token="t", base_url="https://api.test", **kwargs)


def test_retry_after_seconds_parses_delta():
    assert retry_after_seconds(httpx.Response(429, headers={"Retry-After": "7"})) == 7.0
    assert retry_after_seconds(httpx.Response(200)) is None


def test_retry_after_http_date_naive_means_utc():
    from datetime import datetime, timedelta

    when = datetime.now(UTC) + timedelta(seconds=60)
    for zone in ("-0000", "GMT"):  # -0000 parses naive and means UTC (RFC 5322)
        header = when.strftime(f"%a, %d %b %Y %H:%M:%S {zone}")
        delay = retry_after_seconds(httpx.Response(429, headers={"Retry-After": header}))
        assert delay is not None
        assert 50 <= delay <= 70


def test_retry_then_success(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active", status_code=429,
        json={"success": False, "msg": "rate"},
    )
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active",
        json={"success": True, "msg": "", "data": []},
    )
    assert _make(max_retries=3).profiles.list_active() == []
    assert len(httpx_mock.get_requests()) == 2


def test_500_retries_then_raises(httpx_mock):
    for _ in range(4):  # 1 initial + 3 retries
        httpx_mock.add_response(
            method="GET", url="https://api.test/profiles/active", status_code=500,
            json={"success": False, "msg": "boom"},
        )
    with pytest.raises(ServerError):
        _make(max_retries=3).profiles.list_active()
    assert len(httpx_mock.get_requests()) == 4


@pytest.mark.parametrize(
    ("status", "exc"),
    [(401, AuthenticationError), (404, NotFoundError), (429, RateLimitError)],
)
def test_error_mapping(httpx_mock, status, exc):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active", status_code=status,
        json={"success": False, "msg": "nope"},
    )
    with pytest.raises(exc) as info:
        _make(max_retries=0).profiles.list_active()
    assert info.value.status_code == status
    assert info.value.message == "GET /profiles/active: nope"


def test_an_upstream_error_keeps_its_message(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active", status_code=422,
        json={"message": "OS version does not match allowed versions for the platform",
              "code": "validation_error", "code_id": "SA10", "errors": {}},
    )
    with pytest.raises(BadRequestError) as info:
        _make(max_retries=0).profiles.list_active()
    assert info.value.message == (
        "GET /profiles/active: OS version does not match allowed versions for the platform"
    )
    assert info.value.code == "validation_error"


def test_field_errors_are_named_wherever_they_sit(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active", status_code=422,
        json={"message": "bad request", "errors": {"os_version": "unsupported"}},
    )
    with pytest.raises(BadRequestError) as info:
        _make(max_retries=0).profiles.list_active()
    assert info.value.message == "GET /profiles/active: bad request: os_version: unsupported"


def test_rate_limit_exposes_retry_after(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active", status_code=429,
        headers={"Retry-After": "3"}, json={"success": False, "msg": "rate"},
    )
    with pytest.raises(RateLimitError) as info:
        _make(max_retries=0).profiles.list_active()
    assert info.value.retry_after == 3.0


def test_error_carries_request_id_and_headers(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active", status_code=404,
        headers={"cf-ray": "req-9"}, json={"success": False, "msg": "gone"},
    )
    with pytest.raises(NotFoundError) as info:
        _make(max_retries=0).profiles.list_active()
    assert info.value.request_id == "req-9"  # the Cloudflare ray id, what support looks up
    assert info.value.headers["cf-ray"] == "req-9"


def test_connection_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(APIConnectionError):
        _make(max_retries=0).profiles.list_active()


def test_timeout_error(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError):
        _make(max_retries=0).profiles.list_active()


def test_with_options_overrides_max_retries(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active", status_code=500,
        json={"success": False, "msg": "boom"},
    )
    client = Surfsky(
        api_token="t", base_url="https://api.test", max_retries=3, backoff_factor=0.0
    )
    with pytest.raises(ServerError):
        client.with_options(max_retries=0).profiles.list_active()
    assert len(httpx_mock.get_requests()) == 1


def test_with_options_sends_extra_headers(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active",
        json={"success": True, "msg": "", "data": []},
    )
    _make().with_options(headers={"X-Trace": "abc"}).profiles.list_active()
    assert httpx_mock.get_requests()[0].headers["X-Trace"] == "abc"


def test_low_level_request_returns_raw_response(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="https://api.test/anything", status_code=418,
        headers={"x-request-id": "rid"}, json={"x": 1},
    )
    resp = _make().request("GET", "/anything")
    assert resp.status_code == 418
    assert resp.headers["x-request-id"] == "rid"
    assert resp.json() == {"x": 1}


# Retry safety: a non-idempotent write (session creation) must not be retried on
# an ambiguous failure, or a lost response would provision a second paid session.

_ONE_TIME = "https://api.test/profiles/one_time"
_SESSION_OK = {"internal_uuid": "u1", "ws_url": "wss://x/proxy/u1", "success": True}


def test_post_not_retried_on_5xx(httpx_mock):
    httpx_mock.add_response(
        method="POST", url=_ONE_TIME, status_code=502, json={"success": False, "msg": "gw"}
    )
    with pytest.raises(ServerError):
        _make(max_retries=3).profiles.start_one_time()
    assert len(httpx_mock.get_requests()) == 1


def test_post_retried_on_429(httpx_mock):
    httpx_mock.add_response(
        method="POST", url=_ONE_TIME, status_code=429, json={"success": False, "msg": "rate"}
    )
    httpx_mock.add_response(method="POST", url=_ONE_TIME, json=_SESSION_OK)
    session = _make(max_retries=3).profiles.start_one_time()
    assert session.internal_uuid == "u1"  # 429 = rejected, safe to retry
    assert len(httpx_mock.get_requests()) == 2


def test_post_not_retried_on_read_timeout(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("slow"), method="POST", url=_ONE_TIME)
    with pytest.raises(APITimeoutError):
        _make(max_retries=3).profiles.start_one_time()
    assert len(httpx_mock.get_requests()) == 1  # may have landed -> don't retry


def test_post_retried_on_connect_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), method="POST", url=_ONE_TIME)
    httpx_mock.add_response(method="POST", url=_ONE_TIME, json=_SESSION_OK)
    session = _make(max_retries=3).profiles.start_one_time()
    assert session.internal_uuid == "u1"  # never reached server -> safe to retry
    assert len(httpx_mock.get_requests()) == 2


def test_get_retried_on_read_timeout(httpx_mock):
    httpx_mock.add_exception(
        httpx.ReadTimeout("slow"), method="GET", url="https://api.test/profiles/active"
    )
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active",
        json={"success": True, "msg": "", "data": []},
    )
    assert _make(max_retries=3).profiles.list_active() == []  # GET is idempotent
    assert len(httpx_mock.get_requests()) == 2


def test_a_malformed_api_token_is_an_authentication_error(httpx_mock):
    from surfsky import AuthenticationError, Surfsky

    # the server validates the token in its header model, so a too-short or
    # malformed one is a 422, not a 401
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active", status_code=422,
        json={
            "success": False,
            "msg": "Bad request",
            "code": "validation_error",
            "data": {
                "errors": [
                    {"type": "string_too_short", "loc": ["header", "x-cloud-api-token"],
                     "msg": "String should have at least 6 characters"}
                ]
            },
        },
    )
    client = Surfsky(api_token="abc", base_url="https://api.test")
    with pytest.raises(AuthenticationError):
        client.profiles.list_active()
    client.close()
