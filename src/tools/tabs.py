# tab management tools - new tab, list, switch, close
from typing import Annotated

from pydantic import Field

from src.tools.base import ToolBase


class TabTools(ToolBase):
    """tools for multi-tab management"""

    def _register_tools(self) -> None:
        """register tab management tools"""
        self._register(self.new_tab)
        self._register(self.list_tabs)
        self._register(self.switch_tab)
        self._register(self.close_tab)

    async def new_tab(
        self,
        url: Annotated[
            str | None,
            Field(
                description="Absolute URL to open in the new tab, including the scheme. Omit to open a blank tab. Example: 'https://example.com'"
            ),
        ] = None,
    ) -> str:
        """Open a new browser tab and make it the active tab.

        Every subsequent tool acts on this new tab until switch_tab is called.
        Returns a confirmation containing the new tab's id, which is what
        switch_tab and close_tab take.
        """
        tab_id, tab = await self.session.create_tab(url)
        self.session.page = tab
        return f"Opened new tab: {tab_id}" + (f" at {url}" if url else "")

    async def list_tabs(self) -> str:
        """List every open tab with its id and current URL.

        Use to discover tab ids before switch_tab or close_tab, or to find a tab
        opened by the page itself (target=_blank, popups). Returns one line per
        tab as '  - <tab_id>: <url>', or 'No tabs open'.
        """
        tabs = self.session.get_all_tabs()
        if not tabs:
            return "No tabs open"
        lines = [f"Open tabs ({len(tabs)}):"]
        for tab_id, url in tabs.items():
            lines.append(f"  - {tab_id}: {url}")
        return "\n".join(lines)

    async def switch_tab(
        self,
        tab_id: Annotated[
            str,
            Field(
                description="Id of the tab to activate, exactly as returned by list_tabs or new_tab. Not a URL and not a positional index."
            ),
        ],
    ) -> str:
        """Make an existing tab the active one, so later tools act on it.

        Get valid ids from list_tabs. Returns a confirmation naming the tab
        switched to.
        """
        await self.session.switch_tab(tab_id)
        return f"Switched to {tab_id}"

    async def close_tab(
        self,
        tab_id: Annotated[
            str,
            Field(
                description="Id of the tab to close, exactly as returned by list_tabs or new_tab. Not a URL and not a positional index."
            ),
        ],
    ) -> str:
        """Close one tab by id, leaving the browser and other tabs running.

        Use stop_browser to end the whole session instead. Returns a confirmation
        naming the closed tab.
        """
        await self.session.close_tab(tab_id)
        return f"Closed tab: {tab_id}"
