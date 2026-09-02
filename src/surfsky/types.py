from typing import Any, Literal, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

OS = Literal["win", "mac", "android"]
Arch = Literal["x86", "arm"]
DeviceType = Literal["phone", "tablet"]
WaitUntil = Literal["domcontentloaded", "load", "networkidle", "commit"]
ProxyType = Literal["residential", "mobile"]
RegionalPool = Literal[
    "western", "europe", "westeurope", "northamerica", "southamerica", "asia",
    "centralasia", "southasia", "eastasia", "sea", "oceania", "mena",
]
ExportFormat = Literal["json", "netscape"]
ProfileOrdering = Literal["created", "-created", "active", "-active", "title", "-title"]
MouseButton = Literal["left", "right", "middle"]
KeyModifier = Literal["Alt", "Control", "Meta", "Shift"]
ScrollBehavior = Literal["smooth", "instant"]


class Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class Request(Model):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="allow"
    )


class Noise(Model):
    webgl: bool | None = None
    canvas: bool | None = None
    audio: bool | None = None
    client_rects: bool | None = None


class MediaDevices(Model):
    video_in: int | None = None
    audio_out: int | None = None
    audio_in: int | None = None


class Geolocation(Model):
    latitude: float
    longitude: float
    accuracy: int


class Fingerprint(Model):
    os: OS | str | None = None
    os_arch: Arch | str | None = None
    os_version: str | None = None
    device_model: str | None = None
    device_type: DeviceType | str | None = None
    user_agent: str | None = None
    cpu: int | None = None
    ram: int | None = None
    renderer: str | None = None
    noise: Noise | None = None
    media_devices: MediaDevices | None = None
    screen: str | None = None
    languages: list[str] | None = None
    timezone: str | None = None
    geolocation: Geolocation | None = None
    dns: str | None = None


class BrowserSettings(Request):
    inactive_kill_timeout: int | None = None
    cache_enabled: bool | None = None
    cache_key: str | None = None


class ProxyTargeting(Request):
    country: str | None = None
    region: str | None = None
    city: str | None = None
    type: ProxyType | None = None
    pool: RegionalPool | None = None
    asn: int | None = Field(default=None, ge=1, le=4294967295)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    session_minutes: int | None = Field(default=None, ge=1, le=10080)
    unique_ip: bool | None = None
    keep_asn: bool | None = None
    keep_ip: bool | None = None

    @model_validator(mode="after")
    def check_targeting(self) -> Self:
        gps = self.lat is not None or self.lon is not None
        geo = bool(self.country or self.region or self.city or self.asn)
        if sum([bool(self.pool), gps, geo]) > 1:
            raise ValueError(
                "'pool', 'lat'/'lon' and 'country'/'region'/'city'/'asn' are 3 "
                "separate targeting modes - use one!"
            )
        if gps and (self.lat is None or self.lon is None):
            raise ValueError("'lat' and 'lon' must be sent together")
        if self.region and not self.country:
            raise ValueError("region targeting requires country")
        if self.city and not self.region:
            raise ValueError("city targeting requires region")
        if self.asn and not self.country:
            raise ValueError("asn targeting requires country")
        return self


class ProxyGeo(ProxyTargeting):
    ...


class PremiumProxy(ProxyTargeting):
    tier: Literal["premium"] = "premium"


class SharedProxy(Request):
    tier: Literal["shared"] = "shared"
    country: str | None = None


ProxyLike = str | SharedProxy | PremiumProxy | ProxyGeo


class DomainRoute(Request):
    proxy: str
    domain: list[str] | None = None
    domain_suffix: list[str] | None = None
    domain_keyword: list[str] | None = None
    domain_regex: list[str] | None = None


class StorageOptions(Model):
    cookies: bool | None = None
    passwords: bool | None = None
    extensions: bool | None = None
    localstorage: bool | None = None
    history: bool | None = None
    bookmarks: bool | None = None
    serviceworkers: bool | None = None


class OneTimeStartRequest(Request):
    fingerprint: Fingerprint | None = None
    proxy: ProxyLike | None = None
    browser_settings: BrowserSettings | None = None
    enable_chromedriver: bool | None = None
    extensions: list[str] | None = Field(default=None, max_length=5)
    proxy_blacklist: list[str] | None = None
    domain_routes: list[DomainRoute] | None = None
    cookies: str | list[dict[str, Any]] | None = None


class ProfileCreateRequest(Request):
    title: str
    fingerprint: Fingerprint
    description: str | None = None
    proxy: ProxyLike | None = None
    cookies: str | list[dict[str, Any]] | None = None
    storage_options: StorageOptions | None = None


class ProfileStartRequest(Request):
    proxy: ProxyLike | None = None
    browser_settings: BrowserSettings | None = None
    enable_chromedriver: bool | None = None
    extensions: list[str] | None = Field(default=None, max_length=5)
    proxy_blacklist: list[str] | None = None
    domain_routes: list[DomainRoute] | None = None


class ProfileUpdateRequest(Request):
    title: str | None = None
    description: str | None = None
    proxy: ProxyLike | None = None
    fingerprint: Fingerprint | None = None
    storage_options: StorageOptions | None = None


class ScrapeRequest(Request):
    url: str
    screenshot: bool | None = None
    wait: float | None = Field(default=None, ge=0, le=60)
    wait_until: WaitUntil | None = None
    wait_for: str | None = None
    human_actions: int | None = Field(default=None, ge=0, le=3)


