import json

import anyio
import httpx
import pytest

from surfsky import APIError, BadRequestError, Fingerprint


def test_start_one_time_parses_and_sends_snake_case(httpx_mock, client):
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/one_time",
        json={
            "internal_uuid": "u1", "ws_url": "wss://x/proxy/u1",
            "inspector": {"list": "l", "pages": [], "screencast": "sc"},
            "success": True,
        },
    )
    session = client.profiles.start_one_time(
        fingerprint=Fingerprint(os="win", os_arch="x86", os_version="11")
    )
    assert session.internal_uuid == "u1"
    assert session.inspector.screencast == "sc"
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["fingerprint"]["os_arch"] == "x86"


def test_create_returns_uuid(httpx_mock, client):
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles",
        json={"success": True, "msg": "", "data": {"uuid": "p1"}, "code": None},
    )
    created = client.profiles.create(
        title="demo", fingerprint=Fingerprint(os="win", os_arch="x86", os_version="11")
    )
    assert created.uuid == "p1"


def test_delete_many_partial_returns_result_from_400(httpx_mock, client):
    # partial failure is HTTP 400 with the uuid lists in data
    httpx_mock.add_response(
        method="DELETE", url="https://api.test/profiles", status_code=400,
        json={
            "success": False, "msg": "Some profiles were not deleted",
            "data": {"deleted_uuids": ["d"], "active_uuids": ["a"], "not_found_uuids": ["x"]},
        },
    )
    result = client.profiles.delete_many(["d", "a", "x"])
    assert result.deleted_uuids == ["d"]
    assert result.active_uuids == ["a"]
    assert result.not_found_uuids == ["x"]


def test_delete_many_foreign_400_still_raises(httpx_mock, client):
    # a 400 without the uuid lists (blocked account, gateway error) is a real error
    httpx_mock.add_response(
        method="DELETE", url="https://api.test/profiles", status_code=400,
        json={"success": False, "msg": "Blocked", "data": None},
    )
    with pytest.raises(BadRequestError, match="Blocked"):
        client.profiles.delete_many(["x"])


def test_stop_one_time_returns_none(httpx_mock, client):
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/sess-1/stop",
        json={"success": True, "msg": "Profile stopped", "data": None},
    )
    assert client.profiles.stop("sess-1") is None


def test_stop_persistent_returns_uuid(httpx_mock, client):
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/sess-2/stop",
        json={"success": True, "msg": "Profile stopped", "data": {"uuid": "prof-9"}},
    )
    result = client.profiles.stop("sess-2")
    assert result is not None
    assert result.uuid == "prof-9"


def test_scrape_parses_result_and_cookies(httpx_mock, client):
    # the pod returns cookies keyed expires, not the export format's expirationDate
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/sess/scrape",
        json={
            "success": True, "msg": "",
            "data": {
                "url": "https://x/",
                "status": 200,
                "status_text": "OK",
                "content": "<html></html>",
                "cookies": [
                    {"domain": ".x.com", "name": "a", "value": "b", "path": "/",
                     "expires": 123.0, "httpOnly": True, "secure": False, "sameSite": "Lax"}
                ],
                "screenshot": None,
            },
        },
    )
    result = client.profiles.scrape("sess", "https://x", wait_until="networkidle")
    assert result.content == "<html></html>"
    assert result.status == 200
    assert result.cookies[0].expiration_date == 123.0
    assert result.cookies[0].http_only is True
    assert result.cookies[0].same_site == "Lax"


def test_scrape_waits_longer_than_the_client_default(httpx_mock, client):
    # the server gives the pod up to 120s; a 30s read timeout would drop results
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/sess/scrape",
        json={"success": True, "data": {"content": ""}}, is_reusable=True,
    )
    client.profiles.scrape("sess", "https://x")
    client.with_options(timeout=5).profiles.scrape("sess", "https://x")
    default, override = httpx_mock.get_requests()
    assert default.extensions["timeout"]["read"] == 150.0
    assert override.extensions["timeout"]["read"] == 5


