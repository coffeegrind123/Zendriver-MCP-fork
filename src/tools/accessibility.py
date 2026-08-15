"""Accessibility tree with stable uids.

The DOM walker upstream returns numeric IDs based on DOM traversal order;
they re-number on every tree mutation. For long interactive agent flows
we also expose the CDP accessibility tree keyed by stable uids that stay
valid as long as the underlying backend node still exists.

Implementation note: we call ``Accessibility.getFullAXTree`` via a raw CDP
generator that returns the response dict verbatim. Zendriver's bundled
parser ships with a stale ``AXPropertyName`` enum and raises ``ValueError``
on new Chrome values like ``uninteresting``; that exception is swallowed by
the Listener task, which leaves the original future unresolved and the
caller hanging until our timeout guard fires. Parsing the raw JSON here
side-steps the issue entirely.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from zendriver import cdp

from src.errors import AccessibilityUidError, ElementNotFoundError
from src.response import ToolResponse
from src.tools.base import ToolBase


@dataclass(slots=True)
class _UidEntry:
    backend_node_id: int
    role: str
    name: str


def _raw_get_full_ax_tree(
    depth: int | None = None,
) -> Generator[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """CDP generator that returns ``Accessibility.getFullAXTree`` raw nodes.

    Drop-in replacement for ``cdp.accessibility.get_full_ax_tree()`` that
    bypasses zendriver's ``AXNode.from_json`` (which raises on unknown
    enum values). The connection driver hands us the response dict; we
    return it unchanged so the caller can parse what it needs.
    """
    params: dict[str, Any] = {}
    if depth is not None:
        params["depth"] = depth
    response: dict[str, Any] = yield {
        "method": "Accessibility.getFullAXTree",
        "params": params,
    }
    return list(response.get("nodes", []))


class AccessibilityTools(ToolBase):
    """Accessibility-tree snapshots with stable, re-usable uids."""

    def __init__(self, mcp: FastMCP) -> None:
        # Uid -> metadata. Cleared on session reset so old backendDOMNodeIds
        # from a dead browser don't map to random nodes in the new session.
        self._uids: dict[str, _UidEntry] = {}
        super().__init__(mcp)

    def _reset_state(self) -> None:
        self._uids.clear()

    def _register_tools(self) -> None:
        # AX tree is slow on complex pages; 30s keeps the tool callable.
        self._register(self.get_accessibility_snapshot, timeout=30)
        self._register(self.click_by_uid)
        self._register(self.describe_uid)

    async def get_accessibility_snapshot(
        self, max_nodes: int = 400, interesting_only: bool = True
    ) -> dict[str, Any]:
        """Return the page's accessibility tree with stable uids.

        Each node looks like ``{"uid": "ax_1b2c", "role": "button",
        "name": "Submit", "children": [...]}``. The uid can be passed to
        ``click_by_uid`` for deterministic interaction.

        - ``max_nodes``: cap the node count to keep the response small.
        - ``interesting_only``: skip ignored/structural nodes (``none``,
          ``generic``, ``InlineTextBox``, ``StaticText``) while keeping
          their children, so the tree stays compact without losing content.
        """
        tab = self.session.page
        await tab.send(cdp.accessibility.enable())
        raw_nodes: list[dict[str, Any]] = await tab.send(_raw_get_full_ax_tree())

        # Stable uids: reuse the existing uid for a backend_node_id that's
        # still live, so agents can keep a uid between snapshots. Build a
        # lookup of backend_node_id -> uid from the previous cache before
        # rebuilding.
        prev_by_backend: dict[int, str] = {
            entry.backend_node_id: uid for uid, entry in self._uids.items() if entry.backend_node_id
        }
        self._uids.clear()

        by_id: dict[str, dict[str, Any]] = {
            str(node.get("nodeId")): node for node in raw_nodes if node.get("nodeId") is not None
        }

        # The root is the node without a parent, not necessarily raw_nodes[0].
        roots = [n for n in raw_nodes if n.get("parentId") in (None, "")]

        interactive_roles = {
            "button",
            "link",
            "textbox",
            "searchbox",
            "checkbox",
            "radio",
            "switch",
            "combobox",
            "menuitem",
            "menuitemcheckbox",
            "menuitemradio",
            "tab",
            "option",
            "treeitem",
        }
        noise_roles = {
            "none",
            "generic",
            "presentation",
            "InlineTextBox",
            "StaticText",
        }

        def render(node: dict[str, Any]) -> list[dict[str, Any]]:
            """Return a list of rendered children for ``node``.

            A list (not a single dict) makes it trivial to flatten skipped
            wrapper nodes: we just concatenate their children into the
            parent's list.
            """
            role = _string_value(node.get("role"))
            name = _string_value(node.get("name"))
            ignored = bool(node.get("ignored"))
            backend_id = int(node.get("backendDOMNodeId") or 0)
            child_ids = node.get("childIds") or []

            rendered_children: list[dict[str, Any]] = []
            for cid in child_ids:
                child = by_id.get(str(cid))
                if child is None:
                    continue
                rendered_children.extend(render(child))

            skip = False
            if interesting_only:
                is_interactive = role in interactive_roles
                if not is_interactive:
                    if ignored or role in noise_roles:
                        skip = True
                    elif not role and not name:
                        skip = True

            if skip:
                # Don't emit ourselves, bubble children up instead.
                return rendered_children

            if len(self._uids) >= max_nodes:
                return rendered_children

            # Reuse the previous snapshot's uid for this backend_node_id
            # whenever possible so agents can cache uids across snapshots.
            uid = prev_by_backend.get(backend_id) or f"ax_{uuid4().hex[:8]}"
            self._uids[uid] = _UidEntry(backend_node_id=backend_id, role=role, name=name)
            entry: dict[str, Any] = {"uid": uid, "role": role, "name": name}
            if rendered_children:
                entry["children"] = rendered_children
            return [entry]

        if not roots:
            return ToolResponse(summary="Empty accessibility tree", data={"nodes": []}).to_dict()

        # Some pages have multiple top-level AX roots (OOPIF iframes,
        # embedded PDFs, etc.). Render every root and flatten the rendered
        # node lists together so nothing is silently dropped.
        top: list[dict[str, Any]] = []
        for root in roots:
            top.extend(render(root))

        if len(top) == 1:
            tree: dict[str, Any] = top[0]
        else:
            tree = {"uid": "", "role": "", "name": "", "children": top}
        return ToolResponse(
            summary=f"Accessibility snapshot: {len(self._uids)} nodes",
            data={"tree": tree, "uid_count": len(self._uids)},
        ).to_dict()

    async def describe_uid(self, uid: str) -> dict[str, Any]:
        """Return the cached role + name for a uid, or raise if unknown."""
        entry = self._uids.get(uid)
        if entry is None:
            raise AccessibilityUidError(f"Unknown accessibility uid: {uid}")
        return {
            "uid": uid,
            "role": entry.role,
            "name": entry.name,
            "backend_node_id": entry.backend_node_id,
        }

    async def click_by_uid(self, uid: str) -> str:
        """Click the DOM node behind an accessibility uid.

        Resolves the cached ``backend_node_id`` to a remote object, then
        dispatches a native element click. Raises ``AccessibilityUidError``
        if the uid is unknown or the node was removed since the snapshot.
        """
        entry = self._uids.get(uid)
        if entry is None or entry.backend_node_id == 0:
            raise AccessibilityUidError(f"Unknown or non-DOM uid: {uid}")

        tab = self.session.page
        try:
            remote = await tab.send(
                cdp.dom.resolve_node(backend_node_id=cdp.dom.BackendNodeId(entry.backend_node_id))
            )
        except Exception as exc:  # zendriver raises ProtocolException on stale nodes
            raise AccessibilityUidError(f"uid {uid} no longer maps to a live DOM node") from exc

        if remote is None or remote.object_id is None:
            raise ElementNotFoundError(f"uid:{uid}")

        quads = await tab.send(cdp.dom.get_content_quads(object_id=remote.object_id))
        if not quads:
            raise AccessibilityUidError(f"uid {uid} has no visible content box")
        q = quads[0]
        cx = (q[0] + q[2] + q[4] + q[6]) / 4
        cy = (q[1] + q[3] + q[5] + q[7]) / 4
        await tab.send(
            cdp.input_.dispatch_mouse_event(
                type_="mousePressed",
                x=cx,
                y=cy,
                button=cdp.input_.MouseButton.LEFT,
                click_count=1,
            )
        )
        await tab.send(
            cdp.input_.dispatch_mouse_event(
                type_="mouseReleased",
                x=cx,
                y=cy,
                button=cdp.input_.MouseButton.LEFT,
                click_count=1,
            )
        )
        return f"Clicked uid {uid} ({entry.role}: {entry.name[:40]})"


def _string_value(ax_value: dict[str, Any] | None) -> str:
    """Extract the stringified value from a raw ``AXValue`` dict.

    The CDP ``AXValue`` shape is ``{"type": "...", "value": ...}``. We only
    care about string-ish values here.
    """
    if not ax_value:
        return ""
    value = ax_value.get("value")
    if value is None:
        return ""
    return str(value)
