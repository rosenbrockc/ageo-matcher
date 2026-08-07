"""Shared request/response models for service-layer runtime wrappers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sciona.orchestrator import OrchestratorResult
from sciona.protocols import ProofEnvironment
from sciona.synthesizer.models import AssemblyResult, SkeletonFile, SynthesisResult
from sciona.types import MatchFailureReport, MatchResult, PDGNode, Prover


@dataclass(frozen=True)
class ArchitectDecomposeRequest:
    """Request model for goal decomposition."""

    goal: str
    thread_id: str | None = None


@dataclass(frozen=True)
class ArchitectDecomposeResult:
    """Structured response from the architect service."""

    goal: str
    cdg: Any


@dataclass(frozen=True)
class HunterDirectMatchRequest:
    """Request model for direct goal matching without prior decomposition."""

    goal: str
    prover: Prover
    predicate_id: str = "goal_0"
    informal_desc: str = "single-agent direct grounding"
    context: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HunterBatchMatchRequest:
    """Request model for matching an explicit batch of PDG nodes."""

    pdg_nodes: list[PDGNode]


@dataclass(frozen=True)
class HunterBatchMatchResult:
    """Batch match outputs with synthesized failure summaries."""

    match_results: list[MatchResult]
    failures: list[MatchFailureReport]
    ungroundable: list[str]


@dataclass(frozen=True)
class MacroArtifactCandidate:
    """Published macro artifact candidate considered before leaf grounding."""

    fqdn: str
    semver: str = ""
    content_hash: str = ""
    artifact_kind: str = "cdg"
    name: str = ""
    description: str = ""
    conceptual_summary: str = ""
    domain_tags: list[str] = field(default_factory=list)
    required_context_tags: list[str] = field(default_factory=list)
    verified_leaf_coverage: float = 0.0
    score: float = 0.0
    visibility_tier: str = "general"
    cdg: Any | None = None
    terminal_on_match: bool = True


@dataclass(frozen=True)
class MacroMatchRequest:
    """Request model for direct macro-artifact matching."""

    goal: str


@dataclass(frozen=True)
class MacroMatchResult:
    """Deterministic ranked result for direct macro retrieval."""

    success: bool
    candidate: MacroArtifactCandidate | None = None
    ranked_candidates: list[MacroArtifactCandidate] = field(default_factory=list)
    score: float = 0.0
    rejection_reason: str = ""


@dataclass(frozen=True)
class PlannerStep:
    """A planner-visible action taken during tool orchestration."""

    action: str
    detail: str
    status: str = "completed"


@dataclass(frozen=True)
class PlannerPolicy:
    """Explicit execution policy for the single-agent planner."""

    direct_grounding_enabled: bool = True
    decomposition_mode: str = "single_pass"
    retrieval_intensity: str = "light"
    escalation_enabled: bool = True
    repair_policy: str = "bounded"
    partial_accept_enabled: bool = True
    selective_redecompose_enabled: bool = True


@dataclass
class PlannerBudget:
    """Bounded planner budget used for deterministic tool orchestration."""

    max_steps: int = 6
    steps_used: int = 0


@dataclass
class PlannerState:
    """Mutable planner state captured for telemetry and benchmarking."""

    goal: str
    policy: PlannerPolicy = field(default_factory=PlannerPolicy)
    budget: PlannerBudget = field(default_factory=PlannerBudget)
    current_focus: str = "goal"
    open_failures: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    artifact_mutations: dict[str, int] = field(default_factory=dict)
    tool_metrics: dict[str, dict[str, float | int]] = field(default_factory=dict)
    attempt_history: list[str] = field(default_factory=list)
    escalation_events: list[dict[str, str]] = field(default_factory=list)
    tool_trace: list[PlannerStep] = field(default_factory=list)
    verification_status: str = "pending"
    termination_reason: str = ""


@dataclass(frozen=True)
class PlannerRunResult:
    """Top-level result for the first-cut single-agent planner runtime."""

    result: OrchestratorResult
    execution_path: str
    steps: list[PlannerStep]
    state: PlannerState


@dataclass(frozen=True)
class OrchestrationRequest:
    """Request model for full orchestration escalation."""

    cdg: Any
    llm: Any
    prover: Prover
    max_rounds: int
    hunter_concurrency: int


@dataclass(frozen=True)
class SynthesizerAssembleRequest:
    """Request model for building a skeleton from CDG + verified matches."""

    cdg: Any
    match_results: list[MatchResult]
    tunable_params_by_primitive: dict[str, list[str]] | None = None


@dataclass(frozen=True)
class SynthesizerAssembleResult:
    """Structured response for skeleton assembly."""

    skeleton: SkeletonFile


@dataclass(frozen=True)
class SynthesizerCompileRequest:
    """Request model for compiling a synthesized skeleton."""

    skeleton: SkeletonFile
    env: ProofEnvironment


@dataclass(frozen=True)
class SynthesizerCompileResult:
    """Structured response for skeleton compilation."""

    result: AssemblyResult


@dataclass(frozen=True)
class SynthesizerAssembleAndCheckRequest:
    """Request model for assemble + optional ghost sim + compile."""

    cdg: Any
    match_results: list[MatchResult]
    env: ProofEnvironment
    skip_ghost_sim: bool = False


@dataclass(frozen=True)
class SynthesizerAssembleAndCheckResult:
    """Structured response for assemble-and-check execution."""

    result: AssemblyResult


@dataclass(frozen=True)
class SynthesizerRepairRequest:
    """Request model for repair-loop synthesis."""

    skeleton: SkeletonFile


@dataclass(frozen=True)
class SynthesizerRepairResult:
    """Structured response for synthesizer repair."""

    result: SynthesisResult
