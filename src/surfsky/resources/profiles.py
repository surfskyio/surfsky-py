from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from itertools import count
from typing import TYPE_CHECKING, Any, Literal, TypedDict, Unpack, overload

from pydantic import TypeAdapter

from ..proxy import ProxyInput, aresolve_proxy, resolve_proxy
from ..transport import Spec, dump, list_of, ref
from ..types import (
    ActiveProfile,
    BatchDeleteResult,
    BrowserSettings,
    Cookie,
    DomainRoute,
    ExportFormat,
    Fingerprint,
    OneTimeStartRequest,
    Profile,
    ProfileCreateRequest,
    ProfileOrdering,
    ProfileRef,
    ProfileStartRequest,
    ProfileSummary,
    ProfileUpdateRequest,
    ProxyLike,
    ScrapeRequest,
    ScrapeResult,
    Session,
    StopAllResult,
    StorageOptions,
    WaitUntil,
)

if TYPE_CHECKING:
    from ..client import AsyncSurfsky, Surfsky


class SessionOptions(TypedDict, total=False):
    fingerprint: Fingerprint | None
    proxy: ProxyInput | None
    browser_settings: BrowserSettings | None
    enable_chromedriver: bool | None
    extensions: list[str] | None
    proxy_blacklist: list[str] | None
    domain_routes: list[DomainRoute] | None
    cookies: str | list[dict[str, Any]] | None


class ProfileUpdate(TypedDict, total=False):
    title: str
    description: str | None
    proxy: ProxyInput | None
    fingerprint: Fingerprint
    storage_options: StorageOptions | None


SCRAPE_TIMEOUT = 150.0
IMMUTABLE_FINGERPRINT = ("os", "os_arch", "os_version", "device_model", "device_type")
COOKIES = TypeAdapter(list[Cookie])


def session_uuid(session: Session | str) -> str:
    return ref(session if isinstance(session, str) else session.internal_uuid)


def start_one_time(options: Mapping[str, Any], proxy: ProxyLike | None) -> Spec[Session]:
    fields: dict[str, Any] = {**options, "proxy": proxy}
    request = OneTimeStartRequest(**fields)
    return Spec(
        "POST",
        "/profiles/one_time",
        json=dump(request),
        parse=Session.model_validate,
        shield=True,
    )


def start(
    uuid: str, options: Mapping[str, Any], proxy: ProxyLike | None
) -> Spec[Session]:
    fields: dict[str, Any] = {**options, "proxy": proxy}
    for key in ("fingerprint", "cookies"):
        if fields.pop(key, None) is not None:
            raise ValueError(f"{key} applies to one-time sessions only")
    request = ProfileStartRequest(**fields)
    return Spec(
        "POST",
        f"/profiles/{ref(uuid)}/start",
        json=dump(request),
        parse=Session.model_validate,
        shield=True,
    )


def stop(session: Session | str) -> Spec[ProfileRef | None]:
    return Spec(
        "POST",
        f"/profiles/{session_uuid(session)}/stop",
        parse=lambda data: ProfileRef.model_validate(data) if data else None,
    )


def stop_all() -> Spec[StopAllResult]:
    return Spec("POST", "/profiles/stop", parse=StopAllResult.model_validate)


def list_active() -> Spec[list[ActiveProfile]]:
    return Spec("GET", "/profiles/active", parse=list_of(ActiveProfile))


def create(request: ProfileCreateRequest) -> Spec[ProfileRef]:
    return Spec("POST", "/profiles", json=dump(request), parse=ProfileRef.model_validate)


def get(uuid: str) -> Spec[Profile]:
    return Spec("GET", f"/profiles/{ref(uuid)}", parse=Profile.model_validate)


def update(uuid: str, fields: Mapping[str, Any]) -> Spec[Profile]:
    body = dump(ProfileUpdateRequest(**fields))
    for name in fields:
        body.setdefault(name, None)
    if isinstance(fingerprint := body.get("fingerprint"), dict):
        body["fingerprint"] = {
            k: v for k, v in fingerprint.items() if k not in IMMUTABLE_FINGERPRINT
        }
    return Spec(
        "PATCH", f"/profiles/{ref(uuid)}", json=body, parse=Profile.model_validate
    )


def delete(uuid: str) -> Spec[ProfileRef]:
    return Spec("DELETE", f"/profiles/{ref(uuid)}", parse=ProfileRef.model_validate)


