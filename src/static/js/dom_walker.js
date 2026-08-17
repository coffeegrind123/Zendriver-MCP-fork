(function () {
  const ATTR = "data-zendriver-id";
  let id = 1;

  // Cleanup
  document.querySelectorAll(`[${ATTR}]`).forEach(e => e.removeAttribute(ATTR));

  const vh = window.innerHeight, vw = window.innerWidth;

  function vis(el) {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== "none" && s.visibility !== "hidden" && s.opacity !== "0" && r.width > 0 && r.height > 0;
  }

  // Smart label inference - tries multiple sources
  function lbl(el) {
    // aria-label
    let l = el.getAttribute("aria-label");
    if (l) return l.trim();

    // aria-labelledby
    const lblBy = el.getAttribute("aria-labelledby");
    if (lblBy) {
      const ref = document.getElementById(lblBy);
      if (ref) return ref.innerText.trim();
    }

    const tag = el.tagName;

    // Form controls
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      if (el.type === "submit" || el.type === "button") return el.value || "";

      // <label for="...">
      if (el.id) {
        const lbl = document.querySelector(`label[for="${el.id}"]`);
        if (lbl) return lbl.innerText.trim();
      }

      // Wrapped in label
      const pl = el.closest("label");
      if (pl) {
        const c = pl.cloneNode(true);
        c.querySelectorAll("input,textarea,select").forEach(x => x.remove());
        const t = c.innerText.trim();
        if (t) return t;
      }

      if (el.placeholder) return el.placeholder;
      if (el.name) return el.name;

      // Select shows selected option
      if (tag === "SELECT" && el.selectedIndex >= 0) return el.options[el.selectedIndex]?.text || "";
    }

    // Links and buttons - text content
    if (tag === "A" || tag === "BUTTON" || el.getAttribute("role") === "button") {
      const txt = el.innerText?.trim();
      if (txt && txt.length < 60) return txt;

      // Icon button - svg title
      const svg = el.querySelector("svg");
      if (svg) {
        const t = svg.querySelector("title")?.textContent;
        if (t) return t;
        // Use href from <use>
        const use = svg.querySelector("use");
        if (use) {
          const href = use.getAttribute("href") || use.getAttribute("xlink:href");
          if (href) return href.split("#").pop();
        }
      }
    }

    // Generic: short text
    const txt = el.innerText?.trim();
    if (txt && txt.length < 60) return txt;

    // title attribute
    if (el.title) return el.title;

    // img alt
    if (tag === "IMG" && el.alt) return el.alt;

    return "";
  }

  // Compact type
  function typ(el) {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role");
    const t = el.type?.toLowerCase();

    if (tag === "button" || role === "button") return "btn";
    if (tag === "a" || role === "link") return "link";
    if (tag === "input") {
      if (t === "checkbox" || role === "checkbox") return "chk";
      if (t === "radio" || role === "radio") return "rad";
      if (t === "submit" || t === "button") return "btn";
      return "in";
    }
    if (tag === "textarea") return "in";
    if (tag === "select" || role === "combobox") return "sel";
    if (el.getAttribute("contenteditable") === "true" || role === "textbox") return "in";
    if (role === "tab") return "tab";
    if (role === "menuitem") return "mnu";
    return "el";
  }

  // Region detection (compact: 1-4 chars)
  function rgn(el) {
    const c = el.closest("[role='banner'],header,[role='navigation'],nav,[role='main'],main,[role='contentinfo'],footer,aside,[role='complementary'],[role='dialog'],[aria-modal='true']");
    if (c) {
      const r = c.getAttribute("role"), t = c.tagName.toLowerCase();
      if (r === "banner" || t === "header") return "hdr";
      if (r === "navigation" || t === "nav") return "nav";
      if (r === "main" || t === "main") return "main";
      if (r === "contentinfo" || t === "footer") return "ftr";
      if (r === "complementary" || t === "aside") return "side";
      if (r === "dialog" || c.getAttribute("aria-modal") === "true") return "dlg";
    }
    // Heuristic
    const rect = el.getBoundingClientRect();
    if (rect.top < 80) return "hdr";
    if (rect.left < 200 && rect.top > 80) return "side";
    if (rect.top > vh - 80) return "ftr";
    return "main";
  }

  function interactive(el) {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role");
    const s = getComputedStyle(el);

    if (["a", "button", "input", "select", "textarea", "details", "summary"].includes(tag)) return true;
    if (["button", "link", "checkbox", "menuitem", "tab", "textbox", "combobox", "radio", "switch", "option"].includes(role)) return true;
    if (el.getAttribute("contenteditable") === "true") return true;
    if (s.cursor === "pointer" && (el.onclick || el.getAttribute("onclick"))) return true;
    return false;
  }

  // Skip SVG internals and nested interactive children
  function skip(el, seen) {
    const tag = el.tagName.toLowerCase();
    if (["path", "use", "g", "circle", "rect", "line", "polygon", "svg", "defs", "clippath"].includes(tag)) return true;

    // Skip if parent already captured
    let p = el.parentElement;
    for (let i = 0; i < 3 && p; i++) {
      if (seen.has(p)) return true;
      p = p.parentElement;
    }
    return false;
  }

  function walk(root, out, seen) {
    const w = document.createTreeWalker(root.shadowRoot || root, NodeFilter.SHOW_ELEMENT, {
      acceptNode: n => vis(n) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT
    });

    let n = w.currentNode;
    while (n) {
      if (n !== root && interactive(n) && !skip(n, seen)) {
        const label = lbl(n).replace(/\s+/g, " ").trim().slice(0, 50);
        const t = typ(n);

        // Skip unlabeled generic elements
        if (!label && t === "el") { n = w.nextNode(); continue; }

        const i = id++;
        n.setAttribute(ATTR, i);
        seen.add(n);

        const o = { id: i, t: t, l: label || `[${n.tagName.toLowerCase()}]`, r: rgn(n) };

        // Link target.
        //
        // Without this the tree gives an agent an id and a label and no way to
        // learn where a link goes, so "open that article" becomes: click it and
        // hope, or guess the URL. Observed doing exactly that — a link rendered
        // as `[a]` with no text, then a click that timed out, then three
        // invented URL slugs that 404ed, then a curl fallback. The href was in
        // the DOM the whole time.
        //
        // Absolute, because a relative one is not usable without also knowing
        // the page URL, and `n.href` resolves it for us. Skipped when it says
        // nothing: javascript: handlers, bare "#" anchors, and a href equal to
        // the current page are all noise on a token budget.
        if (t === "link" && n.tagName === "A") {
          const raw = n.getAttribute("href");
          const resolved = n.href;
          if (raw && resolved && !/^javascript:/i.test(raw) && raw !== "#") {
            let u = null;
            try { u = new URL(resolved); } catch (e) { u = null; }
            // A link to the page you are already on, fragment or not, is a skip
            // link or a tab anchor. It cannot take you anywhere.
            const samePage =
              u && u.origin === location.origin && u.pathname === location.pathname &&
              u.search === location.search;
            if (u && !samePage) {
              // Same-origin links are emitted as a path. The origin is already
              // known from the page and repeating it on every link is most of
              // what this field costs — measured at +116% on the whole tree for
              // a 125-link search page.
              const short = u.origin === location.origin
                ? u.pathname + u.search + u.hash
                : resolved;
              o.h = short.length > 200 ? short.slice(0, 200) : short;
            }
          }
        }

        // Input type (only non-text)
        if (t === "in" && n.tagName === "INPUT" && n.type && !["text", "search"].includes(n.type)) {
          o.it = n.type;
        }

        // Current value
        if (["in", "sel"].includes(t) && n.value?.trim()) {
          o.v = n.value.trim().slice(0, 30);
        }

        // Checked state
        if (["chk", "rad"].includes(t) && n.checked !== undefined) {
          o.ck = n.checked;
        }

        // Disabled
        if (n.disabled) o.dis = true;

        // Offscreen
        const rect = n.getBoundingClientRect();
        if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw) {
          o.off = true;
        }

        out.push(o);
      }

      if (n.shadowRoot) walk(n.shadowRoot, out, seen);
      n = w.nextNode();
    }
  }

  const out = [], seen = new Set();
  if (document.body) walk(document.body, out, seen);
  return out;
})();
