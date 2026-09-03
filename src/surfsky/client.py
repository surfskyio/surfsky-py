import copy
import logging
import os
from collections.abc import AsyncIterator, Iterable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from importlib.metadata import version
from types import TracebackType
from typing import TYPE_CHECKING, Any, Unpack

import anyio
import httpx

from .errors import ConfigurationError
from .resources import (
    Account,
    AsyncAccount,
    AsyncExtensions,
    AsyncFingerprints,
    AsyncProfiles,
    AsyncProxies,
    Extensions,
    Fingerprints,
    Profiles,
    Proxies,
)
from .resources.profiles import SessionOptions
from .transport import AUTH_HEADER, Spec, asend, send
from .types import Session

if TYPE_CHECKING:
    from .browser import Browser, BrowserPool, PoolHandler, PoolOptions, PoolOutcome

logger = logging.getLogger("surfsky")


def connection(api_token: str | None, base_url: str | None) -> tuple[str, dict[str, str]]:
    token = api_token or os.environ.get("SURFSKY_API_TOKEN")
    if not token:
        raise ConfigurationError("pass api_token or set SURFSKY_API_TOKEN")
    url = base_url or os.environ.get("SURFSKY_API_BASE_URL")
    if not url:
        raise ConfigurationError("pass base_url or set SURFSKY_API_BASE_URL")
    headers = {
        AUTH_HEADER: token,
        "Accept": "application/json",
        "User-Agent": f"surfsky-py/{version('surfsky')} httpx/{httpx.__version__}",
    }
    return url.rstrip("/"), headers


async def stop_session(client: "AsyncSurfsky", internal_uuid: str) -> None:
    fast = client.with_options(timeout=8, max_retries=0)
    with anyio.move_on_after(10, shield=True) as scope:
        try:
            await fast.profiles.stop(internal_uuid)
        except Exception as exc:
            logger.warning(f"failed to stop session {internal_uuid}: {exc}")
    if scope.cancelled_caught:
        logger.warning(f"stopping session {internal_uuid} timed out; it may keep billing")


class Surfsky:
    def __init__(
        self,
        api_token: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        self.base_url, headers = connection(api_token, base_url)
        self.http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.timeout: float | None = None
        self.headers: dict[str, str] = {}
        self._shared_http = False
        self._namespaces()

    def _namespaces(self) -> None:
        self.profiles = Profiles(self)
        self.proxies = Proxies(self)
        self.fingerprints = Fingerprints(self)
        self.extensions = Extensions(self)
        self.account = Account(self)

    def with_options(
        self,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> "Surfsky":
        clone = copy.copy(self)
        clone._shared_http = True
        if timeout is not None:
            clone.timeout = timeout
        if max_retries is not None:
            clone.max_retries = max_retries
        clone.headers = {**self.headers, **(headers or {})}
        clone._namespaces()
        return clone

    def call[T](self, spec: Spec[T]) -> T:
        return spec.result(self._send(spec))

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        spec = Spec(
            method,
            path,
            json=json,
            params=params,
            files=files,
            data=data,
            timeout=timeout,
        )
        return self._send(spec, headers)

    def _send(
        self, spec: Spec[Any], headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return send(
            self.http,
            spec,
            retries=self.max_retries,
            backoff=self.backoff_factor,
            timeout=spec.timeout if self.timeout is None else self.timeout,
            headers={**self.headers, **(headers or {})} or None,
        )

    @contextmanager
    def session(
        self, *, profile_uuid: str | None = None, **options: Unpack[SessionOptions]
    ) -> Iterator[Session]:
        if profile_uuid is None:
            session = self.profiles.start_one_time(**options)
        else:
            session = self.profiles.start(profile_uuid, **options)
        try:
            yield session
        finally:
            fast = self.with_options(timeout=8, max_retries=0)
            try:
                fast.profiles.stop(session)
            except Exception as exc:
                logger.warning(f"failed to stop session {session.internal_uuid}: {exc}")

    def close(self) -> None:
        if not self._shared_http:
            self.http.close()

    def __enter__(self) -> "Surfsky":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class AsyncSurfsky:
    def __init__(
        self,
        api_token: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        self.base_url, headers = connection(api_token, base_url)
        self.http = httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=timeout
        )
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.timeout: float | None = None
        self.headers: dict[str, str] = {}
        self._shared_http = False
        self._namespaces()

    def _namespaces(self) -> None:
        self.profiles = AsyncProfiles(self)
        self.proxies = AsyncProxies(self)
        self.fingerprints = AsyncFingerprints(self)
        self.extensions = AsyncExtensions(self)
        self.account = AsyncAccount(self)

    def with_options(
        self,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> "AsyncSurfsky":
        clone = copy.copy(self)
        clone._shared_http = True
        if timeout is not None:
            clone.timeout = timeout
        if max_retries is not None:
            clone.max_retries = max_retries
        clone.headers = {**self.headers, **(headers or {})}
        clone._namespaces()
        return clone

    async def call[T](self, spec: Spec[T]) -> T:
        return spec.result(await self._send(spec))

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        spec = Spec(
            method,
            path,
            json=json,
            params=params,
            files=files,
            data=data,
            timeout=timeout,
        )
        return await self._send(spec, headers)

    async def _send(
        self, spec: Spec[Any], headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return await asend(
            self.http,
            spec,
            retries=self.max_retries,
            backoff=self.backoff_factor,
            timeout=spec.timeout if self.timeout is None else self.timeout,
            headers={**self.headers, **(headers or {})} or None,
        )

    @asynccontextmanager
    async def session(
        self, *, profile_uuid: str | None = None, **options: Unpack[SessionOptions]
    ) -> AsyncIterator[Session]:
        if profile_uuid is None:
            session = await self.profiles.start_one_time(**options)
        else:
            session = await self.profiles.start(profile_uuid, **options)
        try:
            yield session
        finally:
            await stop_session(self, session.internal_uuid)

    def browser(
        self,
        *,
        profile_uuid: str | None = None,
        block_resources: frozenset[str] | set[str] | None = None,
        block_urls: Sequence[str] | None = None,
        **options: Unpack[SessionOptions],
    ) -> AbstractAsyncContextManager["Browser"]:
        from .browser import start_session

        return start_session(
            self,
            profile_uuid=profile_uuid,
            block_resources=block_resources,
            block_urls=block_urls,
            **options,
        )

    def browsers(self, **options: Unpack["PoolOptions"]) -> "BrowserPool":
        from .browser import BrowserPool

        return BrowserPool(self, **options)

    async def map[Item, Res](
        self,
        handler: "PoolHandler[Item, Res]",
        items: Iterable[Item],
        **options: Unpack["PoolOptions"],
    ) -> "list[PoolOutcome[Item, Res]]":
        async with self.browsers(**options) as browsers:
            return await browsers.map(handler, items)

    async def aclose(self) -> None:
        if not self._shared_http:
            await self.http.aclose()

    async def __aenter__(self) -> "AsyncSurfsky":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
