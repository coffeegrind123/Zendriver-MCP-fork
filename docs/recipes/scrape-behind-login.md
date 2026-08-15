# Recipe: scrape behind a login, once

The usual pain point: you need data from a site that requires auth, the login
is annoying or uses 2FA, and you don't want to script it. Do it by hand once,
then reuse the cookies on every subsequent run.

## First run - log in, save

```python
await start_browser(headless=False)  # human needs to see the form
await navigate("https://example.com/login")
# ... user types credentials, solves 2FA, clicks submit ...
await wait_for_element("#dashboard", timeout=120)
await export_cookies("~/.sessions/example.json")
await stop_browser()
```

The JSON file now holds every cookie Chrome knows about for that domain,
including HTTP-only session tokens.

## Subsequent runs - reuse

```python
await start_browser(headless=True)
await import_cookies("~/.sessions/example.json")
await navigate("https://example.com/dashboard")
# already logged in
data = await get_content()
```

## When it breaks

Cookies do expire. Catch 401s from `get_network_logs()` or just re-run the
first recipe. For very long sessions, consider exporting again after each
run so the most recent refresh token gets persisted.
