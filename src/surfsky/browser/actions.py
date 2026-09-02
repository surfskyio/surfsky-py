from collections.abc import Awaitable, Callable
from typing import Any

from pydantic.alias_generators import to_camel

from ..types import KeyModifier, MouseButton, ScrollBehavior

type Send = Callable[[str, dict[str, Any] | None], Awaitable[Any]]


def cdp_params(**given: Any) -> dict[str, Any]:
    return {to_camel(key): value for key, value in given.items() if value is not None}


class Keyboard:
    def __init__(self, send: Send) -> None:
        self._send = send

    async def type(self, text: str) -> Any:
        return await self._send("Human.type", {"text": text})

    async def press(
        self,
        key: str,
        *,
        modifiers: list[KeyModifier] | None = None,
        delay: float | None = None,
    ) -> Any:
        params = cdp_params(key=key, modifiers=modifiers, delay=delay)
        return await self._send("Human.press", params)


class Mouse:
    def __init__(self, send: Send) -> None:
        self._send = send

    async def move(self, x: float, y: float) -> Any:
        return await self._send("Human.moveTo", cdp_params(x=x, y=y))

    async def click(
        self,
        x: float,
        y: float,
        *,
        button: MouseButton | None = None,
        click_count: int | None = None,
        modifiers: list[KeyModifier] | None = None,
        pre_delay: float | None = None,
        post_delay: float | None = None,
    ) -> Any:
        params = cdp_params(
            x=x,
            y=y,
            button=button,
            click_count=click_count,
            modifiers=modifiers,
            pre_delay=pre_delay,
            post_delay=post_delay,
        )
        return await self._send("Human.click", params)

    async def down(self, x: float, y: float, *, button: MouseButton | None = None) -> Any:
        return await self._send("Human.mouseDown", cdp_params(x=x, y=y, button=button))

    async def up(self, x: float, y: float, *, button: MouseButton | None = None) -> Any:
        return await self._send("Human.mouseUp", cdp_params(x=x, y=y, button=button))

    async def wheel(
        self, delta_x: float | None = None, delta_y: float | None = None
    ) -> Any:
        return await self._send(
            "Human.wheel", cdp_params(delta_x=delta_x, delta_y=delta_y)
        )

    async def drag(
        self,
        *,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        button: MouseButton | None = None,
    ) -> Any:
        params = cdp_params(
            start_x=start_x, start_y=start_y, end_x=end_x, end_y=end_y, button=button
        )
        return await self._send("Human.drag", params)


class Actions:
    async def send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError

    @property
    def keyboard(self) -> Keyboard:
        return Keyboard(self.send)

    @property
    def mouse(self) -> Mouse:
        return Mouse(self.send)

    async def click(
        self,
        selector: str,
        *,
        button: MouseButton | None = None,
        click_count: int | None = None,
        modifiers: list[KeyModifier] | None = None,
        wait_for_visible: bool | None = None,
        scroll_into_view: bool | None = None,
        pre_delay: float | None = None,
        post_delay: float | None = None,
        timeout: float | None = None,
    ) -> Any:
        params = cdp_params(
            selector=selector,
            button=button,
            click_count=click_count,
            modifiers=modifiers,
            wait_for_visible=wait_for_visible,
            scroll_into_view=scroll_into_view,
            pre_delay=pre_delay,
            post_delay=post_delay,
            timeout=timeout,
        )
        return await self.send("Human.click", params)

    async def dblclick(
        self,
        selector: str,
        *,
        button: MouseButton | None = None,
        modifiers: list[KeyModifier] | None = None,
        wait_for_visible: bool | None = None,
        scroll_into_view: bool | None = None,
        pre_delay: float | None = None,
        post_delay: float | None = None,
        timeout: float | None = None,
    ) -> Any:
        params = cdp_params(
            selector=selector,
            button=button,
            modifiers=modifiers,
            wait_for_visible=wait_for_visible,
            scroll_into_view=scroll_into_view,
            pre_delay=pre_delay,
            post_delay=post_delay,
            timeout=timeout,
        )
        return await self.send("Human.dblclick", params)

    async def hover(
        self,
        selector: str,
        *,
        wait_for_visible: bool | None = None,
        scroll_into_view: bool | None = None,
        timeout: float | None = None,
    ) -> Any:
        params = cdp_params(
            selector=selector,
            wait_for_visible=wait_for_visible,
            scroll_into_view=scroll_into_view,
            timeout=timeout,
        )
        return await self.send("Human.moveTo", params)

    async def type(self, selector: str, text: str) -> Any:
        await self.click(selector)
        return await self.keyboard.type(text)

    async def fill(self, selector: str, text: str) -> Any:
        await self.click(selector, click_count=3)
        return await self.keyboard.type(text)

    async def scroll(
        self,
        *,
        delta_x: float | None = None,
        delta_y: float | None = None,
        duration: float | None = None,
    ) -> Any:
        params = cdp_params(delta_x=delta_x, delta_y=delta_y, duration=duration)
        return await self.send("Human.scroll", params)

    async def scroll_into_view(
        self, selector: str, *, behavior: ScrollBehavior | None = None
    ) -> Any:
        params = cdp_params(selector=selector, behavior=behavior)
        return await self.send("Human.scrollIntoView", params)

    async def scroll_to(
        self,
        *,
        x: float | None = None,
        y: float | None = None,
        behavior: ScrollBehavior | None = None,
    ) -> Any:
        return await self.send("Human.scrollTo", cdp_params(x=x, y=y, behavior=behavior))
