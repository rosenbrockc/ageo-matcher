"""Shared domain types for the AGEO-Matcher pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Prover(str, Enum):
    """Supported proof assistants."""

    LEAN4 = "lean4"
    COQ = "coq"
    PYTHON = "python"


class VerificationLevel(str, Enum):
    """Trust level of a verification result."""

    KERNEL_PROOF = "kernel_proof"  # Lean 4 kernel, Coq kernel
    TYPE_CHECKED = "type_checked"  # mypy, Lean elaborator without proof
    CONTRACT_CHECKED = "contract_checked"  # icontract runtime validation
    UNVERIFIED = "unverified"  # no verification performed


@dataclass(frozen=True)
class Declaration:
    """A formal declaration extracted from a proof library.

    Represents a theorem, lemma, definition, or axiom from Lean 4/Mathlib or Coq.
    """

    name: str
    type_signature: str
    docstring: str = ""
    conceptual_summary: str = ""
    source_lib: str = ""
    prover: Prover = Prover.LEAN4
    raw_code: str = ""
    provider_id: str = ""
    distribution_name: str = ""
    distribution_version: str = ""
    install_requirement: str = ""
    import_module: str = ""
    import_symbol: str = ""
    wheel_url: str = ""
    wheel_sha256: str = ""


@dataclass(frozen=True)
class PDGNode:
    """A node from the Predicate Dependency Graph (Round 1 output).

    Represents a high-level predicate that needs to be grounded
    into a verified library function.
    """

    predicate_id: str
    statement: str
    informal_desc: str = ""
    prover: Prover = Prover.LEAN4
    context: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateMatch:
    """A candidate library function that might match a PDG predicate."""

    declaration: Declaration
    score: float
    retrieval_method: str


@dataclass(frozen=True)
class VerificationResult:
    """Result of attempting to verify a candidate match via the compiler."""

    candidate: CandidateMatch
    verified: bool
    compiler_output: str = ""
    proof_term: str = ""
    error_message: str = ""
    verification_level: VerificationLevel = VerificationLevel.UNVERIFIED


class FailureAction(str, Enum):
    """Suggested action when a match fails."""

    SPLIT = "split"  # split the atomic node into finer sub-atoms
    GENERALIZE = "generalize"  # replace with an equivalent formulation
    RELAX_TYPE = "relax_type"  # broaden the type signature
    UNGROUNDABLE = "ungroundable"  # cannot be grounded


@dataclass
class MatchFailureReport:
    """Report generated when the Hunter fails to ground an atomic leaf."""

    pdg_node: PDGNode
    best_candidates: list[CandidateMatch] = field(default_factory=list)
    error_summaries: list[str] = field(default_factory=list)
    suggested_action: FailureAction = FailureAction.SPLIT

    @staticmethod
    def from_match_result(result: "MatchResult") -> "MatchFailureReport":
        """Build a failure report from a failed MatchResult."""
        errors = [
            vr.error_message for vr in result.all_verifications if vr.error_message
        ][
            :5
        ]  # keep top 5 error summaries
        return MatchFailureReport(
            pdg_node=result.pdg_node,
            best_candidates=result.all_candidates[:5],
            error_summaries=errors,
        )


@dataclass
class MatchResult:
    """Final result of the matching pipeline for a single PDG node."""

    pdg_node: PDGNode
    verified_match: VerificationResult | None = None
    all_candidates: list[CandidateMatch] = field(default_factory=list)
    all_verifications: list[VerificationResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.verified_match is not None and self.verified_match.verified

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""

        def _decl_dict(d: Declaration) -> dict:
            return {
                "name": d.name,
                "type_signature": d.type_signature,
                "docstring": d.docstring,
                "conceptual_summary": d.conceptual_summary,
                "source_lib": d.source_lib,
                "prover": d.prover.value,
                "raw_code": d.raw_code,
                "provider_id": d.provider_id,
                "distribution_name": d.distribution_name,
                "distribution_version": d.distribution_version,
                "install_requirement": d.install_requirement,
                "import_module": d.import_module,
                "import_symbol": d.import_symbol,
                "wheel_url": d.wheel_url,
                "wheel_sha256": d.wheel_sha256,
            }

        def _candidate_dict(c: CandidateMatch) -> dict:
            return {
                "declaration": _decl_dict(c.declaration),
                "score": c.score,
                "retrieval_method": c.retrieval_method,
            }

        def _vr_dict(vr: VerificationResult) -> dict:
            return {
                "candidate": _candidate_dict(vr.candidate),
                "verified": vr.verified,
                "compiler_output": vr.compiler_output,
                "proof_term": vr.proof_term,
                "error_message": vr.error_message,
                "verification_level": vr.verification_level.value,
            }

        result: dict = {
            "pdg_node": {
                "predicate_id": self.pdg_node.predicate_id,
                "statement": self.pdg_node.statement,
                "informal_desc": self.pdg_node.informal_desc,
                "prover": self.pdg_node.prover.value,
                "context": dict(self.pdg_node.context),
            },
            "verified_match": (
                _vr_dict(self.verified_match) if self.verified_match else None
            ),
            "all_candidates": [_candidate_dict(c) for c in self.all_candidates],
            "all_verifications": [_vr_dict(vr) for vr in self.all_verifications],
        }
        return result

    @staticmethod
    def from_dict(data: dict) -> "MatchResult":
        """Deserialize from a JSON-friendly dict."""

        def _decl(d: dict) -> Declaration:
            return Declaration(
                name=d["name"],
                type_signature=d.get("type_signature", ""),
                docstring=d.get("docstring", ""),
                conceptual_summary=d.get("conceptual_summary", ""),
                source_lib=d.get("source_lib", ""),
                prover=Prover(d.get("prover", "lean4")),
                raw_code=d.get("raw_code", ""),
            )

        def _candidate(d: dict) -> CandidateMatch:
            return CandidateMatch(
                declaration=_decl(d["declaration"]),
                score=d.get("score", 0.0),
                retrieval_method=d.get("retrieval_method", ""),
            )

        def _vr(d: dict) -> VerificationResult:
            return VerificationResult(
                candidate=_candidate(d["candidate"]),
                verified=d.get("verified", False),
                compiler_output=d.get("compiler_output", ""),
                proof_term=d.get("proof_term", ""),
                error_message=d.get("error_message", ""),
                verification_level=VerificationLevel(
                    d.get("verification_level", "unverified")
                ),
            )

        pdg_data = data["pdg_node"]
        pdg_node = PDGNode(
            predicate_id=pdg_data["predicate_id"],
            statement=pdg_data.get("statement", ""),
            informal_desc=pdg_data.get("informal_desc", ""),
            prover=Prover(pdg_data.get("prover", "lean4")),
            context=pdg_data.get("context", {}),
        )

        verified_match = (
            _vr(data["verified_match"]) if data.get("verified_match") else None
        )
        all_candidates = [_candidate(c) for c in data.get("all_candidates", [])]
        all_verifications = [_vr(vr) for vr in data.get("all_verifications", [])]

        return MatchResult(
            pdg_node=pdg_node,
            verified_match=verified_match,
            all_candidates=all_candidates,
            all_verifications=all_verifications,
        )
