# utility tools - screenshot, js execution, waiting, security audit
import io
import json
import os
import tempfile
from datetime import datetime
from typing import Annotated, Optional

from mcp.server.fastmcp.utilities.types import Image
from PIL import Image as PILImage
from pydantic import Field

from src.tools.base import ToolBase


class UtilityTools(ToolBase):
    """utility tools for screenshots, js, waiting, and security"""

    def _register_tools(self) -> None:
        """register utility tools"""
        self._mcp.tool()(self.screenshot)
        self._mcp.tool()(self.execute_js)
        self._mcp.tool()(self.wait)
        self._mcp.tool()(self.wait_for_element)
        self._mcp.tool()(self.run_security_audit)

    async def screenshot(
        self,
        save_path: Annotated[Optional[str], Field(description="Path to also write the image to on the server's filesystem. The extension picks the format: .png, .gif, and .bmp are saved losslessly, anything else as JPEG. Omit to return the image without writing a file. Example: '/tmp/page.png'")] = None,
    ) -> Image:
        """Take a screenshot of the visible viewport as an image you can look at directly.

        The fastest way to understand a page's actual layout when selectors and
        text are not enough. Set window_size and device_scale_factor on
        start_browser first — the default viewport is roughly 800x600, which is
        why screenshots come out small. Returns the image as JPEG at quality 60
        to stay under the size limit, or a plain red image if no page is loaded.
        """
        if not self.session.page:
            # return red placeholder image with error
            img = PILImage.new("RGB", (400, 100), color=(200, 50, 50))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG")
            return Image(data=buffer.getvalue(), format="jpeg")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await self.session.page.save_screenshot(tmp_path)
            # compress to JPEG for smaller size (under 1MB limit)
            with PILImage.open(tmp_path) as img:
                buffer = io.BytesIO()
                img.convert("RGB").save(buffer, format="JPEG", quality=60, optimize=True)
                jpeg_data = buffer.getvalue()

                # If save_path provided, save to disk
                if save_path:
                    # Determine format from extension
                    ext = os.path.splitext(save_path)[1].lower()
                    if ext in ['.png', '.gif', '.bmp']:
                        # Re-open original for lossless formats
                        with PILImage.open(tmp_path) as orig:
                            orig.save(save_path)
                    else:
                        # Save as JPEG for .jpg/.jpeg or unknown
                        with open(save_path, 'wb') as f:
                            f.write(jpeg_data)

                return Image(data=jpeg_data, format="jpeg")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    async def execute_js(
        self,
        script: Annotated[str, Field(description="A JavaScript EXPRESSION evaluated in the page, not a statement block. A bare 'return' is a syntax error — wrap multi-statement code in an IIFE. Example: 'document.title' or '(function() { return document.links.length; })()'")],
    ) -> str:
        """Execute a JS expression in the page and return its value.

        The escape hatch for anything the dedicated tools do not cover — reading
        computed styles, calling page functions, extracting structured data in one
        pass. Prefer the specific tools where they apply; they handle shadow DOM
        and visibility for you. Returns the result as indented JSON, or
        '(no return value)', or an error string that names the JavaScript fault.

        IMPORTANT: Do NOT use 'return' statements directly in your script.
        The script is automatically wrapped to capture the result.

        Examples:
            Good: document.title
            Good: (function() { const x = 1 + 1; return x; })()
            Bad:  return document.title  // SyntaxError!

        For complex scripts, wrap in an IIFE:
            (function() {
                const data = [];
                // ... your code ...
                return data;
            })()
        """
        # check for common mistakes
        stripped = script.strip()
        if stripped.startswith('return ') and '(' not in stripped[:20]:
            return (
                "Error: Cannot use bare 'return' statement. "
                "Either remove 'return' (for simple expressions) or wrap in an IIFE: "
                "(function() { " + script + " })()"
            )

        try:
            result = await self.run_js(script)
            if result is None:
                return "(no return value)"
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            error_msg = str(e)
            # provide helpful error messages
            if 'SyntaxError' in error_msg and 'return' in script.lower():
                return (
                    f"SyntaxError: Illegal return statement. "
                    f"Wrap your code in an IIFE: (function() {{ {script} }})()"
                )
            return f"JavaScript Error: {error_msg}"

    async def wait(
        self,
        seconds: Annotated[float, Field(description="How long to pause, in seconds. Example: 2.0")] = 1.0,
    ) -> str:
        """Pause for a fixed number of seconds before the next tool runs.

        A blunt fallback for animations and rate limits. Prefer
        wait_for_element, wait_for_network, or wait_for_request wherever the
        thing being waited for can be named — they are faster and do not fail
        when the page is slower than the guess. Returns a confirmation naming the
        duration waited.
        """
        await self.session.page.wait(seconds)
        return f"Waited {seconds}s"

    async def wait_for_element(
        self,
        selector: Annotated[str, Field(description="CSS selector to wait for. Example: '#results .item'")],
        timeout: Annotated[float, Field(description="Maximum seconds to wait before giving up. The default suits single-page apps. Example: 30.0")] = 30.0,
        visible: Annotated[bool, Field(description="Require the element to be visible, not merely present in the DOM. Set false to accept a hidden element. Example: true")] = True,
    ) -> str:
        """Wait until an element appears on the page, polling until it does.

        The right way to synchronise with a single-page app after a click or
        navigation — more reliable than wait with a guessed duration. Returns a
        confirmation once the element appears, or a timeout message naming the
        selector that never matched.
        """
        safe_sel = self.escape_js_string(selector)

        async def check():
            try:
                # use short timeout to avoid blocking
                elem = await self.session.page.select(selector, timeout=0.5)
                if elem is None:
                    return False
                if visible:
                    # also check visibility
                    is_visible = await self.run_js(f'''
                        (function() {{
                            const el = document.querySelector("{safe_sel}");
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
                        }})()
                    ''')
                    return is_visible
                return True
            except Exception:
                return False

        if await self.wait_for_condition(check, timeout):
            return f"Element found: {selector}"

        # provide helpful suggestions on timeout
        suggestions = await self.run_js(f'''
            (function() {{
                const exact = document.querySelector("{safe_sel}");
                if (exact) {{
                    const style = window.getComputedStyle(exact);
                    if (style.display === "none") return "Element exists but has display:none";
                    if (style.visibility === "hidden") return "Element exists but has visibility:hidden";
                }}
                const all = document.querySelectorAll("*");
                const suggestions = [];
                for (const el of all) {{
                    if (el.id && el.id.toLowerCase().includes("{safe_sel}".toLowerCase().replace(/[#.\\[\\]]/g, ""))) {{
                        suggestions.push("#" + el.id);
                    }}
                }}
                return suggestions.length ? "Try: " + suggestions.slice(0, 3).join(", ") : null;
            }})()
        ''')

        hint = f" ({suggestions})" if suggestions else ""
        return f"Timeout: Element not found after {timeout}s: {selector}{hint}"

    async def run_security_audit(self) -> str:
        """Audit the loaded page's client-side security posture.

        Checks HTTPS, CSRF protection, password field handling, mixed content,
        inline scripts, Subresource Integrity, form targets, exposed sensitive
        data, and risky JavaScript patterns. Inspects only what the current page
        exposes to the browser — it is not a scan of the server. Returns a
        formatted multi-section report of findings.
        """
        page = self.session.page
        url = getattr(page, "url", "unknown")

        lines = [
            "=" * 60,
            "SECURITY AUDIT REPORT",
            "=" * 60,
            f"URL: {url}",
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60, ""
        ]

        # https check
        is_https = url.startswith('https://')
        status = "PASS" if is_https else "FAIL"
        lines.append(f"[{status}] HTTPS: {self.bool_to_yes_no(is_https)}" + ("" if is_https else " - INSECURE"))

        # form security check
        forms_result = await self.run_js('''
            (function() {
                const forms = document.forms;
                let hasCSRF = false, hasInsecurePassword = false, formCount = forms.length;
                let passwordForms = 0;
                for (let form of forms) {
                    if (form.querySelector('input[name*="csrf"], input[name*="token"], input[name="_token"]')) hasCSRF = true;
                    if (form.querySelector('input[type="password"]')) {
                        passwordForms++;
                        if (form.method.toLowerCase() === 'get') hasInsecurePassword = true;
                    }
                }
                return { count: formCount, hasCSRF, hasInsecurePassword, passwordForms };
            })()
        ''')

        csrf_status = "WARN" if forms_result['count'] > 0 and not forms_result['hasCSRF'] else "PASS"
        lines.append(f"[{csrf_status}] CSRF Protection: {'Detected' if forms_result['hasCSRF'] else 'Not detected'}")

        pwd_status = "FAIL" if forms_result['hasInsecurePassword'] else "PASS"
        lines.append(f"[{pwd_status}] Password Security: {'INSECURE - GET method' if forms_result['hasInsecurePassword'] else 'OK'}")
        lines.append(f"[INFO] Forms: {forms_result['count']} total, {forms_result['passwordForms']} with passwords")

        # mixed content check
        mixed = await self.run_js('''
            (function() {
                if (location.protocol !== 'https:') return { check: false };
                const httpScripts = Array.from(document.scripts).filter(s => s.src?.startsWith('http://')).length;
                const httpStyles = Array.from(document.styleSheets).filter(s => s.href?.startsWith('http://')).length;
                const httpImages = Array.from(document.images).filter(i => i.src?.startsWith('http://')).length;
                return { check: true, scripts: httpScripts, styles: httpStyles, images: httpImages,
                         total: httpScripts + httpStyles + httpImages };
            })()
        ''')

        if mixed['check']:
            mixed_status = "FAIL" if mixed['total'] > 0 else "PASS"
            if mixed['total'] > 0:
                lines.append(f"[{mixed_status}] Mixed Content: {mixed['scripts']} scripts, {mixed['styles']} styles, {mixed['images']} images over HTTP")
            else:
                lines.append(f"[{mixed_status}] Mixed Content: None")

        # inline scripts check
        inline = await self.run_js('document.querySelectorAll("script:not([src])").length')
        inline_status = "INFO" if inline > 5 else "PASS"
        lines.append(f"[{inline_status}] Inline Scripts: {inline}")

        # sri check
        no_integrity = await self.run_js('Array.from(document.scripts).filter(s => s.src && !s.integrity).length')
        sri_status = "WARN" if no_integrity > 0 else "PASS"
        lines.append(f"[{sri_status}] Scripts without SRI: {no_integrity}")

        # external resources
        external = await self.run_js('''
            (function() {
                const currentHost = location.hostname;
                const getHost = url => { try { return new URL(url).hostname; } catch { return null; } };
                const scripts = Array.from(document.scripts).filter(s => s.src && getHost(s.src) !== currentHost).length;
                const iframes = Array.from(document.querySelectorAll('iframe')).filter(f => f.src && getHost(f.src) !== currentHost).length;
                return { scripts, iframes };
            })()
        ''')
        ext_status = "INFO" if external['scripts'] > 0 else "PASS"
        lines.append(f"[{ext_status}] External Scripts: {external['scripts']}, External Iframes: {external['iframes']}")

        # dangerous js patterns
        dangerous = await self.run_js('''
            (function() {
                const scripts = Array.from(document.scripts).map(s => s.innerHTML).join('\\n');
                return {
                    eval: (scripts.match(/eval\\s*\\(/g) || []).length,
                    innerHTML: (scripts.match(/\\.innerHTML\\s*=/g) || []).length,
                    documentWrite: (scripts.match(/document\\.write\\s*\\(/g) || []).length
                };
            })()
        ''')

        dangerous_total = dangerous['eval'] + dangerous['innerHTML'] + dangerous['documentWrite']
        js_status = "WARN" if dangerous_total > 0 else "PASS"
        if dangerous_total > 0:
            lines.append(f"[{js_status}] Dangerous JS Patterns: eval({dangerous['eval']}), innerHTML({dangerous['innerHTML']}), document.write({dangerous['documentWrite']})")
        else:
            lines.append(f"[{js_status}] Dangerous JS Patterns: None detected")

        # sensitive data scan
        sensitive = await self.run_js('''
            (function() {
                const html = document.documentElement.outerHTML;
                return {
                    awsKeys: (html.match(/AKIA[0-9A-Z]{16}/g) || []).length,
                    jwtTokens: (html.match(/eyJ[a-zA-Z0-9_-]*\\.eyJ[a-zA-Z0-9_-]*\\.[a-zA-Z0-9_-]*/g) || []).length,
                    privateKeys: (html.match(/-----BEGIN (RSA |EC |DSA |)PRIVATE KEY-----/g) || []).length
                };
            })()
        ''')

        sensitive_total = sensitive['awsKeys'] + sensitive['jwtTokens'] + sensitive['privateKeys']
        sens_status = "FAIL" if sensitive_total > 0 else "PASS"
        if sensitive_total > 0:
            lines.append(f"[{sens_status}] Exposed Secrets: AWS keys({sensitive['awsKeys']}), JWT tokens({sensitive['jwtTokens']}), Private keys({sensitive['privateKeys']})")
        else:
            lines.append(f"[{sens_status}] Exposed Secrets: None detected")

        lines.extend(["", "=" * 60])

        # summary
        checks = lines[7:]
        fails = sum(1 for line in checks if line.startswith('[FAIL]'))
        warns = sum(1 for line in checks if line.startswith('[WARN]'))
        passes = sum(1 for line in checks if line.startswith('[PASS]'))

        if fails > 0:
            lines.append(f"RESULT: {fails} CRITICAL, {warns} WARNINGS, {passes} PASSED")
        elif warns > 0:
            lines.append(f"RESULT: {warns} WARNINGS, {passes} PASSED")
        else:
            lines.append(f"RESULT: ALL {passes} CHECKS PASSED")

        return "\n".join(lines)
