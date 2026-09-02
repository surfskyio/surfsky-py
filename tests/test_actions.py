import pytest

from surfsky.browser.actions import Actions


class Recorder(Actions):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send(self, method, params=None):
        self.calls.append((method, params or {}))
        return {"ok": True}


@pytest.mark.anyio
async def test_click_sends_camelcase_params():
    actions = Recorder()
    await actions.click("#a", click_count=2, wait_for_visible=True, pre_delay=0.2)
    assert actions.calls == [
        ("Human.click", {"selector": "#a", "clickCount": 2, "waitForVisible": True, "preDelay": 0.2})
    ]


@pytest.mark.anyio
async def test_mouse_works_at_a_point():
    actions = Recorder()
    await actions.mouse.click(10, 20, button="left")
    await actions.mouse.move(1, 2)
    await actions.mouse.down(1, 2)
    await actions.mouse.up(3, 4, button="right")
    await actions.mouse.wheel(0, 120)
    await actions.mouse.drag(start_x=1, start_y=2, end_x=3, end_y=4)
    assert actions.calls == [
        ("Human.click", {"x": 10, "y": 20, "button": "left"}),
        ("Human.moveTo", {"x": 1, "y": 2}),
        ("Human.mouseDown", {"x": 1, "y": 2}),
        ("Human.mouseUp", {"x": 3, "y": 4, "button": "right"}),
        ("Human.wheel", {"deltaX": 0, "deltaY": 120}),
        ("Human.drag", {"startX": 1, "startY": 2, "endX": 3, "endY": 4}),
    ]


@pytest.mark.anyio
async def test_hover_scroll_and_scroll_into_view():
    actions = Recorder()
    await actions.hover("#a", scroll_into_view=True)
    await actions.scroll(delta_y=100, duration=1.0, delta_x=5)
    await actions.scroll(delta_x=300)  # the server defaults duration and deltaY
    await actions.scroll_into_view(".x", behavior="smooth")
    assert actions.calls == [
        ("Human.moveTo", {"selector": "#a", "scrollIntoView": True}),
        ("Human.scroll", {"deltaX": 5, "deltaY": 100, "duration": 1.0}),
        ("Human.scroll", {"deltaX": 300}),
        ("Human.scrollIntoView", {"selector": ".x", "behavior": "smooth"}),
    ]


@pytest.mark.anyio
async def test_type_clicks_first_and_fill_selects_first():
    actions = Recorder()
    await actions.type("#q", "hi")
    await actions.fill("#q", "yo")
    assert actions.calls == [
        ("Human.click", {"selector": "#q"}),
        ("Human.type", {"text": "hi"}),
        ("Human.click", {"selector": "#q", "clickCount": 3}),
        ("Human.type", {"text": "yo"}),
    ]


@pytest.mark.anyio
async def test_keyboard_type_and_press():
    actions = Recorder()
    await actions.keyboard.type("hi")
    await actions.keyboard.press("Enter")
    await actions.keyboard.press("a", modifiers=["Control"])
    assert actions.calls == [
        ("Human.type", {"text": "hi"}),
        ("Human.press", {"key": "Enter"}),
        ("Human.press", {"key": "a", "modifiers": ["Control"]}),
    ]
