# tools package - modular browser automation tools for Zendriver MCP server
import os

from mcp.server.fastmcp import FastMCP

from src.tools.base import ToolBase
from src.tools.browser import BrowserTools
from src.tools.navigation import NavigationTools
from src.tools.tabs import TabTools
from src.tools.elements import ElementTools
from src.tools.query import QueryTools
from src.tools.content import ContentTools
from src.tools.storage import StorageTools
from src.tools.logging import LoggingTools
from src.tools.forms import FormTools
from src.tools.utils import UtilityTools
from src.tools.stealth import StealthTools

# initialize the MCP server
mcp = FastMCP("Zendriver MCP")

# register all tool modules and keep instances for backwards compatibility.
# Capture, per module, the tool names it registers so tools can be grouped for
# profile filtering and the search gateway (both below) without a hand-kept map
# that would drift as tools are added.
_MODULES = [
    ("browser", BrowserTools),
    ("navigation", NavigationTools),
    ("tabs", TabTools),
    ("elements", ElementTools),
    ("query", QueryTools),
    ("content", ContentTools),
    ("storage", StorageTools),
    ("logging", LoggingTools),
    ("forms", FormTools),
    ("utils", UtilityTools),
    ("stealth", StealthTools),
]

_TOOL_GROUPS: dict[str, list[str]] = {}
_INSTANCES: dict[str, ToolBase] = {}
for _group, _cls in _MODULES:
    _before = set(mcp._tool_manager._tools)
    _INSTANCES[_group] = _cls(mcp)
    _TOOL_GROUPS[_group] = sorted(set(mcp._tool_manager._tools) - _before)

# backwards-compatible instance handles
_browser_tools = _INSTANCES["browser"]
_navigation_tools = _INSTANCES["navigation"]
_tab_tools = _INSTANCES["tabs"]
_element_tools = _INSTANCES["elements"]
_query_tools = _INSTANCES["query"]
_content_tools = _INSTANCES["content"]
_storage_tools = _INSTANCES["storage"]
_logging_tools = _INSTANCES["logging"]
_form_tools = _INSTANCES["forms"]
_utility_tools = _INSTANCES["utils"]
_stealth_tools = _INSTANCES["stealth"]


def _declare_explicit_required(server: FastMCP) -> None:
    """Emit an explicit "required" array on every tool schema.

    pydantic omits "required" entirely when every parameter has a default, so a
    model cannot tell "nothing is mandatory" apart from "this schema forgot to
    say". The empty array states it outright. Do not remove: it cannot be
    expressed from the function signatures.
    """
    for tool in server._tool_manager.list_tools():
        params = tool.parameters
        if isinstance(params, dict) and params.get("type") == "object" and "required" not in params:
            params["required"] = []


def _split_env(name: str) -> set[str]:
    raw = os.environ.get(name, "")
    return {tok.strip() for tok in raw.replace(",", " ").split() if tok.strip()}


# Named profiles map to sets of tool GROUPS. Selecting a profile or a group list
# narrows the exposed surface; ALLOW/DENY then refine by exact tool name. The
# browser lifecycle group is always kept so a session can start/stop the browser.
_PROFILES: dict[str, set[str]] = {
    "full": set(_TOOL_GROUPS),
    "minimal": {"browser", "navigation", "content", "query", "elements"},
    "browse": {"browser", "navigation", "tabs", "content", "query"},
    "interact": {"browser", "navigation", "tabs", "elements", "query", "content", "forms"},
    "scrape": {"browser", "navigation", "tabs", "content", "query", "logging"},
    "stealth": {"browser", "navigation", "tabs", "elements", "query", "content", "forms", "stealth"},
}


def _apply_tool_filter(server: FastMCP) -> None:
    """Narrow the exposed tool surface from env (issue #307: too many tools).

    ZENDRIVER_MCP_PROFILE  one of: full, minimal, browse, interact, scrape, stealth
    ZENDRIVER_MCP_GROUPS   comma/space list of groups (browser, navigation, tabs,
                           elements, query, content, storage, logging, forms,
                           utils, stealth)
    ZENDRIVER_MCP_ALLOW    comma/space list of exact tool names to keep
    ZENDRIVER_MCP_DENY     comma/space list of exact tool names to remove
    Unset -> every tool is exposed (unchanged default). PROFILE=gateway is
    handled by the search gateway below, not here.
    """
    profile = os.environ.get("ZENDRIVER_MCP_PROFILE", "").strip().lower()
    groups = _split_env("ZENDRIVER_MCP_GROUPS")
    allow = _split_env("ZENDRIVER_MCP_ALLOW")
    deny = _split_env("ZENDRIVER_MCP_DENY")
    if profile == "gateway" or not (profile or groups or allow or deny):
        return

    all_tools = set(server._tool_manager._tools)
    narrowed = bool(profile and profile != "full") or bool(groups)

    keep: set[str] = set()
    if profile and profile != "full":
        keep |= {t for g in _PROFILES.get(profile, set(_TOOL_GROUPS)) for t in _TOOL_GROUPS.get(g, [])}
    for g in groups:
        keep |= set(_TOOL_GROUPS.get(g, []))
    if narrowed:
        keep |= set(_TOOL_GROUPS.get("browser", []))  # lifecycle always available
    else:
        keep = set(all_tools)  # ALLOW/DENY alone operate on the full set

    if allow:
        keep = (keep & allow) if narrowed else set(allow)
    if deny:
        keep -= deny
    keep &= all_tools

    for name in sorted(all_tools - keep):
        server._tool_manager.remove_tool(name)


