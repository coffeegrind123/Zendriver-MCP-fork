"""Human-like interaction tools.

Exposes MCP tools that move the cursor and type with realistic timing,
wrapping the low-level primitives in ``src.humaninput``.
"""

from __future__ import annotations

from src.errors import ElementNotFoundError
from src.humaninput import (
    Point,
    human_click,
    human_type,
    keystroke_delays,
)
from src.tools._shadow_js import FIND_CLICK_COORDS_BY_TEXT_JS
from src.tools.base import ToolBase


class HumanInputTools(ToolBase):
    """Cursor paths and typing that look organic to bot-detection."""

    def _register_tools(self) -> None:
        self._register(self.human_click)
        self._register(self.human_type)
        self._register(self.estimated_typing_duration)

    async def human_click(
        self,
        selector: str | None = None,
        text: str | None = None,
        move_duration: float = 0.4,
    ) -> str:
        """Click an element with a humanish cursor path.

        Accepts a CSS selector, the numeric ID from ``get_interaction_tree``,
        or visible text. ``move_duration`` is how long the mouse takes to
        reach the target (default 0.4s). Text-mode is shadow-DOM aware:
        the walker finds the tightest interactive element matching the
        text (descending into nested shadowRoots when needed) and aims
        the cursor at its composed viewport rect, so clicks land on the
        real handler inside custom elements like ``<nes-button>``.
        """
        if selector:
            selector = self.resolve_selector(selector)
            elem = await self.get_element(selector)
            position = await elem.get_position()
            if position is None:
                return f"Error: Element '{selector}' has no visible position"
            cx, cy = position.center
            await human_click(
                self.session.page,
                target=Point(cx, cy),
                move_duration=move_duration,
            )
            return f"Human-clicked: {selector}"

        if text:
            import json as _json

            needle = _json.dumps(text)
            result = await self.run_js(
                f"(() => {{\n{FIND_CLICK_COORDS_BY_TEXT_JS}\nreturn findClickCoordsByText({needle});\n}})()"
            )
            if not isinstance(result, dict) or not result.get("ok"):
                raise ElementNotFoundError(f"text={text!r}")
            await human_click(
                self.session.page,
                target=Point(result["x"], result["y"]),
                move_duration=move_duration,
            )
            shadow_note = " (shadow)" if result.get("shadow") else ""
            return f"Human-clicked: {text}{shadow_note} <{result['tag']}>"

        return "Error: Provide selector or text"

    async def human_type(
        self,
        text: str,
        selector: str | None = None,
        wpm: int = 220,
    ) -> str:
        """Type ``text`` with per-keystroke delays modelling ``wpm``.

        If ``selector`` is provided the element is focused first via a
        human click. 220 wpm is a fast but plausible adult typist.
        """
        if selector:
            await self.human_click(selector=selector)
        await human_type(self.session.page, text=text, wpm=wpm)
        return f"Typed {len(text)} chars at ~{wpm} wpm"

    async def estimated_typing_duration(self, char_count: int, wpm: int = 220) -> float:
        """Return the expected seconds to type ``char_count`` chars at ``wpm``.

        Useful for scheduling follow-up actions without guessing.
        """
        return float(sum(keystroke_delays(char_count, wpm=wpm)))
