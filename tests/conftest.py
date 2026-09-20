import asyncio

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


@pytest.fixture
def client() -> Surfsky:
    return Surfsky(api_token="test-token", base_url="https://api.test")
