"""Tests for the dependency-light installed CLI dispatcher."""

from __future__ import annotations

import builtins
import sys

from sciona import entrypoint


def test_catalog_search_dispatch_does_not_import_legacy_cli(
    monkeypatch,
) -> None:
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert name != "sciona.cli"
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "argv", ["sciona", "catalog", "search", "--help"])
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    try:
        entrypoint.main()
    except SystemExit as exc:
        assert exc.code == 0


def test_catalog_build_dispatch_does_not_import_legacy_cli(monkeypatch) -> None:
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert name != "sciona.cli"
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "argv", ["sciona", "catalog", "build", "--help"])
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    try:
        entrypoint.main()
    except SystemExit as exc:
        assert exc.code == 0