def test_validation_422_is_a_bad_request_naming_the_field(httpx_mock, client):
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles", status_code=422,
        json={
            "success": False, "msg": "Bad request",
            "data": {"errors": [
                {"type": "string_type", "loc": ["body", "fingerprint", "os_version"],
                 "msg": "Input should be a valid string"},
            ]},
            "code": "validation_error",
        },
    )
    with pytest.raises(BadRequestError, match="fingerprint.os_version: Input should") as info:
        client.profiles.create(title="x", fingerprint=Fingerprint(os="win"))
    assert info.value.status_code == 422


def test_unparseable_success_body_is_an_api_error(httpx_mock, client):
    # a WAF page or a mis-set base_url answering 200 must not leak pydantic errors
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/p1", text="<html>blocked</html>",
    )
    with pytest.raises(APIError, match="unexpected response") as info:
        client.profiles.get("p1")
    assert info.value.status_code == 200


def test_success_false_envelope_raises(httpx_mock, client):
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/sess/scrape",
        json={"success": False, "msg": "No subscription", "data": None},
    )
    with pytest.raises(APIError, match="No subscription"):
        client.profiles.scrape("sess", "https://x")


def test_list_active_parses(httpx_mock, client):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/active",
        json={"success": True, "msg": "", "data": [
            {"internal_uuid": "i1", "profile_uuid": None, "one_time": True,
             "started_at": "2025-01-15T10:30:00Z", "active_seconds": 120}
        ]},
    )
    active = client.profiles.list_active()
    assert active[0].internal_uuid == "i1"
    assert active[0].one_time is True


def test_list_bare_array(httpx_mock, client):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles",
        json=[{"uuid": "p1", "title": "t", "status": "stopped"}],
    )
    summaries = client.profiles.list_page()
    assert summaries[0].uuid == "p1"
    assert summaries[0].status == "stopped"


def test_iter_all_paginates_until_short_page(httpx_mock, client):
    httpx_mock.add_response(method="GET", json=[{"uuid": "a"}, {"uuid": "b"}])
    httpx_mock.add_response(method="GET", json=[{"uuid": "c"}])
    profiles = list(client.profiles.iter_all(page_len=2))
    assert [p.uuid for p in profiles] == ["a", "b", "c"]
    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    assert requests[0].url.params["page"] == "0"
    assert requests[1].url.params["page"] == "1"
    assert requests[0].url.params["page_len"] == "2"


def test_iter_all_clamps_page_len_to_server_max(httpx_mock, client):
    # without the clamp a page_len > 100 would stop after the first page
    httpx_mock.add_response(method="GET", json=[{"uuid": "a"}])
    profiles = list(client.profiles.iter_all(page_len=250))
    assert [p.uuid for p in profiles] == ["a"]
    params = httpx_mock.get_requests()[0].url.params
    assert params["page_len"] == "100"
    assert params["ordering"] == "created"  # stable order: offset pagination can't skip


def test_iter_all_clamps_zero_page_len(httpx_mock, client):
    # page_len <= 0 could never satisfy len(batch) < page_len and would loop forever
    httpx_mock.add_response(method="GET", json=[])
    assert list(client.profiles.iter_all(page_len=0)) == []
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].url.params["page_len"] == "1"


@pytest.mark.anyio
async def test_async_iter_all_paginates(httpx_mock):
    from surfsky import AsyncSurfsky

    httpx_mock.add_response(method="GET", json=[{"uuid": "a"}, {"uuid": "b"}])
    httpx_mock.add_response(method="GET", json=[{"uuid": "c"}])
    client = AsyncSurfsky(api_token="t", base_url="https://api.test")
    collected = [p.uuid async for p in client.profiles.iter_all(page_len=2)]
    await client.aclose()
    assert collected == ["a", "b", "c"]


