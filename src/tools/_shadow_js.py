"""Shared JavaScript snippets for shadow-DOM-aware queries and clicks.

Modern design systems (bunq, NS, bol.com, etc.) nest real interactive
elements several shadow-root deep inside custom elements like
``<nes-selectable-radio>`` -> shadowRoot -> ``<nes-selectable>`` ->
shadowRoot -> ``[role="radio"]``. A plain ``document.querySelector`` or
even a one-level ``shadowRoot.querySelector`` misses these. These
helpers recursively pierce every open shadow root and also dispatch
click events that cross shadow boundaries.
"""

from __future__ import annotations

# Roles and tag names that are "real" interactive targets. Broader than a
# pure button list - radios, checkboxes, menuitems, tabs, etc. all live
# inside design-system components and need to be clicked to do anything.
_CLICKABLE_ROLES = (
    "button",
    "link",
    "checkbox",
    "radio",
    "switch",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "tab",
    "option",
    "treeitem",
)


_CLICKABLE_ROLES_JS = "[" + ",".join(f'"{r}"' for r in _CLICKABLE_ROLES) + "]"


# Walks the light DOM + every open shadow root and invokes ``cb`` on each
# element. Defined at module scope so every Python tool that evaluates JS
# can prefix this snippet and then use ``walkAll(root, fn)`` freely.
WALK_ALL_JS = """
function walkAll(root, cb) {
  const doc = root.ownerDocument || (root.nodeType === 9 ? root : null);
  const base = doc ? doc.createTreeWalker : null;
  // Some shadow roots don't expose ownerDocument; fall back to document.
  const w = (doc || document).createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null);
  let n = w.currentNode;
  while (n) {
    cb(n);
    if (n.shadowRoot) walkAll(n.shadowRoot, cb);
    n = w.nextNode();
  }
}
"""


# Recursively look for an interactive element inside a host, crossing any
# number of shadow roots. Falls back to null when the host is a leaf.
FIND_INNER_CLICKABLE_JS = f"""
const CLICKABLE_ROLES = {_CLICKABLE_ROLES_JS};

function isClickableEl(el) {{
  if (!el || el.nodeType !== 1) return false;
  const tag = el.tagName.toLowerCase();
  if (["button", "a", "input", "select", "textarea", "summary"].includes(tag)) return true;
  const role = el.getAttribute && el.getAttribute("role");
  if (role && CLICKABLE_ROLES.includes(role)) return true;
  if (el.getAttribute && el.getAttribute("contenteditable") === "true") return true;
  if (el.onclick || (el.getAttribute && el.getAttribute("onclick"))) return true;
  const cs = getComputedStyle(el);
  if (cs && cs.cursor === "pointer") return true;
  return false;
}}

function findInnerClickable(host, maxDepth) {{
  if (!host || maxDepth < 0) return null;
  if (isClickableEl(host) && host !== host.ownerDocument) {{
    // The host itself qualifies; no need to dive deeper.
    return host;
  }}
  const root = host.shadowRoot;
  if (!root) return null;
  // Breadth-first over the shadow DOM tree so we pick the topmost
  // clickable rather than the deepest descendant.
  const queue = Array.from(root.querySelectorAll("*"));
  for (const el of queue) {{
    if (isClickableEl(el)) return el;
  }}
  // Nothing at this level - recurse into nested custom-element hosts.
  for (const el of queue) {{
    if (el.shadowRoot) {{
      const hit = findInnerClickable(el, maxDepth - 1);
      if (hit) return hit;
    }}
  }}
  return null;
}}
"""


# Dispatches a pointer+click sequence that survives shadow boundaries.
# Some frameworks listen on pointerdown / pointerup (e.g. Lit-based
# components); only dispatching "click" leaves them unconvinced.
DISPATCH_CLICK_JS = """
function dispatchClick(target) {
  try { target.scrollIntoView({ block: "center" }); } catch (_) {}
  const opts = { bubbles: true, composed: true, cancelable: true, view: window };
  ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((t) => {
    target.dispatchEvent(new MouseEvent(t, opts));
  });
  if (typeof target.click === "function") {
    try { target.click(); } catch (_) {}
  }
}
"""


