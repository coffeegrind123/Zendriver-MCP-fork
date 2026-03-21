# element interaction tools - click, type, clear, focus, select, upload
from typing import Optional
from zendriver import cdp

from src.tools.base import ToolBase
from src.errors import ElementNotFoundError


class ElementTools(ToolBase):
    """tools for interacting with page elements"""

    def _register_tools(self) -> None:
        """register element interaction tools"""
        self._mcp.tool()(self.click)
        self._mcp.tool()(self.type_text)
        self._mcp.tool()(self.clear_input)
        self._mcp.tool()(self.focus_element)
        self._mcp.tool()(self.select_option)
        self._mcp.tool()(self.upload_file)

    async def click(self, selector: Optional[str] = None, text: Optional[str] = None) -> str:
        """Click a visible element by CSS selector, numeric ID (from get_interaction_tree), or text content."""
        if selector:
            if selector.isdigit():
                selector = f'[data-zendriver-id="{selector}"]'

            check = await self.check_visibility(selector)
            if not check['found']:
                if '[data-zendriver-id=' in selector:
                     return "Error: ID not found. The page may have changed. Please run get_interaction_tree() again."
                raise ElementNotFoundError(selector)
            if check.get('hidden'):
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

    async def type_text(self, text: str, selector: str) -> str:
        """Type text into an element using CDP Input.insertText (no JS)."""

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



    async def clear_input(self, selector: str) -> str:
        """Clear an input field or contenteditable element."""
        if selector.isdigit():
            selector = f'[data-zendriver-id="{selector}"]'

        elem = await self.session.page.select(selector)
        if not elem:
            return f"Error: Element not found - {selector}"

        # Select all and delete
        await elem.apply("(el) => { el.focus(); document.execCommand('selectAll'); document.execCommand('delete'); }")
        return f"Cleared: {selector}"

    async def focus_element(self, selector: str) -> str:
        """Focus on an element."""
        if selector.isdigit():
            selector = f'[data-zendriver-id="{selector}"]'

        elem = await self.get_element(selector)
        await elem.focus()
        return f"Focused on: {selector}"

    async def select_option(self, selector: str, value: str) -> str:
        """Select an option from a dropdown."""
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

    async def upload_file(self, selector: str, file_path: str) -> str:
        """Upload a file to a file input."""
        if selector.isdigit():
            selector = f'[data-zendriver-id="{selector}"]'

        elem = await self.get_element(selector)
        await elem.send_file(file_path)
        return f"Uploaded file '{file_path}' to: {selector}"
