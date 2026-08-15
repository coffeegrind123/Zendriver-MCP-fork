"""Sanity tests for device + network profile definitions (no browser needed)."""

from __future__ import annotations

from src.tools.emulation import DEVICE_PROFILES, NETWORK_PROFILES


def test_device_profiles_have_sensible_dimensions() -> None:
    assert DEVICE_PROFILES, "expected at least one device profile"
    for key, p in DEVICE_PROFILES.items():
        assert p.width > 0, f"{key} has non-positive width"
        assert p.height > 0, f"{key} has non-positive height"
        assert 0.5 <= p.device_scale_factor <= 4.0, f"{key} DPR out of range"


def test_device_profiles_keys_are_kebab_case() -> None:
    for key in DEVICE_PROFILES:
        assert key == key.lower()
        assert " " not in key
        assert "_" not in key


def test_network_profiles_include_devtools_presets() -> None:
    expected = {"offline", "slow-3g", "fast-3g", "4g", "no-throttling"}
    assert expected.issubset(NETWORK_PROFILES.keys())


def test_network_profile_fields() -> None:
    for key, p in NETWORK_PROFILES.items():
        assert {"offline", "latency", "download", "upload"}.issubset(p.keys()), key
        assert p["latency"] >= 0
