# Zendriver-MCP

A powerful MCP (Model Context Protocol) server for browser automation using Zendriver - an undetectable, async-first browser automation framework. Built specifically for LLM-powered automation with a focus on **token efficiency**.

## Why Zendriver?

Most browser automation tools (Selenium, Playwright, Puppeteer) use WebDriver protocol, which is easily detected by anti-bot systems. **Zendriver is different:**

| Feature | WebDriver-based | Zendriver |
|---------|-----------------|-----------|
| Detection | Easily detected via `navigator.webdriver` | Undetectable - no WebDriver flags |
| Protocol | WebDriver (standardized, detectable) | CDP (Chrome DevTools Protocol) |
| Async | Sync-first, async bolted on | Async-first architecture |
| Bot Protection | Blocked by Cloudflare, PerimeterX, etc. | Bypasses most protections |

**Use cases where Zendriver is better than others:**
- Automating sites with bot protection (Cloudflare, Akamai, etc.)
- Scraping dynamic SPAs that block traditional automation
- Testing authenticated workflows on protected sites
- Building AI agents that interact with the real web

## Features

- **Undetectable** - Uses Chrome DevTools Protocol, bypassing WebDriver detection
- **Token-Optimized DOM Walker** - 78% reduction in token usage vs raw HTML
- **35+ Essential Tools** - Focused, powerful browser automation capabilities
- **Modern Web Support** - Works with contenteditable divs, SPAs, and dynamic content
- **Smart Element Handling** - Auto-skips hidden elements, provides selector suggestions
- **CDP Network Logging** - Real-time network request and console log capture, its super easy to create endpoint based scrappers as llms can directly access the network logs
- **Security Auditing** - Comprehensive security analysis tool
- **Authenticated Proxy Support** - HTTP and SOCKS5 proxies with credentials handled via CDP Fetch domain
- **Bundled Extensions** - uBlock Origin Lite + I Still Don't Care About Cookies, auto-provisioned and installed on every launch



## Bundled Extensions

Every `start_browser` installs two extensions, so pages arrive without ads or
consent walls eating the DOM:

| Extension | Source | Local dir |
|---|---|---|
| uBlock Origin Lite (uBOL) | `uBlockOrigin/uBOL-home`, latest `*.chromium.zip` | `extensions/ubol` |
| I Still Don't Care About Cookies | `OhMyGuus/I-Still-Dont-Care-About-Cookies`, latest `ISDCAC-chrome-source.zip` | `extensions/isdcac` |

Each is downloaded from its GitHub release on first launch and cached on disk;
delete a directory to force a re-fetch of the current release. If a download
fails the launch still proceeds — the reason is printed to stderr rather than
silently leaving you unprotected.

Two constraints drive the implementation, both verified against Chrome
150.0.7871.124:

- **`--load-extension` is dead.** Chrome ≥137 ignores it, and the old
  `--disable-features=DisableLoadExtensionCommandLineSwitch` escape hatch no
  longer works — a one-file MV3 test extension passed that way never appears in
  `/json/list` or in the profile's `Preferences`. Extensions are therefore
  installed after startup via the CDP `Extensions.loadUnpacked` command, which
  requires launching with `--enable-unsafe-extension-debugging`.
- **Manifest v2 is refused.** `gorhill/uBlock`'s `.chromium.zip` is still MV2, and
  loading it returns *"Cannot install extension because it uses an unsupported
  manifest version."* Hence uBOL, which is the MV3 build.

## Proxy Support

Full support for authenticated HTTP and SOCKS5 proxies. Credentials are handled transparently via CDP Fetch domain — no proxy extensions needed.

```python
# No auth
start_browser(proxy="socks5://host:port")

# With authentication (HTTP or SOCKS5)
start_browser(proxy="http://user:pass@host:port")
```

How it works under the hood:
- Credentials are stripped from the URL before passing to Chrome's `--proxy-server` flag (Chrome doesn't support embedded credentials)
- CDP `Fetch.enable(handle_auth_requests=True)` intercepts 407 Proxy Authentication challenges
- `Fetch.authRequired` handler responds with stored credentials automatically
- `Fetch.requestPaused` handler continues non-auth requests transparently
- `cdp.fetch` is pre-added to `tab.enabled_domains` to prevent zendriver's auto-registration from overriding the auth config

