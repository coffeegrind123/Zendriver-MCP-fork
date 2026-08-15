# Context optimization

Exposing all 98 tool schemas to every conversation costs ~15.6k tokens and
measurably degrades tool selection (the "needle in a haystack" problem — see
[RAG-MCP](https://arxiv.org/abs/2505.03275)). This server offers three
**opt-in, non-breaking** ways to shrink the visible surface. With no environment
variables set, all 98 tools are exposed exactly as before.

## 1. Search gateway (≈98 → ≈10 visible tools)

Set `ZENDRIVER_MCP_GATEWAY=1` (or `ZENDRIVER_MCP_PROFILE=gateway`). The client
then sees only a small core loop plus three meta-tools:

| Tool | Purpose |
| --- | --- |
| `search_tools(query, limit=8)` | Find tools by capability; returns ranked `name(params) — summary` lines. |
| `describe_tool(name)` | Full description + JSON parameter schema for one tool. |
| `call_tool(name, arguments)` | Invoke any hidden tool by name, validated against its real schema. |

Every hidden tool stays fully reachable — the model discovers it on demand
instead of paying for 98 schemas up front. Core native tools default to
`start_browser, stop_browser, navigate, get_interaction_tree, click, type_text,
get_text_content`; override with `ZENDRIVER_MCP_GATEWAY_CORE`.

```
search_tools("read cookies")
→ - get_cookies() [storage] — Read the current page's cookies via document.cookie.
  - export_cookies(path?:str) [cookies] — Export all cookies incl. HTTP-only ...
call_tool("export_cookies", {"path": "/tmp/cookies.json"})
```

## 2. Profiles and groups

| Variable | Effect |
| --- | --- |
| `ZENDRIVER_MCP_PROFILE` | `full` (default), `minimal`, `browse`, `interact`, `scrape`, `stealth`, `audit`, `network` |
| `ZENDRIVER_MCP_GROUPS` | comma/space list of tool groups |
| `ZENDRIVER_MCP_ALLOW` | comma/space list of exact tool names to keep |
| `ZENDRIVER_MCP_DENY` | comma/space list of exact tool names to remove |

Groups: `browser navigation tabs elements query content storage logging forms
utils stealth humaninput emulation devtools lighthouse screencast accessibility
cookies network permissions proxy interception`. The `browser` lifecycle group
is always retained. `ALLOW`/`DENY` refine whatever the profile/groups selected.

## 3. Response diet

Leaner output by default (full behaviour still reachable):

- `get_interaction_tree` / `execute_js` emit minified JSON; the tree takes a
  `limit` (default 150).
- `get_content` / `get_text_content` paginate — `max_chars` (default 10000) +
  `offset`, with a `[chars X-Y of TOTAL] (next: offset=N)` header.
- `screenshot` downscales the returned image to 1024px wide
  (`full_resolution=true` opts out); `save_path` keeps full resolution.
- `get_network_logs` / `get_console_logs` default to 20 entries.

## Measured footprint

| Mode | Tools | Schema bytes | ~Tokens |
| --- | --- | --- | --- |
| Full | 98 | ~64 KB | ~15.6k |
| `minimal` profile | 25 | ~19 KB | ~4.8k |
| Gateway | 10 | ~10 KB | ~2.5k |

`tests/test_schema_budget.py` fails the build if either the full or the gateway
surface balloons past budget.
