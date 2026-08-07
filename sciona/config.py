"""Configuration for AGEO-Matcher via pydantic-settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Prompt-specific code defaults are retained for opt-in debugging and benchmark
# work, but deterministic wrappers should not auto-activate them at runtime.
BENCHMARK_JUSTIFIED_PROMPT_KEYS = frozenset()


@dataclass(frozen=True)
class ExecutionModeSettings:
    """Resolved execution-mode feature flags."""

    mode: str
    skill_index_enabled: bool
    graph_retrieval_enabled: bool
    architect_shared_context_enabled: bool
    hunter_shared_context_enabled: bool
    synthesizer_shared_context_enabled: bool
    ingester_shared_context_enabled: bool
    hunter_mode: str
    hunter_use_gbnf: bool
    semantic_index_backend_override: str | None = None


class AgeomConfig(BaseSettings):
    """Central configuration loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_prefix="SCIONA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Atom sources
    sources_file: Path = Field(default=Path("sources.yml"))

    # Embedding model
    embedding_backend: str = "fastembed"  # "fastembed" | "unixcoder"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 768
    embedding_batch_size: int = 32
    semantic_index_backend: str = "postgres"  # "postgres" | "auto" | "faiss" | "lexical"

    # FAISS index
    index_dir: Path = Field(default=Path("data/index"))

    # LLM (global defaults / shared credentials)
    llm_provider: str = "anthropic"  # "anthropic" | "codex"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""
    llm_model: str = "claude-sonnet-4-5-20250929"
    llm_max_tokens: int = 4096
    llama_cpp_base_url: str = "http://127.0.0.1:8080/v1"
    llama_cpp_api_key: str = "local"
    use_agent_layer: bool = False  # prefix CLI commands with `al` (Agent Layer)
    allow_legacy_subprocess_providers: bool = False
    shared_context_backend: str = "auto"  # "auto" | "memory" | "postgres"
    shared_context_postgres_table: str = "sciona_shared_context"
    shared_context_ttl_hours: int = 168
    shared_context_max_records_per_namespace: int = 500
    shared_context_repo_namespace: str = "repo/default"
    shared_context_promotion_enabled: bool = True
    shared_context_promotion_min_confidence: float = 0.9
    shared_context_include_provenance: bool = True
    telemetry_runs_dir: Path = Field(default=Path("output/telemetry_runs"))
    telemetry_stale_seconds: int = 120
    telemetry_backend: str = "auto"  # "auto" | "postgres" | "file"
    execution_mode: str = "verified"  # "rapid" | "structured" | "single_agent" | "verified"
    use_monadic_rewriter: bool = False  # Feature gate for formal DPO graph rewriting
    # Platform API
    api_url: str = "https://api.sciona.dev"
    s3_bucket: str = "sciona-platform"
    jwt_kms_key_id: str = ""
    jwt_private_key_path: str = ""
    jwt_public_key_path: str = ""
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    stripe_secret_key: str = ""

    # Memgraph graph store
    memgraph_uri: str = "bolt://localhost:7687"
    memgraph_user: str = ""
    memgraph_password: str = ""

    # Graph retrieval (CDG subgraph similarity)
    graph_retrieval_enabled: bool = False
    graph_retrieval_timeout_ms: int = 1800
    graph_retrieval_max_examples: int = 3
    graph_retrieval_min_children: int = 2

    # Template retriever
    template_retriever_confidence_threshold: float = 0.6
    template_retriever_max_candidates: int = 50

    # Generalization data paths (empty = use built-in defaults)
    phrase_rules_path: str = ""
    query_rules_path: str = ""
    split_patterns_path: str = ""

    # Auto-upsert of solved runs
    auto_upsert_enabled: bool = False
    auto_upsert_min_coverage: float = 0.5

    # Lean 4
    lean_toolchain: str = "leanprover/lean4:v4.14.0"
    mathlib_path: Path = Field(default=Path(""))

    # Coq
    coq_project_path: Path = Field(default=Path(""))

    # Hunter agent parameters
    hunter_llm_provider: str = "llama_cpp"  # default local quantized worker
    hunter_llm_model: str = "qwen2.5-coder:7b"
    hunter_llm_max_tokens: int = 1024
    hunter_embedding_query_expander: bool = True
    hunter_mode: str = "speculative_local"  # "standard" | "speculative_local"
    hunter_use_gbnf: bool = True
    hunter_max_iterations: int = 5
    hunter_top_k_verify: int = 10
    hunter_search_k: int = 20
    hunter_query_batch_size: int = 40
    hunter_top_k_per_query: int = 50
    hunter_max_candidates_total: int = 3000
    hunter_verify_concurrency: int = 1  # >1 enables parallel verification
    hunter_shared_context_enabled: bool = True
    hunter_shared_context_budget_chars: int = 900

    # Architect (Round 1)
    skill_index_dir: Path = Field(default=Path("data/skill_index"))
    clrs_path: Path = Field(default=Path(""))
    coq100_path: Path = Field(default=Path(""))
    postgres_uri: str = ""
    architect_max_depth: int = 8
    architect_llm_provider: str = ""  # falls back to llm_provider when empty
    architect_llm_model: str = ""
    architect_critique_llm_enabled: bool = False
    architect_shared_context_enabled: bool = True
    architect_shared_context_budget_chars: int = 900

    # Synthesizer (Round 3)
    synthesizer_max_iterations: int = 10
    synthesizer_llm_provider: str = ""  # falls back to llm_provider
    synthesizer_llm_model: str = ""
    synthesizer_shared_context_enabled: bool = True
    synthesizer_shared_context_budget_chars: int = 900

    # Ingester (Round 0)
    ingester_llm_provider: str = ""  # falls back to llm_provider
    ingester_llm_model: str = ""
    ingester_max_depth: int = 12  # max CDG depth (1 = flat behavior)
    ingester_decompose_line_threshold: int = 30  # method lines triggering sub-decomposition
    ingester_parallelism: int = 1
    ingester_shared_context_enabled: bool = True
    ingester_shared_context_budget_chars: int = 900
    ingester_cache_enabled: bool = True
    ingester_cache_dir: Path = Field(default=Path("data/ingest_cache"))

    # --- Per-prompt LLM overrides ---
    # Light tier  → qwen2.5-coder:7b  (mechanical, high-volume, GBNF-safe)
    # Medium tier → qwen3:14b          (structured output, diagnostics, creative)
    # Heavy tier  → remote API          (falls back to agent/global default)

    # Architect per-prompt
    architect_strategy_llm_provider: str = "codex_shim"
    architect_strategy_llm_model: str = "gpt-5.3-codex"  # faster CLI shim while architect debugging is active
    architect_decompose_llm_provider: str = ""  # remote: graph design + type reasoning
    architect_decompose_llm_model: str = ""
    architect_critique_llm_provider: str = "codex_shim"
    architect_critique_llm_model: str = "gpt-5.3-codex"  # faster CLI shim while architect debugging is active

    # Hunter per-prompt
    hunter_score_llm_provider: str = "codex_shim"
    hunter_score_llm_model: str = "gpt-5.3-codex"  # best current latency for constrained ranking
    hunter_reformulate_llm_provider: str = "gemini_shim"
    hunter_reformulate_llm_model: str = "flash-lite"  # light: query diversity loop
    hunter_analyze_failure_llm_provider: str = "gemini_shim"
    hunter_analyze_failure_llm_model: str = "flash"  # medium-light: failure diagnosis while debugging

    # Synthesizer per-prompt (all remote — proof/repair)
    synthesizer_repair_llm_provider: str = ""
    synthesizer_repair_llm_model: str = ""
    synthesizer_tactic_llm_provider: str = ""
    synthesizer_tactic_llm_model: str = ""

    # Ingester per-prompt
    ingester_chunk_llm_provider: str = ""  # remote: 70-line Bayesian state-space prompt
    ingester_chunk_llm_model: str = ""
    ingester_hoist_state_llm_provider: str = "llama_cpp"
    ingester_hoist_state_llm_model: str = "qwen3:14b"  # medium: structured rules from macro plan
    ingester_abstract_llm_provider: str = "llama_cpp"
    ingester_abstract_llm_model: str = "qwen3:14b"  # medium: creative writing, no code gen
    ingester_fix_type_llm_provider: str = "llama_cpp"
    ingester_fix_type_llm_model: str = "qwen2.5-coder:7b"  # light: mechanical mypy fixes
    ingester_fix_ghost_llm_provider: str = "llama_cpp"
    ingester_fix_ghost_llm_model: str = "qwen3:14b"  # medium: shape inference repair
    ingester_opaque_witness_llm_provider: str = "llama_cpp"
    ingester_opaque_witness_llm_model: str = "qwen3:14b"  # medium: DL shape propagation
    ingester_fix_message_cycle_llm_provider: str = "llama_cpp"
    ingester_fix_message_cycle_llm_model: str = "qwen3:14b"  # medium: BP cycle-breaking
    ingester_decompose_llm_provider: str = ""  # remote: recursive atom decomposition
    ingester_decompose_llm_model: str = ""

    # Orchestrator per-prompt
    orchestrator_refine_llm_provider: str = "llama_cpp"
    orchestrator_refine_llm_model: str = "qwen3:14b"  # medium: predicate splitting
    orchestrator_hunter_concurrency: int = 4

    # Python target
    python_path: str = "python"
    python_mypy_path: str = "mypy"
    python_packages: str = "numpy,scipy"

    # Extractor (Round 3 Phase 3)
    export_output_dir: Path = Field(default=Path("export"))
    lean_lake_path: str = "lake"  # path to lake binary
    optimize_by_default: bool = False

    # Ecosystem / benchmark priors
    benchmark_prior_strength: int = 2


