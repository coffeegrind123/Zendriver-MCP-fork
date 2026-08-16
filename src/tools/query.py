# element query tools - find element, find all, get text, get attribute, find buttons
from typing import Annotated

from pydantic import Field

from src.tools.base import ToolBase


class QueryTools(ToolBase):
    """tools for querying and inspecting elements"""

    def _register_tools(self) -> None:
        """register element query tools"""
        self._register(self.find_element)
        self._register(self.find_all_elements)
        self._register(self.get_element_text)
        self._register(self.get_element_attribute)
        self._register(self.find_buttons)
        self._register(self.find_inputs)

    async def find_element(
        self,
        selector: Annotated[
            str | None,
            Field(
                description="CSS selector to look for, tried for up to 2 seconds. Example: '#login-form input[type=\"email\"]'"
            ),
        ] = None,
        text: Annotated[
            str | None,
            Field(
                description="Visible text to search for, matched as a best match. Used only when selector is omitted. Example: 'Sign in'"
            ),
        ] = None,
    ) -> str:
        """Check whether one element exists, and report its tag, text, and visibility.

        Pass exactly one of selector or text. Use to verify a selector before
        acting on it; when it misses, the result lists interactive elements that
        do exist on the page, which is usually enough to correct the selector.
        Returns a 'Found <tag> (visible|HIDDEN): text' line, or a not-found
        message with those suggestions.
        """
        page = self.session.page

        if selector:
            try:
                elem = await page.select(selector, timeout=2)
            except Exception:
                elem = None

            if elem is None:
                # provide helpful suggestions
                suggestions = await self.run_js("""
                    (function() {
                        const found = [];
                        const ce = document.querySelector('[contenteditable="true"]');
                        if (ce) found.push({ sel: '[contenteditable="true"]', tag: ce.tagName });
                        const tb = document.querySelector('[role="textbox"]');
                        if (tb) found.push({ sel: '[role="textbox"]', tag: tb.tagName });
                        const ta = document.querySelector('textarea');
                        if (ta) found.push({ sel: 'textarea', tag: 'TEXTAREA' });
                        const inp = document.querySelector('input:not([type="hidden"])');
                        if (inp) found.push({ sel: 'input', tag: 'INPUT', type: inp.type });
                        const btn = document.querySelector('button');
                        if (btn) found.push({ sel: 'button', tag: 'BUTTON' });
                        return found.slice(0, 5);
                    })()
                """)

                msg = f"Element not found: {selector}"
                if suggestions:
                    suggestions_text = ", ".join([s["sel"] for s in suggestions])
                    msg += f"\nAvailable interactive elements: {suggestions_text}"
                return msg

            tag = getattr(elem, "tag_name", "unknown")
            text_content = getattr(elem, "text", "") or ""

            # get additional useful info
            safe_sel = self.escape_js_string(selector)
            extra_info = await self.run_js(f'''
                (function() {{
                    const el = document.querySelector("{safe_sel}");
                    if (!el) return {{}};
                    const style = window.getComputedStyle(el);
                    return {{
                        visible: style.display !== "none" && style.visibility !== "hidden",
                        editable: el.isContentEditable || el.tagName === "INPUT" || el.tagName === "TEXTAREA",
                        clickable: el.tagName === "BUTTON" || el.tagName === "A" || el.onclick !== null
                    }};
                }})()
            ''')

            visibility = "visible" if extra_info.get("visible", True) else "HIDDEN"
            return f"Found <{tag}> ({visibility}): {text_content[:200] if text_content else '(no text)'}"

        elif text:
            elem = await page.find(text, best_match=True)
            if elem is None:
                return f"Element not found with text: {text}"
            tag = getattr(elem, "tag_name", "unknown")
            return f"Found <{tag}> matching: {text}"

        return "Error: Provide selector or text"

    async def find_all_elements(
        self,
        selector: Annotated[
            str,
            Field(
                description="CSS selector matching the set of elements to list. Example: 'article h2'"
            ),
        ],
        limit: Annotated[
            int,
            Field(
                description="Maximum number of elements to include in the output. The total found is reported regardless. Example: 20"
            ),
        ] = 20,
    ) -> str:
        """List every element matching a selector, with its tag and leading text.

        Use to enumerate repeated structures such as search results, table rows,
        or list items. Returns the total found, then one numbered
        '<tag> text' line per element up to limit, with each text truncated to 50
        characters.
        """
        elems = await self.session.page.select_all(selector)
        if not elems:
            return f"No elements found: {selector}"

        results = []
        for i, elem in enumerate(elems[:limit]):
            tag = getattr(elem, "tag_name", "unknown")
            text = (getattr(elem, "text", "") or "")[:50]
            results.append(f"{i + 1}. <{tag}> {text}")

        total, shown = len(elems), min(len(elems), limit)
        return f"Found {total} element(s) (showing {shown}):\n" + "\n".join(results)

    async def get_element_text(
        self,
        selector: Annotated[
            str, Field(description="CSS selector of the single element to read. Example: '.price'")
        ],
    ) -> str:
        """Read the visible text of one element.

        Use to extract a single value — a price, a status, a heading — instead of
        pulling the whole page with get_text_content. Returns the element's text,
        or '(empty)' when it has none.
        """
        elem = await self.get_element(selector)
        text = getattr(elem, "text", "") or ""
        return text if text else "(empty)"

    async def get_element_attribute(
        self,
        selector: Annotated[
            str,
            Field(description="CSS selector of the single element to read. Example: 'a.download'"),
        ],
        attribute: Annotated[
            str,
            Field(
                description="Name of the HTML attribute to read, as written in the markup. Example: 'href'"
            ),
        ],
    ) -> str:
        """Read one HTML attribute from one element.

        Use for values that are not visible text — href, src, value, data-*,
        aria-*. Returns the attribute value as a string, or '(not set)' when the
        element does not carry it.
        """
        elem = await self.get_element(selector)
        attrs = getattr(elem, "attrs", {})
        value = attrs.get(attribute) if attrs else None
        return str(value) if value else "(not set)"

    async def find_buttons(
        self,
        filter_text: Annotated[
            str | None,
            Field(
                description="Case-insensitive substring to match against each button's label. Omit to list every button. Example: 'submit'"
            ),
        ] = None,
    ) -> str:
        """List the page's clickable buttons with a usable selector for each.

        Covers button tags, input[type=submit|button], [role=button], onclick
        handlers, and icon-only buttons — labelling those from aria-label, title,
        or the icon reference. Hidden buttons are excluded. Returns up to 20
        numbered '[type] description -> selector' lines, each selector ready to
        pass to click.
        """
        safe_filter = self.escape_js_string(filter_text) if filter_text else ""

        buttons = await self.run_js(f'''
            (function() {{
                const filter = "{safe_filter}".toLowerCase();
                const results = [];

                // helper to get button description
                function getDescription(el) {{
                    // check text content
                    const text = el.innerText?.trim();
                    if (text) return text;

                    // check aria-label
                    const ariaLabel = el.getAttribute("aria-label");
                    if (ariaLabel) return ariaLabel;

                    // check title
                    const title = el.getAttribute("title");
                    if (title) return title;

                    // check for icon (svg, img, i tag)
                    const svg = el.querySelector("svg");
                    if (svg) {{
                        const svgTitle = svg.querySelector("title")?.textContent;
                        if (svgTitle) return "Icon: " + svgTitle;
                        const use = svg.querySelector("use");
                        if (use) {{
                            const href = use.getAttribute("href") || use.getAttribute("xlink:href");
                            if (href) return "Icon: " + href.split("#").pop();
                        }}
                        return "Icon button";
                    }}

                    const img = el.querySelector("img");
                    if (img) return "Image: " + (img.alt || img.src.split("/").pop());

                    const icon = el.querySelector("i, span[class*='icon']");
                    if (icon) return "Icon: " + (icon.className || "unknown");

                    return "(no description)";
                }}

                // helper to escape CSS selector special characters
                function escapeCSS(str) {{
                    return str.replace(/([!"#$%&'()*+,./:;<=>?@[\\]^`{{|}}~])/g, '\\\\$1');
                }}

                // helper to check if class is valid for CSS selector
                function isValidClass(c) {{
                    // skip Tailwind responsive/state prefixes and complex classes
                    if (c.includes(':') || c.includes('[') || c.includes(']') || c.includes('/')) return false;
                    if (c.includes('hover') || c.includes('active') || c.includes('focus') || c.includes('visible')) return false;
                    // skip classes with special CSS chars that would need escaping
                    if (/[!"#$%&'()*+,./:;<=>?@\\^`{{|}}~]/.test(c)) return false;
                    // skip very short generic classes or very long ones
                    if (c.length < 2 || c.length > 30) return false;
                    // must start with a letter or hyphen
                    if (!/^[a-zA-Z_-]/.test(c)) return false;
                    return true;
                }}

                // helper to get unique selector
                function getSelector(el) {{
                    // prefer ID
                    if (el.id) return "#" + escapeCSS(el.id);

                    // prefer name attribute
                    if (el.name) return el.tagName.toLowerCase() + "[name='" + el.name + "']";

                    // try aria-label
                    const ariaLabel = el.getAttribute("aria-label");
                    if (ariaLabel && ariaLabel.length < 50) {{
                        const sel = el.tagName.toLowerCase() + "[aria-label='" + ariaLabel.replace(/'/g, "\\'") + "']";
                        try {{
                            if (document.querySelectorAll(sel).length === 1) return sel;
                        }} catch(e) {{}}
                    }}

                    // try data-testid, then data-id (each with its own attr name)
                    const testId = el.getAttribute("data-testid");
                    if (testId) {{
                        return el.tagName.toLowerCase() + "[data-testid='" + testId + "']";
                    }}
                    const dataId = el.getAttribute("data-id");
                    if (dataId) {{
                        return el.tagName.toLowerCase() + "[data-id='" + dataId + "']";
                    }}

                    // try simple classes only (no Tailwind prefixes)
                    const classes = Array.from(el.classList).filter(isValidClass);
                    if (classes.length > 0) {{
                        const escapedClasses = classes.slice(0, 2).map(escapeCSS).join(".");
                        const sel = el.tagName.toLowerCase() + "." + escapedClasses;
                        try {{
                            if (document.querySelectorAll(sel).length === 1) return sel;
                        }} catch(e) {{}}
                    }}

                    // fallback: use data attributes
                    for (const attr of el.attributes) {{
                        if (attr.name.startsWith("data-") && attr.value && attr.value.length < 50) {{
                            const sel = el.tagName.toLowerCase() + "[" + attr.name + "='" + attr.value.replace(/'/g, "\\'") + "']";
                            try {{
                                if (document.querySelectorAll(sel).length === 1) return sel;
                            }} catch(e) {{}}
                        }}
                    }}

                    // last resort: nth-of-type
                    const parent = el.parentElement;
                    if (parent) {{
                        const siblings = Array.from(parent.children).filter(c => c.tagName === el.tagName);
                        const index = siblings.indexOf(el) + 1;
                        return el.tagName.toLowerCase() + ":nth-of-type(" + index + ")";
                    }}

                    return el.tagName.toLowerCase();
                }}

                // find all button-like elements
                const selectors = [
                    "button",
                    "input[type='submit']",
                    "input[type='button']",
                    "[role='button']",
                    "a[href='#']",
                    "a[href='javascript:']",
                    "[onclick]"
                ];

                const seen = new Set();
                for (const sel of selectors) {{
                    for (const el of document.querySelectorAll(sel)) {{
                        if (seen.has(el)) continue;
                        seen.add(el);

                        const style = window.getComputedStyle(el);
                        if (style.display === "none" || style.visibility === "hidden") continue;

                        const desc = getDescription(el);
                        if (filter && !desc.toLowerCase().includes(filter)) continue;

                        results.push({{
                            selector: getSelector(el),
                            tag: el.tagName,
                            description: desc.substring(0, 50),
                            type: el.type || el.getAttribute("role") || "button"
                        }});
                    }}
                }}

                return results.slice(0, 20);
            }})()
        ''')

        if not buttons:
            return "No buttons found" + (f" matching '{filter_text}'" if filter_text else "")

        lines = [f"Found {len(buttons)} button(s):"]
        for i, btn in enumerate(buttons):
            lines.append(f"  {i + 1}. [{btn['type']}] {btn['description']} -> {btn['selector']}")

        return "\n".join(lines)

    async def find_inputs(
        self,
        filter_type: Annotated[
            str | None,
            Field(
                description="Case-insensitive substring matched against each field's input type, e.g. 'text', 'search', 'password', 'email'. Omit to list every field. Example: 'password'"
            ),
        ] = None,
    ) -> str:
        """List the page's input fields with a usable selector for each.

        Covers input, textarea, contenteditable, and [role=textbox], labelling
        each from its <label> element, its inline hint text, or aria-label.
        Hidden and type=hidden fields are excluded. Returns up to 20 numbered
        '[type] description -> selector' lines, each selector ready to pass to
        type_text or fill_form.
        """
        safe_filter = self.escape_js_string(filter_type) if filter_type else ""

        inputs = await self.run_js(f'''
            (function() {{
                const filter = "{safe_filter}".toLowerCase();
                const results = [];

                function getSelector(el) {{
                    if (el.id) return "#" + el.id;
                    if (el.name) return el.tagName.toLowerCase() + "[name='" + el.name + "']";
                    if (el.placeholder) return el.tagName.toLowerCase() + "[placeholder='" + el.placeholder.substring(0, 30) + "']";

                    const classes = Array.from(el.classList || []).filter(c => c.length < 30);
                    if (classes.length > 0) return el.tagName.toLowerCase() + "." + classes[0];

                    return el.tagName.toLowerCase();
                }}

                function getDescription(el) {{
                    const label = document.querySelector("label[for='" + el.id + "']");
                    if (label) return label.textContent.trim();
                    if (el.placeholder) return el.placeholder;
                    if (el.ariaLabel) return el.ariaLabel;
                    return el.name || el.type || "input";
                }}

                // standard inputs
                for (const el of document.querySelectorAll("input, textarea")) {{
                    const style = window.getComputedStyle(el);
                    if (style.display === "none" || el.type === "hidden") continue;

                    const inputType = el.type || "text";
                    if (filter && !inputType.includes(filter)) continue;

                    results.push({{
                        selector: getSelector(el),
                        type: inputType,
                        description: getDescription(el).substring(0, 40)
                    }});
                }}

                // contenteditable and role=textbox
                for (const el of document.querySelectorAll('[contenteditable="true"], [role="textbox"]')) {{
                    const style = window.getComputedStyle(el);
                    if (style.display === "none") continue;

                    if (filter && !["text", "contenteditable", "textbox"].some(t => t.includes(filter))) continue;

                    results.push({{
                        selector: getSelector(el),
                        type: el.getAttribute("role") || "contenteditable",
                        description: el.ariaLabel || el.placeholder || "rich text editor"
                    }});
                }}

                return results.slice(0, 20);
            }})()
        ''')

        if not inputs:
            return "No input fields found" + (f" of type '{filter_type}'" if filter_type else "")

        lines = [f"Found {len(inputs)} input(s):"]
        for i, inp in enumerate(inputs):
            lines.append(f"  {i + 1}. [{inp['type']}] {inp['description']} -> {inp['selector']}")

        return "\n".join(lines)
