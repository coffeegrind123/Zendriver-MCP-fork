"""Screencast: CDP-driven frame capture to disk.

Uses ``Page.startScreencast`` which emits ``screencastFrame`` events with
base64-encoded JPEG/PNG data. We write each frame to a directory and ack the
frame so Chrome keeps sending new ones.
"""

from __future__ import annotations

import asyncio
import base64
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from zendriver import cdp

from src.artifacts import resolve_artifact_dir, resolve_artifact_path
from src.errors import ZendriverMCPError
from src.response import ToolResponse
from src.tools.base import ToolBase

FrameHandler = Callable[[cdp.page.ScreencastFrame], Awaitable[None]]


class ScreencastTools(ToolBase):
    """Start/stop a screencast recording into a directory of frames."""

    def __init__(self, mcp: FastMCP) -> None:
        self._frame_dir: Path | None = None
        self._frame_count = 0
        self._frame_format = "jpeg"
        self._handler: FrameHandler | None = None
        super().__init__(mcp)

    def _reset_state(self) -> None:
        self._frame_dir = None
        self._frame_count = 0
        self._handler = None

    def _register_tools(self) -> None:
        self._register(self.start_screencast)
        self._register(self.stop_screencast)
        # ffmpeg encoding of a long capture can take a while.
        self._register(self.export_screencast_mp4, timeout=300)
        self._register(self.check_ffmpeg_available, timeout=10)

    async def start_screencast(
        self,
        output_dir: str = "",
        fmt: str = "jpeg",
        quality: int = 80,
        max_width: int = 1280,
        every_nth_frame: int = 1,
    ) -> dict[str, object]:
        """Begin recording screencast frames to ``output_dir``.

        - ``fmt``: ``"jpeg"`` (smaller) or ``"png"`` (lossless).
        - ``quality``: 0-100, JPEG only.
        - ``max_width``: frames are downscaled to this width if larger.
        - ``every_nth_frame``: skip frames between captures (1 = every frame).
        """
        if self._frame_dir is not None:
            raise ZendriverMCPError("A screencast is already running; stop it first.")
        if fmt not in {"jpeg", "png"}:
            raise ZendriverMCPError(f"fmt must be 'jpeg' or 'png', got {fmt!r}")

        target = resolve_artifact_dir(output_dir, default_prefix="zendriver-screencast")

        tab = self.session.page
        extension = "jpg" if fmt == "jpeg" else "png"

        async def on_frame(event: cdp.page.ScreencastFrame) -> None:
            # Late straggler after stop_screencast nulled the frame dir.
            # Drop silently rather than raising AssertionError from the
            # Listener task.
            frame_dir = self._frame_dir
            if frame_dir is None:
                return
            index = self._frame_count
            self._frame_count += 1
            path = frame_dir / f"frame-{index:06d}.{extension}"
            path.write_bytes(base64.b64decode(event.data))
            # Acking tells Chrome we consumed the frame - without it the stream stalls.
            await tab.send(cdp.page.screencast_frame_ack(session_id=event.session_id))

        self._frame_dir = target
        self._frame_count = 0
        self._frame_format = extension
        self._handler = on_frame

        tab.add_handler(cdp.page.ScreencastFrame, on_frame)
        await tab.send(
            cdp.page.start_screencast(
                format_=fmt,
                quality=quality,
                max_width=max_width,
                every_nth_frame=every_nth_frame,
            )
        )
        return ToolResponse(
            summary=f"Screencast started -> {target}",
            data={"format": fmt, "quality": quality, "every_nth_frame": every_nth_frame},
            files=[str(target)],
        ).to_dict()

    async def stop_screencast(self) -> dict[str, object]:
        """Stop the active screencast and return the frame directory + count."""
        if self._frame_dir is None or self._handler is None:
            raise ZendriverMCPError("No screencast in progress.")

        tab = self.session.page
        await tab.send(cdp.page.stop_screencast())

        directory = self._frame_dir
        frames = self._frame_count
        handler = self._handler

        # Clear the target dir *before* draining and detaching. Any
        # straggler frame that arrives during the sleep will see
        # _frame_dir = None and drop silently instead of asserting.
        self._frame_dir = None
        self._frame_count = 0
        self._handler = None

        await asyncio.sleep(0.2)
        handlers_list = tab.handlers.get(cdp.page.ScreencastFrame)
        if handlers_list and handler in handlers_list:
            handlers_list.remove(handler)
        return ToolResponse(
            summary=f"Screencast stopped: {frames} frames in {directory}",
            data={"frame_count": frames, "format": self._frame_format},
            files=[str(directory)],
        ).to_dict()

    async def check_ffmpeg_available(self) -> dict[str, Any]:
        """Report whether the ``ffmpeg`` CLI is installed. Required for
        ``export_screencast_mp4``.
        """
        binary = shutil.which("ffmpeg")
        if not binary:
            return {
                "available": False,
                "hint": "Install with `brew install ffmpeg` / `apt install ffmpeg`.",
            }
        proc = await asyncio.create_subprocess_exec(
            binary,
            "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        first_line = out.decode().splitlines()[0] if out else ""
        return {"available": True, "path": binary, "version": first_line}

    async def export_screencast_mp4(
        self,
        frames_dir: str,
        output_path: str = "",
        fps: int = 30,
    ) -> dict[str, Any]:
        """Stitch screencast frames in ``frames_dir`` into an mp4.

        Uses the default ``frame-%06d.jpg`` / ``frame-%06d.png`` pattern that
        ``start_screencast`` writes. ``fps`` is the playback rate (not the
        capture rate). Requires ffmpeg.
        """
        binary = shutil.which("ffmpeg")
        if not binary:
            raise ZendriverMCPError(
                "ffmpeg not found. Install with `brew install ffmpeg` or `apt install ffmpeg`."
            )

        src = Path(frames_dir).expanduser().resolve()
        if not src.is_dir():
            raise ZendriverMCPError(f"frames_dir does not exist: {src}")

        jpegs = list(src.glob("frame-*.jpg"))
        pngs = list(src.glob("frame-*.png"))
        if jpegs and not pngs:
            pattern = str(src / "frame-%06d.jpg")
            count = len(jpegs)
        elif pngs:
            pattern = str(src / "frame-%06d.png")
            count = len(pngs)
        else:
            raise ZendriverMCPError(f"No frame-*.jpg or frame-*.png files found in {src}")

        target = resolve_artifact_path(
            output_path, default_prefix="zendriver-screencast", default_ext="mp4"
        )

        args = [
            binary,
            "-y",
            "-framerate",
            str(fps),
            "-i",
            pattern,
            "-vf",
            # Ensure even dimensions for yuv420p.
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(target),
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ZendriverMCPError(f"ffmpeg failed ({proc.returncode}): {stderr.decode()[:500]}")

        size = target.stat().st_size
        return ToolResponse(
            summary=f"Encoded {count} frames -> {target} ({size // 1024} KiB)",
            data={"frame_count": count, "fps": fps, "size_bytes": size},
            files=[str(target)],
        ).to_dict()
