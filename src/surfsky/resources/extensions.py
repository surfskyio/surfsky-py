from pathlib import Path
from typing import IO, TYPE_CHECKING

from anyio import to_thread

from ..transport import Spec, list_of, ref
from ..types import Extension

if TYPE_CHECKING:
    from ..client import AsyncSurfsky, Surfsky

ExtensionFile = str | Path | bytes | IO[bytes]


def read_file(file: ExtensionFile) -> tuple[str, bytes]:
    if isinstance(file, str | Path):
        return Path(file).name, Path(file).read_bytes()
    if isinstance(file, bytes):
        return "extension.zip", file
    return Path(str(getattr(file, "name", "extension.zip"))).name, file.read()


def upload(file: ExtensionFile, name: str) -> Spec[Extension]:
    filename, content = read_file(file)
    return Spec(
        "POST",
        "/extensions",
        files={"file": (filename, content, "application/zip")},
        data={"name": name},
        parse=Extension.model_validate,
    )


def list_all() -> Spec[list[Extension]]:
    # the payload is {"extensions": [...], "count": n}
    return Spec(
        "GET",
        "/extensions",
        parse=lambda d: list_of(Extension)((d or {}).get("extensions")),
    )


def get(uuid: str) -> Spec[Extension]:
    return Spec("GET", f"/extensions/{ref(uuid)}", parse=Extension.model_validate)


def update(uuid: str, name: str) -> Spec[Extension]:
    return Spec(
        "PATCH",
        f"/extensions/{ref(uuid)}",
        json={"name": name},
        parse=Extension.model_validate,
    )


def delete(uuid: str) -> Spec[None]:
    return Spec("DELETE", f"/extensions/{ref(uuid)}", parse=lambda _: None)


class Extensions:
    def __init__(self, client: "Surfsky") -> None:
        self.client = client

    def upload(self, file: ExtensionFile, name: str) -> Extension:
        """Upload a ZIP (max 100 MB) given as a path, bytes or a binary stream."""
        return self.client.call(upload(file, name))

    def list_all(self) -> list[Extension]:
        return self.client.call(list_all())

    def get(self, uuid: str) -> Extension:
        return self.client.call(get(uuid))

    def update(self, uuid: str, *, name: str) -> Extension:
        return self.client.call(update(uuid, name))

    def delete(self, uuid: str) -> None:
        self.client.call(delete(uuid))


class AsyncExtensions:
    def __init__(self, client: "AsyncSurfsky") -> None:
        self.client = client

    async def upload(self, file: ExtensionFile, name: str) -> Extension:
        """Upload a ZIP (max 100 MB) given as a path, bytes or a binary stream."""
        spec = await to_thread.run_sync(upload, file, name)  # the read blocks
        return await self.client.call(spec)

    async def list_all(self) -> list[Extension]:
        return await self.client.call(list_all())

    async def get(self, uuid: str) -> Extension:
        return await self.client.call(get(uuid))

    async def update(self, uuid: str, *, name: str) -> Extension:
        return await self.client.call(update(uuid, name))

    async def delete(self, uuid: str) -> None:
        await self.client.call(delete(uuid))
