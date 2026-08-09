#!/usr/bin/env python3
"""Run the cross-disciplinary blind catalog evaluation suite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sciona.blind_evaluation import (
    evaluate_postgres_scale,
    load_blind_suite,
    run_agent_comparison,
    run_catalog_suite,
    write_report,
)
from sciona.provider_runtime import RemoteCatalogClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "evaluations"
        / "cross_disciplinary_blind.json",
    )
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ecg-edf", type=Path)
    parser.add_argument("--database-url")
    parser.add_argument("--scale-target", type=int, default=10_500)
    parser.add_argument("--with-agents", action="store_true")
    parser.add_argument("--tool-python", type=Path)
    parser.add_argument("--small-model", default="gpt-5.3-codex-spark")
    parser.add_argument("--large-model", default="gpt-5.6-sol")
    parser.add_argument("--agent-timeout", type=float, default=600.0)
    parser.add_argument("--agent-repetitions", type=int, default=1)
    args = parser.parse_args()

    suite = load_blind_suite(args.manifest)
    report = run_catalog_suite(
        suite=suite,
        client=RemoteCatalogClient(args.api_url),
        output_dir=args.output / "solutions",
        edf_path=args.ecg_edf,
    )
    if args.database_url:
        report["postgres_scale"] = evaluate_postgres_scale(
            args.database_url, suite, target_rows=args.scale_target
        )
        report["passed"] = bool(report["passed"]) and (
            report["postgres_scale"]["row_count"] > 10_000
            and report["postgres_scale"]["top_k_recall"] == 1.0
        )
    if args.with_agents:
        if args.tool_python is None:
            parser.error("--with-agents requires --tool-python")
        report["agent_comparison"] = run_agent_comparison(
            suite,
            api_url=args.api_url,
            tool_python=args.tool_python,
            output_dir=args.output / "agents",
            small_model=args.small_model,
            large_model=args.large_model,
            edf_path=args.ecg_edf,
            timeout=args.agent_timeout,
            repetitions=args.agent_repetitions,
        )
    report_path = args.output / "report.json"
    write_report(report_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