def delete_many(uuids: list[str]) -> Spec[BatchDeleteResult]:
    def partial(status: int, body: Any) -> bool:
        return (
            status == 400
            and isinstance(body, dict)
            and "deleted_uuids" in (body.get("data") or {})
        )

    return Spec(
        "DELETE",
        "/profiles",
        json={"uuids": uuids},
        parse=BatchDeleteResult.model_validate,
        accept_error=partial,
    )


def list_page(
    page: int | None, page_len: int | None, ordering: ProfileOrdering | None
) -> Spec[list[ProfileSummary]]:
    given = {"page": page, "page_len": page_len, "ordering": ordering}
    params = {key: value for key, value in given.items() if value is not None}
    return Spec("GET", "/profiles", params=params, parse=list_of(ProfileSummary))


def export_cookies(uuid: str, export_format: ExportFormat) -> Spec[list[Cookie] | str]:
    def parse(data: Any) -> list[Cookie] | str:
        cookies = (data or {}).get("cookies")
        if isinstance(cookies, str):  # the netscape export is 1 text blob
            return cookies
        return list_of(Cookie)(cookies)

    params = {"export_format": export_format}
    return Spec("GET", f"/profiles/{ref(uuid)}/cookies", params=params, parse=parse)


def import_cookies(
    uuid: str, cookies: str | Sequence[Cookie | dict[str, Any]]
) -> Spec[None]:
    if not isinstance(cookies, str):
        models = COOKIES.validate_python(cookies)
        cookies = COOKIES.dump_json(models, by_alias=True, exclude_none=True).decode()
    return Spec(
        "POST",
        f"/profiles/{ref(uuid)}/cookies",
        json={"cookies": cookies},
        parse=lambda _: None,
    )


def scrape(session: Session | str, request: ScrapeRequest) -> Spec[ScrapeResult]:
    return Spec(
        "POST",
        f"/profiles/{session_uuid(session)}/scrape",
        json=dump(request),
        parse=ScrapeResult.model_validate,
        timeout=SCRAPE_TIMEOUT,
    )


class Profiles:
    def __init__(self, client: "Surfsky") -> None:
        self.client = client

    def start_one_time(self, **options: Unpack[SessionOptions]) -> Session:
        proxy = resolve_proxy(options.get("proxy"))
        return self.client.call(start_one_time(options, proxy))

    def start(self, uuid: str, **options: Unpack[SessionOptions]) -> Session:
        proxy = resolve_proxy(options.get("proxy"))
        return self.client.call(start(uuid, options, proxy))

    def stop(self, session: Session | str) -> ProfileRef | None:
        return self.client.call(stop(session))

    def stop_all(self) -> StopAllResult:
        return self.client.call(stop_all())

    def list_active(self) -> list[ActiveProfile]:
        return self.client.call(list_active())

    def create(
        self,
        *,
        title: str,
        fingerprint: Fingerprint,
        description: str | None = None,
        proxy: ProxyInput | None = None,
        cookies: str | list[dict[str, Any]] | None = None,
        storage_options: StorageOptions | None = None,
    ) -> ProfileRef:
        request = ProfileCreateRequest(
            title=title,
            fingerprint=fingerprint,
            description=description,
            proxy=resolve_proxy(proxy),
            cookies=cookies,
            storage_options=storage_options,
        )
        return self.client.call(create(request))

    def get(self, uuid: str) -> Profile:
        return self.client.call(get(uuid))

    def update(self, uuid: str, **fields: Unpack[ProfileUpdate]) -> Profile:
        if "proxy" in fields:
            fields["proxy"] = resolve_proxy(fields["proxy"])
        return self.client.call(update(uuid, fields))

    def delete(self, uuid: str) -> ProfileRef:
        return self.client.call(delete(uuid))

    def delete_many(self, uuids: list[str]) -> BatchDeleteResult:
        return self.client.call(delete_many(uuids))

    def list_page(
        self,
        *,
        page: int | None = None,
        page_len: int | None = None,
        ordering: ProfileOrdering | None = None,
    ) -> list[ProfileSummary]:
        return self.client.call(list_page(page, page_len, ordering))

    def iter_all(
        self, *, page_len: int = 100, ordering: ProfileOrdering = "created"
    ) -> Iterator[ProfileSummary]:
        page_len = max(1, min(page_len, 100))
        for page in count():
            batch = self.list_page(page=page, page_len=page_len, ordering=ordering)
            yield from batch
            if len(batch) < page_len:
                return

    @overload
    def export_cookies(
        self, uuid: str, *, export_format: Literal["json"] = ...
    ) -> list[Cookie]: ...

    @overload
    def export_cookies(self, uuid: str, *, export_format: Literal["netscape"]) -> str: ...

    def export_cookies(
        self, uuid: str, *, export_format: ExportFormat = "json"
    ) -> list[Cookie] | str:
        return self.client.call(export_cookies(uuid, export_format))

    def import_cookies(
        self, uuid: str, cookies: str | Sequence[Cookie | dict[str, Any]]
    ) -> None:
        self.client.call(import_cookies(uuid, cookies))

    def scrape(
        self,
        session: Session | str,
        url: str,
        *,
        screenshot: bool | None = None,
        wait: float | None = None,
        wait_until: WaitUntil | None = None,
        wait_for: str | None = None,
        human_actions: int | None = None,
    ) -> ScrapeResult:
        request = ScrapeRequest(
            url=url,
            screenshot=screenshot,
            wait=wait,
            wait_until=wait_until,
            wait_for=wait_for,
            human_actions=human_actions,
        )
        return self.client.call(scrape(session, request))


