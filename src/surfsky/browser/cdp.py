"""A tiny Chrome DevTools Protocol client: JSON-RPC over WebSocket."""

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from websockets.asyncio.client import connect

from ..errors import SurfskyError

logger = logging.getLogger("surfsky")

MAX_MESSAGE = 128 * 1024 * 1024

EventHandler = Callable[[dict[str, Any], str | None], None]


class CDPError(SurfskyError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def retrieved(reply: "asyncio.Future[Any]") -> None:
    # marks a failed reply as seen
    if not reply.cancelled():
        reply.exception()


class CDPClient:
    def __init__(
        self, ws_url: str, *, on_close: Callable[[], None] | None = None
    ) -> None:
        self.ws_url = ws_url
        self.on_close = on_close
        self._ws: Any = None
        self._closed = False
        self._reader: asyncio.Task[None] | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._handlers: dict[str, EventHandler] = {}

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._closed

    async def start(self) -> None:
        self._ws = await connect(
            self.ws_url, max_size=MAX_MESSAGE, ping_interval=None, open_timeout=None
        )
        self._reader = asyncio.create_task(self._receive())

    async def stop(self) -> None:
        self._closed = True
        try:
            reader, self._reader = self._reader, None
            if reader is not None:
                reader.cancel()
                await asyncio.wait(
                    [reader]
                )  # not `await reader`: that eats our own cancel
            ws, self._ws = self._ws, None
            if ws is not None:
                with suppress(Exception):
                    await ws.close()
        finally:
            self._fail_pending()

    def on(self, event: str, handler: EventHandler) -> None:
        self._handlers[event] = handler

    async def post(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> tuple[int, "asyncio.Future[Any]"]:
        if self._closed:
            raise CDPError("CDP connection closed")
        if self._ws is None:
            raise RuntimeError("CDP client is not connected; call start() first")
        self._next_id += 1
        msg_id = self._next_id
        message: dict[str, Any] = {"id": msg_id, "method": method, "params": params or {}}
        if session_id is not None:
            message["sessionId"] = session_id
        reply: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        reply.add_done_callback(retrieved)
        self._pending[msg_id] = reply
        try:
            await self._ws.send(json.dumps(message))
        except Exception as exc:
            self._pending.pop(msg_id, None)
            raise CDPError(f"CDP connection closed: {exc}") from exc
        return msg_id, reply

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Any:
        msg_id, reply = await self.post(method, params, session_id)
        try:
            return await reply
        finally:
            self._pending.pop(msg_id, None)

    async def _receive(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except ValueError:
                    logger.warning("dropping unparsable CDP frame")
                    continue
                self._dispatch(message)
            if not self._closed:
                logger.warning("CDP connection closed by the server")
        except Exception as exc:
            logger.warning(f"CDP connection lost: {exc}")
        finally:
            self._closed = True
            self._fail_pending()
            if self.on_close is not None:
                self.on_close()

    def _dispatch(self, message: dict[str, Any]) -> None:
        if (msg_id := message.get("id")) is not None:
            reply = self._pending.pop(msg_id, None)
            if reply is None or reply.done():
                return
            if (error := message.get("error")) is not None:
                text = error.get("message", "CDP error")
                if detail := error.get("data"):
                    text = f"{text}: {detail}"
                reply.set_exception(CDPError(text, code=error.get("code")))
            else:
                reply.set_result(message.get("result", {}))
        elif handler := self._handlers.get(message.get("method", "")):
            try:
                handler(message.get("params", {}), message.get("sessionId"))
            except Exception:
                logger.exception("CDP event handler for %s failed", message["method"])

    def _fail_pending(self) -> None:
        pending, self._pending = self._pending, {}
        for reply in pending.values():
            if not reply.done():
                reply.set_exception(CDPError("CDP connection closed"))
