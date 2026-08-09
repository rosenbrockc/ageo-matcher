#!/usr/bin/env python3
"""Fetch and verify the public datasets used by Sciona evaluations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sciona.open_datasets import fetch_open_data, load_open_data_registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "evaluations"
        / "open_data_sources.json",
    )
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    roots = fetch_open_data(
        load_open_data_registry(args.registry), cache_dir=args.cache_dir
    )
    print(json.dumps({key: str(value) for key, value in roots.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
