"""Pydantic-graph nodes forming the Hunter's search-verify-refine cycle.

Graph topology:
    InitialSearch -> RankCandidates -> VerifyTopK -> ReformulateQuery -> InitialSearch -> ...
                                           |
                                      End[MatchResult]  (on verified match or budget exhausted)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from sciona.atom_identity import logical_atom_id_from_fqdn, known_atom_package_prefixes
from sciona.architect.models import AlgorithmicPrimitive
from sciona.json_utils import extract_json

from pydantic_graph import BaseNode, End, GraphRunContext

from sciona.hunter.deps import HunterDeps
from sciona.hunter.prompts import (
    ANALYZE_FAILURE_SYSTEM,
    ANALYZE_FAILURE_USER,
    REFORMULATE_QUERY_SYSTEM,
    REFORMULATE_QUERY_USER,
    SCORE_CANDIDATES_SYSTEM,
    SCORE_CANDIDATES_USER,
)
from sciona.hunter.query_reformulator import derive_catalog_hints
from sciona.hunter.state import HunterState
from sciona.llm_router import (
    HUNTER_ANALYZE_FAILURE,
    HUNTER_REFORMULATE,
    HUNTER_SCORE,
    select_llm,
)
from sciona.shared_context import format_context_block
from sciona.telemetry import increment_run_metadata_counter, merge_run_metadata
from sciona.types import CandidateMatch, Declaration, MatchResult, Prover

_INT_ARRAY_GBNF = r"""
root ::= ws "[" ws int_list? ws "]" ws
int_list ::= integer (ws "," ws integer)*
integer ::= "-"? [0-9]+
ws ::= [ \t\n\r]*
"""

_STRING_ARRAY_GBNF = r"""
root ::= ws "[" ws string_list? ws "]" ws
string_list ::= string (ws "," ws string)*
string ::= "\"" chars "\""
chars ::= char*
char ::= [^"\\] | "\\" escape
escape ::= ["\\/bfnrt] | "u" hex hex hex hex
hex ::= [0-9a-fA-F]
ws ::= [ \t\n\r]*
"""


def _tokenize_candidate_text(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", text.lower())
        if len(token) >= 2
    }


def _recognized_atom_package_prefix(label: str) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    for prefix in sorted(known_atom_package_prefixes(), key=len, reverse=True):
        if text == prefix or text.startswith(prefix + "."):
            return prefix
    return ""


def _is_atom_package_label(label: str) -> bool:
    return bool(_recognized_atom_package_prefix(label))


def _catalog_lookup_candidates(label: str) -> list[str]:
    text = str(label or "").strip()
    if not text:
        return []

    candidates = [text]
    logical_name = logical_atom_id_from_fqdn(text)
    if logical_name and logical_name != text:
        candidates.append(logical_name)

    raw_parts = [part for part in text.split(".") if part]
    for start in range(1, len(raw_parts)):
        candidates.append(".".join(raw_parts[start:]))

    if logical_name and logical_name != text:
        logical_parts = [part for part in logical_name.split(".") if part]
        for start in range(1, len(logical_parts)):
            candidates.append(".".join(logical_parts[start:]))

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _ordered_query_tokens(text: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for token in re.findall(r"[a-z0-9_]+", text.lower()):
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


_QUERY_STOPWORDS = {
    "from",
    "into",
    "the",
    "and",
    "or",
    "raw",
    "target",
    "salient",
    "conditioned",
    "compute",
    "signal",
    "events",
    "event",
}


def _auxiliary_queries(pdg_node) -> list[str]:
    matched_primitive = str((pdg_node.context or {}).get("matched_primitive", "") or "")
    if not matched_primitive:
        return []
    matched_query = matched_primitive.replace("_", " ").strip()
    ordered = _ordered_query_tokens(
        " ".join([str(pdg_node.statement or ""), str(pdg_node.informal_desc or "")])
    )
    salient = [token for token in ordered if token not in _QUERY_STOPWORDS][:6]
    queries = [matched_query]
    if salient:
        queries.append(" ".join([matched_query, *salient]))
    return [query for query in queries if query]


_STAGE_POSITIVE_TOKENS: dict[str, set[str]] = {
    "filter": {
        "filter",
        "filtered",
        "bandpass",
        "denoise",
        "denoising",
        "smooth",
        "smoothing",
        "condition",
        "conditioned",
        "preprocess",
        "clean",
    },
    "detect": {
        "detect",
        "detection",
        "peak",
        "peaks",
        "r_peak",
        "rpeaks",
        "onset",
        "onsets",
        "beat",
        "beats",
        "qrs",
        "segment",
        "segmenter",
        "segmentation",
        "hamilton",
        "christov",
        "engzee",
        "ssf",
    },
    "rate": {
        "compute",
        "computation",
        "rate",
        "heart_rate",
        "bpm",
        "cadence",
        "interval",
        "intervals",
        "rr",
        "ibi",
        "hr",
    },
}


def _choose_stage_from_hits(tokens: set[str]) -> str | None:
    if not tokens:
        return None
    stage_hits = {
        stage_name: len(tokens & stage_tokens)
        for stage_name, stage_tokens in _STAGE_POSITIVE_TOKENS.items()
    }
    best_stage = max(
        stage_hits,
        key=lambda stage_name: (
            stage_hits[stage_name],
            1 if stage_name == "rate" else 0,
            1 if stage_name == "filter" else 0,
            1 if stage_name == "detect" else 0,
        ),
    )
    if stage_hits[best_stage] <= 0:
        return None
    return best_stage


def _leading_stage_hint(text: str) -> str | None:
    ordered = _ordered_query_tokens(text.replace("_", " "))
    if not ordered:
        return None
    first = ordered[0]
    if first in {"compute", "estimate", "measure"}:
        return "rate"
    if first in {"filter", "denoise", "condition", "preprocess", "clean"}:
        return "filter"
    if first in {"detect", "find", "segment", "extract"}:
        return "detect"
    return None


def _infer_stage_tokens(pdg_node) -> tuple[str | None, set[str]]:
    matched_primitive = str((pdg_node.context or {}).get("matched_primitive", "") or "")
    matched_tokens = _tokenize_candidate_text(matched_primitive.replace("_", " "))
    tokens = _tokenize_candidate_text(
        " ".join(
            [
                str(pdg_node.statement or ""),
                str(pdg_node.informal_desc or ""),
                matched_primitive.replace("_", " "),
            ]
        )
    )

    leading_stage = _leading_stage_hint(matched_primitive)
    if leading_stage is not None:
        return leading_stage, tokens

    matched_stage = _choose_stage_from_hits(matched_tokens)
    if matched_stage is not None:
        return matched_stage, tokens
    return _choose_stage_from_hits(tokens), tokens


def _stage_alignment_bonus(pdg_node, candidate_tokens: set[str]) -> float:
    stage, node_tokens = _infer_stage_tokens(pdg_node)
    if stage is None:
        return 0.0
    positive = _STAGE_POSITIVE_TOKENS[stage]
    overlap = candidate_tokens & positive
    if not overlap:
        return 0.0

    bonus = 1.0 + min(1.2, 0.35 * len(overlap))
    if (node_tokens & {"peak", "peaks", "r_peak", "rpeaks"}) and (
        candidate_tokens & {"r_peak", "rpeaks", "qrs", "peak", "peaks"}
    ):
        bonus += 0.4
    return min(2.2, bonus)


def _cross_stage_penalty(pdg_node, candidate_tokens: set[str]) -> float:
    stage, _ = _infer_stage_tokens(pdg_node)
    if stage is None:
        return 0.0
    stage_hits = {
        stage_name: len(candidate_tokens & tokens)
        for stage_name, tokens in _STAGE_POSITIVE_TOKENS.items()
    }
    target_hits = stage_hits.get(stage, 0)
    best_stage = max(stage_hits, key=stage_hits.get)
    best_hits = stage_hits[best_stage]
    if best_hits == 0:
        return 0.0
    if best_stage != stage and target_hits == 0:
        return min(2.2, 1.4 + 0.35 * best_hits)
    if best_stage != stage and best_hits > target_hits:
        return min(1.8, 0.8 + 0.35 * (best_hits - target_hits))
    penalties = 0.0
    for other_stage, hits in stage_hits.items():
        if other_stage == stage or hits <= 0:
            continue
        penalties += 0.3 * hits
    return min(1.2, penalties)


def _candidate_prior_bonus(
    pdg_node,
    candidate: CandidateMatch,
) -> float:
    """Compute a small deterministic lexical prior for semantic fit."""
    goal_text = " ".join(
        [
            str(pdg_node.statement or ""),
            str(pdg_node.informal_desc or ""),
            " ".join(f"{key} {value}" for key, value in sorted((pdg_node.context or {}).items())),
        ]
    )
    goal_tokens = _tokenize_candidate_text(goal_text)
    if not goal_tokens:
        return 0.0

    declaration = candidate.declaration
    candidate_text = " ".join(
        [
            declaration.name,
            declaration.source_lib,
            declaration.type_signature,
            declaration.docstring,
            declaration.conceptual_summary,
        ]
    )
    candidate_tokens = _tokenize_candidate_text(candidate_text)
    raw_code_tokens = _tokenize_candidate_text(declaration.raw_code or "")
    if not candidate_tokens:
        return 0.0

    overlap = goal_tokens & candidate_tokens
    if not overlap:
        return 0.0

    bonus = min(0.18, 0.03 * len(overlap))
    modality_tokens = {"ecg", "ppg", "eeg", "emg", "heart_rate", "r_peak", "rpeaks"}
    if overlap & modality_tokens:
        bonus += 0.04
    namespace_tokens = _tokenize_candidate_text(
        f"{declaration.name} {declaration.source_lib}"
    )
    active_modality = None
    for modality in ("ecg", "ppg", "eeg", "emg", "pcg"):
        if modality in goal_tokens and modality in namespace_tokens:
            active_modality = modality
            bonus += 0.7
            break
        if modality in goal_tokens:
            active_modality = modality
    if active_modality is not None:
        for other_modality in ("ecg", "ppg", "eeg", "emg", "pcg"):
            if other_modality == active_modality:
                continue
            if other_modality in namespace_tokens:
                bonus -= 4.0
                break
    stage, _ = _infer_stage_tokens(pdg_node)
    if (
        active_modality is not None
        and active_modality in namespace_tokens
        and stage in _STAGE_POSITIVE_TOKENS
        and candidate_tokens & _STAGE_POSITIVE_TOKENS[stage]
    ):
        bonus += 0.8
    sampled_signal_tokens = {
        "signal",
        "waveform",
        "ecg",
        "ppg",
        "eeg",
        "emg",
        "peak",
        "peaks",
        "event",
        "events",
        "heart",
        "rate",
    }
    if "sampling_rate" in candidate_tokens and goal_tokens & sampled_signal_tokens:
        bonus += 0.45
    if {"sampling_rate", "fs"} & raw_code_tokens and goal_tokens & sampled_signal_tokens:
        bonus += 0.08
    if (
        active_modality in {"ecg", "ppg", "eeg", "emg", "pcg"}
        and stage in {"filter", "detect", "rate"}
        and "sampling_rate" not in candidate_tokens
    ):
        bonus -= 0.55
    bonus += _stage_alignment_bonus(pdg_node, candidate_tokens)
    bonus -= _cross_stage_penalty(pdg_node, candidate_tokens)
    return max(-0.5, min(2.4, bonus))


def _declaration_from_primitive(
    primitive: AlgorithmicPrimitive,
    *,
    name: str,
    source_lib: str,
    prover: Prover,
    raw_code: str = "",
    conceptual_summary: str = "",
) -> Declaration:
    return Declaration(
        name=name,
        type_signature=primitive.type_signature,
        docstring=primitive.description,
        conceptual_summary=conceptual_summary,
        source_lib=source_lib,
        prover=prover,
        raw_code=raw_code,
    )


def _resolve_live_primitive(
    deps: HunterDeps,
    declaration: Declaration,
) -> AlgorithmicPrimitive | None:
    catalog = deps.live_catalog
    if catalog is None:
        return None

    seen: set[str] = set()
    for key in [
        *_catalog_lookup_candidates(declaration.name),
        *_catalog_lookup_candidates(declaration.source_lib),
    ]:
        normalized = key.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        primitive = catalog.get(normalized)
        if primitive is not None:
            return primitive
    return None


def _canonicalize_candidate_match(
    deps: HunterDeps,
    candidate: CandidateMatch,
) -> CandidateMatch | None:
    catalog = deps.live_catalog
    if catalog is None:
        return candidate

    declaration = candidate.declaration
    primitive = _resolve_live_primitive(deps, declaration)
    if primitive is not None:
        canonical_declaration = _declaration_from_primitive(
            primitive,
            name=declaration.name,
            source_lib=declaration.source_lib or primitive.source,
            prover=declaration.prover,
            raw_code=declaration.raw_code,
            conceptual_summary=declaration.conceptual_summary,
        )
        if declaration.install_requirement:
            canonical_declaration = replace(
                canonical_declaration,
                provider_id=declaration.provider_id,
                distribution_name=declaration.distribution_name,
                distribution_version=declaration.distribution_version,
                install_requirement=declaration.install_requirement,
                import_module=declaration.import_module,
                import_symbol=declaration.import_symbol,
                wheel_url=declaration.wheel_url,
                wheel_sha256=declaration.wheel_sha256,
            )
        return CandidateMatch(
            declaration=canonical_declaration,
            score=candidate.score,
            retrieval_method=candidate.retrieval_method,
        )

    if _is_atom_package_label(declaration.name) or _is_atom_package_label(
        declaration.source_lib
    ):
        if declaration.install_requirement and declaration.import_module:
            return candidate
        return None
    return candidate


def _canonicalize_candidates(
    deps: HunterDeps,
    candidates: list[CandidateMatch],
) -> list[CandidateMatch]:
    normalized: dict[str, CandidateMatch] = {}
    for candidate in candidates:
        canonical = _canonicalize_candidate_match(deps, candidate)
        if canonical is None:
            continue
        name = canonical.declaration.name
        incumbent = normalized.get(name)
        if incumbent is None or canonical.score > incumbent.score:
            normalized[name] = canonical
    return list(normalized.values())


def _exact_matched_primitive_candidate(
    deps: HunterDeps,
    pdg_node,
) -> CandidateMatch | None:
    """Build a deterministic exact-match candidate from a bound primitive hint."""
    catalog = deps.live_catalog
    if catalog is None:
        return None

    raw = str((pdg_node.context or {}).get("matched_primitive", "") or "").strip()
    if not raw:
        return None

    primitive = None
    for key in _catalog_lookup_candidates(raw):
        primitive = catalog.get(key)
        if primitive is not None:
            break
    if primitive is None:
        return None

    source_lib = raw.rsplit(".", 1)[0] if "." in raw else primitive.source
    return CandidateMatch(
        declaration=_declaration_from_primitive(
            primitive,
            name=raw,
            source_lib=source_lib,
            prover=pdg_node.prover,
        ),
        score=10.0,
        retrieval_method="exact_primitive",
    )


def _apply_deterministic_candidate_priors(
    pdg_node,
    candidates: list[CandidateMatch],
) -> list[CandidateMatch]:
    """Stably rerank candidates with deterministic lexical priors."""
    indexed = list(enumerate(candidates))
    indexed.sort(
        key=lambda item: (
            -(item[1].score + _candidate_prior_bonus(pdg_node, item[1])),
            item[0],
        )
    )
    return [candidate for _, candidate in indexed]


async def _complete_with_optional_grammar(
    llm: Any, *, system: str, user: str, grammar: str, use_gbnf: bool
) -> str:
    if use_gbnf and hasattr(type(llm), "complete_with_grammar"):
        return await llm.complete_with_grammar(system, user, grammar)
    return await llm.complete(system, user)


async def _search_context(
    deps: HunterDeps,
    state: HunterState,
    *,
    channel: str,
    query: str,
    limit: int,
) -> str:
    store = deps.shared_context
    ns = state.context_namespace
    if store is None or not ns:
        return ""
    try:
        records = await store.search(f"{ns}/{channel}", query, limit=limit)
        if channel == "failure" and deps.shared_context_metrics is not None:
            deps.shared_context_metrics.record_failure_search(hits=len(records))
        block = format_context_block(
            "Shared Context",
            records,
            max_chars=state.context_budget_chars,
            metrics=deps.shared_context_metrics,
        )
        if channel == "failure" and deps.shared_context_metrics is not None:
            deps.shared_context_metrics.record_failure_injection(
                chars=len(block),
                records=len(records),
            )
        if block:
            state.shared_context_used = True
        return block
    except Exception:
        return ""


async def _put_context(
    deps: HunterDeps,
    state: HunterState,
    *,
    channel: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    store = deps.shared_context
    ns = state.context_namespace
    if store is None or not ns:
        return
    try:
        await store.put(f"{ns}/{channel}", text, metadata=metadata)
        if channel == "failure" and deps.shared_context_metrics is not None:
            deps.shared_context_metrics.record_failure_put()
    except Exception:
        return


def _merge_hunter_metrics(payload: dict[str, Any]) -> None:
    merge_run_metadata({"hunter_metrics": payload})


def _increment_hunter_metric(metric: str, amount: int = 1) -> None:
    increment_run_metadata_counter("hunter_metrics", metric, amount=amount)


@dataclass
class InitialSearch(BaseNode[HunterState, HunterDeps, MatchResult]):
    """Search the semantic index using embedding and type queries."""

    async def run(
        self, ctx: GraphRunContext[HunterState, HunterDeps]
    ) -> RankCandidates | End[MatchResult]:
        state = ctx.state
        deps = ctx.deps
        node = state.pdg_node

        # Build query from predicate
        if state.queries_tried:
            query = state.queries_tried[-1]
        else:
            query = f"{node.statement} {node.informal_desc}".strip()
            state.queries_tried.append(query)

        # In speculative-local mode, flood retrieval with a batch of queries.
        queries_to_use = [query]
        for extra_query in _auxiliary_queries(node):
            if extra_query not in queries_to_use:
                queries_to_use.append(extra_query)
        embedding_k = state.search_k
        if state.mode == "speculative_local":
            queries_to_use = state.queries_tried[-state.query_batch_size :]
            for extra_query in _auxiliary_queries(node):
                if extra_query not in queries_to_use:
                    queries_to_use.append(extra_query)
            embedding_k = state.top_k_per_query

        embedding_results = []
        for q in queries_to_use:
            embedding_results.extend(deps.index.search_by_embedding(q, k=embedding_k))

        # Type search
        type_results = deps.index.search_by_type(node.statement, k=state.search_k)

        candidate_pool = {
            candidate.declaration.name: candidate for candidate in state.candidates_found
        }

        exact_candidate = _exact_matched_primitive_candidate(deps, node)
        if exact_candidate is not None:
            candidate_pool[exact_candidate.declaration.name] = exact_candidate

        for decl, score in embedding_results:
            candidate = _canonicalize_candidate_match(
                deps,
                CandidateMatch(
                    declaration=decl,
                    score=score,
                    retrieval_method="embedding",
                ),
            )
            if candidate is None:
                continue
            incumbent = candidate_pool.get(candidate.declaration.name)
            if incumbent is None or candidate.score > incumbent.score:
                candidate_pool[candidate.declaration.name] = candidate

        for decl in type_results:
            candidate = _canonicalize_candidate_match(
                deps,
                CandidateMatch(
                    declaration=decl,
                    score=0.0,
                    retrieval_method="type_search",
                ),
            )
            if candidate is None:
                continue
            incumbent = candidate_pool.get(candidate.declaration.name)
            if incumbent is None or candidate.score > incumbent.score:
                candidate_pool[candidate.declaration.name] = candidate

        new_candidates = [
            candidate
            for name, candidate in candidate_pool.items()
            if name not in {c.declaration.name for c in state.candidates_found}
        ]
        state.candidates_found = list(candidate_pool.values())
        state.candidates_found = _canonicalize_candidates(deps, state.candidates_found)
        if (
            state.max_candidates_total > 0
            and len(state.candidates_found) > state.max_candidates_total
        ):
            # Keep highest-scoring candidates when speculative retrieval floods the pool.
            state.candidates_found = sorted(
                state.candidates_found, key=lambda c: c.score, reverse=True
            )[: state.max_candidates_total]

        _increment_hunter_metric("search_iterations")
        _increment_hunter_metric("embedding_results_total", len(embedding_results))
        _increment_hunter_metric("type_results_total", len(type_results))
        _increment_hunter_metric("new_candidates_total", len(new_candidates))
        _merge_hunter_metrics(
            {
                "candidate_pool_size": len(state.candidates_found),
                "query_count": len(state.queries_tried),
                "last_query": query,
            }
        )

        if not state.candidates_found:
            # No candidates at all - end immediately
            _increment_hunter_metric("empty_search_terminations")
            if deps.shared_context_metrics is not None:
                deps.shared_context_metrics.record_match_outcome(
                    used_context=state.shared_context_used,
                    success=False,
                )
            return End(
                MatchResult(
                    pdg_node=node,
                    all_candidates=state.candidates_found,
                    all_verifications=state.verification_results,
                )
            )

        return RankCandidates()


@dataclass
class RankCandidates(BaseNode[HunterState, HunterDeps, MatchResult]):
    """Use LLM to rank candidates by likelihood of being correct."""

    async def run(self, ctx: GraphRunContext[HunterState, HunterDeps]) -> VerifyTopK:
        state = ctx.state
        deps = ctx.deps

        # Build candidate list for the LLM
        candidates_text = "\n".join(
            f"[{i}] {c.declaration.name} : {c.declaration.type_signature}"
            for i, c in enumerate(state.candidates_found)
        )

        user_msg = SCORE_CANDIDATES_USER.format(
            statement=state.pdg_node.statement,
            informal_desc=state.pdg_node.informal_desc,
            candidates_list=candidates_text,
        )
        shared_block = await _search_context(
            deps,
            state,
            channel="success",
            query=f"{state.pdg_node.statement} {state.pdg_node.informal_desc}",
            limit=3,
        )
        if shared_block:
            user_msg += f"\n\n{shared_block}"

        try:
            response = await _complete_with_optional_grammar(
                select_llm(deps.llm, HUNTER_SCORE),
                system=SCORE_CANDIDATES_SYSTEM,
                user=user_msg,
                grammar=_INT_ARRAY_GBNF,
                use_gbnf=state.use_gbnf,
            )
            ranked_indices = extract_json(response)
            # Reorder candidates
            reordered = []
            seen = set()
            for idx in ranked_indices:
                if isinstance(idx, int) and 0 <= idx < len(state.candidates_found):
                    if idx not in seen:
                        seen.add(idx)
                        reordered.append(state.candidates_found[idx])
            # Append any candidates the LLM didn't rank
            for i, c in enumerate(state.candidates_found):
                if i not in seen:
                    reordered.append(c)
            state.candidates_found = reordered
            _increment_hunter_metric("rank_calls")
            _merge_hunter_metrics(
                {
                    "candidate_pool_size": len(state.candidates_found),
                    "ranked_candidate_count": len(reordered),
                }
            )
        except (json.JSONDecodeError, Exception):
            # If LLM ranking fails, keep original order
            pass

        state.candidates_found = _apply_deterministic_candidate_priors(
            state.pdg_node,
            state.candidates_found,
        )

        return VerifyTopK()


@dataclass
class VerifyTopK(BaseNode[HunterState, HunterDeps, MatchResult]):
    """Send top-K candidates to the Verification Oracle."""

    async def run(
        self, ctx: GraphRunContext[HunterState, HunterDeps]
    ) -> ReformulateQuery | End[MatchResult]:
        state = ctx.state
        deps = ctx.deps

        # Take top-K unverified candidates
        already_verified = {
            vr.candidate.declaration.name for vr in state.verification_results
        }
        to_verify = [
            c
            for c in state.candidates_found
            if c.declaration.name not in already_verified
        ][: state.top_k_verify]

        if not to_verify:
            # Nothing new to verify
            _increment_hunter_metric("empty_verify_batches")
            if state.iteration >= state.max_iterations:
                if deps.shared_context_metrics is not None:
                    deps.shared_context_metrics.record_match_outcome(
                        used_context=state.shared_context_used,
                        success=False,
                    )
                return End(
                    MatchResult(
                        pdg_node=state.pdg_node,
                        all_candidates=state.candidates_found,
                        all_verifications=state.verification_results,
                    )
                )
            return ReformulateQuery()

        if state.verify_concurrency > 1 and hasattr(
            deps.oracle, "verify_candidates_parallel"
        ):
            results = await deps.oracle.verify_candidates_parallel(
                state.pdg_node, to_verify, max_concurrent=state.verify_concurrency
            )
        else:
            results = await deps.oracle.verify_candidates(state.pdg_node, to_verify)
        state.verification_results.extend(results)
        verified_count = sum(1 for result in results if result.verified)
        rejected_count = len(results) - verified_count
        _increment_hunter_metric("verify_batches")
        _increment_hunter_metric("verified_candidates_total", len(results))
        _increment_hunter_metric("verification_success_total", verified_count)
        _increment_hunter_metric("verification_failure_total", rejected_count)
        _merge_hunter_metrics(
            {
                "verification_pool_size": len(state.verification_results),
                "last_verify_batch_size": len(results),
            }
        )

        # Collect compiler feedback for potential reformulation
        for r in results:
            if not r.verified and r.compiler_output:
                state.compiler_feedback.append(r.compiler_output)
                snippet = " ".join(r.compiler_output.split())
                if len(snippet) > 240:
                    snippet = snippet[:237] + "..."
                await _put_context(
                    deps,
                    state,
                    channel="failure",
                    text=(
                        f"Predicate: {state.pdg_node.statement}\n"
                        f"Rejected: {r.candidate.declaration.name}\n"
                        f"Signal: {snippet}"
                    ),
                    metadata={
                        "predicate_id": state.pdg_node.predicate_id,
                        "candidate": r.candidate.declaration.name,
                    },
                )

        # Check for verified match
        for r in results:
            if r.verified:
                await _put_context(
                    deps,
                    state,
                    channel="success",
                    text=(
                        f"Predicate: {state.pdg_node.statement}\n"
                        f"Matched: {r.candidate.declaration.name}\n"
                        f"Type: {r.candidate.declaration.type_signature}"
                    ),
                    metadata={
                        "predicate_id": state.pdg_node.predicate_id,
                        "declaration": r.candidate.declaration.name,
                    },
                )
                state.verified_match = r
                _increment_hunter_metric("verified_matches")
                _merge_hunter_metrics(
                    {
                        "last_verified_candidate": r.candidate.declaration.name,
                    }
                )
                if deps.shared_context_metrics is not None:
                    deps.shared_context_metrics.record_match_outcome(
                        used_context=state.shared_context_used,
                        success=True,
                    )
                return End(
                    MatchResult(
                        pdg_node=state.pdg_node,
                        verified_match=r,
                        all_candidates=state.candidates_found,
                        all_verifications=state.verification_results,
                    )
                )

        # No verified match - check budget
        if state.iteration >= state.max_iterations:
            if deps.shared_context_metrics is not None:
                deps.shared_context_metrics.record_match_outcome(
                    used_context=state.shared_context_used,
                    success=False,
                )
            return End(
                MatchResult(
                    pdg_node=state.pdg_node,
                    all_candidates=state.candidates_found,
                    all_verifications=state.verification_results,
                )
            )

        return ReformulateQuery()


@dataclass
class ReformulateQuery(BaseNode[HunterState, HunterDeps, MatchResult]):
    """Use LLM to analyze failures and generate new search queries."""

    async def run(self, ctx: GraphRunContext[HunterState, HunterDeps]) -> InitialSearch:
        state = ctx.state
        deps = ctx.deps
        state.iteration += 1
        _increment_hunter_metric("reformulations")
        _merge_hunter_metrics({"iteration": state.iteration})

        # Analyze the most recent failure if available
        recent_failures = [r for r in state.verification_results if not r.verified]
        analysis = ""
        if recent_failures:
            last_fail = recent_failures[-1]
            analyze_msg = ANALYZE_FAILURE_USER.format(
                statement=state.pdg_node.statement,
                candidate_name=last_fail.candidate.declaration.name,
                candidate_type=last_fail.candidate.declaration.type_signature,
                compiler_output=last_fail.compiler_output,
            )
            try:
                analysis = await select_llm(deps.llm, HUNTER_ANALYZE_FAILURE).complete(
                    ANALYZE_FAILURE_SYSTEM, analyze_msg
                )
                if analysis:
                    _increment_hunter_metric("failure_analyses")
            except Exception:
                analysis = ""

        # Generate new queries
        queries_text = "\n".join(f"- {q}" for q in state.queries_tried)
        errors_text = "\n".join(state.compiler_feedback[-3:])  # Last 3 errors
        if analysis:
            errors_text += f"\n\nAnalysis: {analysis}"

        shared_failures = await _search_context(
            deps,
            state,
            channel="failure",
            query=f"{state.pdg_node.statement} {errors_text}",
            limit=3,
        )
        if shared_failures:
            errors_text += f"\n\n{shared_failures}"

        reformulate_msg = REFORMULATE_QUERY_USER.format(
            predicate_id=state.pdg_node.predicate_id,
            statement=state.pdg_node.statement,
            informal_desc=state.pdg_node.informal_desc,
            prover=state.pdg_node.prover.value,
            queries_tried=queries_text,
            compiler_errors=errors_text,
        )
        catalog_hints = derive_catalog_hints(
            deps.index,
            statement=state.pdg_node.statement,
            informal_desc=state.pdg_node.informal_desc,
            compiler_errors=errors_text,
            queries_tried=state.queries_tried,
        )
        if catalog_hints:
            reformulate_msg += "\n\n## Catalog Hints\n"
            reformulate_msg += "\n".join(f"- {hint}" for hint in catalog_hints)

        try:
            if state.mode == "speculative_local":
                reformulate_msg += (
                    f"\n\nGenerate exactly {state.query_batch_size} highly diverse queries "
                    "that maximize synonym and namespace coverage."
                )

            response = await _complete_with_optional_grammar(
                select_llm(deps.llm, HUNTER_REFORMULATE),
                system=REFORMULATE_QUERY_SYSTEM,
                user=reformulate_msg,
                grammar=_STRING_ARRAY_GBNF,
                use_gbnf=state.use_gbnf,
            )
            new_queries = extract_json(response)
            if isinstance(new_queries, list) and new_queries:
                deduped: list[str] = []
                seen = set(state.queries_tried)
                for q in new_queries:
                    q_str = str(q).strip()
                    if q_str and q_str not in seen:
                        seen.add(q_str)
                        deduped.append(q_str)
                if state.mode == "speculative_local":
                    deduped = deduped[: state.query_batch_size]
                state.queries_tried.extend(deduped)
                max_keep = max(50, state.query_batch_size * 3)
                if len(state.queries_tried) > max_keep:
                    state.queries_tried = state.queries_tried[-max_keep:]
                _merge_hunter_metrics(
                    {
                        "query_count": len(state.queries_tried),
                        "last_query": state.queries_tried[-1],
                    }
                )
        except (json.JSONDecodeError, Exception):
            # Fallback: try a simple variant
            state.queries_tried.append(state.pdg_node.statement)
            _increment_hunter_metric("reformulate_fallbacks")
            _merge_hunter_metrics(
                {
                    "query_count": len(state.queries_tried),
                    "last_query": state.queries_tried[-1],
                }
            )

        return InitialSearch()
