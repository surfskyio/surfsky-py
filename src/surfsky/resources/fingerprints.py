from typing import TYPE_CHECKING

from ..transport import Spec, list_of
from ..types import OS, Arch, DeviceModel, DeviceType, Renderer, Screen

if TYPE_CHECKING:
    from ..client import AsyncSurfsky, Surfsky


def renderers(os: OS, os_arch: Arch) -> Spec[list[Renderer]]:
    params = {"os": os, "os_arch": os_arch}
    return Spec("GET", "/fingerprint/renderers", params=params, parse=list_of(Renderer))


def screens(os: OS, os_arch: Arch) -> Spec[list[Screen]]:
    params = {"os": os, "os_arch": os_arch}
    return Spec("GET", "/fingerprint/screens", params=params, parse=list_of(Screen))


def device_models(
    os: OS | None,
    os_arch: Arch | None,
    os_version: str | None,
    device_type: DeviceType | None,
) -> Spec[list[DeviceModel]]:
    given = {
        "os": os,
        "os_arch": os_arch,
        "os_version": os_version,
        "device_type": device_type,
    }
    params = {key: value for key, value in given.items() if value is not None}
    return Spec(
        "GET", "/fingerprint/device_models", params=params, parse=list_of(DeviceModel)
    )


class Fingerprints:
    def __init__(self, client: "Surfsky") -> None:
        self.client = client

    def renderers(self, os: OS, os_arch: Arch) -> list[Renderer]:
        return self.client.call(renderers(os, os_arch))

    def screens(self, os: OS, os_arch: Arch) -> list[Screen]:
        return self.client.call(screens(os, os_arch))

    def device_models(
        self,
        *,
        os: OS | None = None,
        os_arch: Arch | None = None,
        os_version: str | None = None,
        device_type: DeviceType | None = None,
    ) -> list[DeviceModel]:
        return self.client.call(device_models(os, os_arch, os_version, device_type))


class AsyncFingerprints:
    def __init__(self, client: "AsyncSurfsky") -> None:
        self.client = client

    async def renderers(self, os: OS, os_arch: Arch) -> list[Renderer]:
        return await self.client.call(renderers(os, os_arch))

    async def screens(self, os: OS, os_arch: Arch) -> list[Screen]:
        return await self.client.call(screens(os, os_arch))

    async def device_models(
        self,
        *,
        os: OS | None = None,
        os_arch: Arch | None = None,
        os_version: str | None = None,
        device_type: DeviceType | None = None,
    ) -> list[DeviceModel]:
        return await self.client.call(device_models(os, os_arch, os_version, device_type))
