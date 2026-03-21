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

## License

MIT