# Main helper used by elements.click(text=...): walk the composed tree,
# pick the tightest text match, climb/dive to the most interactive
# candidate, and dispatch a composed click sequence on it.
CLICK_BY_TEXT_SHADOW_JS = (
    WALK_ALL_JS
    + FIND_INNER_CLICKABLE_JS
    + DISPATCH_CLICK_JS
    + """
function clickByTextShadow(target) {
  const needle = (target || "").trim().toLowerCase();
  if (!needle) return { ok: false, reason: "empty_text" };
  const candidates = [];
  walkAll(document, (el) => {
    const text = (el.innerText || el.textContent || "").trim();
    if (!text) return;
    if (!text.toLowerCase().includes(needle)) return;
    candidates.push(el);
  });
  if (candidates.length === 0) return { ok: false, reason: "no_match" };

  // Prefer the deepest, smallest match. A match on <body> also matches
  // every descendant; the tightest wrapper is almost always what the
  // caller meant.
  candidates.sort((a, b) => {
    const at = (a.innerText || a.textContent || "").length;
    const bt = (b.innerText || b.textContent || "").length;
    return at - bt;
  });

  // Pick the first clickable candidate (either itself or via a climb/
  // dive through shadow DOM).
  let pick = null;
  for (const el of candidates) {
    if (isClickableEl(el)) { pick = el; break; }
    // Climb up to find a clickable ancestor, crossing shadow boundaries.
    let p = el.parentNode;
    while (p) {
      if (p.host) p = p.host;
      if (p && p.nodeType === 1 && isClickableEl(p)) { pick = p; break; }
      p = p && p.parentNode;
    }
    if (pick) break;
  }
  if (!pick) pick = candidates[0];

  // Normalize to the nearest real navigable ancestor. A <span>/icon
  // inside an <a> often matches isClickableEl via cursor:pointer, and
  // dispatching events on that inner element never fires the <a>'s
  // default navigation. closest() climbs to the owning link/button
  // within the current (light or shadow) DOM context and gives us a
  // target whose .click() actually navigates.
  if (pick && typeof pick.closest === "function") {
    const navigable = pick.closest("a, button, [role='link'], [role='button']");
    if (navigable) pick = navigable;
  }

  // If the pick is a custom element host, dive into its shadow DOM chain
  // until we hit a leaf-level clickable (button, role=radio, etc.).
  const inner = findInnerClickable(pick, 5);
  const targetEl = inner || pick;
  dispatchClick(targetEl);
  return {
    ok: true,
    tag: targetEl.tagName,
    role: targetEl.getAttribute ? (targetEl.getAttribute("role") || null) : null,
    shadow_depth: inner ? (function () {
      // Count how many shadow boundaries we crossed from `pick` to `inner`.
      let d = 0; let cur = inner;
      while (cur && cur !== pick) {
        if (cur.getRootNode && cur.getRootNode().host) d++;
        cur = cur.parentNode || (cur.getRootNode && cur.getRootNode().host);
      }
      return d;
    })() : 0,
    text: (targetEl.innerText || targetEl.textContent || "").trim().slice(0, 80),
  };
}
"""
)


# Click helper used by the explicit click_shadow tool: given a host
# selector in the light DOM, walk into its nested shadow roots and click
# the deepest interactive element we can reach.
CLICK_SHADOW_HOST_JS = (
    FIND_INNER_CLICKABLE_JS
    + DISPATCH_CLICK_JS
    + """
function clickShadowHost(hostSelector, maxDepth) {
  const host = document.querySelector(hostSelector);
  if (!host) return { ok: false, reason: "host_not_found" };
  const inner = findInnerClickable(host, maxDepth || 6);
  if (!inner) return { ok: false, reason: "no_inner_clickable" };
  dispatchClick(inner);
  return {
    ok: true,
    tag: inner.tagName,
    role: inner.getAttribute ? (inner.getAttribute("role") || null) : null,
    text: (inner.innerText || inner.textContent || "").trim().slice(0, 80),
  };
}
"""
)