_apply_tool_filter(mcp)

# Optional search gateway: hide most tools behind search_tools/describe_tool/
# call_tool so the client sees ~10 tools instead of ~56 (issue #307 / RAG-MCP).
_gateway_on = os.environ.get("ZENDRIVER_MCP_GATEWAY", "").strip().lower() in {"1", "true", "yes", "on"}
if _gateway_on or os.environ.get("ZENDRIVER_MCP_PROFILE", "").strip().lower() == "gateway":
    from src.tools.gateway import install_gateway

    install_gateway(mcp, _TOOL_GROUPS)

_declare_explicit_required(mcp)

# export individual tool functions for backwards compatibility
# browser lifecycle
start_browser = _browser_tools.start_browser
stop_browser = _browser_tools.stop_browser
get_browser_status = _browser_tools.get_browser_status

# navigation
navigate = _navigation_tools.navigate
go_back = _navigation_tools.go_back
go_forward = _navigation_tools.go_forward
reload_page = _navigation_tools.reload_page
get_page_info = _navigation_tools.get_page_info

# tabs
new_tab = _tab_tools.new_tab
list_tabs = _tab_tools.list_tabs
switch_tab = _tab_tools.switch_tab
close_tab = _tab_tools.close_tab

# elements
click = _element_tools.click
type_text = _element_tools.type_text
clear_input = _element_tools.clear_input
focus_element = _element_tools.focus_element
select_option = _element_tools.select_option
upload_file = _element_tools.upload_file

# query
find_element = _query_tools.find_element
find_all_elements = _query_tools.find_all_elements
get_element_text = _query_tools.get_element_text
get_element_attribute = _query_tools.get_element_attribute
find_buttons = _query_tools.find_buttons
find_inputs = _query_tools.find_inputs

# content
get_content = _content_tools.get_content
get_text_content = _content_tools.get_text_content
get_interaction_tree = _content_tools.get_interaction_tree
scroll = _content_tools.scroll
scroll_to_element = _content_tools.scroll_to_element

# storage
get_cookies = _storage_tools.get_cookies
set_cookie = _storage_tools.set_cookie
get_local_storage = _storage_tools.get_local_storage
set_local_storage = _storage_tools.set_local_storage
clear_storage = _storage_tools.clear_storage

# logging
get_network_logs = _logging_tools.get_network_logs
get_console_logs = _logging_tools.get_console_logs
clear_logs = _logging_tools.clear_logs
wait_for_network = _logging_tools.wait_for_network
wait_for_request = _logging_tools.wait_for_request

# forms
fill_form = _form_tools.fill_form
submit_form = _form_tools.submit_form
press_key = _form_tools.press_key
press_enter = _form_tools.press_enter
mouse_click = _form_tools.mouse_click

# utils
screenshot = _utility_tools.screenshot
execute_js = _utility_tools.execute_js
wait = _utility_tools.wait
wait_for_element = _utility_tools.wait_for_element
run_security_audit = _utility_tools.run_security_audit

__all__ = [
    # mcp server
    "mcp",
    # base class
    "ToolBase",
    # tool classes
    "BrowserTools",
    "NavigationTools",
    "TabTools",
    "ElementTools",
    "QueryTools",
    "ContentTools",
    "StorageTools",
    "LoggingTools",
    "FormTools",
    "UtilityTools",
    # individual tool functions
    "start_browser", "stop_browser", "get_browser_status",
    "navigate", "go_back", "go_forward", "reload_page", "get_page_info",
    "new_tab", "list_tabs", "switch_tab", "close_tab",
    "click", "type_text", "clear_input", "focus_element", "select_option", "upload_file",
    "find_element", "find_all_elements", "get_element_text", "get_element_attribute",
    "find_buttons", "find_inputs",
    "get_content", "get_text_content", "get_interaction_tree", "scroll", "scroll_to_element",
    "get_cookies", "set_cookie", "get_local_storage", "set_local_storage", "clear_storage",
    "get_network_logs", "get_console_logs", "clear_logs", "wait_for_network", "wait_for_request",
    "fill_form", "submit_form", "press_key", "press_enter", "mouse_click",
    "screenshot", "execute_js", "wait", "wait_for_element", "run_security_audit",
]
