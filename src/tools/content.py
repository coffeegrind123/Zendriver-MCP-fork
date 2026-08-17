# page content tools - get html, get text, scroll
from typing import Annotated, Literal

from pydantic import Field

from src.tools.base import ToolBase


class ContentTools(ToolBase):
    """tools for page content and scrolling"""

    def _register_tools(self) -> None:
        """register content tools"""
        self._register(self.get_content)
        self._register(self.get_text_content)
        self._register(self.get_interaction_tree)
        self._register(self.scroll)
        self._register(self.scroll_to_element)

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
        # Null-safe on purpose. `document.body.innerText` THROWS when the body
        # does not exist yet, and a thrown CDP exception reaches the caller as a
        # stack trace plus a parameter dump — which says nothing about the page
        # and sends them looking for a bad argument. Observed for real:
        # `TypeError: Cannot read properties of null (reading 'innerText')` on a
        # page that had simply not finished loading. Returning empty lets the
        # diagnosis below say which of the possible causes it actually is.
        text = await self.run_js("document.body ? document.body.innerText : ''")
        rendered = self._paginate(str(text or ""), max_chars, offset)
        if text:
            return rendered
        # An empty result is the one answer this tool cannot leave ambiguous.
        # "[chars 0-0 of 0]" reads as "this page has no text", and a caller that
        # believes it goes looking for a consent wall or a different source. The
        # usual cause is that nothing has rendered yet, which is a completely
        # different fix, so say which one it is.
        return f"{rendered}\n{await self._empty_text_diagnosis()}"

    async def _empty_text_diagnosis(self) -> str:
        """Explain an empty innerText: not-yet-rendered, no body, or genuinely blank."""
        try:
            info = await self.run_js("""
                (function() {
                    return {
                        readyState: document.readyState,
                        url: location.href,
                        hasBody: !!document.body,
                        htmlChars: (document.documentElement
                            ? document.documentElement.outerHTML.length : 0),
                    };
                })()
            """)
        except Exception as exc:  # the diagnosis must never replace the result with an error
            return f"(no visible text; could not diagnose: {exc})"

        if not isinstance(info, dict):
            return "(no visible text; page did not answer a diagnostic query)"

        ready = info.get("readyState", "unknown")
        html_chars = info.get("htmlChars", 0)
        detail = f"readyState={ready}, html={html_chars} chars, url={info.get('url', 'unknown')}"
        if ready != "complete":
            return (
                f"(no visible text yet — the page is still loading: {detail}. "
                "Call wait_for_network or navigate with a larger settle, then read again.)"
            )
        if not info.get("hasBody"):
            return f"(no visible text — the document has no body element: {detail})"
        if html_chars > 2000:
            return (
                f"(no visible text, but the page has markup: {detail}. "
                "The text may be in an iframe, a shadow root, or hidden by CSS — "
                "try get_content or get_interaction_tree.)"
            )
        return f"(no visible text — the page really is blank: {detail})"

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
        links: Annotated[
            bool,
            Field(
                description="Include each link's target as 'h' (a path when same-origin). Off by default because it roughly doubles the output; turn it on when you need to follow or identify a link. Example: true"
            ),
        ] = False,
    ) -> str:
        """List the page's interactive elements, each with a short numeric id.

        The cheapest way to see what can be clicked or typed into: it walks the
        DOM including shadow roots for buttons, links, and inputs. The numeric ids
        it assigns are accepted directly by click, type_text, and the other
        element tools in place of a CSS selector. Ids are invalidated by any
        navigation or re-render, so re-run after those. Returns compact JSON,
        capped at limit with a note when the cap is hit, or an error string if
        the page could not be analysed.

        Pass links=true to get each link's target as `h` — a path when it is
        same-origin, a full URL otherwise. Following a link then means a navigate
        instead of a click-and-hope, which matters when the link text is
        unhelpful or missing. It is off by default because it roughly doubles the
        output; measured at +116% on a 125-link search page. Nothing is emitted
        for javascript: handlers, bare "#" anchors, or links back to the current
        page, none of which can take you anywhere.
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
        # The walker always collects link targets; whether the CALLER pays for
        # them is decided here, because the cost is in the returned JSON rather
        # than in the walk.
        if not links:
            for element in tree:
                if isinstance(element, dict):
                    element.pop("h", None)
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
