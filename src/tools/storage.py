# storage tools - cookies and localStorage management
from typing import Annotated

from pydantic import Field

from src.tools.base import ToolBase


class StorageTools(ToolBase):
    """tools for cookies and browser storage"""

    def _register_tools(self) -> None:
        """register storage tools"""
        self._mcp.tool()(self.get_cookies)
        self._mcp.tool()(self.set_cookie)
        self._mcp.tool()(self.get_local_storage)
        self._mcp.tool()(self.set_local_storage)
        self._mcp.tool()(self.clear_storage)

    async def get_cookies(self) -> str:
        """Read the current page's cookies via document.cookie.

        Use to inspect session state after a login. HttpOnly cookies are
        deliberately invisible to document.cookie and will NOT appear here.
        Returns one 'name=value; name=value' string, or '(no cookies)'.
        """
        cookies = await self.run_js("document.cookie")
        return cookies if cookies else "(no cookies)"

    async def set_cookie(
        self,
        name: Annotated[str, Field(description="Cookie name. Example: 'session_id'")],
        value: Annotated[str, Field(description="Cookie value, unencoded. Example: 'abc123'")],
        domain: Annotated[
            str | None,
            Field(
                description="Domain to scope the cookie to. Omit to scope it to the current page's host. Example: '.example.com'"
            ),
        ] = None,
    ) -> str:
        """Set one cookie on the current page via document.cookie.

        Writes a session cookie scoped to the current page unless domain is given;
        expiry, Secure, and HttpOnly cannot be set through this path. Returns a
        confirmation of the name and value written.
        """
        safe_name = self.escape_js_string(name)
        safe_value = self.escape_js_string(value)
        cookie_str = f"{safe_name}={safe_value}"
        if domain:
            cookie_str += f"; domain={self.escape_js_string(domain)}"
        await self.run_js(f'document.cookie = "{cookie_str}"')
        return f"Cookie set: {name}={value}"

    async def get_local_storage(self) -> str:
        """Read every localStorage key and value for the current page's origin.

        Use to inspect client-side app state such as auth tokens or feature
        flags. sessionStorage is not included. Returns a JSON object string, or
        '{}' when the origin has no localStorage entries.
        """
        storage = await self.run_js("JSON.stringify(localStorage)")
        return storage if storage else "{}"

    async def set_local_storage(
        self,
        key: Annotated[str, Field(description="localStorage key to write. Example: 'theme'")],
        value: Annotated[
            str,
            Field(
                description="Value to store. localStorage holds strings only; serialise objects to JSON yourself. Example: 'dark'"
            ),
        ],
    ) -> str:
        """Write one localStorage item for the current page's origin.

        Overwrites any existing value under the same key. Most apps read
        localStorage at load, so reload_page after writing if the change should
        take effect. Returns a confirmation naming the key written.
        """
        safe_key = self.escape_js_string(key)
        safe_value = self.escape_js_string(value)
        await self.run_js(f'localStorage.setItem("{safe_key}", "{safe_value}")')
        return f"localStorage set: {key}"

    async def clear_storage(self) -> str:
        """Clear both localStorage and sessionStorage for the current origin.

        Use to reset client-side app state between runs. Cookies are NOT cleared —
        this usually will not log a session out on its own. Returns a
        confirmation string.
        """
        await self.run_js("localStorage.clear(); sessionStorage.clear()")
        return "Cleared localStorage and sessionStorage"