## Usage with Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "zendriver": {
      "command": "python",
      "args": ["path/to/Zendriver-MCP/run.py"]
    }
  }
}
```

## HTTP transport — one browser, many callers

stdio spawns a server per client, so the browser dies with the client and two
clients get two Chromes. `--transport streamable-http` keeps **one** long-lived
server that anything can call:

```bash
python run.py --transport streamable-http --host 127.0.0.1 --port 8931
```

```jsonc
// any MCP client that speaks HTTP
{"mcpServers": {"zendriver": {"type": "http", "url": "http://127.0.0.1:8931/mcp"}}}
```

`BrowserSession` is a process-wide singleton, so the tab, cookies and login
survive across separate connections — which is what makes a shell wrapper
possible at all: one command navigates, the next one reads the page.

The default is **stateless** MCP sessions plus JSON responses, so a single POST
carries a whole tool call with no initialize handshake and no SSE framing:

```bash
curl -s -X POST http://127.0.0.1:8931/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"navigate","arguments":{"url":"https://example.com"}}}'
```

Pass `--stateful` for per-client MCP sessions (initialize required, replies framed
as SSE). Two things worth knowing before you wire this up:

- **`FASTMCP_HOST` / `FASTMCP_PORT` do nothing.** In mcp SDK ≥ 1.28 `FastMCP.__init__`
  passes its own keyword defaults straight to `Settings`, bypassing the environment;
  with `FASTMCP_PORT=8931` exported the server binds **8000** and says so. `run.py`
  therefore sets `mcp.settings` directly.
- **Terminating the server does not close Chrome.** Call `stop_browser` before you
  kill it, or the browser is left resident with its profile open.

## Opening the browser on demand

Set `ZENDRIVER_MCP_AUTOSTART_BROWSER=1` and the first tool that needs a browser
opens one, instead of returning *"Browser not started. Call start_browser
first."* — one retry, once, then the real error if the launch itself failed.

It exists for the token cost of the alternative: `start_browser` has the largest
schema on the server (2.3 KB, ~570 tokens), and a client that only wants the
common browse loop otherwise carries all of it to satisfy a precondition it has
no decision to make about.

Off by default, deliberately: a client that needs headless, a proxy, or a
persistent profile must be able to call `start_browser` with its own arguments
before anything launches Chrome with defaults. `start_browser` itself is never
auto-retried, so a failing launch reports the launch error rather than looping.

Concurrent first calls are serialised on one lock, so a cold server that gets
several tool calls at once still launches exactly one browser.

Making this work turned up an older gap: 51 of the 98 tools — including the whole
browse loop — were registered straight onto FastMCP rather than through
`ToolBase._register`, so they had neither the auto-start path nor the per-tool
**time budget** that exists precisely so one hung CDP call cannot freeze the
session. Every tool now goes through the registrar; the emitted schemas are
byte-identical before and after (verified by hashing `tools/list` across the
change).

## Token Optimization Protocol

### The Problem

Traditional approaches send raw HTML or verbose element trees to LLMs:
```html
<!-- Raw HTML: ~50KB, thousands of tokens -->
<div class="css-1dbjc4n r-1awozwy r-18u37iz r-1h0z5md" data-testid="toolBar">
  <button class="css-18t94o4 css-1dbjc4n r-1niwhzg r-42olwf" aria-label="Search">
    <svg viewBox="0 0 24 24" class="r-jwli3a r-4qtqp9">
      <g><path d="M21.53 20.47l-3.66-3.66C19.195..."></path></g>
    </svg>
  </button>
</div>
```

### The Solution

Our DOM walker produces **compact, semantic output**:
```json
{"id": 1, "t": "btn", "l": "Search", "r": "hdr"}
```

### Optimization Techniques

| Technique | Before | After | Reduction |
|-----------|--------|-------|-----------|
| **Compact Keys** | `tagName`, `label`, `region` | `t`, `l`, `r` | ~60% |
| **Smart Labels** | "(unlabeled)" everywhere | Inferred from aria/text/placeholder | ~40% fewer elements |
| **SVG Filtering** | Include path, g, circle, etc. | Skip SVG internals | ~30% fewer elements |
| **Noise Removal** | Nested interactive children | Skip redundant elements | ~20% fewer |
| **Type Compression** | `button`, `checkbox`, `radio` | `btn`, `chk`, `rad` | ~50% |

### Real-World Results

**Perplexity.ai homepage:**
- Raw HTML: ~45KB (~11,000 tokens)
- Standard element dump: 95 elements (~2,800 tokens)
- Our optimized output: 17 elements (~400 tokens)
- **Total reduction: 96% fewer tokens**

### Label Inference Priority

Instead of showing "(unlabeled)", we infer labels from multiple sources:
1. `aria-label` attribute
2. `aria-labelledby` reference
3. Associated `<label>` element
4. `placeholder` attribute
5. Direct text content
6. `title` attribute
7. `alt` attribute (for images)

### Region Detection

Elements are tagged with their page region for context:
- `hdr` - Header/banner area
- `nav` - Navigation
- `main` - Main content
- `side` - Sidebar/aside
- `ftr` - Footer
- `dlg` - Modal/dialog

### Usage

```python
# Get the optimized interaction tree
tree = get_interaction_tree()
# Returns: [{"id": 1, "t": "btn", "l": "Submit", "r": "main"}, ...]

# Click using the numeric ID
click("1")  # Clicks the element with id=1