# Shadow-aware coord lookup for human_click: find the same "tightest
# interactive element" as clickByTextShadow but return its viewport
# center instead of dispatching synthetic events. The human-input
# pipeline then moves the cursor there with a bezier path and performs
# a real CDP Input.dispatchMouseEvent click sequence.
FIND_CLICK_COORDS_BY_TEXT_JS = (
    WALK_ALL_JS
    + FIND_INNER_CLICKABLE_JS
    + """
function findClickCoordsByText(target) {
  const needle = (target || "").trim().toLowerCase();
  if (!needle) return { ok: false, reason: "empty_text" };
  const candidates = [];
  walkAll(document, (el) => {
    const text = (el.innerText || el.textContent || "").trim();
    if (!text || !text.toLowerCase().includes(needle)) return;
    candidates.push(el);
  });
  if (candidates.length === 0) return { ok: false, reason: "no_match" };
  candidates.sort((a, b) => (a.innerText || a.textContent || "").length - (b.innerText || b.textContent || "").length);

  let pick = null;
  for (const el of candidates) {
    if (isClickableEl(el)) { pick = el; break; }
    let p = el.parentNode;
    while (p) {
      if (p.host) p = p.host;
      if (p && p.nodeType === 1 && isClickableEl(p)) { pick = p; break; }
      p = p && p.parentNode;
    }
    if (pick) break;
  }
  if (!pick) pick = candidates[0];

  // Normalize to the nearest real navigable ancestor so the cursor
  // lands on the <a>/<button>'s rect instead of an inner icon span.
  if (pick && typeof pick.closest === "function") {
    const navigable = pick.closest("a, button, [role='link'], [role='button']");
    if (navigable) pick = navigable;
  }

  // Dive into shadow DOM for the real interactive leaf (e.g. the inner
  // <button> of a <nes-button>). Coords come from that element's rect
  // so the human-cursor path lands on the actual hit target.
  const inner = findInnerClickable(pick, 5);
  const targetEl = inner || pick;
  try { targetEl.scrollIntoView({ block: "center" }); } catch (_) {}
  const rect = targetEl.getBoundingClientRect();
  if (!rect || (rect.width === 0 && rect.height === 0)) {
    return { ok: false, reason: "no_rect", tag: targetEl.tagName };
  }
  return {
    ok: true,
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2,
    tag: targetEl.tagName,
    shadow: !!inner,
    text: (targetEl.innerText || targetEl.textContent || "").trim().slice(0, 80),
  };
}
"""
)


# Dump the nested shadow-DOM tree of a light-DOM element for debugging.
# Returns a condensed JSON tree so agents don't have to hand-roll
# recursive execute_js calls to find the right selector path.
DESCRIBE_SHADOW_JS = """
function describeShadow(hostSelector, maxDepth) {
  const host = document.querySelector(hostSelector);
  if (!host) return { ok: false, reason: "host_not_found" };
  const cap = maxDepth || 6;
  function summary(el) {
    const tag = el.tagName.toLowerCase();
    const id = el.id || null;
    const role = el.getAttribute ? el.getAttribute("role") : null;
    const type = el.getAttribute ? el.getAttribute("type") : null;
    const text = (el.innerText || el.textContent || "").trim().slice(0, 40);
    return { tag, id, role, type, text };
  }
  function walk(el, depth) {
    const node = summary(el);
    node.light = [];
    for (const child of Array.from(el.children || [])) {
      if (depth <= 0) break;
      node.light.push(walk(child, depth - 1));
    }
    if (el.shadowRoot && depth > 0) {
      node.shadow = Array.from(el.shadowRoot.children || []).map(
        (c) => walk(c, depth - 1),
      );
    }
    return node;
  }
  return { ok: true, tree: walk(host, cap) };
}
"""


