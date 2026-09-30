import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import httpx
import pytest

from surfsky import Surfsky


class PostsViaSend:
    async def post(self, method, params=None, session_id=None):
        result = await self.send(method, params, session_id)
        reply = asyncio.get_running_loop().create_future()
        reply.set_result(result)
        return 0, reply

    def document_response(
        self, session: str, request_id: str, status: int, frame_id: str = "T"
    ) -> None:
        self.handlers["Network.responseReceived"](
            {
                "requestId": request_id,
                "type": "Document",
                "frameId": frame_id,
                "response": {"url": "https://x.test/", "status": status},
            },
            session,
        )


class StoppingClient:

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.stopped: list[str] = []
        self.profiles = self

    def with_options(self, **kwargs):
        return self

    async def stop(self, uuid: str) -> None:
        await asyncio.sleep(self.delay)
        self.stopped.append(uuid)


def session_endpoints(httpx_mock, start_delay: float = 0) -> None:
    async def start(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(start_delay)
        return httpx.Response(200, json={"internal_uuid": "s9", "ws_url": "wss://x"})

    httpx_mock.add_callback(start, method="POST", url="https://api.test/profiles/one_time")
    httpx_mock.add_response(
        method="POST", url="https://api.test/profiles/s9/stop",
        json={"success": True, "data": None},
    )


@asynccontextmanager
async def cancelled_after(seconds: float, the_anyio_way: bool) -> AsyncIterator[None]:
    if the_anyio_way:
        with anyio.move_on_after(seconds) as scope:
            yield
        assert scope.cancelled_caught
    else:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(seconds):
                yield


both_ways = pytest.mark.parametrize("the_anyio_way", [False, True], ids=["asyncio", "anyio"])


@pytest.fixture
def client() -> Surfsky:
    return Surfsky(api_token="test-token", base_url="https://api.test")