# Type into an input
type_text("Hello", "3")  # Types into element with id=3
```

This approach lets LLMs work with web pages using minimal context while maintaining full functionality.

## Tool Surface / Context Optimization

The server registers ~56 tools. Exposing all of them to every conversation
bloats context and degrades tool-selection accuracy (see the RAG-MCP discussion
in ghidra-mcp#307). Three env-configurable modes let a client shrink the visible
surface without the server losing any capability. All are **off by default** —
with no env set, all 56 tools are exposed exactly as before.

### 1. Search gateway (biggest win: ~56 → ~10 visible tools)

Set `ZENDRIVER_MCP_GATEWAY=1` (or `ZENDRIVER_MCP_PROFILE=gateway`). The client
then sees only a small core loop plus three meta-tools:

- `search_tools(query, limit=8)` — find tools by capability ("read cookies",
  "wait for a network request"); returns ranked `name(params) — summary` lines.
- `describe_tool(name)` — full description + JSON parameter schema for one tool.
- `call_tool(name, arguments)` — invoke any hidden tool by name; `arguments` is
  validated against that tool's real schema, so bad input fails loudly.

Every hidden tool stays fully reachable — the model just discovers it on demand
instead of paying for all 56 schemas up front. Core native tools default to
`start_browser, stop_browser, navigate, get_interaction_tree, click, type_text,
get_text_content`; override with `ZENDRIVER_MCP_GATEWAY_CORE`.

### 2. Profiles and groups (static subset)

| Variable | Effect |
| --- | --- |
| `ZENDRIVER_MCP_PROFILE` | `full` (default), `minimal`, `browse`, `interact`, `scrape`, `stealth`, `audit`, `network` |
| `ZENDRIVER_MCP_GROUPS` | comma/space list of groups: `browser navigation tabs elements query content storage logging forms utils stealth humaninput emulation devtools lighthouse screencast accessibility cookies network permissions proxy interception` |
| `ZENDRIVER_MCP_ALLOW` | comma/space list of exact tool names to keep |
| `ZENDRIVER_MCP_DENY` | comma/space list of exact tool names to remove |

The `browser` (lifecycle) group is always retained so a session can start/stop
the browser. `ALLOW`/`DENY` refine whatever the profile/groups selected, e.g.
`ZENDRIVER_MCP_PROFILE=browse ZENDRIVER_MCP_DENY=go_forward`.

### 3. Response diet (leaner output by default)

Output-side token cost, not just schema cost:

- `get_interaction_tree` and `execute_js` emit minified JSON (no pretty-print
  indentation); the tree also takes a `limit` (default 150) and notes when hit.
- `get_content` / `get_text_content` are paginated — `max_chars` (default 10000)
  + `offset`, with a `[chars X-Y of TOTAL] (next: offset=Y)` header so the agent
  pulls only what it needs and knows how much remains (was a flat 50k/30k cut).
- `screenshot` downscales the returned image to 1024px wide (vision tokens scale
  with pixel area); `full_resolution=true` opts out, and `save_path` files always
  keep full resolution.
- `get_network_logs` / `get_console_logs` default to 20 entries (was 50); network
  lines now include the resource type.

Measured surface (2026-08-15, 98-tool build): full ≈ 64 KB (~15.6k tokens); the
`minimal` profile ≈ 4.8k tokens; the gateway ≈ 10 KB (~2.5k), an ~84% cut. The
full surface is intentionally large — the gateway and profiles are the
mitigation. `tests/test_schema_budget.py` fails the build if either the full or
the gateway surface balloons past budget.

## Tool families (98 tools)

Grouped for the profile/gateway system above:

- **Core browse/interact:** browser, navigation, tabs, elements (incl.
  shadow-DOM `click_shadow`/`describe_shadow`), query, content, forms.
- **Human-like input:** `human_click`, `human_type`, `estimated_typing_duration`
  — Bézier mouse paths and per-keystroke timing (`humaninput.py`).
- **Emulation:** viewport, device presets, CPU/network throttling, media.
- **DevTools:** performance traces, heap snapshots.
- **Lighthouse:** audits via the `lighthouse` CLI (when installed).
- **Screencast:** frame capture + MP4 export (when `ffmpeg` is available).
- **Accessibility:** AX-tree snapshot with stable uids, `click_by_uid`.
- **Cookies:** full-fidelity export/import (incl. HTTP-only), list, clear.
- **Network control:** URL blocking, extra headers, service-worker bypass.
- **Interception:** mock responses, fail requests.
- **Permissions / Proxy:** grant/reset permissions; configure/clear proxy.
- **Stealth:** Cloudflare bypass, UA/locale/timezone/geolocation overrides.

Tools that write files (`screenshot`, `export_cookies`, `run_lighthouse`,
`stop_trace`, `take_heap_snapshot`, `start_screencast`) sandbox the path to
`$HOME`, the temp dir, or `$ZENDRIVER_MCP_ARTIFACT_ROOT` (`src/artifacts.py`) so
an agent-supplied path can't overwrite arbitrary files.

## License

MIT
