# form and input tools - fill form, submit, keyboard, mouse
import json
from typing import Annotated

from pydantic import Field

from src.tools.base import ToolBase


class FormTools(ToolBase):
    """tools for forms and input handling"""

    def _register_tools(self) -> None:
        """register form and input tools"""
        self._mcp.tool()(self.fill_form)
        self._mcp.tool()(self.submit_form)
        self._mcp.tool()(self.press_key)
        self._mcp.tool()(self.press_enter)
        self._mcp.tool()(self.mouse_click)

    async def fill_form(
        self,
        form_data: Annotated[
            str,
            Field(
                description='A JSON object string mapping CSS selector to the value to type, NOT a nested object and not field names. Example: \'{"#email": "user@example.com", "#password": "hunter2"}\''
            ),
        ],
    ) -> str:
        """Fill several form fields in one call, clearing each before typing.

        Faster than repeated clear_input/type_text pairs. Selectors that match
        nothing are skipped silently, so check the count in the result. Does not
        submit — follow with submit_form or click. Returns how many fields were
        filled and which selectors they were.
        """
        data = json.loads(form_data)
        filled = []

        for selector, value in data.items():
            elem = await self.session.page.select(selector)
            if elem:
                await elem.clear_input()
                await elem.send_keys(str(value))
                filled.append(selector)

        return f"Filled {len(filled)} field(s): {', '.join(filled)}"

    async def submit_form(
        self,
        selector: Annotated[
            str,
            Field(
                description="CSS selector of the form element. Defaults to the page's first form. Example: '#login-form'"
            ),
        ] = "form",
    ) -> str:
        """Submit a form by calling its native submit() method.

        Note that submit() does NOT fire the form's submit handlers, so on
        JavaScript-driven forms clicking the submit button — or press_enter — is
        more reliable. Silently does nothing if the selector matches no form.
        Returns a confirmation naming the selector.
        """
        safe_sel = self.escape_js_string(selector)
        await self.run_js(f'document.querySelector("{safe_sel}")?.submit()')
        return f"Form submitted: {selector}"

    async def press_key(
        self,
        key: Annotated[
            str,
            Field(
                description="Key name for named keys ('Enter', 'Tab', 'Escape', 'Backspace', 'Delete', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space', 'Home', 'End', 'PageUp', 'PageDown'), or a single character for literal keys. Example: 'Enter'"
            ),
        ],
        selector: Annotated[
            str | None,
            Field(
                description="CSS selector of the element to send the key to. Omit to target whatever currently has focus. Example: '#search'"
            ),
        ] = None,
    ) -> str:
        """Press one key with full keydown, keypress, and keyup event simulation.

        Use when a framework listens for real key events and type_text's bulk
        insert is ignored. Enter additionally submits an enclosing form or clicks
        a button, and Tab advances focus. Returns a confirmation naming the key
        and target.
        """
        safe_key = self.escape_js_string(key)

        # map common key names to their codes
        key_codes = {
            "Enter": 13,
            "Tab": 9,
            "Escape": 27,
            "Backspace": 8,
            "Delete": 46,
            "ArrowUp": 38,
            "ArrowDown": 40,
            "ArrowLeft": 37,
            "ArrowRight": 39,
            "Space": 32,
            " ": 32,
            "Home": 36,
            "End": 35,
            "PageUp": 33,
            "PageDown": 34,
        }

        if selector:
            safe_sel = self.escape_js_string(selector)
            target_js = f'document.querySelector("{safe_sel}")'
        else:
            target_js = "document.activeElement"

        await self.run_js(f'''
            (function() {{
                const el = {target_js};
                if (!el) return;

                const key = "{safe_key}";
                const keyCode = {json.dumps(key_codes)};
                const code = keyCode[key] || key.charCodeAt(0);

                // create event options
                const eventOptions = {{
                    key: key,
                    code: key.length === 1 ? "Key" + key.toUpperCase() : key,
                    keyCode: code,
                    which: code,
                    charCode: key === "Enter" ? 13 : 0,
                    bubbles: true,
                    cancelable: true,
                    composed: true
                }};

                // dispatch keydown
                const keydownEvent = new KeyboardEvent("keydown", eventOptions);
                const keydownResult = el.dispatchEvent(keydownEvent);

                // dispatch keypress for character keys (deprecated but some frameworks need it)
                if (key.length === 1 || key === "Enter") {{
                    const keypressEvent = new KeyboardEvent("keypress", eventOptions);
                    el.dispatchEvent(keypressEvent);
                }}

                // special handling for Enter key
                if (key === "Enter") {{
                    // check if element is in a form
                    const form = el.closest("form");
                    if (form && el.tagName !== "TEXTAREA") {{
                        // trigger form submission
                        const submitEvent = new Event("submit", {{ bubbles: true, cancelable: true }});
                        const submitted = form.dispatchEvent(submitEvent);
                        if (submitted && !submitEvent.defaultPrevented) {{
                            // find submit button and click it, or submit form
                            const submitBtn = form.querySelector('[type="submit"], button:not([type="button"])');
                            if (submitBtn) {{
                                submitBtn.click();
                            }}
                        }}
                    }}
                    // also dispatch click on buttons
                    if (el.tagName === "BUTTON" || el.getAttribute("role") === "button") {{
                        el.click();
                    }}
                }}

                // special handling for Tab key
                if (key === "Tab" && keydownResult) {{
                    // move focus to next focusable element
                    const focusable = Array.from(document.querySelectorAll(
                        'button, [href], input:not([type="hidden"]), select, textarea, [tabindex]:not([tabindex="-1"])'
                    )).filter(e => !e.disabled && e.offsetParent !== null);

                    const currentIndex = focusable.indexOf(el);
                    if (currentIndex !== -1 && currentIndex < focusable.length - 1) {{
                        focusable[currentIndex + 1].focus();
                    }}
                }}

                // dispatch keyup
                const keyupEvent = new KeyboardEvent("keyup", eventOptions);
                el.dispatchEvent(keyupEvent);
            }})()
        ''')
        return f"Pressed key: {key}" + (f" on {selector}" if selector else "")

    async def press_enter(
        self,
        selector: Annotated[
            str | None,
            Field(
                description="CSS selector of the element to send Enter to. Omit to target whatever currently has focus. Example: '#search'"
            ),
        ] = None,
    ) -> str:
        """Press Enter, submitting an enclosing form or activating a button.

        A convenience wrapper for press_key('Enter'); use that for any other key.
        More reliable than submit_form on JavaScript-driven forms, because it
        fires the events the page actually listens for. Returns a confirmation
        naming the key and target.
        """
        return await self.press_key("Enter", selector)

    async def mouse_click(
        self,
        x: Annotated[
            int,
            Field(
                description="Horizontal position in CSS pixels from the viewport's left edge. Example: 640"
            ),
        ],
        y: Annotated[
            int,
            Field(
                description="Vertical position in CSS pixels from the viewport's top edge. Example: 400"
            ),
        ],
    ) -> str:
        """Click at absolute viewport coordinates rather than at an element.

        A last resort for canvas, map, and custom widgets that expose nothing
        selectable — prefer click for anything with a selector or numeric id,
        since coordinates break whenever the layout shifts. The point must be
        within the current viewport; scroll first if not. Returns a confirmation
        naming the coordinates.
        """
        await self.session.page.mouse_click(x, y)
        return f"Clicked at ({x}, {y})"
