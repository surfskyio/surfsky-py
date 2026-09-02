import pytest

_BASE = "https://api.test/fingerprint"


def test_renderers_and_screens_send_the_platform_pair(httpx_mock, client):
    # both are required: the catalogue differs per OS/arch
    httpx_mock.add_response(
        method="GET", url=f"{_BASE}/renderers?os=win&os_arch=x86",
        json={"success": True, "data": [
            {"value": "ANGLE (NVIDIA GeForce RTX 3060)", "platform": "win", "archs": ["x86"]}
        ]},
    )
    httpx_mock.add_response(
        method="GET", url=f"{_BASE}/screens?os=win&os_arch=x86",
        json={"success": True, "data": [{"value": "1920x1080", "platform": "win"}]},
    )
    [renderer] = client.fingerprints.renderers("win", "x86")
    [screen] = client.fingerprints.screens("win", "x86")
    assert renderer.value.startswith("ANGLE")
    assert renderer.archs == ["x86"]
    assert (screen.value, screen.archs) == ("1920x1080", [])  # a list the server left out


def test_device_models_omits_the_filters_left_out(httpx_mock, client):
    # a None sent as ?os= would filter on the empty string, not on nothing
    httpx_mock.add_response(
        method="GET", url=f"{_BASE}/device_models?device_type=mobile",
        json={"success": True, "data": [
            {"value": "Pixel 8", "os": "android", "os_versions": ["14"],
             "device_type": "mobile"}
        ]},
    )
    [model] = client.fingerprints.device_models(device_type="mobile")
    assert (model.value, model.os_versions) == ("Pixel 8", ["14"])
    assert dict(httpx_mock.get_requests()[0].url.params) == {"device_type": "mobile"}


def test_device_models_with_no_filters_asks_for_everything(httpx_mock, client):
    httpx_mock.add_response(
        method="GET", url=f"{_BASE}/device_models", json={"success": True, "data": None}
    )
    assert client.fingerprints.device_models() == []  # a null list is not an error
    assert not httpx_mock.get_requests()[0].url.params


@pytest.mark.anyio
async def test_async_device_models_sends_every_filter(httpx_mock):
    from surfsky import AsyncSurfsky

    httpx_mock.add_response(
        method="GET",
        url=f"{_BASE}/device_models?os=android&os_arch=arm&os_version=14&device_type=mobile",
        json={"success": True, "data": []},
    )
    async with AsyncSurfsky(api_token="t", base_url="https://api.test") as aclient:
        await aclient.fingerprints.device_models(
            os="android", os_arch="arm", os_version="14", device_type="mobile"
        )
    assert dict(httpx_mock.get_requests()[0].url.params) == {
        "os": "android", "os_arch": "arm", "os_version": "14", "device_type": "mobile",
    }
