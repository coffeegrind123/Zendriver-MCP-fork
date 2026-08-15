"""Path-sandbox tests for ``src.artifacts.resolve_artifact_path``."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.artifacts import resolve_artifact_path
from src.errors import ZendriverMCPError


def test_empty_path_generates_timestamped_tempfile() -> None:
    target = resolve_artifact_path("", default_prefix="test", default_ext="json")
    # On macOS /tmp is a symlink to /private/tmp; compare resolved values.
    assert target.parent.resolve() == Path(tempfile.gettempdir()).resolve()
    assert target.name.startswith("test-")
    assert target.name.endswith(".json")


def test_home_path_is_allowed() -> None:
    target = resolve_artifact_path(
        str(Path.home() / "demo-zendriver.txt"),
        default_prefix="unused",
        default_ext="txt",
    )
    assert target == (Path.home() / "demo-zendriver.txt").resolve()


def test_tmp_path_is_allowed() -> None:
    candidate = Path(tempfile.gettempdir()) / "zendriver-under-tmp.txt"
    target = resolve_artifact_path(str(candidate), default_prefix="unused", default_ext="txt")
    assert target == candidate.resolve()


def test_system_path_is_rejected() -> None:
    # /etc is outside home + tmp; it should raise.
    with pytest.raises(ZendriverMCPError, match="Refusing to write"):
        resolve_artifact_path("/etc/zendriver-should-fail", default_prefix="x", default_ext="y")


def test_env_root_extends_allowed_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ZENDRIVER_MCP_ARTIFACT_ROOT", str(tmp_path))
    target = resolve_artifact_path(
        str(tmp_path / "extra" / "out.json"),
        default_prefix="x",
        default_ext="json",
    )
    assert target == (tmp_path / "extra" / "out.json").resolve()
    assert target.parent.exists()


def test_traversal_still_resolves_under_home() -> None:
    # Even with "..", if the final resolved path is under $HOME we accept;
    # the sandbox cares about the resolved path, not the literal string.
    tricky = str(Path.home() / ".." / Path.home().name / "ok.txt")
    target = resolve_artifact_path(tricky, default_prefix="x", default_ext="txt")
    assert Path.home() in target.parents or target.parent == Path.home()
