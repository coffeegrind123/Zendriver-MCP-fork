# page content tools - get html, get text, scroll
from typing import Annotated, Literal

from pydantic import Field

from src.tools.base import ToolBase


class ContentTools(ToolBase):
    """tools for page content and scrolling"""

    def _register_tools(self) -> None:
        """register content tools"""
        self._mcp.tool()(self.get_content)
        self._mcp.tool()(self.get_text_content)
        self._mcp.tool()(self.get_interaction_tree)
        self._mcp.tool()(self.scroll)
        self._mcp.tool()(self.scroll_to_element)

    async def get_content(self) -> str:
        """Get the page's full rendered HTML, including markup and attributes.

        Use when you need selectors, attributes, or hidden markup. Prefer
        get_text_content when you only want readable text, and
        get_interaction_tree when you want something to click — both are far
        cheaper. Returns the HTML source, truncated at 50000 characters.
        """
        content = await self.session.page.get_content()
        return self.truncate(content, 50000)

    async def get_text_content(self) -> str:
        """Get the page's visible text, without any HTML markup.

        Use for reading or extracting page copy. Text hidden by CSS is excluded,
        and no selectors are returned, so use get_interaction_tree if you intend
        to interact. Returns document.body.innerText, truncated at 30000
        characters.
        """
        text = await self.run_js('document.body.innerText')
        return self.truncate(text, 30000)

    async def get_interaction_tree(self) -> str:
        """List the page's interactive elements, each with a short numeric id.

        The cheapest way to see what can be clicked or typed into: it walks the
        DOM including shadow roots for buttons, links, and inputs. The numeric ids
        it assigns are accepted directly by click, type_text, and the other
        element tools in place of a CSS selector. Ids are invalidated by any
        navigation or re-render, so re-run after those. Returns indented JSON, or
        an error string if the page could not be analysed.
        """
        import json
        import os

        # Load the JS walker script
        script_path = os.path.join(os.path.dirname(__file__), "..", "static", "js", "dom_walker.js")
        if not os.path.exists(script_path):
            return "Error: dom_walker.js not found in static/js"

        with open(script_path, "r", encoding="utf-8") as f:
            js_code = f.read()

        try:
            tree = await self.run_js(js_code)
            return json.dumps(tree, separators=(",", ":"))
        except Exception as e:
            return f"Error analyzing page: {str(e)}"

    async def scroll(
        self,
        direction: Annotated[Literal["down", "up"], Field(description="Direction to scroll the page. Example: 'down'")] = "down",
        amount: Annotated[int, Field(description="Distance to scroll in CSS pixels. Example: 500")] = 500,
    ) -> str:
        """Scroll the page vertically by a pixel amount.

        Use to reach content below the fold or to trigger lazy loading. To reach
        one known element instead, scroll_to_element is more reliable. Returns a
        confirmation naming the direction and distance scrolled.
        """
        page = self.session.page
        if direction == "down":
            await page.scroll_down(amount)
            return f"Scrolled down {amount}px"
        elif direction == "up":
            await page.scroll_up(amount)
            return f"Scrolled up {amount}px"
        return f"Invalid direction: {direction}"

    async def scroll_to_element(
        self,
        selector: Annotated[str, Field(description="CSS selector of the element to scroll into view. Example: '#checkout-button'")],
    ) -> str:
        """Smooth-scroll the page until one element is centred in the viewport.

        Use before clicking something below the fold, or to trigger lazy loading
        at a known point. Silently does nothing if the selector matches no
        element. Returns a confirmation naming the selector.
        """
        safe_sel = self.escape_js_string(selector)
        await self.run_js(f'document.querySelector("{safe_sel}")?.scrollIntoView({{behavior: "smooth", block: "center"}})')
        return f"Scrolled to: {selector}"
