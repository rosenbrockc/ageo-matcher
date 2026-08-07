"""CLI entrypoint for AGEO-Matcher."""

from __future__ import annotations

import argparse
import sys

# ---------------------------------------------------------------------------
# Backwards-compatible re-exports — tests and other modules import these
# from sciona.cli. Do NOT remove without updating all call sites.
# ---------------------------------------------------------------------------
from sciona.commands._helpers import (  # noqa: F401
    RetrievalPolicy,
    _add_label_argument,
    _add_mode_argument,
    _create_llm,
    _create_llm_router,
    _create_proof_env,
    _create_shared_context,
    _load_architect_catalog,
    _load_semantic_index,
    _load_skill_index_or_empty,
    _mode_feature_summary,
    _parse_prompt_benchmark_provider_specs,
    _print_mode_summary,
    _print_prompt_routing_summary,
    _print_retrieval_policy,
    _print_shared_context_metrics,
    _resolve_retrieval_policy,
    _routing_metadata_summary,
    _run_async_command,
    _shared_context_metadata,
    _snapshot_shared_context_metrics,
    _summarize_prompt_routing,
    _warm_llm_if_supported,
    _write_shared_context_metrics_file,
)
from sciona.commands.benchmark_cmds import (  # noqa: F401
    _benchmark_validation_metadata,
    _cmd_benchmark_validate,
    _cmd_prompt_benchmark,
    _cmd_release_validate,
)
from sciona.commands.decompose_cmds import (  # noqa: F401
    _cmd_decompose,
    _cmd_history,
    _run_decompose,
)
from sciona.commands.index_cmds import (  # noqa: F401
    _cmd_catalog_gaps,
    _cmd_index_build,
    _cmd_skill_index,
    _cmd_skill_ingest,
    _cmd_skill_search,
)
from sciona.commands.ingest_cmds import (  # noqa: F401
    _cmd_ingest,
    _cmd_ingest_status,
)
from sciona.commands.match_cmds import _cmd_match  # noqa: F401
from sciona.commands.optimize_cmds import (  # noqa: F401
    _cmd_optimize,
    _cmd_profile,
)
from sciona.commands.run_cmds import (  # noqa: F401
    _cmd_run,
)
from sciona.commands.sources_cmds import (  # noqa: F401
    _cmd_sources_list,
    _cmd_sources_sync,
)
from sciona.commands.synthesize_cmds import (  # noqa: F401
    _cmd_assemble,
    _cmd_export,
    _cmd_synthesize,
)
from sciona.commands.atom_cmds import _cmd_atom_publish  # noqa: F401
from sciona.commands.bounty_cmds import _cmd_bounty_generate  # noqa: F401
from sciona.commands.catalog_cmds import (  # noqa: F401
    _cmd_catalog_audit_providers,
    _cmd_catalog_install,
    _cmd_catalog_publish_providers,
    _cmd_catalog_reconcile_providers,
    _cmd_catalog_search,
    _cmd_catalog_sync,
    _cmd_catalog_validate_provider_release,
)
from sciona.commands.login_cmds import _cmd_login  # noqa: F401
from sciona.commands.receipt_cmds import (  # noqa: F401
    _cmd_receipt_sign,
    _cmd_receipt_verify,
)
from sciona.commands.telemetry_cmds import (  # noqa: F401
    _cmd_telemetry_list,
    _cmd_telemetry_show,
)
from sciona.commands.upsert_cmds import _cmd_upsert_cdg  # noqa: F401
from sciona.commands.visualize_cmds import _cmd_visualize  # noqa: F401
from sciona.principal.metric_selection import SUPPORTED_OBJECTIVES
from sciona.runtime_paths import (  # noqa: F401
    _build_rapid_direct_cdg,
    _run_rapid_direct_match,
    _run_structured_single_pass,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sciona",
        description="AGEO-Matcher: ground predicates into verified library functions",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- index build ---
    index_parser = subparsers.add_parser("index", help="Index management")
    index_sub = index_parser.add_subparsers(dest="index_command")

    build_parser = index_sub.add_parser("build", help="Build FAISS index from library")
    build_parser.add_argument(
        "--prover",
        choices=["lean4", "coq", "python"],
        required=True,
        help="Proof assistant",
    )
    build_parser.add_argument(
        "--path", type=str, default="", help="Path to Coq project (for --prover coq)"
    )
    build_parser.add_argument(
        "--packages",
        type=str,
        default=None,
        help="Comma-separated Python packages to index (for --prover python, default: numpy,scipy)",
    )
    build_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory for index (default: from .env)",
    )

    # --- skill ---
    skill_parser = subparsers.add_parser(
        "skill", help="Manage the algorithmic skill catalog"
    )
    skill_sub = skill_parser.add_subparsers(dest="skill_command")

    ingest_parser = skill_sub.add_parser(
        "ingest", help="Ingest primitives from a source"
    )
    ingest_parser.add_argument(
        "--source",
        choices=["clrs", "coq100"],
        required=True,
        help="Source to ingest from",
    )
    ingest_parser.add_argument(
        "--path", type=str, required=True, help="Path to the cloned source repo"
    )
    ingest_parser.add_argument(
        "--output", type=str, default=None, help="Output path for catalog JSON"
    )

    skill_index_parser = skill_sub.add_parser(
        "index", help="Build FAISS skill index from catalog"
    )
    skill_index_parser.add_argument(
        "--catalog",
        type=str,
        default=None,
        help="Path to catalog JSON (default: auto-detect)",
    )
    skill_index_parser.add_argument(
        "--output", type=str, default=None, help="Output directory for skill index"
    )
    skill_index_parser.add_argument(
        "--sources-only",
        action="store_true",
        default=False,
        help="Ignore persisted catalog_*.json snapshots and rebuild from built-ins plus sources.yml only",
    )

    skill_search_parser = skill_sub.add_parser("search", help="Search the skill index")
    skill_search_parser.add_argument("query", type=str, help="Search query")
    skill_search_parser.add_argument(
        "--k", type=int, default=10, help="Number of results to return"
    )
    skill_search_parser.add_argument(
        "--index-dir", type=str, default=None, help="Skill index directory"
    )

    # --- decompose ---
    decompose_parser = subparsers.add_parser(
        "decompose", help="Decompose a goal into a Conceptual Dependency Graph"
    )
    decompose_parser.add_argument("goal", type=str, help="High-level goal to decompose")
    decompose_parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="Max decomposition depth (default: from config)",
    )
    decompose_parser.add_argument(
        "--output", type=str, default=None, help="Output path for CDG JSON"
    )
    decompose_parser.add_argument(
        "--catalog",
        type=str,
        default=None,
        help="Path to catalog JSON (default: auto-detect)",
    )
    decompose_parser.add_argument(
        "--thread-id",
        type=str,
        default=None,
        help="Checkpoint thread ID (auto-generated if omitted)",
    )
    decompose_parser.add_argument(
        "--llm-provider",
        choices=["anthropic", "codex", "llama_cpp", "claude_cli", "codex_cli", "gemini_cli", "claude_shim", "codex_shim", "gemini_shim"],
        default=None,
        help="LLM provider override (default: from config)",
    )
    decompose_parser.add_argument(
        "--llm-model",
        type=str,
        default=None,
        help="LLM model override for decomposition (default: from config)",
    )
    decompose_parser.add_argument(
        "--llm-max-tokens",
        type=int,
        default=None,
        help="Max output tokens for decomposition LLM calls",
    )
    decompose_parser.add_argument(
        "--no-persist",
        action="store_true",
        default=False,
        help="Disable PostgreSQL persistence (use in-memory only)",
    )
    decompose_parser.add_argument(
        "--trace",
        action="store_true",
        default=False,
        help="Write pipeline event trace to {output_dir}/trace.jsonl",
    )
    _add_mode_argument(decompose_parser)
    _add_label_argument(decompose_parser)

    # --- history ---
    history_parser = subparsers.add_parser(
        "history", help="Show checkpoint history for a decomposition thread"
    )
    history_parser.add_argument("thread_id", type=str, help="Thread ID to inspect")

    # --- visualize ---
    viz_parser = subparsers.add_parser(
        "visualize", help="Open browser-based CDG visualization"
    )
    viz_parser.add_argument(
        "cdg_file",
        nargs="?",
        default=None,
        help="Path to CDG JSON to pre-load (optional)",
    )
    viz_parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="HTTP server port (default: auto-pick)",
    )
    viz_parser.add_argument(
        "--no-serve",
        action="store_true",
        default=False,
        help="Open file:// directly instead of starting a local server",
    )
    viz_parser.add_argument(
        "--api",
        action="store_true",
        default=False,
        help="Start FastAPI server with Memgraph CDG browsing (requires Memgraph connection)",
    )
    viz_parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Enable uvicorn auto-reload on code changes (--api mode only)",
    )

    # --- assemble ---
    assemble_parser = subparsers.add_parser(
        "assemble", help="Assemble CDG + match results into a compilable skeleton"
    )
    assemble_parser.add_argument("cdg_file", type=str, help="Path to CDG JSON")
    assemble_parser.add_argument(
        "matches_file", type=str, help="Path to match results JSON"
    )
    assemble_parser.add_argument(
        "--prover",
        choices=["lean4", "coq", "python"],
        default="lean4",
        help="Proof assistant",
    )
    assemble_parser.add_argument(
        "--output", type=str, default=None, help="Output path for generated source file"
    )
    assemble_parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Also compile the skeleton and report errors",
    )

    # --- synthesize ---
    synth_parser = subparsers.add_parser(
        "synthesize", help="Assemble, compile, and repair a skeleton (full Round 3)"
    )
    synth_parser.add_argument("cdg_file", type=str, help="Path to CDG JSON")
    synth_parser.add_argument(
        "matches_file", type=str, help="Path to match results JSON"
    )
    synth_parser.add_argument(
        "--prover",
        choices=["lean4", "coq", "python"],
        default="lean4",
        help="Proof assistant",
    )
    synth_parser.add_argument(
        "--output", type=str, default=None, help="Output path for final verified source"
    )
    synth_parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Max repair iterations (default: from config)",
    )
    synth_parser.add_argument(
        "--llm-provider",
        choices=["anthropic", "codex", "llama_cpp", "claude_cli", "codex_cli", "gemini_cli", "claude_shim", "codex_shim", "gemini_shim"],
        default=None,
        help="LLM provider override (default: from config)",
    )
    synth_parser.add_argument(
        "--llm-model",
        type=str,
        default=None,
        help="LLM model override (default: from config)",
    )
    synth_parser.add_argument(
        "--llm-max-tokens",
        type=int,
        default=None,
        help="Max output tokens for LLM calls",
    )
    synth_parser.add_argument(
        "--trace",
        action="store_true",
        default=False,
        help="Write pipeline event trace to {output_dir}/trace.jsonl",
    )
    _add_mode_argument(synth_parser)

    # --- run (full orchestration) ---
    run_parser = subparsers.add_parser(
        "run", help="Run full orchestration: decompose -> match -> (refine) -> assemble"
    )
    run_parser.add_argument("goal", type=str, help="High-level goal")
    run_parser.add_argument(
        "--prover",
        choices=["lean4", "coq", "python"],
        default="lean4",
        help="Proof assistant",
    )
    run_parser.add_argument(
        "--max-rounds", type=int, default=3, help="Max refinement rounds (default: 3)"
    )
    run_parser.add_argument(
        "--output", type=str, default=None, help="Output directory for all artifacts"
    )
    run_parser.add_argument(
        "--catalog", type=str, default=None, help="Path to catalog JSON"
    )
    run_parser.add_argument(
        "--llm-provider",
        choices=["anthropic", "codex", "llama_cpp", "claude_cli", "codex_cli", "gemini_cli", "claude_shim", "codex_shim", "gemini_shim"],
        default=None,
        help="LLM provider override",
    )
    run_parser.add_argument(
        "--llm-model", type=str, default=None, help="LLM model override"
    )
    run_parser.add_argument(
        "--llm-max-tokens", type=int, default=None, help="Max output tokens"
    )
    run_parser.add_argument(
        "--trace", action="store_true", default=False, help="Write trace.jsonl"
    )
    _add_mode_argument(run_parser)
    _add_label_argument(run_parser)

    # --- export ---
    export_parser = subparsers.add_parser(
        "export", help="Export verified source to compiled artifacts and FFI bindings"
    )
    export_parser.add_argument(
        "source_file",
        type=str,
        help="Path to verified .lean/.v file or SynthesisResult JSON",
    )
    export_parser.add_argument(
        "--target",
        choices=["lean-lib", "coq-lib", "rust-ffi", "c-header", "python-pkg"],
        default="lean-lib",
        help="Export target (default: lean-lib)",
    )
    export_parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: from config)",
    )
    export_parser.add_argument(
        "--optimize",
        action="store_true",
        default=False,
        help="Run hot-path optimizer before export",
    )
    export_parser.add_argument(
        "--prover",
        choices=["lean4", "coq", "python"],
        default="lean4",
        help="Proof assistant (default: lean4)",
    )

    # --- optimize (Principal) ---
    optimize_parser = subparsers.add_parser(
        "optimize", help="Run NAS/AutoML optimisation loop (Principal role)"
    )
    optimize_parser.add_argument("goal", type=str, help="High-level goal to optimise")
    optimize_parser.add_argument(
        "--benchmark",
        type=str,
        required=True,
        help="Path to benchmark dataset (CSV or JSON)",
    )
    optimize_parser.add_argument(
        "--dataset-var",
        action="append",
        default=[],
        help="Adapter variable substitution in KEY=VALUE form; repeat as needed",
    )
    optimize_parser.add_argument(
        "--eval-spec",
        type=str,
        default=None,
        help="Path to a JSON evaluation spec, or inline JSON, for reference-based loss computation",
    )
    optimize_parser.add_argument(
        "--metric",
        choices=list(SUPPORTED_OBJECTIVES),
        default="latency",
        help="Optimisation objective (default: latency)",
    )
    optimize_parser.add_argument(
        "--trials",
        type=int,
        default=50,
        help="Number of optimisation trials (default: 50)",
    )
    optimize_parser.add_argument(
        "--prover",
        choices=["lean4", "coq", "python"],
        default="python",
        help="Proof assistant (default: python)",
    )
    optimize_parser.add_argument(
        "--catalog",
        type=str,
        default=None,
        help="Path to catalog JSON",
    )
    optimize_parser.add_argument(
        "--llm-provider",
        choices=["anthropic", "codex", "llama_cpp", "claude_cli", "codex_cli", "gemini_cli", "claude_shim", "codex_shim", "gemini_shim"],
        default=None,
        help="LLM provider override",
    )
    optimize_parser.add_argument(
        "--llm-model", type=str, default=None, help="LLM model override"
    )
    optimize_parser.add_argument(
        "--llm-max-tokens", type=int, default=None, help="Max output tokens"
    )
    optimize_parser.add_argument(
        "--no-persist",
        action="store_true",
        default=False,
        help="Disable PostgreSQL persistence",
    )
    optimize_parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-trial subprocess timeout in seconds (default: 120)",
    )
    optimize_parser.add_argument(
        "--dataset-slice-start",
        type=float,
        default=None,
        help="Optional relative dataset slice start in seconds from adapter start time.",
    )
    optimize_parser.add_argument(
        "--dataset-slice-stop",
        type=float,
        default=None,
        help="Optional relative dataset slice stop in seconds from adapter start time.",
    )
    optimize_parser.add_argument(
        "--heuristic-cohort-size",
        type=int,
        default=5,
        help=(
            "Number of dataset members to aggregate for heuristic guidance before "
            "proposal selection (default: 5; set to 1 to disable cohorting)."
        ),
    )
    optimize_parser.add_argument(
        "--heuristic-cohort-concurrency",
        type=int,
        default=2,
        help=(
            "Maximum number of cohort member evaluations to run concurrently "
            "(default: 2)."
        ),
    )
    _add_mode_argument(optimize_parser)

    # --- profile ---
    profile_parser = subparsers.add_parser(
        "profile", help="Evaluate an existing CDG and compiled artifact against a dataset"
    )
    profile_parser.add_argument(
        "--cdg", type=str, required=True, help="Path to the CDG JSON file"
    )
    profile_parser.add_argument(
        "--artifact", type=str, required=True, help="Path to the compiled artifact (Python file)"
    )
    profile_parser.add_argument(
        "--dataset", type=str, required=True, help="Path to the benchmark dataset (CSV/JSON)"
    )
    profile_parser.add_argument(
        "--dataset-var",
        action="append",
        default=[],
        help="Adapter variable substitution in KEY=VALUE form; repeat as needed",
    )
    profile_parser.add_argument(
        "--eval-spec",
        type=str,
        default=None,
        help="Path to a JSON evaluation spec, or inline JSON, for reference-based loss computation",
    )
    profile_parser.add_argument(
        "--metric",
        choices=list(SUPPORTED_OBJECTIVES),
        default="precision",
        help="Optimization objective to profile (default: precision)",
    )
    profile_parser.add_argument(
        "--dataset-slice-start",
        type=float,
        default=None,
        help="Optional relative dataset slice start in seconds from adapter start time.",
    )
    profile_parser.add_argument(
        "--dataset-slice-stop",
        type=float,
        default=None,
        help="Optional relative dataset slice stop in seconds from adapter start time.",
    )

    # --- prompt-benchmark ---
    prompt_benchmark_parser = subparsers.add_parser(
        "prompt-benchmark",
        help="Benchmark prompt keys across providers on a small cross-domain suite",
    )
    prompt_benchmark_parser.add_argument(
        "--provider",
        action="append",
        default=[],
        help="Provider spec in the form provider:model. Repeat to compare multiple providers.",
    )
    prompt_benchmark_parser.add_argument(
        "--prompt-key",
        action="append",
        choices=["hunter_score", "hunter_reformulate", "hunter_analyze_failure"],
        default=[],
        help="Restrict the benchmark to one or more prompt keys (default: all benchmarked keys).",
    )
    prompt_benchmark_parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of times to run each provider/case pair (default: 1)",
    )
    prompt_benchmark_parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Max output tokens for benchmark calls (default: hunter config)",
    )
    prompt_benchmark_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional JSON report path",
    )
    prompt_benchmark_parser.add_argument(
        "--compare-direct-baseline",
        action="store_true",
        default=False,
        help="Also run a simpler direct-baseline prompt variant for each provider/case pair",
    )

    benchmark_validate_parser = subparsers.add_parser(
        "benchmark-validate",
        help="Run deterministic prompt and flow benchmark validation and save reports",
    )
    benchmark_validate_parser.add_argument(
        "--output",
        type=str,
        default="build/benchmark_validation",
        help="Directory where validation reports will be written.",
    )
    _add_label_argument(benchmark_validate_parser)

    release_validate_parser = subparsers.add_parser(
        "release-validate",
        help="Run deterministic release validation and write a manifest bundle",
    )
    release_validate_parser.add_argument(
        "--output",
        type=str,
        default="build/release_validation",
        help="Directory where the release validation bundle will be written.",
    )
    _add_label_argument(release_validate_parser)

    # --- sources ---
    sources_parser = subparsers.add_parser(
        "sources", help="Manage multi-repo atom sources"
    )
    sources_sub = sources_parser.add_subparsers(dest="sources_command")

    sources_sub.add_parser("list", help="List resolved atom sources")
    sources_sync_parser = sources_sub.add_parser(
        "sync", help="Fetch / update git atom sources"
    )
    sources_sync_parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Sync only the named source (default: all)",
    )

    # --- ingest ---
    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Ingest source code into the atom framework (Round 0)",
    )
    ingest_parser.add_argument(
        "source", type=str, help="Path to source file (.py/.rs/.jl/.cpp/.h/.hpp)"
    )
    ingest_parser.add_argument(
        "--class",
        dest="class_name",
        type=str,
        required=True,
        help="Name of the class to ingest",
    )
    ingest_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory for generated files",
    )
    ingest_parser.add_argument(
        "--output-scope",
        dest="output_scope",
        choices=["symbol", "family"],
        default=None,
        help=(
            "Publication scope for the output directory "
            "(default: infer from output path)"
        ),
    )
    family_publish_group = ingest_parser.add_mutually_exclusive_group()
    family_publish_group.add_argument(
        "--allow-family-replace",
        action="store_true",
        default=False,
        help=(
            "Allow replacing an existing family-scope output directory "
            "that already contains published canonical artifacts"
        ),
    )
    family_publish_group.add_argument(
        "--allow-family-merge",
        action="store_true",
        default=False,
        help=(
            "Allow merging newly staged family artifacts into an existing "
            "family-scope output directory without replacing untouched files"
        ),
    )
    ingest_parser.add_argument(
        "--llm-provider",
        choices=["anthropic", "codex", "llama_cpp", "claude_cli", "codex_cli", "gemini_cli", "claude_shim", "codex_shim", "gemini_shim"],
        default=None,
        help="LLM provider override (default: from config)",
    )
    ingest_parser.add_argument(
        "--llm-model",
        type=str,
        default=None,
        help="LLM model override (default: from config)",
    )
    ingest_parser.add_argument(
        "--procedural",
        action="store_true",
        default=False,
        help="Use deterministic procedural extraction instead of LLM chunking",
    )
    ingest_parser.add_argument(
        "--trace",
        action="store_true",
        default=False,
        help="Write pipeline event trace to {output_dir}/trace.jsonl",
    )
    ingest_parser.add_argument(
        "--monitor",
        action="store_true",
        default=False,
        help="Print live ingestion status updates to stdout",
    )
    ingest_parser.add_argument(
        "--stale-seconds",
        type=int,
        default=120,
        help="Heartbeat threshold for stalled detection (default: 120)",
    )
    _add_mode_argument(ingest_parser)

    ingest_status_parser = subparsers.add_parser(
        "ingest-status",
        help="Inspect ingestion run state from monitor files",
    )
    ingest_status_parser.add_argument(
        "output",
        type=str,
        help="Ingestion output directory",
    )
    ingest_status_parser.add_argument(
        "--stale-seconds",
        type=int,
        default=120,
        help="Heartbeat threshold for stalled detection (default: 120)",
    )
    ingest_status_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print full status payload as JSON",
    )

    # --- match ---
    match_parser = subparsers.add_parser(
        "match", help="Match predicates to library functions"
    )
    match_parser.add_argument("--statement", type=str, help="Single statement to match")
    match_parser.add_argument("--pdg-file", type=str, help="JSON file with PDG nodes")
    match_parser.add_argument(
        "--prover",
        choices=["lean4", "coq", "python"],
        default="lean4",
        help="Proof assistant",
    )
    match_parser.add_argument(
        "--index-dir",
        type=str,
        default=None,
        help="Directory containing FAISS index (default: from .env)",
    )
    match_parser.add_argument(
        "--llm-provider",
        choices=["anthropic", "codex", "llama_cpp", "claude_cli", "codex_cli", "gemini_cli", "claude_shim", "codex_shim", "gemini_shim"],
        default=None,
        help="LLM provider override (default: from config)",
    )
    match_parser.add_argument(
        "--llm-model",
        type=str,
        default=None,
        help="LLM model override for matching (default: from config)",
    )
    match_parser.add_argument(
        "--llm-max-tokens",
        type=int,
        default=None,
        help="Max output tokens for matching LLM calls",
    )
    match_parser.add_argument(
        "--trace",
        action="store_true",
        default=False,
        help="Write pipeline event trace to trace.jsonl",
    )
    _add_mode_argument(match_parser)
    _add_label_argument(match_parser)

    # --- catalog-gaps ---
    gaps_parser = subparsers.add_parser(
        "catalog-gaps",
        help="Detect catalog coverage gaps from a CDG file",
    )
    gaps_parser.add_argument(
        "--cdg",
        type=str,
        required=True,
        help="Path to CDG JSON file",
    )
    gaps_parser.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="Similarity ceiling below which a node is considered unmatched (default: 0.6)",
    )
    gaps_parser.add_argument(
        "--catalog",
        type=str,
        default=None,
        help="Path to a catalog JSON to load instead of built-ins",
    )

    # --- upsert-cdg ---
    upsert_cdg_parser = subparsers.add_parser(
        "upsert-cdg",
        help="Upsert CDG JSON files into Memgraph graph store",
    )
    upsert_cdg_parser.add_argument(
        "repo_path",
        type=str,
        help=(
            "Path to atoms repo directory "
            "(e.g. ~/personal/sciona-atoms/src/sciona/atoms/signal_processing/biosppy "
            "or ~/personal/sciona-atoms/sciona/atoms/biosppy)"
        ),
    )
    upsert_cdg_parser.add_argument(
        "--repo-name",
        type=str,
        default=None,
        help="Repo namespace override (default: directory basename)",
    )
    upsert_cdg_parser.add_argument(
        "--memgraph-uri",
        type=str,
        default=None,
        help="Memgraph bolt URI override (default: from config)",
    )

    # --- login ---
    login_parser = subparsers.add_parser(
        "login", help="Authenticate with the SCIONA platform via GitHub"
    )
    login_parser.add_argument(
        "--api-url", type=str, default=None, help="Platform API URL override"
    )

    # --- catalog ---
    catalog_parser = subparsers.add_parser(
        "catalog", help="Manage the global atom catalog"
    )
    catalog_sub = catalog_parser.add_subparsers(dest="catalog_command")

    catalog_sync_parser = catalog_sub.add_parser(
        "sync", help="Download latest manifest.sqlite from the platform"
    )
    catalog_sync_parser.add_argument(
        "--api-url", type=str, default=None, help="Platform API URL override"
    )
    catalog_sync_parser.add_argument(
        "--manifest-url", type=str, default=None, help="Manifest artifact URL override"
    )
    catalog_sync_parser.add_argument(
        "--tier",
        type=str,
        default=None,
        help="Manifest tier to download (default: general)",
    )
    catalog_sync_parser.add_argument(
        "--output", type=str, default=None, help="Output path for manifest.sqlite"
    )
    catalog_search_parser = catalog_sub.add_parser(
        "search", help="Search the Postgres-backed atom catalog"
    )
    catalog_search_parser.add_argument("query", type=str)
    catalog_search_parser.add_argument("--domain-tag", type=str, default=None)
    catalog_search_parser.add_argument("--limit", type=int, default=20)
    catalog_search_parser.add_argument("--api-url", type=str, default=None)
    catalog_install_parser = catalog_sub.add_parser(
        "install", help="Install the provider for one selected atom"
    )
    catalog_install_parser.add_argument("fqdn", type=str)
    catalog_install_parser.add_argument("--api-url", type=str, default=None)
    catalog_publish_parser = catalog_sub.add_parser(
        "publish-providers", help="Seed, enrich, and embed all provider catalogs"
    )
    catalog_publish_parser.add_argument("--workspace-root", type=str, default=None)
    catalog_publish_parser.add_argument("--apply", action="store_true")
    catalog_publish_parser.add_argument("--ensure-owner", action="store_true")
    catalog_publish_parser.add_argument("--database-url", type=str, default=None)
    catalog_publish_parser.add_argument("--allow-duplicate-fqdns", action="store_true")
    catalog_publish_parser.add_argument("--skip-backfills", action="store_true")
    catalog_publish_parser.add_argument("--skip-embeddings", action="store_true")
    catalog_reconcile_parser = catalog_sub.add_parser(
        "reconcile-providers",
        help="Compare provider metadata identities with the seed inventory",
    )
    catalog_reconcile_parser.add_argument("--workspace-root", type=str, default=None)
    catalog_reconcile_parser.add_argument("--strict", action="store_true")
    catalog_reconcile_parser.add_argument("--apply", action="store_true")
    catalog_reconcile_parser.add_argument(
        "--retire-unresolved",
        action="store_true",
        help="Remove orphaned metadata and record catalog tombstones (implies --apply)",
    )
    catalog_provider_release_parser = catalog_sub.add_parser(
        "validate-provider-release",
        help="Build and validate one PEP 420 atom provider distribution",
    )
    catalog_provider_release_parser.add_argument("repo", nargs="?", default=".")
    catalog_provider_release_parser.add_argument("--no-build", action="store_true")
    catalog_audit_parser = catalog_sub.add_parser(
        "audit-providers",
        help="Report per-provider catalog completeness and trust readiness",
    )
    catalog_audit_parser.add_argument("--database-url", type=str, default=None)
    catalog_audit_parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero while any seeded atom is not audit-ready",
    )

    # --- atom ---
    atom_parser = subparsers.add_parser(
        "atom", help="Manage atoms in the global registry"
    )
    atom_sub = atom_parser.add_subparsers(dest="atom_command")

    atom_publish_parser = atom_sub.add_parser(
        "publish", help="Publish an atom to the global registry"
    )
    atom_publish_parser.add_argument(
        "path", type=str, help="Path to atom source directory"
    )
    atom_publish_parser.add_argument(
        "--semver", type=str, required=True, help="Semver label (e.g. 1.0.0)"
    )

    # --- bounty ---
    bounty_parser = subparsers.add_parser(
        "bounty", help="Dead-End Flare and bounty management"
    )
    bounty_sub = bounty_parser.add_subparsers(dest="bounty_command")

    bounty_generate_parser = bounty_sub.add_parser(
        "generate", help="Generate Dead-End Flare from a completed optimization run"
    )
    bounty_generate_parser.add_argument(
        "--run-dir",
        type=str,
        required=True,
        help="Path to the optimization run output directory",
    )
    bounty_generate_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output path for flare YAML (default: <run-dir>/flare.yml)",
    )
    bounty_generate_parser.add_argument(
        "--domain-tags",
        nargs="*",
        default=[],
        help="Domain tags for the flare (e.g. crystallography signal-processing)",
    )

    bounty_list_parser = bounty_sub.add_parser(
        "list", help="List open bounties"
    )
    bounty_list_parser.add_argument(
        "--domain", type=str, default=None, help="Filter by domain tag"
    )
    bounty_list_parser.add_argument(
        "--limit", type=int, default=20, help="Max results"
    )

    bounty_fund_parser = bounty_sub.add_parser(
        "fund", help="Fund a draft bounty"
    )
    bounty_fund_parser.add_argument(
        "bounty_id", type=str, help="Bounty ID to fund"
    )

    # --- receipt ---
    receipt_parser = subparsers.add_parser(
        "receipt", help="Execution receipt signing and verification"
    )
    receipt_sub = receipt_parser.add_subparsers(dest="receipt_command")

    receipt_sign_parser = receipt_sub.add_parser(
        "sign", help="Sign an execution receipt"
    )
    receipt_sign_parser.add_argument(
        "--cdg", type=str, required=True, help="Path to CDG file"
    )
    receipt_sign_parser.add_argument(
        "--split", type=str, required=True, help="Path to split file"
    )
    receipt_sign_parser.add_argument(
        "--output", type=str, required=True, help="Path to output file"
    )
    receipt_sign_parser.add_argument(
        "--key", type=str, required=True, help="Path to SSH private key"
    )
    receipt_sign_parser.add_argument(
        "--bounty-id", type=str, required=True, help="Bounty ID"
    )
    receipt_sign_parser.add_argument(
        "--metric-name", type=str, default="loss", help="Metric name (default: loss)"
    )
    receipt_sign_parser.add_argument(
        "--metric-value", type=str, default=None, help="Metric value"
    )
    receipt_sign_parser.add_argument(
        "--receipt-output",
        type=str,
        default=None,
        help="Output path for the signed receipt JSON (default: receipt.json)",
    )

    receipt_verify_parser = receipt_sub.add_parser(
        "verify", help="Verify a signed execution receipt"
    )
    receipt_verify_parser.add_argument(
        "--receipt", type=str, required=True, help="Path to signed receipt JSON"
    )
    receipt_verify_parser.add_argument(
        "--allowed-signers",
        type=str,
        required=True,
        help="Path to allowed_signers file",
    )

    # --- telemetry ---
    telemetry_parser = subparsers.add_parser(
        "telemetry", help="Inspect telemetry runs"
    )
    telemetry_sub = telemetry_parser.add_subparsers(dest="telemetry_command")

    tl_list_parser = telemetry_sub.add_parser("list", help="List recent telemetry runs")
    tl_list_parser.add_argument(
        "--limit", type=int, default=20, help="Max runs to show (default: 20)"
    )
    tl_list_parser.add_argument(
        "--state",
        choices=["all", "running", "completed", "failed"],
        default="all",
        help="Filter by state",
    )

    tl_show_parser = telemetry_sub.add_parser("show", help="Show details for a run")
    tl_show_parser.add_argument("run_id", type=str, help="Run ID to inspect")

    args = parser.parse_args()

    if args.command == "index" and getattr(args, "index_command", None) == "build":
        _cmd_index_build(args)
    elif args.command == "skill":
        skill_cmd = getattr(args, "skill_command", None)
        if skill_cmd == "ingest":
            _cmd_skill_ingest(args)
        elif skill_cmd == "index":
            _cmd_skill_index(args)
        elif skill_cmd == "search":
            _cmd_skill_search(args)
        else:
            print(
                "Error: provide a skill subcommand (ingest, index, search)",
                file=sys.stderr,
            )
            sys.exit(1)
    elif args.command == "sources":
        sources_cmd = getattr(args, "sources_command", None)
        if sources_cmd == "list":
            _cmd_sources_list(args)
        elif sources_cmd == "sync":
            _cmd_sources_sync(args)
        else:
            print(
                "Error: provide a sources subcommand (list, sync)",
                file=sys.stderr,
            )
            sys.exit(1)
    elif args.command == "optimize":
        _run_async_command(_cmd_optimize(args))
    elif args.command == "profile":
        _run_async_command(_cmd_profile(args))
    elif args.command == "prompt-benchmark":
        _run_async_command(_cmd_prompt_benchmark(args))
    elif args.command == "benchmark-validate":
        _run_async_command(_cmd_benchmark_validate(args))
    elif args.command == "release-validate":
        _run_async_command(_cmd_release_validate(args))
    elif args.command == "decompose":
        _run_async_command(_cmd_decompose(args))
    elif args.command == "history":
        _run_async_command(_cmd_history(args))
    elif args.command == "ingest":
        _run_async_command(_cmd_ingest(args))
    elif args.command == "ingest-status":
        _cmd_ingest_status(args)
    elif args.command == "match":
        _run_async_command(_cmd_match(args))
    elif args.command == "assemble":
        _run_async_command(_cmd_assemble(args))
    elif args.command == "synthesize":
        _run_async_command(_cmd_synthesize(args))
    elif args.command == "run":
        _run_async_command(_cmd_run(args))
    elif args.command == "export":
        _run_async_command(_cmd_export(args))
    elif args.command == "visualize":
        _cmd_visualize(args)
    elif args.command == "catalog-gaps":
        _cmd_catalog_gaps(args)
    elif args.command == "upsert-cdg":
        _run_async_command(_cmd_upsert_cdg(args))
    elif args.command == "login":
        _run_async_command(_cmd_login(args))
    elif args.command == "catalog":
        catalog_cmd = getattr(args, "catalog_command", None)
        if catalog_cmd == "sync":
            _run_async_command(_cmd_catalog_sync(args))
        elif catalog_cmd == "search":
            _run_async_command(_cmd_catalog_search(args))
        elif catalog_cmd == "install":
            _run_async_command(_cmd_catalog_install(args))
        elif catalog_cmd == "publish-providers":
            _cmd_catalog_publish_providers(args)
        elif catalog_cmd == "reconcile-providers":
            _cmd_catalog_reconcile_providers(args)
        elif catalog_cmd == "validate-provider-release":
            _cmd_catalog_validate_provider_release(args)
        elif catalog_cmd == "audit-providers":
            _cmd_catalog_audit_providers(args)
        else:
            print(
                "Error: provide a catalog subcommand "
                "(sync, search, install, publish-providers, reconcile-providers, "
                "validate-provider-release, audit-providers)",
                file=sys.stderr,
            )
            sys.exit(1)
    elif args.command == "atom":
        atom_cmd = getattr(args, "atom_command", None)
        if atom_cmd == "publish":
            _run_async_command(_cmd_atom_publish(args))
        else:
            print(
                "Error: provide an atom subcommand (publish)",
                file=sys.stderr,
            )
            sys.exit(1)
    elif args.command == "bounty":
        bounty_cmd = getattr(args, "bounty_command", None)
        if bounty_cmd == "generate":
            _cmd_bounty_generate(args)
        else:
            print(
                "Error: provide a bounty subcommand (generate, list, fund)",
                file=sys.stderr,
            )
            sys.exit(1)
    elif args.command == "receipt":
        receipt_cmd = getattr(args, "receipt_command", None)
        if receipt_cmd == "sign":
            _cmd_receipt_sign(args)
        elif receipt_cmd == "verify":
            _cmd_receipt_verify(args)
        else:
            print(
                "Error: provide a receipt subcommand (sign, verify)",
                file=sys.stderr,
            )
            sys.exit(1)
    elif args.command == "telemetry":
        telemetry_cmd = getattr(args, "telemetry_command", None)
        if telemetry_cmd == "list":
            _cmd_telemetry_list(args)
        elif telemetry_cmd == "show":
            _cmd_telemetry_show(args)
        else:
            print(
                "Error: provide a telemetry subcommand (list, show)",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