class InspectorPage(Model):
    page_title: str | None = None
    page_url: str | None = None
    devtools_url: str | None = None


class Inspector(Model):
    list_url: str | None = Field(default=None, alias="list")
    pages: list[InspectorPage] = Field(default_factory=list)
    screencast: str | None = None


class Session(Model):
    internal_uuid: str
    ws_url: str
    inspector: Inspector | None = None

    @property
    def connect_url(self) -> str:
        return self.ws_url


class ProfileRef(Model):
    uuid: str


class ActiveProfile(Model):
    internal_uuid: str
    profile_uuid: str | None = None
    one_time: bool | None = None
    started_at: str | None = None
    active_seconds: int | None = None


class ProfileSummary(Model):
    uuid: str
    title: str | None = None
    description: str | None = None
    proxy: str | None = None
    status: str | None = None


class Profile(Model):
    uuid: str
    title: str | None = None
    description: str | None = None
    start_pages: list[Any] = Field(default_factory=list)
    pinned_tag: str | None = None
    proxy: str | None = None
    status: str | None = None
    storage_options: StorageOptions | None = None
    last_active: str | None = None
    fingerprint: Fingerprint | None = None
    has_user_password: bool | None = None
    password_set_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class Cookie(CamelModel):
    domain: str | None = None
    name: str | None = None
    value: str | None = None
    path: str | None = None
    expiration_date: float | None = Field(
        default=None, validation_alias=AliasChoices("expirationDate", "expires")
    )
    host_only: bool | None = None
    http_only: bool | None = None
    same_site: str | None = None
    secure: bool | None = None


class ScrapeResult(Model):
    url: str | None = None
    status: int | None = None
    status_text: str | None = None
    content: str | None = None
    cookies: list[Cookie] = Field(default_factory=list)
    screenshot: str | None = None  # base64 PNG when requested


class BatchDeleteResult(Model):
    deleted_uuids: list[str] = Field(default_factory=list)
    active_uuids: list[str] = Field(default_factory=list)
    not_found_uuids: list[str] = Field(default_factory=list)


class StopAllResult(Model):
    stopped: list[str] = Field(default_factory=list)
    failed: list[Any] = Field(default_factory=list)


class ProxyCountry(Model):
    code: str
    name: str | None = None


class ProxyRegion(Model):
    code: str
    name: str | None = None
    country: str | None = None
    country_code: str | None = None


class ProxyCity(Model):
    code: str
    name: str | None = None
    region: str | None = None
    region_code: str | None = None
    country: str | None = None
    country_code: str | None = None


class ProxyQuota(Model):
    remaining_bytes: int | None = None
    remaining_gb: float | None = None


class SharedProxyQuota(Model):
    """``-1`` means unlimited."""

    limit_gb: float | None = None
    limit_bytes: int | None = None
    used_bytes: int | None = None
    remaining_bytes: int | None = None
    remaining_gb: float | None = None
    reset_time: int | None = None


class TrafficVolume(Model):
    size_bytes: int | None = Field(default=None, alias="bytes")
    gb: float | None = None


class TrafficStats(Model):
    last_24h: TrafficVolume | None = Field(default=None, alias="24h")
    last_7d: TrafficVolume | None = Field(default=None, alias="7d")
    last_30d: TrafficVolume | None = Field(default=None, alias="30d")


class Renderer(Model):
    value: str
    platform: str | None = None
    archs: list[str] = Field(default_factory=list)


class Screen(Model):
    value: str
    platform: str | None = None
    archs: list[str] = Field(default_factory=list)


class DeviceModel(Model):
    value: str
    os: str | None = None
    os_versions: list[str] = Field(default_factory=list)
    archs: list[str] = Field(default_factory=list)
    device_type: str | None = None


class Extension(Model):
    uuid: str
    name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SessionLimits(Model):
    has_session_limits: bool | None = None
    spm: int | None = None
    remaining: int | None = None
    used: int | None = None
    additional_spm: int | None = None
    reset_time: int | None = None


class BrowserLimits(Model):
    has_browser_limits: bool | None = None
    parallel_browsers: int | None = None
    running: int | None = None
    available: int | None = None


__all__ = [
    "OS",
    "Arch",
    "DeviceType",
    "WaitUntil",
    "ProxyType",
    "RegionalPool",
    "ExportFormat",
    "ProfileOrdering",
    "MouseButton",
    "KeyModifier",
    "ScrollBehavior",
    "Noise",
    "MediaDevices",
    "Geolocation",
    "Fingerprint",
    "BrowserSettings",
    "ProxyGeo",
    "SharedProxy",
    "PremiumProxy",
    "ProxyLike",
    "DomainRoute",
    "StorageOptions",
    "OneTimeStartRequest",
    "ProfileCreateRequest",
    "ProfileStartRequest",
    "ProfileUpdateRequest",
    "ScrapeRequest",
    "InspectorPage",
    "Inspector",
    "Session",
    "ProfileRef",
    "ActiveProfile",
    "ProfileSummary",
    "Profile",
    "Cookie",
    "ScrapeResult",
    "BatchDeleteResult",
    "StopAllResult",
    "ProxyCountry",
    "ProxyRegion",
    "ProxyCity",
    "ProxyQuota",
    "SharedProxyQuota",
    "TrafficVolume",
    "TrafficStats",
    "Renderer",
    "Screen",
    "DeviceModel",
    "Extension",
    "SessionLimits",
    "BrowserLimits",
]