# Used by shadow-aware find_buttons / find_inputs: collect every button-
# or input-like element across light + shadow DOM and return a minimal
# descriptor. The Python side formats the output.
COLLECT_INTERACTIVES_JS = (
    WALK_ALL_JS
    + FIND_INNER_CLICKABLE_JS
    + """
function describeElement(el) {
  const aria = el.getAttribute && el.getAttribute("aria-label");
  if (aria) return aria.trim();
  const title = el.getAttribute && el.getAttribute("title");
  if (title) return title.trim();
  const text = (el.innerText || el.textContent || "").trim();
  if (text) return text.slice(0, 80);
  const svg = el.querySelector && el.querySelector("svg");
  if (svg) {
    const t = svg.querySelector("title");
    if (t && t.textContent) return "Icon: " + t.textContent.trim();
    const use = svg.querySelector("use");
    if (use) {
      const href = use.getAttribute("href") || use.getAttribute("xlink:href");
      if (href) return "Icon: " + href.split("#").pop();
    }
  }
  const img = el.querySelector && el.querySelector("img");
  if (img) return "Image: " + (img.alt || (img.src || "").split("/").pop());
  return "";
}

function hostSelector(el) {
  if (el.id) {
    try { return "#" + CSS.escape(el.id); } catch (_) {}
  }
  const name = el.getAttribute && el.getAttribute("name");
  if (name) return el.tagName.toLowerCase() + "[name='" + name + "']";
  const testId = el.getAttribute && (el.getAttribute("data-testid") || el.getAttribute("data-test-id") || el.getAttribute("data-qa"));
  if (testId) return "[data-testid='" + testId.replace(/'/g, "\\\\'") + "']";
  const aria = el.getAttribute && el.getAttribute("aria-label");
  if (aria && aria.length < 60) {
    const sel = el.tagName.toLowerCase() + "[aria-label='" + aria.replace(/'/g, "\\\\'") + "']";
    try {
      if (document.querySelectorAll(sel).length === 1) return sel;
    } catch (_) {}
  }
  const nesId = el.getAttribute && el.getAttribute("nes-id");
  if (nesId) return el.tagName.toLowerCase() + "[nes-id='" + nesId.replace(/'/g, "\\\\'") + "']";
  return el.tagName.toLowerCase();
}

function visible(el) {
  try {
    const cs = getComputedStyle(el);
    if (cs.display === "none") return false;
    if (cs.visibility === "hidden") return false;
    // NOTE: we intentionally do NOT reject opacity === "0". Custom
    // elements are often set to opacity 0 during hydration (Stencil,
    // Lit, Vue SFC's <transition>) and become visible milliseconds
    // later. A strict opacity filter hides <nes-ovpas-input> and
    // similar components from find_inputs / find_buttons right after
    // navigation.
  } catch (_) {}
  return true;
}

function collectButtons(needle) {
  const needle_l = (needle || "").toLowerCase();
  const seen = new Set();
  const out = [];
  walkAll(document, (el) => {
    if (!isClickableEl(el)) return;
    if (seen.has(el)) return;
    // Prefer collecting the outer custom-element host over its
    // shadow-DOM internals; the host is what the caller can grab via
    // document.querySelector.
    if (el.getRootNode && el.getRootNode().host) {
      // This element lives inside a shadow root; skip it - we'll
      // collect its host instead.
      return;
    }
    if (!visible(el)) return;
    seen.add(el);
    const desc = describeElement(el);
    if (needle_l && !desc.toLowerCase().includes(needle_l)) return;
    out.push({
      selector: hostSelector(el),
      tag: el.tagName,
      type: el.type || (el.getAttribute ? el.getAttribute("role") : null) || "button",
      description: desc || "(no description)",
      custom: el.tagName.includes("-"),
    });
  });
  return out.slice(0, 40);
}

function collectInputs(filterType) {
  const filter = (filterType || "").toLowerCase();
  const seen = new Set();
  const out = [];
  walkAll(document, (el) => {
    if (seen.has(el)) return;
    const tag = el.tagName.toLowerCase();
    const isInput = tag === "input" || tag === "textarea";
    const role = el.getAttribute && el.getAttribute("role");
    const ce = el.getAttribute && el.getAttribute("contenteditable") === "true";
    const customInput = tag.includes("-") && (
      (el.getAttribute && (el.getAttribute("type") === "text"
        || el.getAttribute("data-input")
        || tag.includes("input")
        || tag.includes("field")
        || tag.includes("textbox")
        || tag.includes("textarea")))
    );
    const isTextboxRole = role === "textbox" || role === "searchbox" || role === "combobox";
    if (!(isInput || ce || isTextboxRole || customInput)) return;
    // Skip elements that live INSIDE another shadow root UNLESS they are
    // themselves a custom element host that happens to be shadow-nested.
    // A vanilla <input> inside a shadow root can't be selected via
    // document.querySelector, so skip those; but nested custom-element
    // hosts (like <nes-ovpas-input> inside another component's shadow)
    // are still callable via click_shadow with a chained host path.
    if (el.getRootNode && el.getRootNode().host && !tag.includes("-")) return;
    if (!visible(el)) return;
    if (el.type === "hidden") return;
    seen.add(el);
    const inputType = el.type || role || (ce ? "contenteditable" : tag);
    if (filter && !String(inputType).toLowerCase().includes(filter)) return;
    out.push({
      selector: hostSelector(el),
      tag: el.tagName,
      type: inputType,
      description: describeElement(el) || el.name || inputType,
      custom: tag.includes("-"),
    });
  });
  return out.slice(0, 40);
}
"""
)
