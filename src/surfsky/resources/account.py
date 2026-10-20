import os
from typing import TYPE_CHECKING

from ..errors import NotFoundError
from ..transport import Spec
from ..types import BrowserLimits

if TYPE_CHECKING:
    from ..client import AsyncSurfsky, Surfsky

DEFAULT_MAX_BROWSERS = 10


def browser_limits() -> Spec[BrowserLimits]:
    return Spec("GET", "/users/browser-limits", parse=BrowserLimits.model_validate)


def env_max_browsers() -> int | None:
    value = os.environ.get("SURFSKY_MAX_BROWSERS", "")
    return max(1, int(value)) if value.isdigit() else None


class Account:
    def __init__(self, client: "Surfsky") -> None:
        self.client = client

    def browser_limits(self) -> BrowserLimits:
        return self.client.call(browser_limits())

    def max_browsers(self) -> int:
        return (
            env_max_browsers() or self._limits().parallel_browsers or DEFAULT_MAX_BROWSERS
        )

    def _limits(self) -> BrowserLimits:
        try:
            return self.browser_limits()
        except NotFoundError:
            return BrowserLimits()


class AsyncAccount:
    def __init__(self, client: "AsyncSurfsky") -> None:
        self.client = client

    async def browser_limits(self) -> BrowserLimits:
        return await self.client.call(browser_limits())

    async def max_browsers(self) -> int:
        if override := env_max_browsers():
            return override
        return (await self._limits()).parallel_browsers or DEFAULT_MAX_BROWSERS

    async def _limits(self) -> BrowserLimits:
        try:
            return await self.browser_limits()
        except NotFoundError:
            return BrowserLimits()
