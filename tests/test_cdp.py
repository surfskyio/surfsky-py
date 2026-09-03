import asyncio
import json

import pytest

from surfsky.browser import cdp
from surfsky.browser.cdp import CDPClient, CDPError

_SENTINEL = object()


class FakeWebSocket:
    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self.sent: list[str] = []
        self.closed = False
        self.responder = None  # callable(outgoing_msg: dict) -> reply dict | None

    async def send(self, data: str) -> None:
        self.sent.append(data)
        if self.responder is not None:
            reply = self.responder(json.loads(data))
            if reply is not None:
                await self._queue.put(json.dumps(reply))

    async def push(self, message: dict) -> None:
        await self._queue.put(json.dumps(message))

    async def close(self) -> None:
        self.closed = True
        await self._queue.put(_SENTINEL)

    def __aiter__(self) -> "FakeWebSocket":
        return self

    async def __anext__(self) -> str:
        item = await self._queue.get()
        if item is _SENTINEL:
            raise StopAsyncIteration
        return item


async def _connected(monkeypatch, ws: FakeWebSocket, **kwargs) -> CDPClient:
    async def fake_connect(url, **_):
        return ws

    monkeypatch.setattr(cdp, "connect", fake_connect)
    client = CDPClient("wss://fake", **kwargs)
    await client.start()
    return client


def _echo(msg: dict) -> dict:
    return {"id": msg["id"], "result": {"method": msg["method"]}}


@pytest.mark.anyio
async def test_send_returns_matching_result(monkeypatch):
    ws = FakeWebSocket()
    ws.responder = _echo
    client = await _connected(monkeypatch, ws)
    try:
        assert await client.send("Page.enable") == {"method": "Page.enable"}
    finally:
        await client.stop()


@pytest.mark.anyio
async def test_concurrent_commands_correlate_by_id(monkeypatch):
    ws = FakeWebSocket()
    ws.responder = lambda m: {"id": m["id"], "result": {"echo": m["method"]}}
    client = await _connected(monkeypatch, ws)
    try:
        a, b = await asyncio.gather(
            client.send("A.one"), client.send("B.two")
        )
        assert a == {"echo": "A.one"}
        assert b == {"echo": "B.two"}
    finally:
        await client.stop()


@pytest.mark.anyio
async def test_outgoing_frame_shape(monkeypatch):
    ws = FakeWebSocket()
    ws.responder = lambda m: {"id": m["id"], "result": {}}
    client = await _connected(monkeypatch, ws)
    try:
        await client.send("Page.navigate", {"url": "https://x"}, session_id="S")
        sent = json.loads(ws.sent[-1])
        assert sent["method"] == "Page.navigate"
        assert sent["params"] == {"url": "https://x"}
        assert sent["sessionId"] == "S"
        assert isinstance(sent["id"], int)
    finally:
        await client.stop()


@pytest.mark.anyio
async def test_no_session_id_omits_the_key(monkeypatch):
    ws = FakeWebSocket()
    ws.responder = lambda m: {"id": m["id"], "result": {}}
    client = await _connected(monkeypatch, ws)
    try:
        await client.send("Browser.getVersion")
        assert "sessionId" not in json.loads(ws.sent[-1])
    finally:
        await client.stop()


@pytest.mark.anyio
async def test_error_reply_raises_cdp_error(monkeypatch):
    ws = FakeWebSocket()
    ws.responder = lambda m: {"id": m["id"], "error": {"code": -32000, "message": "boom"}}
    client = await _connected(monkeypatch, ws)
    try:
        with pytest.raises(CDPError, match="boom"):
            await client.send("Bad.command")
    finally:
        await client.stop()


@pytest.mark.anyio
async def test_event_dispatched_to_registered_handler(monkeypatch):
    ws = FakeWebSocket()
    client = await _connected(monkeypatch, ws)
    got = asyncio.get_running_loop().create_future()

    client.on("Page.lifecycleEvent", lambda params, sid: got.set_result((params, sid)))
    try:
        await ws.push(
            {"method": "Page.lifecycleEvent", "params": {"name": "load"}, "sessionId": "S"}
        )
        params, sid = await asyncio.wait_for(got, timeout=1)
        assert params == {"name": "load"}
        assert sid == "S"
    finally:
        await client.stop()


@pytest.mark.anyio
async def test_unregistered_event_is_ignored(monkeypatch):
    ws = FakeWebSocket()
    ws.responder = _echo
    client = await _connected(monkeypatch, ws)
    try:
        await ws.push({"method": "Runtime.consoleAPICalled", "params": {}})
        # the receive loop must survive an event with no handler
        assert await client.send("Page.enable") == {"method": "Page.enable"}
    finally:
        await client.stop()


@pytest.mark.anyio
async def test_stop_fails_pending_commands(monkeypatch):
    ws = FakeWebSocket()
    ws.responder = lambda m: None
    client = await _connected(monkeypatch, ws)
    pending = asyncio.create_task(client.send("Never.returns"))
    await asyncio.sleep(0.01)  # let it register the pending future

    await client.stop()

    with pytest.raises(CDPError):
        await pending  # must not hang


