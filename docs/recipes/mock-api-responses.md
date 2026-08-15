# Recipe: mock an API response to test the UI

Useful when you want to verify how your app renders a 500 from the backend,
or what happens if an endpoint returns an empty list. `mock_response` makes
this a two-line change.

```python
# Force /api/orders to return an empty array, whatever the backend says.
await mock_response(
    url_pattern="*/api/orders",
    status=200,
    body="[]",
    headers={"Content-Type": "application/json"},
)
await navigate("https://app.example.com/orders")
# UI now shows the empty state.
```

## Simulating failures

```python
# Pretend the user's offline for a specific feature.
await fail_requests("*/api/analytics/*", error_reason="InternetDisconnected")
```

Valid `error_reason` values: `Failed`, `Aborted`, `TimedOut`, `AccessDenied`,
`ConnectionClosed`, `ConnectionReset`, `ConnectionRefused`,
`ConnectionAborted`, `ConnectionFailed`, `NameNotResolved`,
`InternetDisconnected`, `AddressUnreachable`, `BlockedByClient`,
`BlockedByResponse`.

## Managing rules

```python
rules = await list_interceptions()
# [{"id": "rule_001", "url_pattern": "*/api/orders", ...}]

await stop_interception("rule_001")  # remove one
await stop_interception()  # clear all
```

Every rule tracks `match_count`, handy for asserting "the page did hit the
mocked endpoint":

```python
await navigate("https://app.example.com/orders")
stats = await list_interceptions()
assert stats[0]["match_count"] > 0
```