def test_cookie_export_import_round_trip(httpx_mock, client):
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/p1/cookies?export_format=json",
        json={"success": True, "msg": "", "data": {"cookies": [
            {"domain": ".x.com", "name": "a", "value": "b",
             "expirationDate": 123.0, "httpOnly": True},
        ]}},
    )
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/p1/cookies",
        json={"success": True, "msg": "", "data": None},
    )
    cookies = client.profiles.export_cookies("p1")
    client.profiles.import_cookies("p1", cookies)

    body = json.loads(httpx_mock.get_requests()[1].content)
    sent = json.loads(body["cookies"])
    # wire format is camelCase
    assert sent[0]["expirationDate"] == 123.0
    assert sent[0]["httpOnly"] is True
    assert "expiration_date" not in sent[0]


def test_export_cookies_netscape_returns_text_blob(httpx_mock, client):
    blob = ".x.com\tTRUE\t/\tFALSE\t0\ta\tb"
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/p1/cookies?export_format=netscape",
        json={"success": True, "msg": "", "data": {"cookies": blob}},
    )
    exported = client.profiles.export_cookies("p1", export_format="netscape")
    assert exported == blob


def test_session_context_manager_starts_and_stops(httpx_mock, client):
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/one_time",
        json={"internal_uuid": "sess-1", "ws_url": "wss://x/proxy/sess-1", "success": True},
    )
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/sess-1/stop",
        json={"success": True, "msg": "Profile stopped", "data": None},
    )
    with client.session() as session:
        assert session.connect_url == "wss://x/proxy/sess-1"
    urls = [str(r.url) for r in httpx_mock.get_requests()]
    assert urls == [
        "https://api.test/profiles/one_time",
        "https://api.test/profiles/sess-1/stop",
    ]


@pytest.mark.anyio
async def test_async_session_stop_failure_logs_instead_of_raising(httpx_mock, caplog):
    from surfsky import AsyncSurfsky

    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/one_time",
        json={"internal_uuid": "s3", "ws_url": "wss://x/proxy/s3", "success": True},
    )
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/s3/stop", status_code=500,
        json={"success": False, "msg": "boom"},
    )
    client = AsyncSurfsky(api_token="t", base_url="https://api.test")
    with caplog.at_level("WARNING", logger="surfsky"):
        async with client.session():
            pass
    await client.aclose()
    assert any("failed to stop session s3" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_async_session_start_is_not_lost_to_cancellation(httpx_mock):
    # a cancellation mid-POST must not orphan the session the server created
    from surfsky import AsyncSurfsky

    async def slow_start(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(0.05)
        return httpx.Response(200, json={"internal_uuid": "s9", "ws_url": "wss://x"})

    httpx_mock.add_callback(
        slow_start, method="POST", url="https://api.test/profiles/one_time"
    )
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/s9/stop",
        json={"success": True, "data": None},
    )
    async with AsyncSurfsky(api_token="t", base_url="https://api.test") as client:
        with anyio.move_on_after(0.01) as scope:
            async with client.session():
                await anyio.sleep(1)  # pragma: no cover - cancelled before this returns
    assert scope.cancelled_caught
    paths = [r.url.path for r in httpx_mock.get_requests()]
    assert paths == ["/profiles/one_time", "/profiles/s9/stop"]


def test_stop_and_scrape_accept_a_session_object(httpx_mock, client):
    from surfsky import Session

    session = Session(internal_uuid="sess-9", ws_url="wss://x")
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/sess-9/scrape",
        json={"success": True, "data": {"url": "https://x/", "content": "<html></html>"}},
    )
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/sess-9/stop",
        json={"success": True, "msg": "Profile stopped", "data": None},
    )
    assert client.profiles.scrape(session, "https://x").content == "<html></html>"
    assert client.profiles.stop(session) is None


def test_update_can_resend_a_fingerprint_read_back_from_the_api(httpx_mock, client):
    # read-modify-write is the natural flow; the update endpoint forbids the
    # immutable OS fields that every GET hands back
    from surfsky import Fingerprint

    httpx_mock.add_response(
        method="PATCH", url="https://api.test/profiles/p1",
        json={"success": True, "data": {"uuid": "p1", "title": "t"}},
    )
    stored = Fingerprint(
        os="win", os_arch="x86", os_version="11", timezone="Europe/Paris"
    )
    client.profiles.update("p1", fingerprint=stored)
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["fingerprint"] == {"timezone": "Europe/Paris"}


