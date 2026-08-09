#!/usr/bin/env python3
"""Fetch pinned public data and run the cross-disciplinary blind suite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sciona.blind_evaluation import (
    load_blind_suite,
    run_agent_comparison,
    run_catalog_suite,
    write_report,
)
from sciona.open_datasets import (
    default_open_data_cache,
    fetch_open_data,
    load_open_data_registry,
)
from sciona.provider_runtime import RemoteCatalogClient


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "evaluations" / "open_data_blind.json",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=ROOT / "evaluations" / "open_data_sources.json",
    )
    parser.add_argument("--cache-dir", type=Path, default=default_open_data_cache())
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--with-agents", action="store_true")
    parser.add_argument("--tool-python", type=Path)
    parser.add_argument("--small-model", default="gpt-5.3-codex-spark")
    parser.add_argument("--large-model", default="gpt-5.6-sol")
    parser.add_argument("--agent-timeout", type=float, default=600.0)
    parser.add_argument("--agent-repetitions", type=int, default=1)
    args = parser.parse_args()

    if args.with_agents and args.tool_python is None:
        parser.error("--with-agents requires --tool-python")
    if args.agent_repetitions < 1:
        parser.error("--agent-repetitions must be positive")

    suite = load_blind_suite(args.manifest)
    registry = load_open_data_registry(args.registry)
    dataset_roots = fetch_open_data(registry, cache_dir=args.cache_dir)
    report = run_catalog_suite(
        suite=suite,
        client=RemoteCatalogClient(args.api_url),
        output_dir=args.output / "solutions",
        dataset_roots=dataset_roots,
    )
    report["datasets"] = {
        source.source_id: {
            "title": source.title,
            "landing_url": source.landing_url,
            "license": source.license_spdx,
            "citation": source.citation,
        }
        for source in registry.sources
    }
    if args.with_agents:
        report["agent_comparison"] = run_agent_comparison(
            suite,
            api_url=args.api_url,
            tool_python=args.tool_python,
            output_dir=args.output / "agents",
            small_model=args.small_model,
            large_model=args.large_model,
            edf_path=None,
            dataset_roots=dataset_roots,
            timeout=args.agent_timeout,
            repetitions=args.agent_repetitions,
        )
    report_path = args.output / "report.json"
    write_report(report_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
