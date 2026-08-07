"""Dependency-light dispatcher for the installed ``sciona`` command."""

from __future__ import annotations

import sys


def main() -> None:
    """Avoid importing optional agent stacks for catalog search and install."""
    if len(sys.argv) >= 3 and sys.argv[1] == "catalog" and sys.argv[2] in {
        "build",
        "search",
        "search-artifacts",
        "plan",
        "install",
        "install-artifact",
    }:
        from sciona.catalog_cli import main as catalog_main

        catalog_main(sys.argv[2:])
        return

    from sciona.cli import main as legacy_main

    legacy_main()
