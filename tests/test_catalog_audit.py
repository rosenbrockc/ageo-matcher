from __future__ import annotations

from sciona.catalog_audit import summarize_catalog_audit


def _complete_row(**overrides):
    row = {
        "fqdn": "sciona.atoms.signal.filter",
        "repo_name": "sciona-atoms-signal",
        "is_publishable": True,
        "has_technical_description": True,
        "has_io_specs": True,
        "has_parameters": True,
        "has_dejargonized_description": True,
        "has_references": True,
        "has_audit_rollup": True,
        "review_status": "approved",
        "review_semantic_verdict": "pass",
        "review_developer_semantics_verdict": "pass_with_limits",
        "trust_readiness": "ready_for_publication",
        "overall_verdict": "trusted",
    }
    row.update(overrides)
    return row


def test_catalog_audit_separates_mechanical_publication_from_trust_readiness() -> None:
    report = summarize_catalog_audit(
        [
            _complete_row(),
            _complete_row(
                fqdn="sciona.atoms.signal.unsafe_filter",
                has_references=False,
                review_status="missing",
            ),
        ]
    )

    assert report["totals"] == {
        "atoms": 2,
        "publishable": 2,
        "audit_ready": 1,
        "audit_gap": 1,
        "published_without_audit_ready": 1,
        "published_without_audit_ready_fqdns": [
            "sciona.atoms.signal.unsafe_filter"
        ],
    }
    provider = report["providers"]["sciona-atoms-signal"]
    assert provider["blockers"] == {"references": 1, "trust_review": 1}


def test_catalog_audit_groups_provider_gaps_and_unowned_rows() -> None:
    report = summarize_catalog_audit(
        [
            _complete_row(repo_name="sciona-atoms-physics"),
            _complete_row(
                fqdn="sciona.atoms.robotics.plan",
                repo_name="sciona-atoms-robotics",
                is_publishable=False,
                has_parameters=False,
            ),
            _complete_row(
                fqdn="sciona.atoms.unknown.asset",
                repo_name=None,
                is_publishable=False,
                has_audit_rollup=False,
                review_status=None,
                review_semantic_verdict=None,
                review_developer_semantics_verdict=None,
                trust_readiness=None,
                overall_verdict=None,
            ),
        ]
    )

    assert report["providers"]["sciona-atoms-physics"]["audit_gap"] == 0
    assert report["providers"]["sciona-atoms-robotics"]["blockers"] == {
        "parameters": 1
    }
    assert report["providers"]["unowned"]["blockers"] == {
        "audit_rollup": 1,
        "trust_review": 1,
    }


def test_explicit_zero_parameter_contract_is_complete() -> None:
    row = _complete_row(
        fqdn="sciona.atoms.fintech.maxstep",
        has_parameters=False,
    )

    without_declaration = summarize_catalog_audit([row])
    with_declaration = summarize_catalog_audit(
        [row],
        zero_parameter_fqdns={"sciona.atoms.fintech.maxstep"},
    )

    assert without_declaration["totals"]["audit_ready"] == 0
    assert with_declaration["totals"]["audit_ready"] == 1
    assert with_declaration["blockers"] == {}