class AsyncProfiles:
    def __init__(self, client: "AsyncSurfsky") -> None:
        self.client = client

    async def start_one_time(self, **options: Unpack[SessionOptions]) -> Session:
        proxy = await aresolve_proxy(options.get("proxy"))
        return await self.client.call(start_one_time(options, proxy))

    async def start(self, uuid: str, **options: Unpack[SessionOptions]) -> Session:
        proxy = await aresolve_proxy(options.get("proxy"))
        return await self.client.call(start(uuid, options, proxy))

    async def stop(self, session: Session | str) -> ProfileRef | None:
        return await self.client.call(stop(session))

    async def stop_all(self) -> StopAllResult:
        return await self.client.call(stop_all())

    async def list_active(self) -> list[ActiveProfile]:
        return await self.client.call(list_active())

    async def create(
        self,
        *,
        title: str,
        fingerprint: Fingerprint,
        description: str | None = None,
        proxy: ProxyInput | None = None,
        cookies: str | list[dict[str, Any]] | None = None,
        storage_options: StorageOptions | None = None,
    ) -> ProfileRef:
        request = ProfileCreateRequest(
            title=title,
            fingerprint=fingerprint,
            description=description,
            proxy=await aresolve_proxy(proxy),
            cookies=cookies,
            storage_options=storage_options,
        )
        return await self.client.call(create(request))

    async def get(self, uuid: str) -> Profile:
        return await self.client.call(get(uuid))

    async def update(self, uuid: str, **fields: Unpack[ProfileUpdate]) -> Profile:
        if "proxy" in fields:
            fields["proxy"] = await aresolve_proxy(fields["proxy"])
        return await self.client.call(update(uuid, fields))

    async def delete(self, uuid: str) -> ProfileRef:
        return await self.client.call(delete(uuid))

    async def delete_many(self, uuids: list[str]) -> BatchDeleteResult:
        return await self.client.call(delete_many(uuids))

    async def list_page(
        self,
        *,
        page: int | None = None,
        page_len: int | None = None,
        ordering: ProfileOrdering | None = None,
    ) -> list[ProfileSummary]:
        return await self.client.call(list_page(page, page_len, ordering))

    async def iter_all(
        self, *, page_len: int = 100, ordering: ProfileOrdering = "created"
    ) -> AsyncIterator[ProfileSummary]:
        page_len = max(1, min(page_len, 100))
        for page in count():
            batch = await self.list_page(page=page, page_len=page_len, ordering=ordering)
            for profile in batch:
                yield profile
            if len(batch) < page_len:
                return

    @overload
    async def export_cookies(
        self, uuid: str, *, export_format: Literal["json"] = ...
    ) -> list[Cookie]: ...

    @overload
    async def export_cookies(
        self, uuid: str, *, export_format: Literal["netscape"]
    ) -> str: ...

    async def export_cookies(
        self, uuid: str, *, export_format: ExportFormat = "json"
    ) -> list[Cookie] | str:
        return await self.client.call(export_cookies(uuid, export_format))

    async def import_cookies(
        self, uuid: str, cookies: str | Sequence[Cookie | dict[str, Any]]
    ) -> None:
        await self.client.call(import_cookies(uuid, cookies))

    async def scrape(
        self,
        session: Session | str,
        url: str,
        *,
        screenshot: bool | None = None,
        wait: float | None = None,
        wait_until: WaitUntil | None = None,
        wait_for: str | None = None,
        human_actions: int | None = None,
    ) -> ScrapeResult:
        request = ScrapeRequest(
            url=url,
            screenshot=screenshot,
            wait=wait,
            wait_until=wait_until,
            wait_for=wait_for,
            human_actions=human_actions,
        )
        return await self.client.call(scrape(session, request))
