"""Defensive monkey-patches that harden zendriver against enum version skew.

Zendriver's bundled CDP bindings hard-code enum members from the Chrome
revision they shipped with. When Chrome adds a new value (e.g. a new
``AXPropertyName``) the generated ``from_json`` classmethods raise
``ValueError``, which bubbles up through :meth:`Transaction.__call__` into the
Listener task and gets silently swallowed. The original caller's future is
never resolved, so every affected tool call hangs forever.

We can't change zendriver without vendoring it, so we patch
``Transaction.__call__`` to convert parser errors into exceptions on the
future. The awaiter then wakes up with a clear, typed error instead of
sitting on a stuck future. (Ported from bituq/zendriver-mcp.)
"""

from __future__ import annotations

import logging
from typing import Any

from zendriver.core import connection as _zdc

logger = logging.getLogger(__name__)

_PATCH_ATTR = "__zendriver_mcp_patched__"


def _safe_transaction_call(self: _zdc.Transaction, **response: Any) -> None:
    """Drop-in replacement for ``Transaction.__call__`` that never leaves a
    future unresolved on a parser error."""
    if self.__cdp_obj__ is None:
        return
    if "error" in response:
        self.set_exception(_zdc.ProtocolException(response["error"]))
        return
    try:
        self.__cdp_obj__.send(response.get("result", {}))
    except StopIteration as stop:
        self.set_result(stop.value)
        return
    except Exception as exc:
        logger.debug(
            "CDP response parser raised for %s: %r",
            getattr(self, "method", "<unknown>"),
            exc,
        )
        self.set_exception(exc)
        return
    self.set_exception(
        _zdc.ProtocolException(f"could not parse the cdp response: {response}")
    )


def apply_zendriver_patches() -> None:
    """Install all compatibility patches. Safe to call multiple times."""
    if getattr(_zdc.Transaction, _PATCH_ATTR, False):
        return
    _zdc.Transaction.__call__ = _safe_transaction_call  # type: ignore[method-assign]
    setattr(_zdc.Transaction, _PATCH_ATTR, True)
    logger.debug("zendriver Transaction.__call__ patched for parser-error handling")


# Apply at import time so anyone importing this module (transitively) is
# protected before the first CDP round-trip.
apply_zendriver_patches()
