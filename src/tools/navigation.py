# navigation tools - navigate, back, forward, reload, page info
from typing import Annotated

from pydantic import Field

from src.tools.base import ToolBase


class NavigationTools(ToolBase):
    """tools for page navigation"""

    def _register_tools(self) -> None:
        """register navigation tools"""
        self._register(self.navigate)
        self._register(self.go_back)
        self._register(self.go_forward)
        self._register(self.reload_page)
        self._register(self.get_page_info)

    async def navigate(
        self,
        url: Annotated[
            str,
            Field(
                description="Absolute URL to load, including the scheme. Relative paths and bare hostnames are not resolved. Example: 'https://example.com/pricing'"
            ),
        ],
    ) -> str:
        """Navigate the active tab to a URL and wait for the page to load.

        Use this before any element query, click, or content read — those tools
        fail if no page is loaded. Call start_browser first if the browser is not
        running. Returns a confirmation string naming the URL that was loaded.
        """
        await self.session.navigate(url)
        return f"Navigated to {url}"

    async def go_back(self) -> str:
        """Go back one entry in the active tab's history, as the browser back button does.

        Only useful after at least one navigation in this tab. Returns a
        confirmation string; it does not report the resulting URL, so follow with
        get_page_info if you need to know where you landed.
        """
        await self.session.page.back()
        return "Navigated back"

    async def go_forward(self) -> str:
        """Go forward one entry in the active tab's history, as the browser forward button does.

        Only meaningful after go_back. Returns a confirmation string; use
        get_page_info if you need the resulting URL.
        """
        await self.session.page.forward()
        return "Navigated forward"

    async def reload_page(self) -> str:
        """Reload the active tab at its current URL.

        Use to pick up server-side changes or to retry a page that rendered
        incompletely. Element IDs from get_interaction_tree do not survive a
        reload — re-run it afterwards. Returns a confirmation string.
        """
        await self.session.page.reload()
        return "Page reloaded"

    async def get_page_info(self) -> str:
        """Get the active tab's current URL and title.

        Use to confirm where a navigation, redirect, or history move actually
        landed. Returns two lines: 'URL: <url>' and 'Title: <title>', each
        'unknown' if the page has not reported it yet.
        """
        page = self.session.page
        url = getattr(page, "url", "unknown")
        title = getattr(page, "title", "unknown")
        return f"URL: {url}\nTitle: {title}"
