"""Read-only completeness and trust audit for a seeded provider catalog."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable


TRUST_READY = {
    "catalog_ready",
    "ready",
    "ready_for_manifest_merge",
    "ready_for_publication",
    "reviewed_with_limits",
    "trust_ready",
}
REVIEW_PASS = {"pass", "pass_with_limits"}

_CATALOG_AUDIT_SQL = """
SELECT
    a.fqdn,
    COALESCE(r.repo_name, 'unowned') AS repo_name,
    a.is_publishable,
    EXISTS (
        SELECT 1
        FROM public.atom_descriptions td
        WHERE td.atom_id = a.atom_id
          AND td.kind = 'technical'
          AND td.language = 'en'
          AND NULLIF(BTRIM(td.content), '') IS NOT NULL
    ) AS has_technical_description,
    EXISTS (
        SELECT 1 FROM public.atom_io_specs io WHERE io.atom_id = a.atom_id
    ) AS has_io_specs,
    EXISTS (
        SELECT 1 FROM public.atom_parameters p WHERE p.atom_id = a.atom_id
    ) AS has_parameters,
    EXISTS (
        SELECT 1
        FROM public.atom_descriptions d
        WHERE d.atom_id = a.atom_id
          AND d.kind = 'dejargonized'
          AND d.language = 'en'
          AND d.jargon_score < 0.4
    ) AS has_dejargonized_description,
    EXISTS (
        SELECT 1 FROM public.atom_references ref WHERE ref.atom_id = a.atom_id
    ) AS has_references,
    ar.atom_id IS NOT NULL AS has_audit_rollup,
    ar.review_status,
    ar.review_semantic_verdict,
    ar.review_developer_semantics_verdict,
    ar.trust_readiness,
    ar.overall_verdict
FROM public.atoms a
LEFT JOIN public.atom_source_repositories r
  ON r.source_repo_id = a.source_repo_id
LEFT JOIN public.atom_audit_rollups ar
  ON ar.atom_id = a.atom_id
ORDER BY r.repo_name, a.fqdn
"""

_PILLARS = (
    "technical_description",
    "io_specs",
    "parameters",
    "dejargonized_description",
    "references",
    "audit_rollup",
)


def _row_value(row: Any, name: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return row[index]


def _normalize_row(row: Any) -> dict[str, Any]:
    names = (
        "fqdn",
        "repo_name",
        "is_publishable",
        "has_technical_description",
        "has_io_specs",
        "has_parameters",
        "has_dejargonized_description",
        "has_references",
        "has_audit_rollup",
        "review_status",
        "review_semantic_verdict",
        "review_developer_semantics_verdict",
        "trust_readiness",
        "overall_verdict",
    )
    return {name: _row_value(row, name, index) for index, name in enumerate(names)}


def _missing_pillars(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        pillar
        for pillar in _PILLARS
        if not bool(row[f"has_{pillar}"])
    )


def _trust_ready(row: dict[str, Any]) -> bool:
    return (
        bool(row["has_audit_rollup"])
        and row["review_status"] == "approved"
        and row["review_semantic_verdict"] in REVIEW_PASS
        and row["review_developer_semantics_verdict"] in REVIEW_PASS
        and row["trust_readiness"] in TRUST_READY
        and row["overall_verdict"] not in {"broken", "misleading"}
    )


def summarize_catalog_audit(
    rows: Iterable[Any],
    *,
    zero_parameter_fqdns: Iterable[str] = (),
) -> dict[str, Any]:
    """Summarize provider coverage without changing publication state."""
    normalized = [_normalize_row(row) for row in rows]
    explicit_zero_parameter_contracts = set(zero_parameter_fqdns)
    for row in normalized:
        if row["fqdn"] in explicit_zero_parameter_contracts:
            row["has_parameters"] = True
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in normalized:
        grouped[str(row["repo_name"] or "unowned")].append(row)

    providers: dict[str, dict[str, Any]] = {}
    total_blockers: Counter[str] = Counter()
    for repo_name in sorted(grouped):
        provider_rows = grouped[repo_name]
        blockers: Counter[str] = Counter()
        audit_ready = 0
        published_without_audit_ready = 0
        for row in provider_rows:
            missing = _missing_pillars(row)
            blockers.update(missing)
            ready = not missing and _trust_ready(row)
            if not _trust_ready(row):
                blockers["trust_review"] += 1
            if ready:
                audit_ready += 1
            elif row["is_publishable"]:
                published_without_audit_ready += 1
        total_blockers.update(blockers)
        atom_count = len(provider_rows)
        publishable = sum(bool(row["is_publishable"]) for row in provider_rows)
        providers[repo_name] = {
            "atoms": atom_count,
            "publishable": publishable,
            "audit_ready": audit_ready,
            "audit_gap": atom_count - audit_ready,
            "published_without_audit_ready": published_without_audit_ready,
            "published_without_audit_ready_fqdns": sorted(
                str(row["fqdn"])
                for row in provider_rows
                if row["is_publishable"]
                and (bool(_missing_pillars(row)) or not _trust_ready(row))
            ),
            "audit_gap_sample": sorted(
                str(row["fqdn"])
                for row in provider_rows
                if bool(_missing_pillars(row)) or not _trust_ready(row)
            )[:20],
            "blockers": dict(sorted(blockers.items())),
        }

    atom_count = len(normalized)
    publishable = sum(bool(row["is_publishable"]) for row in normalized)
    audit_ready = sum(
        not _missing_pillars(row) and _trust_ready(row) for row in normalized
    )
    return {
        "totals": {
            "atoms": atom_count,
            "publishable": publishable,
            "audit_ready": audit_ready,
            "audit_gap": atom_count - audit_ready,
            "published_without_audit_ready": sum(
                bool(row["is_publishable"])
                and (bool(_missing_pillars(row)) or not _trust_ready(row))
                for row in normalized
            ),
            "published_without_audit_ready_fqdns": sorted(
                str(row["fqdn"])
                for row in normalized
                if row["is_publishable"]
                and (bool(_missing_pillars(row)) or not _trust_ready(row))
            ),
        },
        "blockers": dict(sorted(total_blockers.items())),
        "providers": providers,
    }


def audit_catalog(database_url: str) -> dict[str, Any]:
    """Read the catalog audit surface directly from Postgres."""
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Catalog audit requires psycopg") from exc

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(_CATALOG_AUDIT_SQL)
            rows = cursor.fetchall()

    try:
        from sciona.atoms.supabase_backfill import load_manifest_argument_names

        argument_names = load_manifest_argument_names()
        explicit_zero_parameter_contracts = {
            fqdn for fqdn, names in argument_names.items() if not names
        }
    except ImportError:
        explicit_zero_parameter_contracts = set()
    return summarize_catalog_audit(
        rows,
        zero_parameter_fqdns=explicit_zero_parameter_contracts,
    )
