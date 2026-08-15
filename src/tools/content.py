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

    async def get_content(
        self,
        max_chars: Annotated[
            int,
            Field(
                description="Maximum characters of HTML to return in this call. Pass a larger value or page with offset to read more. Example: 10000"
            ),
        ] = 10000,
        offset: Annotated[
            int,
            Field(
                description="Character offset to start from, for paging through a large page. Example: 0"
            ),
        ] = 0,
    ) -> str:
        """Get the page's full rendered HTML, including markup and attributes.

        Use when you need selectors, attributes, or hidden markup. Prefer
        get_text_content when you only want readable text, and
        get_interaction_tree when you want something to click — both are far
        cheaper. Returns a '[chars X-Y of TOTAL]' header then the requested
        slice; when more remains the header names the next offset to request.
        """
        content = await self.session.page.get_content()
        return self._paginate(content, max_chars, offset)

    async def get_text_content(
        self,
        max_chars: Annotated[
            int,
            Field(
                description="Maximum characters of text to return in this call. Pass a larger value or page with offset to read more. Example: 10000"
            ),
        ] = 10000,
        offset: Annotated[
            int,
            Field(
                description="Character offset to start from, for paging through long text. Example: 0"
            ),
        ] = 0,
    ) -> str:
        """Get the page's visible text, without any HTML markup.

        Use for reading or extracting page copy. Text hidden by CSS is excluded,
        and no selectors are returned, so use get_interaction_tree if you intend
        to interact. Same '[chars X-Y of TOTAL]' pagination contract as
        get_content: the header reports total length and the next offset.
        """
        text = await self.run_js("document.body.innerText")
        return self._paginate(str(text), max_chars, offset)

    @staticmethod
    def _paginate(text: str, max_chars: int, offset: int) -> str:
        """Slice ``text`` with a one-line header so agents can page deliberately."""
        max_chars = max(1, max_chars)
        total = len(text)
        offset = min(max(0, offset), total)
        chunk = text[offset : offset + max_chars]
        end = offset + len(chunk)
        header = f"[chars {offset}-{end} of {total}]"
        if end < total:
            header += f" (next: offset={end})"
        return f"{header}\n{chunk}"

    async def get_interaction_tree(
        self,
        limit: Annotated[
            int,
            Field(
                description="Maximum number of interactive elements to return. Raise it on dense pages; a note reports when the cap was hit. Example: 150"
            ),
        ] = 150,
    ) -> str:
        """List the page's interactive elements, each with a short numeric id.

        The cheapest way to see what can be clicked or typed into: it walks the
        DOM including shadow roots for buttons, links, and inputs. The numeric ids
        it assigns are accepted directly by click, type_text, and the other
        element tools in place of a CSS selector. Ids are invalidated by any
        navigation or re-render, so re-run after those. Returns compact JSON,
        capped at limit with a note when the cap is hit, or an error string if
        the page could not be analysed.
        """
        import json
        import os

        # Load the JS walker script
        script_path = os.path.join(os.path.dirname(__file__), "..", "static", "js", "dom_walker.js")
        if not os.path.exists(script_path):
            return "Error: dom_walker.js not found in static/js"

        with open(script_path, encoding="utf-8") as f:
            js_code = f.read()

        try:
            tree = await self.run_js(js_code)
        except Exception as e:
            return f"Error analyzing page: {str(e)}"

        tree = tree or []
        limit = max(1, limit)
        total = len(tree)
        payload = json.dumps(tree[:limit], separators=(",", ":"))
        if total > limit:
            return f"[showing {limit} of {total} elements; raise limit for more]\n{payload}"
        return payload

    async def scroll(
        self,
        direction: Annotated[
            Literal["down", "up"],
            Field(description="Direction to scroll the page. Example: 'down'"),
        ] = "down",
        amount: Annotated[
            int, Field(description="Distance to scroll in CSS pixels. Example: 500")
        ] = 500,
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
        selector: Annotated[
            str,
            Field(
                description="CSS selector of the element to scroll into view. Example: '#checkout-button'"
            ),
        ],
    ) -> str:
        """Smooth-scroll the page until one element is centred in the viewport.

        Use before clicking something below the fold, or to trigger lazy loading
        at a known point. Silently does nothing if the selector matches no
        element. Returns a confirmation naming the selector.
        """
        safe_sel = self.escape_js_string(selector)
        await self.run_js(
            f'document.querySelector("{safe_sel}")?.scrollIntoView({{behavior: "smooth", block: "center"}})'
        )
        return f"Scrolled to: {selector}"
