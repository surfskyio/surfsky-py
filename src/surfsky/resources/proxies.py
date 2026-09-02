from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ..transport import Spec, list_of, ref
from ..types import (
    ProxyCity,
    ProxyCountry,
    ProxyQuota,
    ProxyRegion,
    SharedProxyQuota,
    TrafficStats,
)

if TYPE_CHECKING:
    from ..client import AsyncSurfsky, Surfsky


def countries() -> Spec[list[ProxyCountry]]:
    return Spec("GET", "/proxies/countries", parse=list_of(ProxyCountry))


def regions(country: str) -> Spec[list[ProxyRegion]]:
    return Spec("GET", f"/proxies/regions/{ref(country)}", parse=list_of(ProxyRegion))


def cities(country: str, region: str) -> Spec[list[ProxyCity]]:
    return Spec(
        "GET", f"/proxies/cities/{ref(country)}/{ref(region)}", parse=list_of(ProxyCity)
    )


def quota() -> Spec[ProxyQuota]:
    return Spec("GET", "/proxies/quota", parse=ProxyQuota.model_validate)


def premium_stats() -> Spec[TrafficStats]:
    return Spec("GET", "/proxies/premium/stats", parse=TrafficStats.model_validate)


def shared_countries() -> Spec[list[str]]:
    return Spec(
        "GET", "/proxies/shared/countries", parse=TypeAdapter(list[str]).validate_python
    )


def shared_quota() -> Spec[SharedProxyQuota]:
    return Spec("GET", "/proxies/shared/quota", parse=SharedProxyQuota.model_validate)


def shared_stats() -> Spec[TrafficStats]:
    return Spec("GET", "/proxies/shared/stats", parse=TrafficStats.model_validate)


class Proxies:
    def __init__(self, client: "Surfsky") -> None:
        self.client = client

    def countries(self) -> list[ProxyCountry]:
        return self.client.call(countries())

    def regions(self, country: str) -> list[ProxyRegion]:
        return self.client.call(regions(country))

    def cities(self, country: str, region: str) -> list[ProxyCity]:
        return self.client.call(cities(country, region))

    def quota(self) -> ProxyQuota:
        return self.client.call(quota())

    def premium_stats(self) -> TrafficStats:
        return self.client.call(premium_stats())

    def shared_countries(self) -> list[str]:
        return self.client.call(shared_countries())

    def shared_quota(self) -> SharedProxyQuota:
        return self.client.call(shared_quota())

    def shared_stats(self) -> TrafficStats:
        return self.client.call(shared_stats())


class AsyncProxies:
    def __init__(self, client: "AsyncSurfsky") -> None:
        self.client = client

    async def countries(self) -> list[ProxyCountry]:
        return await self.client.call(countries())

    async def regions(self, country: str) -> list[ProxyRegion]:
        return await self.client.call(regions(country))

    async def cities(self, country: str, region: str) -> list[ProxyCity]:
        return await self.client.call(cities(country, region))

    async def quota(self) -> ProxyQuota:
        return await self.client.call(quota())

    async def premium_stats(self) -> TrafficStats:
        return await self.client.call(premium_stats())

    async def shared_countries(self) -> list[str]:
        return await self.client.call(shared_countries())

    async def shared_quota(self) -> SharedProxyQuota:
        return await self.client.call(shared_quota())

    async def shared_stats(self) -> TrafficStats:
        return await self.client.call(shared_stats())
