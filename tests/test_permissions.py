"""Validate the short permission-name table maps to real CDP enum members."""

from __future__ import annotations

from zendriver import cdp

from src.tools.permissions import _COMMON_PERMISSIONS


def test_common_permissions_are_real_enum_members() -> None:
    for name, value in _COMMON_PERMISSIONS.items():
        assert isinstance(value, cdp.browser.PermissionType), name
        # to_json round-trip
        assert cdp.browser.PermissionType.from_json(value.value) is value


def test_common_permissions_keys_are_kebab_case_ascii() -> None:
    for key in _COMMON_PERMISSIONS:
        assert key == key.lower()
        assert " " not in key
        assert "_" not in key
