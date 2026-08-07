"""Deterministic direct macro-artifact retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from sciona.services.models import (
    MacroArtifactCandidate,
    MacroMatchRequest,
    MacroMatchResult,
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")


def _normalize_text(text: str) -> str:
    return " ".join(_TOKEN_PATTERN.findall((text or "").lower()))


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall((text or "").lower()))


@dataclass(frozen=True)
class _CandidateScore:
    context_mismatch: int
    context_match: int
    exact_goal_match: int
    goal_overlap: float
    summary_overlap: float
    verified_leaf_coverage: float
    score: float
    content_hash: str
    fqdn: str
    semver: str


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _candidate_text(candidate: MacroArtifactCandidate) -> str:
    return " ".join(
        part
        for part in [
            candidate.fqdn,
            candidate.name,
            candidate.description,
            candidate.conceptual_summary,
            " ".join(candidate.domain_tags),
        ]
        if str(part or "").strip()
    )


def _rank_key(goal: str, candidate: MacroArtifactCandidate) -> tuple[Any, ...]:
    normalized_goal = _normalize_text(goal)
    goal_tokens = _tokenize(goal)
    candidate_text = _candidate_text(candidate)
    candidate_tokens = _tokenize(candidate_text)
    required_tokens = {
        token
        for tag in candidate.required_context_tags
        for token in _tokenize(tag)
    }
    context_mismatch = int(bool(required_tokens) and not bool(goal_tokens & required_tokens))
    context_match = int(bool(required_tokens) and bool(goal_tokens & required_tokens))
    summary_tokens = _tokenize(
        " ".join(
            part
            for part in [candidate.name, candidate.conceptual_summary, candidate.description]
            if str(part or "").strip()
        )
    )
    exact_goal_match = int(normalized_goal != "" and normalized_goal == _normalize_text(candidate_text))
    goal_overlap = (
        len(goal_tokens & candidate_tokens) / max(1, len(goal_tokens))
        if goal_tokens
        else 0.0
    )
    summary_overlap = (
        len(goal_tokens & summary_tokens) / max(1, len(goal_tokens))
        if goal_tokens
        else 0.0
    )
    score = _safe_float(candidate.score)
    coverage = max(0.0, min(1.0, _safe_float(candidate.verified_leaf_coverage)))
    score_parts = _CandidateScore(
        context_mismatch=context_mismatch,
        context_match=context_match,
        exact_goal_match=exact_goal_match,
        goal_overlap=goal_overlap,
        summary_overlap=summary_overlap,
        verified_leaf_coverage=coverage,
        score=score,
        content_hash=str(candidate.content_hash or ""),
        fqdn=str(candidate.fqdn or ""),
        semver=str(candidate.semver or ""),
    )
    return (
        score_parts.context_mismatch,
        -score_parts.context_match,
        -score_parts.exact_goal_match,
        -score_parts.goal_overlap,
        -score_parts.summary_overlap,
        -score_parts.verified_leaf_coverage,
        -score_parts.score,
        score_parts.content_hash,
        score_parts.fqdn,
        score_parts.semver,
    )


def _match_score(goal: str, candidate: MacroArtifactCandidate) -> float:
    goal_tokens = _tokenize(goal)
    candidate_tokens = _tokenize(_candidate_text(candidate))
    summary_tokens = _tokenize(
        " ".join(
            part
            for part in [candidate.name, candidate.conceptual_summary, candidate.description]
            if str(part or "").strip()
        )
    )
    exact = float(int(_normalize_text(goal) == _normalize_text(_candidate_text(candidate)) and goal.strip()))
    goal_overlap = len(goal_tokens & candidate_tokens) / max(1, len(goal_tokens)) if goal_tokens else 0.0
    summary_overlap = len(goal_tokens & summary_tokens) / max(1, len(goal_tokens)) if goal_tokens else 0.0
    coverage = max(0.0, min(1.0, _safe_float(candidate.verified_leaf_coverage)))
    required_tokens = {
        token
        for tag in candidate.required_context_tags
        for token in _tokenize(tag)
    }
    context_match = bool(required_tokens) and bool(goal_tokens & required_tokens)
    context_penalty = 1.5 if required_tokens and not context_match else 0.0
    return (
        (1.5 * exact)
        + goal_overlap
        + (0.5 * summary_overlap)
        + (0.25 * coverage)
        + (1.0 if context_match else 0.0)
        - context_penalty
    )


class MacroArtifactRetriever:
    """Pure-Python deterministic macro retriever over a candidate set."""

    def __init__(
        self,
        candidates: Iterable[MacroArtifactCandidate] | None = None,
        *,
        min_score: float = 0.55,
    ) -> None:
        self._candidates = list(candidates or [])
        self._min_score = min_score

    def replace_candidates(
        self,
        candidates: Iterable[MacroArtifactCandidate],
    ) -> None:
        self._candidates = list(candidates)

    async def match_goal(self, request: MacroMatchRequest) -> MacroMatchResult:
        ranked = sorted(self._candidates, key=lambda candidate: _rank_key(request.goal, candidate))
        if not ranked:
            return MacroMatchResult(
                success=False,
                ranked_candidates=[],
                rejection_reason="no_macro_candidates",
            )

        best = ranked[0]
        score = _match_score(request.goal, best)
        if score < self._min_score:
            return MacroMatchResult(
                success=False,
                candidate=best,
                ranked_candidates=ranked,
                score=score,
                rejection_reason="macro_score_below_threshold",
            )
        return MacroMatchResult(
            success=True,
            candidate=best,
            ranked_candidates=ranked,
            score=score,
        )
