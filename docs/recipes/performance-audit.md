# Recipe: performance audit of an authenticated flow

Lighthouse audits are great for unauthenticated landing pages but painful for
pages behind a login. Drive the flow with zendriver-mcp, then point Lighthouse
at the already-authenticated browser.

```python
await start_browser()
await import_cookies("~/.sessions/example.json")
await navigate("https://example.com/dashboard")

# Lighthouse audit on the exact URL we landed on, with the session live
report = await run_lighthouse(
    url="https://example.com/dashboard",
    form_factor="mobile",
    categories=["performance", "accessibility"],
)
print(report["scores"])  # {"performance": 74, "accessibility": 92}
print(report["report_path"])  # /tmp/lighthouse-XXXXXX.json
```

## Pair with a performance trace

For a specific interaction (not a whole page load), prefer a trace:

```python
await start_trace()
await human_click(selector="#buy-now")
await wait_for_element("#thanks")
await stop_trace("/tmp/buy-trace.json")
```

Open the JSON in Chrome DevTools (Performance tab > Load profile) - you get
the same flame chart you'd get from the built-in recorder, minus the manual
click-record-click dance.
