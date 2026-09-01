# Zendriver-MCP

Undetectable browser automation exposed to LLM agents over the Model Context
Protocol. Built on the Chrome DevTools Protocol via
[zendriver](https://github.com/cdpdriver/zendriver), it ships **98 tools** for
driving a real Chrome — navigation, interaction, scraping, stealth, human-like
input, emulation, DevTools traces, Lighthouse, screencasting, accessibility,
cookies, network interception, and more.

This fork's distinguishing feature is **context efficiency**: 98 tools would
normally bloat an agent's context window and degrade tool selection, so the
server can collapse its surface to a tiny **search gateway** or a named
**profile** — without losing any capability. See
[Context optimization](optimization.md).

## Quick start

```jsonc
// Claude Desktop / Claude Code MCP config
{
  "mcpServers": {
    "browser": {
      "command": "python",
      "args": ["/path/to/zendriver-mcp/run.py"]
    }
  }
}
```

A minimal agent loop:

```python
await start_browser()  # always headed: headless is redirected (it loses to bot detection)
await navigate("https://example.com")
print(await get_interaction_tree())  # numeric ids for every control
await click("3")  # click by id
await type_text("hello", "5")  # type into an input by id
print(await get_text_content())  # read the page
await stop_browser()
```

## Tool families

- **Core browse/interact** — browser lifecycle, navigation, tabs, elements
  (incl. shadow-DOM `click_shadow`/`describe_shadow`), query, content, forms.
- **Human-like input** — `human_click`, `human_type` (Bézier mouse paths and
  per-keystroke timing).
- **Emulation** — viewport, device presets, CPU/network throttling, media.
- **DevTools** — performance traces, heap snapshots.
- **Lighthouse** — audits via the `lighthouse` CLI.
- **Screencast** — frame capture and MP4 export (needs `ffmpeg`).
- **Accessibility** — AX-tree snapshot with stable uids, `click_by_uid`.
- **Cookies** — full-fidelity export/import (incl. HTTP-only), list, clear.
- **Network control & interception** — block URLs, extra headers, mock
  responses, fail requests, service-worker bypass.
- **Permissions / Proxy** — grant/reset permissions; configure/clear proxy.
- **Stealth** — Cloudflare bypass, UA/locale/timezone/geolocation overrides.

## Safety

Tools that write files (`screenshot`, `export_cookies`, `run_lighthouse`,
`stop_trace`, `take_heap_snapshot`, `start_screencast`) sandbox the path to
`$HOME`, the temp dir, or `$ZENDRIVER_MCP_ARTIFACT_ROOT`, so an agent-supplied
path can never overwrite arbitrary files.
