import inspect
import random
import secrets
import threading
from collections.abc import Awaitable, Callable, Iterable
from typing import cast

from .types import ProxyLike


class ProxySource:
    def pick(self) -> ProxyLike | None | Awaitable[ProxyLike | None]:
        raise NotImplementedError


type ProxyFactory = Callable[[], ProxyLike | None | Awaitable[ProxyLike | None]]
type ProxyInput = ProxyLike | ProxySource | ProxyFactory


def proxy_list(proxies: Iterable[ProxyLike], owner: str) -> list[ProxyLike]:
    if isinstance(proxies, str):
        raise TypeError(f"{owner} takes an iterable of proxies, not a single URL")
    proxies = list(proxies)
    if not proxies:
        raise ValueError(f"{owner} needs at least one proxy")
    return proxies


class ProxyCycle(ProxySource):
    def __init__(self, proxies: Iterable[ProxyLike]) -> None:
        self.proxies = proxy_list(proxies, "ProxyCycle")
        self._lock = threading.Lock()
        self._index = 0

    def pick(self) -> ProxyLike:
        with self._lock:
            proxy = self.proxies[self._index % len(self.proxies)]
            self._index += 1
        return proxy


class ProxyRandom(ProxySource):
    def __init__(self, proxies: Iterable[ProxyLike]) -> None:
        self.proxies = proxy_list(proxies, "ProxyRandom")

    def pick(self) -> ProxyLike:
        return random.choice(self.proxies)


class ProxyTemplate(ProxySource):
    """A proxy URL from a format string: ``{session}`` is a fresh hex id, ``{n}``
    a counter. Covers the sticky-session syntax providers put in the username::

        ProxyTemplate("http://user-cc-us-sessid-{session}:pw@gate.example.com:7000")

    Literal braces are ``{{`` and ``}}``.
    """

    def __init__(self, template: str) -> None:
        try:
            template.format(session="0" * 12, n=0)
        except (KeyError, IndexError) as exc:
            raise ValueError(f"unknown placeholder in proxy template: {exc}") from None
        self.template = template
        self._lock = threading.Lock()
        self._count = 0

    def pick(self) -> str:
        with self._lock:
            n = self._count
            self._count += 1
        return self.template.format(session=secrets.token_hex(6), n=n)


def validate_proxy(proxy: ProxyInput | None) -> None:
    if proxy is None or isinstance(proxy, ProxyLike | ProxySource) or callable(proxy):
        return
    raise TypeError(
        "proxy must be a URL, a SharedProxy/PremiumProxy/ProxyGeo selector, a "
        "ProxySource or a zero-argument callable"
    )


def pick(proxy: ProxyInput | None) -> ProxyLike | None | Awaitable[ProxyLike | None]:
    validate_proxy(proxy)
    if proxy is None or isinstance(proxy, ProxyLike):
        return proxy
    if isinstance(proxy, ProxySource):
        return proxy.pick()
    return proxy()


def resolve_proxy(proxy: ProxyInput | None) -> ProxyLike | None:
    picked = pick(proxy)
    if inspect.isawaitable(picked):
        if inspect.iscoroutine(picked):
            picked.close()
        raise TypeError("an async proxy factory needs the async client (AsyncSurfsky)")
    return picked


async def aresolve_proxy(proxy: ProxyInput | None) -> ProxyLike | None:
    picked = pick(proxy)
    if inspect.isawaitable(picked):
        return cast(ProxyLike | None, await picked)
    return picked
