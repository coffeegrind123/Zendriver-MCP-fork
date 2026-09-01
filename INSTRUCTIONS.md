# Zendriver MCP - LLM Instructions

## How to Use These Tools Effectively

This guide helps LLMs (like Claude) understand how to use Zendriver MCP tools for browser automation and security testing.

---

## IMPORTANT: Tool Usage Order

**Always follow this sequence:**

1. **Start browser first**: Call `start_browser` before any other tool
2. **Navigate to page**: Call `navigate` with the target URL — it waits for the
   network to go quiet before returning, so **no separate wait is needed**
3. **Then interact**: Now you can click, type, screenshot, etc.
4. **Stop when done**: Call `stop_browser` to clean up

### Example Flow:
```
1. start_browser()
2. navigate("https://example.com")   # returns once the page has settled
3. screenshot()  # or other actions
4. stop_browser()
```

`navigate` reports how the settle ended, e.g. `(network idle after 0.6s, 246
requests)` or `(network still active after 10.0s, ...)`. The second is normal for
a page that polls or streams and usually still reads fine. Pass `settle=0` to
return the instant navigation commits, when you do not intend to read the page.

Reach for an explicit wait only when `navigate`'s own settle is not the right
condition: `wait_for_element` for something that appears after a click,
`wait_for_request` for one known API call, `wait_for_network` after an
interaction that triggers more traffic. A blind `wait(2)` is the last resort —
it is slower than settling on a fast page and too short on a slow one.

---

## Finding Elements - Best Practices

### CSS Selectors (Most Reliable)
Use these selector patterns:

| Pattern | Example | When to Use |
|---------|---------|-------------|
| By ID | `#login-button` | When element has unique ID |
| By class | `.submit-btn` | When element has distinctive class |
| By tag + class | `button.primary` | When multiple elements share class |
| By attribute | `input[type="email"]` | For form inputs |
| By data attribute | `[data-testid="submit"]` | For testing-friendly sites |
| Nested | `form#login input[type="password"]` | When you need specificity |

### Finding by Text (When Selectors Fail)
```
click(text="Sign In")
click(text="Submit")
find_element(text="Welcome")
```

### Strategy When Element Not Found:
1. First try: `find_element(selector="...")` to check if it exists
2. If not found: `get_content()` or `get_text_content()` to see page structure
3. Try broader selector: `find_all_elements(selector="button")` to see all buttons
4. Use `highlight_element(selector="...")` to visually verify

---

## Common Tasks - Step by Step

### Login to a Website
```
1. start_browser()
2. navigate("https://site.com/login")
3. wait(2)
4. type_text(text="username", selector="input[name='username']")
5. type_text(text="password", selector="input[name='password']")
6. click(selector="button[type='submit']")
7. wait(3)
8. screenshot()  # verify login worked
```

### Fill a Form
```
1. Use fill_form with JSON:
   fill_form('{"#name": "John", "#email": "john@test.com", "#message": "Hello"}')

2. Or fill individually:
   type_text(text="John", selector="#name")
   type_text(text="john@test.com", selector="#email")
```

### Scrape Data
```
1. navigate to page
2. get_text_content()  # for visible text
3. OR get_content()    # for full HTML
4. OR get_links()      # for all links
5. OR execute_js('return document.querySelectorAll("...").map(...)')  # custom extraction
```

### Take Screenshots
```
screenshot()                              # full page
screenshot(filename="output.png")         # save to file
screenshot_element(selector=".chart")     # specific element
```

---

## Security Testing Workflow

### Quick Security Audit
```
1. start_browser()
2. navigate("https://target-site.com")
3. wait(2)
4. run_security_audit()  # comprehensive overview
```

### Detailed Security Analysis
```
1. run_security_audit()           # overview
2. find_forms_security()          # analyze all forms
3. find_sensitive_data()          # look for exposed data
4. analyze_javascript_security()  # check JS issues
5. check_mixed_content()          # HTTP/HTTPS issues
6. find_external_resources()      # third-party dependencies
```

### Security Tools Available:
| Tool | What It Checks |
|------|----------------|
| `run_security_audit` | Overall security score |
| `analyze_security_headers` | Missing security headers |
| `find_forms_security` | CSRF, password fields, form actions |
| `find_sensitive_data` | Exposed emails, API keys, tokens |
| `find_input_vulnerabilities` | Input validation issues |
| `check_mixed_content` | HTTP resources on HTTPS |
| `analyze_javascript_security` | eval(), innerHTML, etc. |
| `check_clickjacking_protection` | Frame protection |
| `find_external_resources` | Third-party scripts |

---

## Handling Dynamic Content

### Wait for Elements
```
wait_for_element(selector="#loaded-content", timeout=10)
wait_for_text(text="Success", timeout=10)
```

### Handle SPAs (Single Page Apps)
```
1. navigate to page
2. wait(3)  # SPAs need more time
3. scroll(direction="down", amount=500)  # trigger lazy loading
4. wait(1)
5. Now interact with content
```

### Infinite Scroll Pages
```
for i in range(5):
    scroll(direction="down", amount=1000)
    wait(1)
get_content()  # now has more content
```

---

## Multi-Tab Operations

```
1. start_browser()
2. navigate("https://site1.com")    # opens in tab_1
3. new_tab("https://site2.com")     # opens tab_2
4. list_tabs()                       # see all tabs
5. switch_tab("tab_1")              # go back to first
6. close_tab("tab_2")               # close second
```

---

## JavaScript Execution

### Simple Queries
```
execute_js('return document.title')
execute_js('return document.querySelectorAll("a").length')
```

### Extract Data
```
execute_js('''
    return Array.from(document.querySelectorAll('.product')).map(p => ({
        name: p.querySelector('.name')?.innerText,
        price: p.querySelector('.price')?.innerText
    }))
''')
```

### Modify Page
```
execute_js('document.body.style.background = "red"')
hide_element(selector=".annoying-popup")
remove_element(selector=".cookie-banner")
```

---

## Troubleshooting

### Element Not Found
1. Check if page loaded: `get_page_info()`
2. See page structure: `get_content()` or `get_text_content()`
3. Try finding all similar: `find_all_elements(selector="button")`
4. Wait longer: `wait(3)` then retry
5. Try text search: `find_element(text="Button Text")`

### Page Not Loading
1. Check URL: `get_page_info()`
2. Take screenshot: `screenshot()`
3. Check for errors: `get_console_logs()`
4. Try reload: `reload_page()`

### Clicks Not Working
1. Scroll to element: `scroll_to_element(selector="...")`
2. Wait after scroll: `wait(0.5)`
3. Try clicking by coordinates: First find element, then `mouse_click(x, y)`
4. Use JavaScript: `execute_js('document.querySelector("...").click()')`

---

## Best Practices

1. **Always start/stop browser** - Don't leave browsers running
2. **Do not add a wait after `navigate`** - it already settles the page; add an
   explicit wait only after a click or an action that starts new requests
3. **Take screenshots for verification** - Visual confirmation helps
4. **Handle errors gracefully** - Elements may not always exist
5. **Be specific with selectors** - Avoid ambiguous matches
6. **Do not ask for headless** - it is accepted and ignored; every launch is
   headed, because headless is what Cloudflare and other bot detection look for
7. **Clean up storage if needed** - `clear_storage()` between tests
