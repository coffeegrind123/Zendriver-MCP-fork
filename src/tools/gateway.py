# Search gateway - collapse the full tool surface into a tiny
# search -> (describe) -> call trio so the client only ever sees a handful of
# tools and discovers the rest on demand. This is the RAG-MCP pattern raised in
# ghidra-mcp#307: exposing ~56 tools to every conversation bloats context and
# degrades tool-selection accuracy; a retrieval front-end keeps the catalog one
# hop away without losing any capability. Opt-in (ZENDRIVER_MCP_GATEWAY=1 or
# ZENDRIVER_MCP_PROFILE=gateway); the default surface is unchanged.
import json
import os
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

# Tools kept natively visible so the common browse loop (start -> navigate ->
# read the interactive tree -> click/type -> read text) never needs a search
# hop. Override with ZENDRIVER_MCP_GATEWAY_CORE (comma/space separated names).
DEFAULT_CORE = [
    "start_browser",
    "stop_browser",
    "navigate",
    "get_interaction_tree",
    "click",
    "type_text",
    "get_text_content",
]


def _first_sentence(desc: str) -> str:
    """First sentence of a docstring, whitespace-collapsed, for a compact summary."""
    text = " ".join((desc or "").split())  # fold newlines + indentation to spaces
    i = text.find(". ")
    return text[: i + 1] if i != -1 else text


def _param_sig(schema: dict) -> str:
    """Compact 'a:int, b?:str' signature from a JSON Schema (? = optional)."""
    props = (schema or {}).get("properties", {}) or {}
    required = set((schema or {}).get("required", []) or [])
    parts = []
    for name, spec in props.items():
        typ = spec.get("type", spec.get("anyOf", "any"))
        if isinstance(typ, list):
            typ = "/".join(
                str(t.get("type", "any")) if isinstance(t, dict) else str(t) for t in typ
            )
        mark = "" if name in required else "?"
        parts.append(f"{name}{mark}:{typ}")
    return ", ".join(parts)


def _score(query: str, name: str, desc: str, group: str) -> int:
    """Dependency-free lexical relevance: name >> group > description."""
    q = query.lower().strip()
    terms = [w for w in "".join(c if c.isalnum() else " " for c in q).split() if w]
    if not terms:
        return 0
    name_l, desc_l, grp = name.lower(), (desc or "").lower(), (group or "").lower()
    score = 0
    for term in terms:
        if term == name_l:
            score += 100
        if term in name_l:
            score += 30
        if term in grp:
            score += 15
        if term in desc_l:
            score += 5
    if q in name_l:
        score += 40
    if q in desc_l:
        score += 8
    return score


def install_gateway(mcp: FastMCP, groups: "dict[str, list[str]]") -> None:
    """Strip every non-core tool from the live surface and add the search trio.

    The removed tools stay reachable: they are snapshotted into a private catalog
    that search_tools ranks over and call_tool dispatches to (with full schema
    validation via Tool.run). Nothing loses capability; the client just sees
    core + 3 meta-tools instead of the whole set.
    """
    tm = mcp._tool_manager
    catalog: dict[str, Any] = dict(tm._tools)  # name -> Tool, taken post-filter

    tool_group: dict[str, str] = {}
    for g, names in groups.items():
        for n in names:
            tool_group[n] = g

    core_env = os.environ.get("ZENDRIVER_MCP_GATEWAY_CORE", "").strip()
    core = (
        [c.strip() for c in core_env.replace(",", " ").split() if c.strip()]
        if core_env
        else list(DEFAULT_CORE)
    )
    keep = {c for c in core if c in catalog}

    for name in list(tm._tools):
        if name not in keep:
            tm.remove_tool(name)

    async def search_tools(
        query: Annotated[
            str,
            Field(
                description="What you want to do, in plain words, e.g. 'read cookies', 'wait for a network request', 'set the timezone', 'take a screenshot'. Example: 'fill a login form'"
            ),
        ],
        limit: Annotated[
            int,
            Field(
                description="Maximum number of matching tools to return, ranked best-first. Example: 8"
            ),
        ] = 8,
    ) -> str:
        """Find browser tools by capability. THIS SERVER HIDES MOST OF ITS TOOLS behind this search to keep your context small.

        Only a few core tools (start_browser, navigate, get_interaction_tree,
        click, type_text, get_text_content, stop_browser) are shown directly.
        Everything else — cookies, local storage, tabs, network/console logs,
        forms, screenshots, scrolling, JS execution, stealth, user-agent,
        geolocation, timezone/locale, security audit — is reached by searching
        here, then invoking the match with call_tool. Returns up to `limit`
        ranked 'name(params) — summary' lines; pass a name to call_tool, or to
        describe_tool for its full schema.
        """
        ranked = []
        for name, tool in catalog.items():
            s = _score(query, name, tool.description or "", tool_group.get(name, ""))
            if s > 0:
                ranked.append((s, name, tool))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        top = ranked[:limit]
        if not top:
            return (
                f"No tools matched '{query}'. Try broader words (e.g. 'storage', "
                "'network', 'input'), or call describe_tool('<name>') if you know it."
            )
        lines = [f"{len(top)} match(es) for '{query}' (invoke with call_tool):"]
        for _s, name, tool in top:
            sig = _param_sig(tool.parameters)
            grp = tool_group.get(name, "")
            native = "  [native — call directly]" if name in keep else ""
            lines.append(f"- {name}({sig}) [{grp}] — {_first_sentence(tool.description)}{native}")
        return "\n".join(lines)

    async def describe_tool(
        name: Annotated[
            str,
            Field(description="Exact tool name from a search_tools result. Example: 'set_cookie'"),
        ],
    ) -> str:
        """Show one tool's full description and parameter schema.

        Use when a search_tools summary is not enough to call the tool correctly:
        it returns the complete docstring and the JSON Schema of the parameters
        (types, defaults, which are required). Then invoke it with call_tool.
        """
        tool = catalog.get(name)
        if tool is None:
            return f"Unknown tool: {name}. Use search_tools to find the right name."
        return json.dumps(
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
            separators=(",", ":"),
            default=str,
        )

    async def call_tool(
        name: Annotated[
            str,
            Field(
                description="Exact tool name from search_tools/describe_tool. Example: 'get_cookies'"
            ),
        ],
        arguments: Annotated[
            dict,
            Field(
                description='Arguments object for that tool, matching its schema. Pass {} for a no-argument tool. Example: {"url": "https://example.com"}'
            ),
        ] = {},  # noqa: B006
    ) -> Any:
        """Invoke any of this server's tools by name — the path to every hidden capability.

        Look the tool up first with search_tools (and describe_tool for its exact
        schema), then call it here. `arguments` is validated against that tool's
        schema, so a missing or mistyped field returns a clear error instead of
        failing silently. Returns exactly what the underlying tool returns.
        """
        tool = catalog.get(name)
        if tool is None:
            return f"Unknown tool: {name}. Use search_tools to find the right name."
        try:
            return await tool.run(arguments or {})
        except Exception as e:  # surface validation / runtime faults as text
            return f"Error calling {name}: {type(e).__name__}: {e}"

    mcp.tool()(search_tools)
    mcp.tool()(describe_tool)
    mcp.tool()(call_tool)
