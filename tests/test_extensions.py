import json

import pytest


def test_list_all_parses_extensions_key(httpx_mock, client):
    # GET /extensions has no {success, data} envelope.
    httpx_mock.add_response(
        method="GET", url="https://api.test/extensions",
        json={"extensions": [{"uuid": "e1", "name": "ublock"}], "count": 1},
    )
    extensions = client.extensions.list_all()
    assert len(extensions) == 1
    assert extensions[0].uuid == "e1"
    assert extensions[0].name == "ublock"


def test_list_all_null_list(httpx_mock, client):
    httpx_mock.add_response(
        method="GET", url="https://api.test/extensions",
        json={"extensions": None, "count": 0},
    )
    assert client.extensions.list_all() == []


def _multipart(request) -> dict[str, bytes]:
    # crude but enough: the parts we care about are the filename and the name field
    body = request.read()
    return {
        b"filename": body.split(b'filename="')[1].split(b'"')[0],
        b"name": body.rsplit(b'name="name"\r\n\r\n', 1)[1].split(b"\r\n")[0],
    }


@pytest.mark.parametrize("kind", ["path", "bytes", "stream"])
def test_upload_takes_a_path_bytes_or_a_stream(httpx_mock, client, tmp_path, kind):
    zip_path = tmp_path / "ublock.zip"
    zip_path.write_bytes(b"PK\x03\x04")
    # bytes carry no name of their own, so the SDK supplies one
    file, expected = {
        "path": (str(zip_path), b"ublock.zip"),
        "bytes": (b"PK\x03\x04", b"extension.zip"),
        "stream": (zip_path.open("rb"), b"ublock.zip"),
    }[kind]

    httpx_mock.add_response(
        method="POST", url="https://api.test/extensions",
        json={"success": True, "data": {"uuid": "e1", "name": "ublock"}},
    )
    assert client.extensions.upload(file, name="ublock").uuid == "e1"
    sent = _multipart(httpx_mock.get_requests()[0])
    assert sent[b"filename"] == expected
    assert sent[b"name"] == b"ublock"


def test_get_update_and_delete_keep_the_uuid_one_path_segment(httpx_mock, client):
    quoted = "https://api.test/extensions/a%2F..%2Fb"
    body = {"success": True, "data": {"uuid": "a/../b", "name": "renamed"}}
    httpx_mock.add_response(method="GET", url=quoted, json=body)
    httpx_mock.add_response(method="PATCH", url=quoted, json=body)
    httpx_mock.add_response(
        method="DELETE", url=quoted, json={"success": True, "data": None}
    )

    assert client.extensions.get("a/../b").uuid == "a/../b"
    assert client.extensions.update("a/../b", name="renamed").name == "renamed"
    assert client.extensions.delete("a/../b") is None

    assert all(str(r.url) == quoted for r in httpx_mock.get_requests())
    assert json.loads(httpx_mock.get_requests()[1].content) == {"name": "renamed"}


def test_an_empty_extension_id_never_reaches_the_wire(httpx_mock, client):
    for call in (client.extensions.get, client.extensions.delete):
        with pytest.raises(ValueError, match="empty"):
            call("")
    assert not httpx_mock.get_requests()