@pytest.mark.anyio
async def test_cancelled_command_is_removed_from_pending(monkeypatch):
    ws = FakeWebSocket()
    ws.responder = lambda m: None
    client = await _connected(monkeypatch, ws)
    try:
        task = asyncio.create_task(client.send("Never.returns"))
        await asyncio.sleep(0.01)
        assert client._pending
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not client._pending
    finally:
        await client.stop()


@pytest.mark.anyio
async def test_an_abandoned_post_is_removed_from_pending(monkeypatch):
    ws = FakeWebSocket()
    ws.responder = lambda m: None
    client = await _connected(monkeypatch, ws)
    try:
        _, reply = await client.post("Never.returns")
        assert client._pending
        reply.cancel()
        await asyncio.sleep(0)
        assert not client._pending
    finally:
        await client.stop()


@pytest.mark.anyio
async def test_send_before_start_raises():
    client = CDPClient("wss://fake")
    with pytest.raises(RuntimeError, match="not connected"):
        await client.send("Page.enable")


@pytest.mark.anyio
async def test_stop_is_idempotent(monkeypatch):
    ws = FakeWebSocket()
    client = await _connected(monkeypatch, ws)
    await client.stop()
    await client.stop()
    assert ws.closed


@pytest.mark.anyio
async def test_handler_exception_does_not_kill_the_connection(monkeypatch, caplog):
    ws = FakeWebSocket()
    ws.responder = _echo
    client = await _connected(monkeypatch, ws)
    client.on("Fetch.requestPaused", lambda params, sid: params["missing"])
    try:
        with caplog.at_level("ERROR", logger="surfsky"):
            await ws.push({"method": "Fetch.requestPaused", "params": {}})
            assert await client.send("Page.enable") == {"method": "Page.enable"}
        assert any("handler" in r.message for r in caplog.records)
    finally:
        await client.stop()


@pytest.mark.anyio
async def test_server_close_fails_later_commands(monkeypatch):
    ws = FakeWebSocket()
    closed: list[bool] = []
    client = await _connected(monkeypatch, ws, on_close=lambda: closed.append(True))
    await ws.close()
    await asyncio.sleep(0.01)
    assert closed == [True]
    assert not client.connected
    with pytest.raises(CDPError, match="connection closed"):  # not a raw socket error
        await client.send("Page.enable")
    await client.stop()


@pytest.mark.anyio
async def test_send_after_stop_reports_a_cdp_error(monkeypatch):
    # "the connection is gone" must be one exception type, whoever closed it
    ws = FakeWebSocket()
    client = await _connected(monkeypatch, ws)
    await client.stop()
    with pytest.raises(CDPError, match="closed"):
        await client.send("Page.enable")


@pytest.mark.anyio
async def test_a_failing_socket_send_reports_a_cdp_error(monkeypatch):
    # the socket can drop between the receive loop noticing and our send
    ws = FakeWebSocket()
    client = await _connected(monkeypatch, ws)

    async def broken_send(data: str) -> None:
        raise ConnectionResetError("socket went away")

    monkeypatch.setattr(ws, "send", broken_send)
    with pytest.raises(CDPError):
        await client.send("Page.enable")
    assert not client._pending  # the slot is dropped either way
    await client.stop()


@pytest.mark.anyio
async def test_a_malformed_frame_does_not_kill_the_connection(monkeypatch):
    ws = FakeWebSocket()
    client = await _connected(monkeypatch, ws)
    ws.responder = lambda msg: {"id": msg["id"], "result": {"ok": True}}

    await ws._queue.put("not json at all")
    await asyncio.sleep(0)
    assert client.connected

    assert await client.send("Page.enable") == {"ok": True}
    await client.stop()


@pytest.mark.anyio
async def test_error_replies_keep_the_server_detail(monkeypatch):
    ws = FakeWebSocket()
    client = await _connected(monkeypatch, ws)
    ws.responder = lambda msg: {
        "id": msg["id"],
        "error": {"code": -32000, "message": "Cannot find context", "data": "frame 7"},
    }
    with pytest.raises(CDPError, match="frame 7"):  # data is where the detail lives
        await client.send("Runtime.evaluate")
    await client.stop()


@pytest.mark.anyio
async def test_pending_commands_fail_even_if_the_close_errors(monkeypatch):
    # nothing will answer once we are stopping, whatever the socket does
    ws = FakeWebSocket()
    client = await _connected(monkeypatch, ws)

    async def broken_close() -> None:
        raise OSError("close failed")

    monkeypatch.setattr(ws, "close", broken_close)
    pending = asyncio.ensure_future(client.send("Page.enable"))
    await asyncio.sleep(0)
    await client.stop()
    with pytest.raises(CDPError):
        await pending
