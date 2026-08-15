# Recipe: get past a Cloudflare Turnstile challenge

Zendriver's fingerprint usually sails through Cloudflare. When it doesn't -
typically on the interactive Turnstile checkbox - use the solver.

```python
await start_browser()
await navigate("https://a-cloudflare-protected-site.example")

if await is_cloudflare_challenge_present(timeout=5):
    await bypass_cloudflare(timeout=20, click_delay=4)

# page is now visible
```

## Tips

- **Don't call `bypass_cloudflare` when there's no challenge.** It will wait
  the full `timeout` and then raise. Always probe first.
- **`click_delay` too small looks like a bot.** 3-5 seconds is realistic; the
  default is 5.
- **Still blocked?** The challenge isn't the only thing Cloudflare watches.
  IP reputation matters - try a residential proxy.
