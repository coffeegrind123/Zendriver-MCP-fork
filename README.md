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
| `ZENDRIVER_MCP_PROFILE` | `full` (default), `minimal`, `browse`, `interact`, `scrape`, `stealth` |
| `ZENDRIVER_MCP_GROUPS` | comma/space list of groups: `browser navigation tabs elements query content storage logging forms utils stealth` |
| `ZENDRIVER_MCP_ALLOW` | comma/space list of exact tool names to keep |
| `ZENDRIVER_MCP_DENY` | comma/space list of exact tool names to remove |

The `browser` (lifecycle) group is always retained so a session can start/stop
the browser. `ALLOW`/`DENY` refine whatever the profile/groups selected, e.g.
`ZENDRIVER_MCP_PROFILE=browse ZENDRIVER_MCP_DENY=go_forward`.

### 3. Compact output

`get_interaction_tree` and `execute_js` now emit minified JSON (no pretty-print
indentation), roughly halving their token cost with no loss of information.

## License

MIT
