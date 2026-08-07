"""Deterministic sync helpers for local skeleton-family artifact catalog rows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sciona.architect.skeleton_assets import (
    SkeletonFamilyAsset,
    load_local_skeleton_assets,
    load_provider_cdg_assets,
    skeleton_asset_content_json,
)
from sciona.architect.skeletons import infer_boundary_ports
from sciona.architect.skeletons import NAMED_SKELETONS, SKELETON_TEMPLATES
from sciona.cdg_projection import (
    PublishedCDGProjection,
    build_published_cdg_projection,
)
from sciona.services.skeleton_artifacts import build_skeleton_asset_cdg

_ARTIFACT_NAMESPACE = uuid5(NAMESPACE_URL, "sciona/unified-artifacts")
_VERSION_NAMESPACE = uuid5(NAMESPACE_URL, "sciona/unified-artifact-versions")


@dataclass(frozen=True)
class SkeletonArtifactBundle:
    asset_id: str
    artifact: dict[str, Any]
    version: dict[str, Any]
    descriptions: list[dict[str, Any]] = field(default_factory=list)
    io_specs: list[dict[str, Any]] = field(default_factory=list)
    references_registry: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    audit_rollup: dict[str, Any] = field(default_factory=dict)
    audit_evidence: list[dict[str, Any]] = field(default_factory=list)
    uncertainty_estimates: list[dict[str, Any]] = field(default_factory=list)
    verification_matches: list[dict[str, Any]] = field(default_factory=list)
    cdg_nodes: list[dict[str, Any]] = field(default_factory=list)
    cdg_edges: list[dict[str, Any]] = field(default_factory=list)
    cdg_bindings: list[dict[str, Any]] = field(default_factory=list)
    binding_evidence: list[dict[str, Any]] = field(default_factory=list)
    projection: PublishedCDGProjection | None = None
    source_asset: SkeletonFamilyAsset | None = None


@dataclass(frozen=True)
class _AtomBinding:
    atom_id: str
    fqdn: str
    content_hash: str
    is_publishable: bool
    binding_source: str
    binding_confidence: float


@dataclass(frozen=True)
class _CatalogVerificationState:
    bindings_by_hint: dict[str, _AtomBinding]
    verification_by_atom_id: dict[str, dict[str, Any]]
    audit_rows_by_atom_id: dict[str, list[dict[str, Any]]]
    uncertainty_rows_by_atom_id: dict[str, list[dict[str, Any]]]


def _artifact_fqdn(asset: SkeletonFamilyAsset) -> str:
    return asset.fqdn or f"cdg.skeleton.{asset.asset_id}"


def _artifact_id(asset: SkeletonFamilyAsset) -> str:
    return str(uuid5(_ARTIFACT_NAMESPACE, _artifact_fqdn(asset)))


def _content_hash(asset: SkeletonFamilyAsset) -> str:
    payload = skeleton_asset_content_json(asset)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _version_id(asset: SkeletonFamilyAsset) -> str:
    return str(uuid5(_VERSION_NAMESPACE, f"{_artifact_fqdn(asset)}@{_content_hash(asset)}"))


def _reference_type(reference: dict[str, Any]) -> str:
    url = str(reference.get("url", "") or "").strip().lower()
    if "doi.org" in url:
        return "paper"
    if url.startswith("http"):
        return "web"
    citation = str(reference.get("citation", "") or "").strip()
    if citation:
        return "book"
    return "web"


def _reference_slug(text: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "reference"


def _reference_id(asset: SkeletonFamilyAsset, reference: dict[str, Any], ordinal: int) -> str:
    title = str(reference.get("title", "") or "").strip()
    return f"skeleton:{asset.asset_id}:{ordinal}:{_reference_slug(title)}"


def _normalize_name(text: str) -> str:
    return "".join(ch.lower() for ch in str(text or "") if ch.isalnum())


def _verification_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    level_rank = {
        "kernel_proof": 0,
        "type_checked": 1,
        "contract_checked": 2,
        "unverified": 3,
    }
    return (
        0 if bool(row.get("verified")) else 1,
        level_rank.get(str(row.get("verification_level", "unverified")), 4),
        -float(row.get("candidate_score") or 0.0),
        str(row.get("candidate_name", "") or ""),
        str(row.get("predicate_id", "") or ""),
    )


def _pick_best_verification_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(rows, key=_verification_rank)[0]


def _build_binding_hints(asset: SkeletonFamilyAsset) -> dict[str, str]:
    hints: dict[str, str] = {
        stage.stage_id: str(stage.matched_primitive or "").strip()
        for stage in asset.stages
        if str(stage.matched_primitive or "").strip()
    }
    template = (
        NAMED_SKELETONS.get(asset.asset_id)
        or NAMED_SKELETONS.get(asset.family)
        or SKELETON_TEMPLATES.get(asset.paradigm)
    )
    if template is None:
        return hints
    by_name = {
        _normalize_name(node.name): str(node.matched_primitive or "").strip()
        for node in template.template_nodes
        if str(node.matched_primitive or "").strip()
    }
    for stage in asset.stages:
        if stage.stage_id in hints:
            continue
        primitive = by_name.get(_normalize_name(stage.name), "")
        if not primitive and str(stage.stage_id).startswith("tpl_"):
            primitive = str(stage.stage_id)[4:]
        if primitive:
            hints[stage.stage_id] = primitive
    return hints


def _resolve_atom_bindings(
    atom_rows: list[dict[str, Any]],
    latest_versions: list[dict[str, Any]],
    primitive_hints: set[str],
) -> dict[str, _AtomBinding]:
    latest_by_atom_id = {
        str(row.get("atom_id", "") or ""): row
        for row in latest_versions
        if str(row.get("atom_id", "") or "")
    }
    rows_by_fqdn = {
        str(row.get("fqdn", "") or ""): row
        for row in atom_rows
        if str(row.get("fqdn", "") or "")
    }
    rows_by_suffix: dict[str, list[dict[str, Any]]] = {}
    for row in atom_rows:
        fqdn = str(row.get("fqdn", "") or "")
        suffix = fqdn.rsplit(".", 1)[-1]
        rows_by_suffix.setdefault(suffix, []).append(row)

    resolved: dict[str, _AtomBinding] = {}
    for hint in sorted(primitive_hints):
        exact = rows_by_fqdn.get(hint)
        suffix = hint.rsplit(".", 1)[-1]
        candidates = [exact] if exact is not None else list(rows_by_suffix.get(suffix, []))
        candidates = [row for row in candidates if row is not None]
        if not candidates:
            continue
        publishable = [row for row in candidates if bool(row.get("is_publishable"))]
        if exact is not None:
            winner = exact
            source = "matched_primitive_exact"
            confidence = 1.0
        elif len(publishable) == 1:
            winner = publishable[0]
            source = "matched_primitive_publishable_suffix"
            confidence = 0.95
        elif len(candidates) == 1:
            winner = candidates[0]
            source = "matched_primitive_suffix"
            confidence = 0.8
        else:
            continue
        atom_id = str(winner.get("atom_id", "") or "")
        latest = latest_by_atom_id.get(atom_id, {})
        resolved[hint] = _AtomBinding(
            atom_id=atom_id,
            fqdn=str(winner.get("fqdn", "") or ""),
            content_hash=str(latest.get("content_hash", "") or ""),
            is_publishable=bool(winner.get("is_publishable")),
            binding_source=source,
            binding_confidence=confidence,
        )
    return resolved


def _execute_all_pages(query: Any, *, page_size: int = 1000) -> list[dict[str, Any]]:
    """Exhaust a PostgREST query instead of accepting its server row cap."""
    if not hasattr(query, "range"):
        return [dict(row) for row in (query.execute().data or [])]
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = query.range(offset, offset + page_size - 1).execute().data or []
        rows.extend(dict(row) for row in page)
        if len(page) < page_size:
            return rows
        offset += page_size


def _fetch_catalog_verification_state(
    supabase: Any,
    primitive_hints: set[str],
) -> _CatalogVerificationState:
    atom_rows = _execute_all_pages(
        supabase.table("atoms")
        .select("atom_id, fqdn, is_publishable")
    )
    latest_versions = _execute_all_pages(
        supabase.table("atom_versions")
        .select("atom_id, content_hash")
        .eq("is_latest", True)
    )
    bindings_by_hint = _resolve_atom_bindings(atom_rows, latest_versions, primitive_hints)
    atom_ids = sorted({binding.atom_id for binding in bindings_by_hint.values()})
    verification_by_atom_id: dict[str, dict[str, Any]] = {}
    audit_rows_by_atom_id: dict[str, list[dict[str, Any]]] = {}
    uncertainty_rows_by_atom_id: dict[str, list[dict[str, Any]]] = {}
    if atom_ids:
        verification_response = (
            supabase.table("atom_verification_matches")
            .select(
                "atom_id, predicate_id, predicate_statement, informal_desc, candidate_name, candidate_source_lib, candidate_score, retrieval_method, verified, verification_level, proof_term, compiler_output, error_message, all_candidates, all_verifications"
            )
            .in_("atom_id", atom_ids)
            .execute()
        )
        rows_by_atom: dict[str, list[dict[str, Any]]] = {}
        for row in (verification_response.data or []):
            atom_id = str(row.get("atom_id", "") or "")
            if atom_id:
                rows_by_atom.setdefault(atom_id, []).append(dict(row))
        verification_by_atom_id = {
            atom_id: best
            for atom_id, rows in rows_by_atom.items()
            if (best := _pick_best_verification_row(rows)) is not None
        }

        audit_response = (
            supabase.table("atom_audit_evidence")
            .select("atom_id, audit_type, passed, status, details, source_kind, runner_version")
            .in_("atom_id", atom_ids)
            .execute()
        )
        for row in (audit_response.data or []):
            atom_id = str(row.get("atom_id", "") or "")
            if atom_id:
                audit_rows_by_atom_id.setdefault(atom_id, []).append(dict(row))

        uncertainty_response = (
            supabase.table("atom_uncertainty_estimates")
            .select("atom_id, mode, scalar_factor, confidence, n_trials, epsilon, input_regime, notes")
            .in_("atom_id", atom_ids)
            .execute()
        )
        for row in (uncertainty_response.data or []):
            atom_id = str(row.get("atom_id", "") or "")
            if atom_id:
                uncertainty_rows_by_atom_id.setdefault(atom_id, []).append(dict(row))

    return _CatalogVerificationState(
        bindings_by_hint=bindings_by_hint,
        verification_by_atom_id=verification_by_atom_id,
        audit_rows_by_atom_id=audit_rows_by_atom_id,
        uncertainty_rows_by_atom_id=uncertainty_rows_by_atom_id,
    )


def _fetch_artifact_benchmarks(
    supabase: Any,
    *,
    version_id: str,
) -> list[dict[str, Any]]:
    response = (
        supabase.table("artifact_benchmarks")
        .select("version_id,benchmark_name,metric_name,metric_value,dataset_tag,measured_at")
        .eq("version_id", version_id)
        .execute()
    )
    rows = [dict(row) for row in (response.data or [])]
    rows.sort(
        key=lambda row: (
            str(row.get("benchmark_name", "")),
            str(row.get("metric_name", "")),
            str(row.get("dataset_tag", "")),
            str(row.get("measured_at", "")),
        )
    )
    return rows


def _aggregate_audit_status(
    atom_bindings: list[_AtomBinding],
    audit_rows_by_atom_id: dict[str, list[dict[str, Any]]],
    audit_type: str,
) -> tuple[str, bool, dict[str, Any]]:
    found = 0
    passed = 0
    missing: list[str] = []
    for binding in atom_bindings:
        rows = [
            row
            for row in audit_rows_by_atom_id.get(binding.atom_id, [])
            if str(row.get("audit_type", "") or "") == audit_type
        ]
        if not rows:
            missing.append(binding.fqdn)
            continue
        found += 1
        if any(bool(row.get("passed")) for row in rows):
            passed += 1
    if not atom_bindings or found == 0:
        return ("skipped", False, {"found": found, "missing_atoms": missing})
    return (
        "completed",
        found == len(atom_bindings) and passed == len(atom_bindings),
        {
            "found": found,
            "passed": passed,
            "missing_atoms": missing,
        },
    )


def enrich_bundle_with_catalog_verification(
    bundle: SkeletonArtifactBundle,
    *,
    supabase: Any,
) -> SkeletonArtifactBundle:
    asset = bundle.source_asset
    if asset is None:
        return bundle

    binding_hints = _build_binding_hints(asset)
    state = _fetch_catalog_verification_state(supabase, set(binding_hints.values()))
    benchmark_rows = _fetch_artifact_benchmarks(
        supabase,
        version_id=str(bundle.version["version_id"]),
    )
    nodes_by_id = {str(row["node_id"]): row for row in bundle.cdg_nodes}
    sorted_node_ids = sorted(nodes_by_id)
    bindings: list[dict[str, Any]] = []
    verification_matches: list[dict[str, Any]] = []
    bound_atoms: list[_AtomBinding] = []
    unresolved_nodes: list[str] = []
    verified_leaf_count = 0
    benchmark_pass = bool(benchmark_rows)

    for node_id in sorted_node_ids:
        node = nodes_by_id[node_id]
        hint = str(binding_hints.get(node_id, "") or node.get("matched_primitive", "") or "").strip()
        binding = state.bindings_by_hint.get(hint) if hint else None
        if binding is None:
            unresolved_nodes.append(node_id)
        else:
            bound_atoms.append(binding)
            bindings.append(
                {
                    "version_id": bundle.version["version_id"],
                    "node_id": node_id,
                    "bound_artifact_fqdn": binding.fqdn,
                    "bound_version_content_hash": binding.content_hash,
                    "binding_confidence": binding.binding_confidence,
                    "binding_source": binding.binding_source,
                }
            )
        best_verification = (
            state.verification_by_atom_id.get(binding.atom_id) if binding is not None else None
        )
        exact_publishable_binding = bool(
            binding is not None
            and binding.binding_source == "matched_primitive_exact"
            and binding.is_publishable
        )
        binding_verified = bool(
            (best_verification is not None and best_verification.get("verified"))
            or exact_publishable_binding
        )
        if binding_verified:
            verified_leaf_count += 1
        verification_matches.append(
            {
                "artifact_id": bundle.artifact["artifact_id"],
                "version_id": bundle.version["version_id"],
                "predicate_id": node_id,
                "predicate_statement": str(node.get("name", "") or node_id),
                "informal_desc": str(node.get("description", "") or ""),
                "candidate_name": binding.fqdn if binding is not None else "",
                "candidate_source_lib": (
                    binding.fqdn.rsplit(".", 1)[0] if binding is not None and "." in binding.fqdn else ""
                ),
                "candidate_score": (
                    float(best_verification.get("candidate_score"))
                    if best_verification is not None and best_verification.get("candidate_score") is not None
                    else (binding.binding_confidence if binding is not None else 0.0)
                ),
                "retrieval_method": (
                    str(best_verification.get("retrieval_method", "") or binding.binding_source)
                    if binding is not None and best_verification is not None
                    else (binding.binding_source if binding is not None else "no_binding")
                ),
                "verified": binding_verified,
                "verification_level": str(
                    best_verification.get("verification_level", "unverified")
                )
                if best_verification is not None
                else (
                    "contract_checked"
                    if exact_publishable_binding
                    else "unverified"
                ),
                "proof_term": str(best_verification.get("proof_term", "") or "")
                if best_verification is not None
                else "",
                "compiler_output": str(best_verification.get("compiler_output", "") or "")
                if best_verification is not None
                else "",
                "error_message": (
                    str(best_verification.get("error_message", "") or "")
                    if best_verification is not None
                    else ("" if binding is not None else "no catalog binding resolved")
                ),
                "all_candidates": (
                    best_verification.get("all_candidates", [])
                    if best_verification is not None
                    else (
                        [{"candidate_name": binding.fqdn, "binding_source": binding.binding_source}]
                        if binding is not None
                        else []
                    )
                ),
                "all_verifications": (
                    best_verification.get("all_verifications", [])
                    if best_verification is not None
                    else []
                ),
            }
        )

    leaf_count = max(1, len(sorted_node_ids))
    coverage = len(bindings) / leaf_count
    any_verified = any(bool(row.get("verified")) for row in verification_matches)

    uncertainty_estimates: list[dict[str, Any]] = []
    all_uncertainty_rows = [
        row
        for binding in bound_atoms
        for row in state.uncertainty_rows_by_atom_id.get(binding.atom_id, [])
    ]
    if all_uncertainty_rows:
        uncertainty_estimates.append(
            {
                "artifact_id": bundle.artifact["artifact_id"],
                "version_id": bundle.version["version_id"],
                "mode": "propagated",
                "scalar_factor": max(float(row.get("scalar_factor") or 0.0) for row in all_uncertainty_rows),
                "confidence": min(float(row.get("confidence") or 0.0) for row in all_uncertainty_rows),
                "n_trials": sum(int(row.get("n_trials") or 0) for row in all_uncertainty_rows),
                "epsilon": max(float(row.get("epsilon") or 0.0) for row in all_uncertainty_rows),
                "input_regime": "bound_leaf_atoms",
                "notes": (
                    "Derived from bound atoms: "
                    + ", ".join(sorted({binding.fqdn for binding in bound_atoms}))
                ),
            }
        )
    if benchmark_rows:
        benchmark_rows_by_name = {
            str(row.get("benchmark_name", "") or ""): row for row in benchmark_rows
        }
        uncertainty_estimates.append(
            {
                "artifact_id": bundle.artifact["artifact_id"],
                "version_id": bundle.version["version_id"],
                "mode": "empirical",
                "scalar_factor": float(len(benchmark_rows)),
                "confidence": 1.0,
                "n_trials": len(benchmark_rows),
                "epsilon": 0.0,
                "input_regime": "artifact_benchmarks",
                "notes": "Benchmarks available: "
                + ", ".join(sorted(benchmark_rows_by_name)),
            }
        )

    structural_pass = bool(bundle.cdg_nodes and bundle.cdg_edges is not None and bundle.references)
    semantic_pass = (
        coverage == 1.0
        and all(binding.is_publishable for binding in bound_atoms)
        and len(bound_atoms) == leaf_count
        and benchmark_pass
    )
    runtime_status, smoke_pass, smoke_details = _aggregate_audit_status(
        bound_atoms,
        state.audit_rows_by_atom_id,
        "smoke_test",
    )
    regression_status, regression_pass, regression_details = _aggregate_audit_status(
        bound_atoms,
        state.audit_rows_by_atom_id,
        "regression_test",
    )
    fuzz_status, fuzz_pass, fuzz_details = _aggregate_audit_status(
        bound_atoms,
        state.audit_rows_by_atom_id,
        "fuzz_test",
    )
    parity_status, parity_pass, parity_details = _aggregate_audit_status(
        bound_atoms,
        state.audit_rows_by_atom_id,
        "parity_check",
    )
    risk_score = min(100, 20 + (leaf_count - len(bindings)) * 15 + (0 if any_verified else 10))
    risk_tier = "low" if coverage == 1.0 and any_verified else "medium" if coverage > 0 else "high"
    review_status = str(asset.audit.review_status or "draft")
    trust_blockers: list[str] = []
    if review_status == "draft":
        trust_blockers.append("review_status_draft")
    if coverage < 1.0:
        trust_blockers.append("unresolved_leaf_bindings")
    if not any_verified:
        trust_blockers.append("leaf_verification_missing")
    if not smoke_pass:
        trust_blockers.append("smoke_evidence_incomplete")
    if not benchmark_pass:
        trust_blockers.append("benchmark_evidence_missing")

    audit_evidence = [
        {
            "artifact_id": bundle.artifact["artifact_id"],
            "version_id": bundle.version["version_id"],
            "audit_type": "structural_audit",
            "passed": structural_pass,
            "status": "completed",
            "details": {
                "node_count": len(bundle.cdg_nodes),
                "edge_count": len(bundle.cdg_edges),
                "reference_count": len(bundle.references),
                "binding_coverage": coverage,
            },
            "source_kind": "automated",
            "runner_version": "skeleton-sync-v2",
            "source_revision": bundle.version["content_hash"],
            "upstream_version": bundle.version["semver"],
        },
        {
            "artifact_id": bundle.artifact["artifact_id"],
            "version_id": bundle.version["version_id"],
            "audit_type": "semantic_audit",
            "passed": semantic_pass,
            "status": "completed",
            "details": {
                "binding_coverage": coverage,
                "bound_atoms": [binding.fqdn for binding in bound_atoms],
                "unresolved_nodes": unresolved_nodes,
                "benchmark_pass": benchmark_pass,
                "benchmark_count": len(benchmark_rows),
                "benchmark_names": sorted(
                    {
                        str(row.get("benchmark_name", "") or "")
                        for row in benchmark_rows
                        if str(row.get("benchmark_name", "") or "")
                    }
                ),
            },
            "source_kind": "automated",
            "runner_version": "skeleton-sync-v2",
            "source_revision": bundle.version["content_hash"],
            "upstream_version": bundle.version["semver"],
        },
        {
            "artifact_id": bundle.artifact["artifact_id"],
            "version_id": bundle.version["version_id"],
            "audit_type": "risk_assessment",
            "passed": risk_tier == "low",
            "status": "completed",
            "details": {
                "risk_tier": risk_tier,
                "risk_score": risk_score,
                "coverage": coverage,
                "trust_blockers": trust_blockers,
                "uncertainty_notes": list(asset.audit.uncertainty_notes),
            },
            "source_kind": "automated",
            "runner_version": "skeleton-sync-v2",
            "source_revision": bundle.version["content_hash"],
            "upstream_version": bundle.version["semver"],
        },
        {
            "artifact_id": bundle.artifact["artifact_id"],
            "version_id": bundle.version["version_id"],
            "audit_type": "smoke_test",
            "passed": smoke_pass,
            "status": runtime_status,
            "details": smoke_details,
            "source_kind": "automated",
            "runner_version": "skeleton-sync-v2",
            "source_revision": bundle.version["content_hash"],
            "upstream_version": bundle.version["semver"],
        },
    ]
    if regression_status != "skipped":
        audit_evidence.append(
            {
                "artifact_id": bundle.artifact["artifact_id"],
                "version_id": bundle.version["version_id"],
                "audit_type": "regression_test",
                "passed": regression_pass,
                "status": regression_status,
                "details": regression_details,
                "source_kind": "automated",
                "runner_version": "skeleton-sync-v2",
                "source_revision": bundle.version["content_hash"],
                "upstream_version": bundle.version["semver"],
            }
        )
    if fuzz_status != "skipped":
        audit_evidence.append(
            {
                "artifact_id": bundle.artifact["artifact_id"],
                "version_id": bundle.version["version_id"],
                "audit_type": "fuzz_test",
                "passed": fuzz_pass,
                "status": fuzz_status,
                "details": fuzz_details,
                "source_kind": "automated",
                "runner_version": "skeleton-sync-v2",
                "source_revision": bundle.version["content_hash"],
                "upstream_version": bundle.version["semver"],
            }
        )
    if parity_status != "skipped":
        audit_evidence.append(
            {
                "artifact_id": bundle.artifact["artifact_id"],
                "version_id": bundle.version["version_id"],
                "audit_type": "parity_check",
                "passed": parity_pass,
                "status": parity_status,
                "details": parity_details,
                "source_kind": "automated",
                "runner_version": "skeleton-sync-v2",
                "source_revision": bundle.version["content_hash"],
                "upstream_version": bundle.version["semver"],
            }
        )

    updated_artifact = dict(bundle.artifact)
    updated_artifact["verified_leaf_coverage"] = coverage
    updated_artifact["is_publishable"] = (
        review_status in {"approved", "transitional"}
        and coverage == 1.0
        and any_verified
        and benchmark_pass
        and semantic_pass
        and smoke_pass
    )
    updated_artifact["status"] = (
        "approved" if updated_artifact["is_publishable"] else "draft"
    )

    updated_rollup = dict(bundle.audit_rollup)
    updated_rollup.update(
        {
            "structural_status": "pass" if structural_pass else "fail",
            "runtime_status": "pass" if smoke_pass else "not_run" if runtime_status == "skipped" else "fail",
            "semantic_status": "pass" if semantic_pass else "fail",
            "developer_semantics_status": "reviewed" if review_status != "draft" else "draft",
            "risk_tier": risk_tier,
            "risk_score": risk_score,
            "acceptability_score": 90 if updated_artifact["is_publishable"] else 65 if coverage > 0 and benchmark_pass else 35,
            "acceptability_band": (
                "acceptable_with_limits"
                if updated_artifact["is_publishable"]
                else "limited_acceptability"
            ),
            "parity_test_status": "pass" if parity_pass else "not_run" if parity_status == "skipped" else "fail",
            "review_status": review_status,
            "trust_readiness": "ready" if updated_artifact["is_publishable"] else "not_ready",
            "trust_blockers": trust_blockers,
            "overall_verdict": (
                "acceptable_with_limits"
                if updated_artifact["is_publishable"]
                else "limited_acceptability" if coverage > 0 else "unknown"
            ),
            "review_required_actions": sorted(set(updated_rollup.get("review_required_actions", [])) | set(trust_blockers)),
        }
    )

    return replace(
        bundle,
        artifact=updated_artifact,
        audit_rollup=updated_rollup,
        audit_evidence=sorted(audit_evidence, key=lambda row: str(row.get("audit_type", ""))),
        uncertainty_estimates=uncertainty_estimates,
        verification_matches=sorted(verification_matches, key=lambda row: str(row.get("predicate_id", ""))),
        cdg_bindings=sorted(bindings, key=lambda row: (str(row.get("node_id", "")), str(row.get("bound_artifact_fqdn", "")))),
    )


def build_skeleton_artifact_bundle(asset: SkeletonFamilyAsset) -> SkeletonArtifactBundle:
    """Build the deterministic Supabase/Memgraph sync bundle for one asset."""
    fqdn = _artifact_fqdn(asset)
    artifact_id = _artifact_id(asset)
    version_id = _version_id(asset)
    content_hash = _content_hash(asset)
    cdg = build_skeleton_asset_cdg(asset)
    graph = asset.to_skeleton_graph()
    boundary_inputs, boundary_outputs = infer_boundary_ports(
        graph.template_nodes,
        graph.template_edges,
    )
    projection = build_published_cdg_projection(
        artifact={
            "artifact_id": artifact_id,
            "fqdn": fqdn,
            "artifact_kind": "cdg",
            "namespace_root": asset.source_package,
            "namespace_path": asset.family,
        },
        version={
            "version_id": version_id,
            "semver": asset.asset_version,
            "content_hash": content_hash,
        },
        cdg=cdg,
    )
    review_status = str(asset.audit.review_status or "draft")
    is_publishable = False
    technical_description = str(asset.summary or "").strip()
    if asset.required_context_tags:
        technical_description += " [requires-context:" + ",".join(
            sorted(set(asset.required_context_tags))
        ) + "]"
    dejargonized_description = str(
        asset.dejargonized_summary or asset.audit.dejargonized_summary or asset.summary
    ).strip()

    references_registry: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for ordinal, reference in enumerate(asset.audit.references, start=1):
        reference_row = reference.model_dump(mode="json")
        ref_id = _reference_id(asset, reference_row, ordinal)
        ref_type = _reference_type(reference_row)
        title = str(reference_row.get("title", "") or "").strip()
        url = str(reference_row.get("url", "") or "").strip()
        note = str(reference_row.get("note", "") or "").strip()
        citation = str(reference_row.get("citation", "") or "").strip()
        registry_row = {
            "ref_id": ref_id,
            "ref_type": ref_type,
            "title": title,
            "authors": [],
            "year": None,
            "venue": "",
            "doi": None,
            "url": url,
            "bibtex_key": f"{asset.asset_id}_{ordinal}",
            "bibtex_raw": citation,
        }
        references_registry.append(registry_row)
        references.append(
            {
                "artifact_id": artifact_id,
                "ref_id": ref_id,
                "ref_key": registry_row["bibtex_key"],
                "doi": None,
                "title": title,
                "authors": [],
                "year": None,
                "url": url,
                "relevance_note": note or citation,
                "confidence": "medium" if review_status != "draft" else "low",
                "matched_nodes": [],
                "source": "manual",
                "verified": review_status != "draft",
            }
        )

    descriptions = [
        {
            "artifact_id": artifact_id,
            "kind": "technical",
            "content": technical_description,
            "language": "en",
            "generated_by": "local_skeleton_asset",
            "reviewed": review_status != "draft",
            "jargon_score": 0.85,
        },
        {
            "artifact_id": artifact_id,
            "kind": "dejargonized",
            "content": dejargonized_description,
            "language": "en",
            "generated_by": "local_skeleton_asset",
            "reviewed": True,
            "jargon_score": 0.2,
        },
        {
            "artifact_id": artifact_id,
            "kind": "conceptual_summary",
            "content": dejargonized_description,
            "language": "en",
            "generated_by": "local_skeleton_asset",
            "reviewed": True,
            "jargon_score": 0.2,
        },
    ]

    io_specs: list[dict[str, Any]] = []
    for ordinal, port in enumerate(boundary_inputs):
        io_specs.append(
            {
                "artifact_id": artifact_id,
                "version_id": version_id,
                "direction": "input",
                "name": port.name,
                "type_desc": port.type_desc,
                "constraints": port.constraints,
                "required": bool(port.required),
                "default_value_repr": port.default_value_repr,
                "ordinal": ordinal,
            }
        )
    for ordinal, port in enumerate(boundary_outputs):
        io_specs.append(
            {
                "artifact_id": artifact_id,
                "version_id": version_id,
                "direction": "output",
                "name": port.name,
                "type_desc": port.type_desc,
                "constraints": port.constraints,
                "required": bool(port.required),
                "default_value_repr": port.default_value_repr,
                "ordinal": ordinal,
            }
        )

    audit_rollup = {
        "artifact_id": artifact_id,
        "overall_verdict": "acceptable_with_limits"
        if review_status in {"approved", "transitional"}
        else "unknown",
        "structural_status": "documented",
        "runtime_status": "not_run",
        "semantic_status": "skeleton_asset",
        "developer_semantics_status": "reviewed"
        if review_status != "draft"
        else "draft",
        "risk_tier": "medium",
        "risk_score": 25 if review_status != "draft" else 45,
        "risk_dimensions": {},
        "risk_reasons": list(asset.audit.uncertainty_notes),
        "acceptability_score": 70 if review_status != "draft" else 40,
        "acceptability_band": (
            "acceptable_with_limits" if review_status != "draft" else "limited_acceptability"
        ),
        "parity_coverage_level": "not_applicable",
        "parity_test_status": "not_run",
        "parity_fixture_count": 0,
        "parity_case_count": 0,
        "review_status": review_status,
        "review_semantic_verdict": "documented",
        "review_developer_semantics_verdict": "documented",
        "review_limitations": list(asset.audit.provenance_notes),
        "review_required_actions": list(asset.audit.uncertainty_notes),
        "trust_readiness": "ready" if is_publishable else "not_ready",
        "trust_blockers": []
        if is_publishable
        else ["artifact_cdg_bindings pending", "leaf verification coverage pending"],
    }

    artifact = {
        "artifact_id": artifact_id,
        "artifact_kind": "cdg",
        "fqdn": fqdn,
        "owner_id": None,
        "source_repo_id": None,
        "namespace_root": asset.source_package,
        "namespace_path": asset.family,
        "source_package": asset.source_package,
        "source_module_path": asset.source_path,
        "source_symbol": asset.asset_id,
        "status": "approved" if is_publishable else "draft",
        "visibility_tier": "general",
        "description": technical_description,
        "source_kind": "hand_written",
        "stateful_kind": "none",
        "is_stochastic": False,
        "is_ffi": False,
        "is_publishable": is_publishable,
        "topo_hash": projection.topo_hash,
        "top_level_input_arity": len(boundary_inputs),
        "top_level_output_arity": len(boundary_outputs),
        "leaf_count": len(cdg.leaf_nodes()),
        "verified_leaf_coverage": 0.0,
    }
    version = {
        "version_id": version_id,
        "artifact_id": artifact_id,
        "content_hash": content_hash,
        "semver": asset.asset_version,
        "is_latest": True,
        "derives_from": None,
        "s3_key": f"cdg-assets/{content_hash}.json",
        "fingerprint": content_hash,
    }
    cdg_nodes = [
        {
            "version_id": version_id,
            "node_id": str(node.get("node_id", "")),
            "parent_node_id": str(node.get("parent_id", "") or ""),
            "name": str(node.get("name", "") or ""),
            "description": str(node.get("description", "") or ""),
            "concept_type": str(node.get("concept_type", "") or ""),
            "status": str(node.get("status", "") or ""),
            "type_signature": str(node.get("type_signature", "") or ""),
            "matched_primitive": str(node.get("matched_primitive", "") or ""),
        }
        for node in projection.nodes
    ]
    cdg_edges = [
        {
            "version_id": version_id,
            "source_id": str(edge.get("source_id", "")),
            "target_id": str(edge.get("target_id", "")),
            "output_name": str(edge.get("output_name", "")),
            "input_name": str(edge.get("input_name", "")),
        }
        for edge in projection.edges
    ]

    return SkeletonArtifactBundle(
        asset_id=asset.asset_id,
        artifact=artifact,
        version=version,
        descriptions=descriptions,
        io_specs=io_specs,
        references_registry=references_registry,
        references=references,
        audit_rollup=audit_rollup,
        cdg_nodes=cdg_nodes,
        cdg_edges=cdg_edges,
        projection=projection,
        source_asset=asset,
    )


def load_skeleton_artifact_bundles(
    *, workspace_root: Any | None = None
) -> list[SkeletonArtifactBundle]:
    """Build deterministic sync bundles for matcher and explicit provider CDGs."""
    assets = (
        *load_provider_cdg_assets(workspace_root=workspace_root),
        *load_local_skeleton_assets(),
    )
    bundles = [build_skeleton_artifact_bundle(asset) for asset in assets]
    bundles.sort(key=lambda bundle: bundle.artifact["fqdn"])
    return bundles


def sync_bundle_to_supabase(
    supabase: Any,
    bundle: SkeletonArtifactBundle,
) -> None:
    """Apply one skeleton artifact bundle to Supabase deterministically."""
    bundle = enrich_bundle_with_catalog_verification(bundle, supabase=supabase)
    artifact_id = bundle.artifact["artifact_id"]
    version_id = bundle.version["version_id"]
    existing_versions_response = (
        supabase.table("artifact_versions")
        .select("version_id")
        .eq("artifact_id", artifact_id)
        .execute()
    )
    existing_version_ids = sorted(
        {
            str(row.get("version_id", "") or "")
            for row in (existing_versions_response.data or [])
            if str(row.get("version_id", "") or "")
        }
    )

    supabase.table("artifacts").upsert(bundle.artifact).execute()

    for table_name in (
        "artifact_descriptions",
        "artifact_io_specs",
        "artifact_references",
        "artifact_audit_evidence",
        "artifact_uncertainty_estimates",
        "artifact_verification_matches",
    ):
        supabase.table(table_name).delete().eq("artifact_id", artifact_id).execute()
    for table_name in ("artifact_cdg_nodes", "artifact_cdg_edges", "artifact_cdg_bindings"):
        for existing_version_id in existing_version_ids:
            (
                supabase.table(table_name)
                .delete()
                .eq("version_id", existing_version_id)
                .execute()
            )
        if version_id not in existing_version_ids:
            supabase.table(table_name).delete().eq("version_id", version_id).execute()
    supabase.table("artifact_audit_rollups").delete().eq("artifact_id", artifact_id).execute()
    (
        supabase.table("artifact_versions")
        .update({"is_latest": False})
        .eq("artifact_id", artifact_id)
        .execute()
    )
    for stale_version_id in existing_version_ids:
        if stale_version_id == version_id:
            continue
        (
            supabase.table("artifact_versions")
            .delete()
            .eq("version_id", stale_version_id)
            .execute()
        )
    (
        supabase.table("artifact_versions")
        .upsert(bundle.version, on_conflict="artifact_id,semver")
        .execute()
    )

    if bundle.references_registry:
        supabase.table("references_registry").upsert(bundle.references_registry).execute()
    if bundle.descriptions:
        supabase.table("artifact_descriptions").upsert(bundle.descriptions).execute()
    if bundle.io_specs:
        supabase.table("artifact_io_specs").upsert(bundle.io_specs).execute()
    if bundle.references:
        supabase.table("artifact_references").upsert(bundle.references).execute()
    if bundle.audit_rollup:
        supabase.table("artifact_audit_rollups").upsert(bundle.audit_rollup).execute()
    if bundle.audit_evidence:
        supabase.table("artifact_audit_evidence").insert(bundle.audit_evidence).execute()
    if bundle.uncertainty_estimates:
        supabase.table("artifact_uncertainty_estimates").insert(bundle.uncertainty_estimates).execute()
    if bundle.verification_matches:
        supabase.table("artifact_verification_matches").insert(bundle.verification_matches).execute()
    if bundle.cdg_nodes:
        supabase.table("artifact_cdg_nodes").upsert(bundle.cdg_nodes).execute()
    if bundle.cdg_edges:
        supabase.table("artifact_cdg_edges").upsert(bundle.cdg_edges).execute()
    if bundle.cdg_bindings:
        supabase.table("artifact_cdg_bindings").upsert(bundle.cdg_bindings).execute()
    if bundle.binding_evidence:
        supabase.table("artifact_cdg_binding_evidence").insert(
            bundle.binding_evidence
        ).execute()


async def sync_bundles_to_graph_store(
    graph_store: Any,
    bundles: list[SkeletonArtifactBundle],
) -> list[tuple[str, dict[str, int]]]:
    """Apply the published CDG projection for each bundle to Memgraph."""
    await graph_store.ensure_constraints()
    results: list[tuple[str, dict[str, int]]] = []
    for bundle in bundles:
        if bundle.projection is None:
            continue
        counts = await graph_store.upsert_published_cdg(bundle.projection)
        results.append((bundle.asset_id, counts))
    return results
