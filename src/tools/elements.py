# element interaction tools - click, type, clear, focus, select, upload
from typing import Annotated

from pydantic import Field
from zendriver import cdp

from src.errors import ElementNotFoundError
from src.tools._shadow_js import CLICK_SHADOW_HOST_JS, DESCRIBE_SHADOW_JS
from src.tools.base import ToolBase


class ElementTools(ToolBase):
    """tools for interacting with page elements"""

    def _register_tools(self) -> None:
        """register element interaction tools"""
        self._register(self.click)
        self._register(self.click_shadow)
        self._register(self.describe_shadow)
        self._register(self.type_text)
        self._register(self.clear_input)
        self._register(self.focus_element)
        self._register(self.select_option)
        self._register(self.upload_file)

    async def click(
        self,
        selector: Annotated[
            str | None,
            Field(
                description="CSS selector, or a bare numeric id from get_interaction_tree (passed as a string). Example: '#submit' or '42'"
            ),
        ] = None,
        text: Annotated[
            str | None,
            Field(
                description="Visible text of the element to click, matched as a best match. Used only when selector is omitted. Example: 'Add to cart'"
            ),
        ] = None,
    ) -> str:
        """Click one visible element, located by selector, numeric id, or visible text.

        Pass exactly one of selector or text. Numeric ids come from
        get_interaction_tree and are the most reliable route on complex pages;
        they are invalidated by any navigation or re-render. Returns a
        confirmation naming what was clicked, or an error string if the element is
        missing, hidden, or neither argument was given.
        """
        if selector:
            if selector.isdigit():
                selector = f'[data-zendriver-id="{selector}"]'

            check = await self.check_visibility(selector)
            if not check["found"]:
                if "[data-zendriver-id=" in selector:
                    return "Error: ID not found. The page may have changed. Please run get_interaction_tree() again."
                raise ElementNotFoundError(selector)
            if check.get("hidden"):
                return f"Error: Element '{selector}' is hidden. Cannot click."
            elem = await self.session.page.select(selector)
            if elem:
                await elem.click()
                return f"Clicked: {selector}"
            raise ElementNotFoundError(selector)
        elif text:
            elem = await self.get_element_by_text(text)
            await elem.click()
            return f"Clicked: {text}"
        return "Error: Provide selector or text"

    async def click_shadow(
        self,
        selector: Annotated[
            str,
            Field(
                description="CSS selector, or a bare numeric id, of the outer custom element in the light DOM whose shadow tree holds the real control. Example: 'nes-button' or '5'"
            ),
        ],
        max_depth: Annotated[
            int, Field(description="How many nested shadow roots to descend through. Example: 6")
        ] = 6,
    ) -> str:
        """Click the deepest interactive element inside a custom element's shadow DOM.

        Use when a page wraps a real <button> / [role="radio"] /
        [role="checkbox"] in one or more open shadow roots (<nes-button>,
        <sds-radio>, <lion-input> and friends). selector should match the outer
        custom element in the light DOM; this recurses through every nested
        shadowRoot up to max_depth and dispatches a composed click on the first
        interactive descendant. Returns an error when the host is missing or no
        inner interactive element exists.
        """
        import json as _json

        selector = self.resolve_selector(selector)
        safe_sel = _json.dumps(selector)
        result = await self.run_js(
            f"(() => {{\n{CLICK_SHADOW_HOST_JS}\nreturn clickShadowHost({safe_sel}, {int(max_depth)});\n}})()"
        )
        if not isinstance(result, dict) or not result.get("ok"):
            reason = result.get("reason") if isinstance(result, dict) else "unknown"
            if reason == "host_not_found":
                raise ElementNotFoundError(selector)
            return f"Error: no interactive element inside {selector} ({reason})"
        tag = result["tag"]
        role = result.get("role")
        role_info = f" role={role}" if role else ""
        return f"Shadow-clicked: <{tag}{role_info}> inside {selector}"

    async def describe_shadow(
        self,
        selector: Annotated[
            str,
            Field(
                description="CSS selector, or a bare numeric id, of the custom element whose nested shadow tree to dump. Example: 'lion-input' or '9'"
            ),
        ],
        max_depth: Annotated[
            int, Field(description="How many nested shadow roots to descend through. Example: 6")
        ] = 6,
    ) -> dict:
        """Dump a custom element's nested shadow-DOM tree for debugging.

        Returns a condensed JSON tree — each node has tag, id, role, type, text,
        a 'light' array of light-DOM children and a 'shadow' array for the
        element's shadowRoot children (when open). Use this when find_buttons /
        find_inputs are not surfacing a control you can see; the result names the
        exact nested-host path so you can target click_shadow.
        """
        import json as _json

        selector = self.resolve_selector(selector)
        safe_sel = _json.dumps(selector)
        result = await self.run_js(
            f"(() => {{\n{DESCRIBE_SHADOW_JS}\nreturn describeShadow({safe_sel}, {int(max_depth)});\n}})()"
        )
        if not isinstance(result, dict) or not result.get("ok"):
            raise ElementNotFoundError(selector)
        return {"selector": selector, "tree": result["tree"]}

    async def type_text(
        self,
        text: Annotated[
            str,
            Field(
                description="Literal text to insert. Inserted as one chunk, so it fires no per-key events. Example: 'user@example.com'"
            ),
        ],
        selector: Annotated[
            str,
            Field(
                description="CSS selector, or a bare numeric id from get_interaction_tree (passed as a string). Example: '#email' or '7'"
            ),
        ],
    ) -> str:
        """Type text into an input by inserting it via CDP, without executing JavaScript.

        Clicks the element to focus it first, so the element must be visible.
        Appends to any existing value — call clear_input first to replace it.
        Because it inserts in one operation, use press_key for anything that needs
        real keydown/keyup events. Returns a confirmation naming the target.
        """

        # Make selector consistent
        if selector.isdigit():
            selector = f'[data-zendriver-id="{selector}"]'

        # Focus the element by clicking it
        await self.click(selector)

        # Now insert text via CDP
        # `self._tab` refers to the current Tab object,
        # which exposes the CDP command interface
        await self.session.page.send(cdp.input_.insert_text(text))

        return f"Typed into {selector}"

    async def clear_input(
        self,
        selector: Annotated[
            str,
            Field(
                description="CSS selector, or a bare numeric id from get_interaction_tree (passed as a string). Example: '#search' or '3'"
            ),
        ],
    ) -> str:
        """Empty an input, textarea, or contenteditable element.

        Use before type_text to replace a value rather than append to it. Works
        by selecting all and deleting, so the page sees a real edit and its
        change handlers fire. Returns a confirmation naming the element, or an
        error string if the selector matched nothing.
        """
        if selector.isdigit():
            selector = f'[data-zendriver-id="{selector}"]'

        elem = await self.session.page.select(selector)
        if not elem:
            return f"Error: Element not found - {selector}"

        # Select all and delete
        await elem.apply(
            "(el) => { el.focus(); document.execCommand('selectAll'); document.execCommand('delete'); }"
        )
        return f"Cleared: {selector}"

    async def focus_element(
        self,
        selector: Annotated[
            str,
            Field(
                description="CSS selector, or a bare numeric id from get_interaction_tree (passed as a string). Example: '#search' or '3'"
            ),
        ],
    ) -> str:
        """Move keyboard focus to an element without clicking it.

        Use before press_key when a click would trigger unwanted behaviour, or to
        open a widget that reacts to focus. type_text focuses on its own, so this
        is not needed first. Returns a confirmation naming the element.
        """
        if selector.isdigit():
            selector = f'[data-zendriver-id="{selector}"]'

        elem = await self.get_element(selector)
        await elem.focus()
        return f"Focused on: {selector}"

    async def select_option(
        self,
        selector: Annotated[
            str,
            Field(
                description="CSS selector of the <select> element, or a bare numeric id from get_interaction_tree. Example: '#country' or '9'"
            ),
        ],
        value: Annotated[
            str,
            Field(
                description="The option's value attribute, NOT its visible label. Read the markup with get_content if unsure. Example: 'FI'"
            ),
        ],
    ) -> str:
        """Choose an option in a native <select> dropdown by its value attribute.

        Sets the value and dispatches a bubbling change event, so framework
        listeners fire. Only works on native <select>; custom dropdowns built from
        divs need click instead. Returns a confirmation naming the value and
        element.
        """
        if selector.isdigit():
            selector = f'[data-zendriver-id="{selector}"]'

        await self.get_element(selector)
        safe_sel = self.escape_js_string(selector)
        safe_val = self.escape_js_string(value)
        await self.run_js(f'''
            const select = document.querySelector("{safe_sel}");
            select.value = "{safe_val}";
            select.dispatchEvent(new Event("change", {{ bubbles: true }}));
        ''')
        return f"Selected '{value}' in: {selector}"

    async def upload_file(
        self,
        selector: Annotated[
            str,
            Field(
                description="CSS selector of the <input type=\"file\"> element, or a bare numeric id from get_interaction_tree. Example: 'input[type=\"file\"]' or '5'"
            ),
        ],
        file_path: Annotated[
            str,
            Field(
                description="Absolute path to the file, as seen by the machine running this server, not the browser host. Example: '/home/user/documents/id.png'"
            ),
        ],
    ) -> str:
        """Attach a local file to a file input, without opening the OS file picker.

        The path must exist on the server's filesystem. Submit the surrounding
        form afterwards with submit_form or click. Returns a confirmation naming
        the file and target element.
        """
        if selector.isdigit():
            selector = f'[data-zendriver-id="{selector}"]'

        elem = await self.get_element(selector)
        await elem.send_file(file_path)
        return f"Uploaded file '{file_path}' to: {selector}"