def test_a_typo_inside_a_nested_option_object_is_rejected():
    # the server ignores extras, so this used to change nothing and say nothing:
    # the browser would die at the default 30s instead of the requested 600
    from surfsky import BrowserSettings

    with pytest.raises(ValueError, match="inactive_kill_timeot"):
        BrowserSettings(inactive_kill_timeot=600)  # type: ignore[call-arg]
    assert BrowserSettings(inactive_kill_timeout=600).inactive_kill_timeout == 600


def test_responses_still_tolerate_fields_the_sdk_does_not_model(httpx_mock, client):
    # the other half of the trade: a server that grows a field must not break us
    httpx_mock.add_response(
        method="GET", url="https://api.test/profiles/p1",
        json={
            "success": True,
            "data": {
                "uuid": "p1", "title": "t",
                "fingerprint": {"os": "win", "brand_new_field": 1},
                "some_new_top_level_key": True,
            },
        },
    )
    profile = client.profiles.get("p1")
    assert profile.fingerprint is not None and profile.fingerprint.os == "win"


def test_update_clears_a_stored_proxy(httpx_mock, client):
    httpx_mock.add_response(
        method="PATCH", url="https://api.test/profiles/p1",
        json={"success": True, "data": {"uuid": "p1", "title": "t"}},
    )
    client.profiles.update("p1", proxy=None)
    assert json.loads(httpx_mock.get_requests()[0].content) == {"proxy": None}


def test_one_time_sessions_can_carry_cookies(httpx_mock, client):
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/one_time",
        json={"success": True, "data": {"internal_uuid": "s1", "ws_url": "wss://x"}},
    )
    client.profiles.start_one_time(
        cookies=[{"domain": ".x.com", "name": "a", "value": "b", "path": "/"}]
    )
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["cookies"][0]["name"] == "a"


def test_update_resolves_a_proxy_selector_before_sending(httpx_mock, client):
    from surfsky import SharedProxy

    httpx_mock.add_response(
        method="PATCH", url="https://api.test/profiles/p1",
        json={"success": True, "data": {"uuid": "p1", "title": "t"}},
    )
    client.profiles.update("p1", proxy=SharedProxy(country="us"))
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body == {"proxy": {"tier": "shared", "country": "us"}}


def test_a_persistent_profile_start_rejects_cookies(client):
    with pytest.raises(ValueError, match="cookies"):
        client.profiles.start("p1", cookies=[{"name": "a"}])


def test_an_empty_id_never_reaches_the_wire(httpx_mock, client):
    # "/profiles/" is the list endpoint: an empty id would be answered with a list
    for call in (client.profiles.get, client.profiles.stop, client.profiles.delete):
        with pytest.raises(ValueError, match="empty"):
            call("")
    with pytest.raises(ValueError, match="segment"):
        client.profiles.get("..")
    assert not httpx_mock.get_requests()


def test_an_id_stays_one_path_segment(httpx_mock, client):
    httpx_mock.add_response(
        method="DELETE",
        url="https://api.test/profiles/a%2F..%2Fb",
        json={"success": True, "data": {"uuid": "a/../b"}},
    )
    client.profiles.delete("a/../b")
    assert str(httpx_mock.get_requests()[0].url).endswith("/profiles/a%2F..%2Fb")


def test_fingerprint_enums_take_values_the_server_added():
    # parsed from responses too: a value added server-side must not break every get
    fingerprint = Fingerprint.model_validate(
        {"os": "linux", "os_arch": "riscv", "device_type": "watch"}
    )
    assert (fingerprint.os, fingerprint.os_arch, fingerprint.device_type) == (
        "linux", "riscv", "watch",
    )