def prompt_override_matches_code_default(config: AgeomConfig, prompt_key: str) -> bool:
    """Return True when a per-prompt provider/model pair matches code defaults."""
    provider_field = AgeomConfig.model_fields.get(f"{prompt_key}_llm_provider")
    model_field = AgeomConfig.model_fields.get(f"{prompt_key}_llm_model")
    if provider_field is None or model_field is None:
        return False
    return (
        getattr(config, f"{prompt_key}_llm_provider", "") == provider_field.default
        and getattr(config, f"{prompt_key}_llm_model", "") == model_field.default
    )


def round_override_matches_code_default(config: AgeomConfig, round_name: str) -> bool:
    """Return True when a per-round provider/model pair matches code defaults."""
    provider_field = AgeomConfig.model_fields.get(f"{round_name}_llm_provider")
    model_field = AgeomConfig.model_fields.get(f"{round_name}_llm_model")
    if provider_field is None or model_field is None:
        return False
    return (
        getattr(config, f"{round_name}_llm_provider", "") == provider_field.default
        and getattr(config, f"{round_name}_llm_model", "") == model_field.default
    )


def should_apply_round_override(
    config: AgeomConfig,
    round_name: str,
    execution_mode: str | None = None,
) -> bool:
    """Apply per-round overrides when mode or operator intent allows them."""
    normalized_mode = str(
        execution_mode or getattr(config, "execution_mode", "verified") or "verified"
    ).strip().lower()
    if normalized_mode in {"rapid", "structured", "single_agent"}:
        return not round_override_matches_code_default(config, round_name)
    return True


