"""DevTools-parity tools: performance traces, heap snapshots.

These use the lower-level CDP event streams (``Tracing.dataCollected``,
``HeapProfiler.addHeapSnapshotChunk``) rather than higher-level wrappers, so
the captured data is byte-for-byte what Chrome DevTools would save.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from mcp.server.fastmcp import FastMCP
from zendriver import cdp

from src.artifacts import resolve_artifact_path
from src.errors import TracingError
from src.tools.base import ToolBase

DataHandler = Callable[[cdp.tracing.DataCollected], Awaitable[None]]
CompleteHandler = Callable[[cdp.tracing.TracingComplete], Awaitable[None]]


def _safe_detach(tab: object, event_type: type, handler: object) -> None:
    """Remove ``handler`` from ``tab.handlers[event_type]`` without raising.

    ``tab.handlers.get(x, []).remove(...)`` returns a throwaway list on a
    missing key, so the removal silently no-ops in that case; and calling
    ``.remove()`` on a list that doesn't hold ``handler`` would raise
    ``ValueError``. Both are tolerated here.
    """
    handlers: list = getattr(tab, "handlers", {}).get(event_type)  # type: ignore[assignment]
    if handlers and handler in handlers:
        handlers.remove(handler)


# Roughly matches DevTools' default "Performance" trace profile.
_DEFAULT_CATEGORIES = ",".join(
    [
        "devtools.timeline",
        "v8.execute",
        "disabled-by-default-devtools.timeline",
        "disabled-by-default-devtools.timeline.frame",
        "toplevel",
        "blink.console",
        "blink.user_timing",
        "latencyInfo",
        "disabled-by-default-v8.cpu_profiler",
        "disabled-by-default-devtools.timeline.stack",
    ]
)


class DevToolsTools(ToolBase):
    """Performance trace recording and heap snapshot capture."""

    def __init__(self, mcp: FastMCP) -> None:
        self._trace_events: list[dict] | None = None
        self._trace_complete: asyncio.Event | None = None
        self._trace_handlers: tuple[DataHandler, CompleteHandler] | None = None
        super().__init__(mcp)

    def _reset_state(self) -> None:
        self._trace_events = None
        self._trace_complete = None
        self._trace_handlers = None

    def _register_tools(self) -> None:
        self._register(self.start_trace)
        # stop_trace waits up to 30s internally for tracingComplete; pad it.
        self._register(self.stop_trace, timeout=120)
        # Heap snapshots on large pages stream megabytes of chunks.
        self._register(self.take_heap_snapshot, timeout=180)

    async def start_trace(self, categories: str = "") -> str:
        """Begin recording a performance trace on the current tab.

        ``categories`` is a comma-separated CDP category filter; empty string
        uses the DevTools Performance-panel default profile. Only one trace
        can be active per tool instance at a time.
        """
        if self._trace_events is not None:
            return "Error: A trace is already in progress. Call stop_trace first."

        events: list[dict] = []
        complete = asyncio.Event()

        async def on_data(event: cdp.tracing.DataCollected) -> None:
            events.extend(event.value)

        async def on_complete(_: cdp.tracing.TracingComplete) -> None:
            complete.set()

        tab = self.session.page
        tab.add_handler(cdp.tracing.DataCollected, on_data)
        tab.add_handler(cdp.tracing.TracingComplete, on_complete)

        self._trace_events = events
        self._trace_complete = complete
        self._trace_handlers = (on_data, on_complete)

        await tab.send(
            cdp.tracing.start(
                categories=categories or _DEFAULT_CATEGORIES,
                transfer_mode="ReportEvents",
            )
        )
        return "Trace started"

    async def stop_trace(self, file_path: str = "") -> str:
        """Stop the active trace and write it to disk as JSON.

        Returns the absolute file path. If ``file_path`` is empty, writes to
        a timestamped file in the system temp directory. The output matches
        the format that Chrome DevTools' "Load profile" accepts.

        Handlers and state are cleared via ``finally`` so a failed
        ``tracing.end()`` or a late ``tracingComplete`` never leaves the
        tool stuck on "a trace is already in progress" for the rest of the
        process.
        """
        if (
            self._trace_events is None
            or self._trace_complete is None
            or self._trace_handlers is None
        ):
            raise TracingError("No trace in progress. Call start_trace first.")

        events = self._trace_events
        complete = self._trace_complete
        on_data, on_complete = self._trace_handlers
        tab = self.session.page

        try:
            await tab.send(cdp.tracing.end())
            try:
                await asyncio.wait_for(complete.wait(), timeout=30.0)
            except TimeoutError as exc:
                raise TracingError("Trace did not complete within 30s") from exc
        finally:
            # Drop handlers whether we succeeded or failed; either way the
            # tool is no longer mid-trace.
            _safe_detach(tab, cdp.tracing.DataCollected, on_data)
            _safe_detach(tab, cdp.tracing.TracingComplete, on_complete)
            self._trace_events = None
            self._trace_complete = None
            self._trace_handlers = None

        target = resolve_artifact_path(
            file_path, default_prefix="zendriver-trace", default_ext="json"
        )
        target.write_text(json.dumps({"traceEvents": events}))
        return f"Trace saved: {target} ({len(events)} events)"

    async def take_heap_snapshot(self, file_path: str = "") -> str:
        """Capture a V8 heap snapshot and write it to disk.

        The output is the same ``.heapsnapshot`` format Chrome DevTools saves,
        loadable via DevTools > Memory > Load.
        """
        tab = self.session.page

        chunks: list[str] = []
        finished = asyncio.Event()

        async def on_chunk(event: cdp.heap_profiler.AddHeapSnapshotChunk) -> None:
            chunks.append(event.chunk)

        async def on_progress(event: cdp.heap_profiler.ReportHeapSnapshotProgress) -> None:
            if event.finished:
                finished.set()

        tab.add_handler(cdp.heap_profiler.AddHeapSnapshotChunk, on_chunk)
        tab.add_handler(cdp.heap_profiler.ReportHeapSnapshotProgress, on_progress)

        try:
            await tab.send(cdp.heap_profiler.enable())
            await tab.send(cdp.heap_profiler.take_heap_snapshot(report_progress=True))
            # Once take_heap_snapshot's RPC returns, chunks are all delivered.
            # Wait briefly for the last progress ping if the backend emits one.
            try:
                await asyncio.wait_for(finished.wait(), timeout=2.0)
            except TimeoutError:
                pass  # some Chrome versions don't emit the final progress event
        finally:
            _safe_detach(tab, cdp.heap_profiler.AddHeapSnapshotChunk, on_chunk)
            _safe_detach(tab, cdp.heap_profiler.ReportHeapSnapshotProgress, on_progress)

        target = resolve_artifact_path(
            file_path, default_prefix="zendriver-heap", default_ext="heapsnapshot"
        )
        target.write_text("".join(chunks))
        return f"Heap snapshot saved: {target} ({len(chunks)} chunks)"