def effective_round_provider_model(
    config: AgeomConfig,
    round_name: str,
    *,
    execution_mode: str | None = None,
) -> tuple[str, str]:
    """Resolve the effective default provider/model for a round."""
    provider_attr = f"{round_name}_llm_provider"
    model_attr = f"{round_name}_llm_model"
    provider = getattr(config, provider_attr, "") or ""
    model = getattr(config, model_attr, "") or ""
    if not should_apply_round_override(config, round_name, execution_mode):
        provider = ""
        model = ""
    return (
        str(provider or getattr(config, "llm_provider", "") or "").strip(),
        str(model or getattr(config, "llm_model", "") or "").strip(),
    )


def should_apply_prompt_override(
    config: AgeomConfig,
    prompt_key: str,
    execution_mode: str | None = None,
) -> bool:
    """Apply override when benchmark-justified or explicitly changed by the user."""
    normalized_mode = str(
        execution_mode or getattr(config, "execution_mode", "verified") or "verified"
    ).strip().lower()
    if normalized_mode in {"rapid", "structured", "single_agent"}:
        return not prompt_override_matches_code_default(config, prompt_key)
    if prompt_key in BENCHMARK_JUSTIFIED_PROMPT_KEYS:
        return True
    return not prompt_override_matches_code_default(config, prompt_key)


def resolve_execution_mode(
    config: AgeomConfig,
    mode: str | None = None,
) -> ExecutionModeSettings:
    """Resolve high-level execution mode into concrete feature flags."""
    normalized = str(
        mode or getattr(config, "execution_mode", "verified") or "verified"
    ).strip().lower()

    if normalized == "verified":
        return ExecutionModeSettings(
            mode=normalized,
            skill_index_enabled=True,
            graph_retrieval_enabled=config.graph_retrieval_enabled,
            architect_shared_context_enabled=config.architect_shared_context_enabled,
            hunter_shared_context_enabled=config.hunter_shared_context_enabled,
            synthesizer_shared_context_enabled=config.synthesizer_shared_context_enabled,
            ingester_shared_context_enabled=config.ingester_shared_context_enabled,
            hunter_mode=config.hunter_mode,
            hunter_use_gbnf=config.hunter_use_gbnf,
            semantic_index_backend_override=None,
        )

    if normalized == "structured":
        return ExecutionModeSettings(
            mode=normalized,
            skill_index_enabled=True,
            graph_retrieval_enabled=False,
            architect_shared_context_enabled=False,
            hunter_shared_context_enabled=False,
            synthesizer_shared_context_enabled=False,
            ingester_shared_context_enabled=False,
            hunter_mode="standard",
            hunter_use_gbnf=config.hunter_use_gbnf,
            semantic_index_backend_override=None,
        )

    if normalized == "single_agent":
        return ExecutionModeSettings(
            mode=normalized,
            skill_index_enabled=True,
            graph_retrieval_enabled=False,
            architect_shared_context_enabled=False,
            hunter_shared_context_enabled=False,
            synthesizer_shared_context_enabled=False,
            ingester_shared_context_enabled=False,
            hunter_mode="standard",
            hunter_use_gbnf=config.hunter_use_gbnf,
            semantic_index_backend_override=None,
        )

    if normalized == "rapid":
        return ExecutionModeSettings(
            mode=normalized,
            skill_index_enabled=False,
            graph_retrieval_enabled=False,
            architect_shared_context_enabled=False,
            hunter_shared_context_enabled=False,
            synthesizer_shared_context_enabled=False,
            ingester_shared_context_enabled=False,
            hunter_mode="standard",
            hunter_use_gbnf=False,
            semantic_index_backend_override="lexical",
        )

    raise ValueError(f"Unsupported execution mode: {mode}")
