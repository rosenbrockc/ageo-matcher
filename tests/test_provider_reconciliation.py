import json
from pathlib import Path

from sciona.provider_reconciliation import (
    apply_provider_reconciliation,
    classify_metadata_fqdns,
    reconcile_provider_catalog,
)


def test_reconciliation_classifies_exact_alias_and_unresolved_identities() -> None:
    report = classify_metadata_fqdns(
        [
            ("signal", "audit_manifest", "sciona.atoms.signal.filter.apply"),
            ("signal", "references", "sciona.atoms.signal.filter.atoms.apply"),
            ("physics", "cdg", "sciona.atoms.physics.pipeline.abstract_step"),
        ],
        seeded_fqdns={"sciona.atoms.signal.filter.apply"},
    )

    assert report["counts"] == {
        "canonicalizable": 1,
        "exact": 1,
        "unresolved": 1,
    }
    assert report["by_repo"] == {
        "physics": {"unresolved": 1},
        "signal": {"canonicalizable": 1, "exact": 1},
    }
    assert report["by_source"] == {
        "audit_manifest": {"exact": 1},
        "cdg": {"unresolved": 1},
        "references": {"canonicalizable": 1},
    }
    assert report["resolutions"] == [
        {
            "repo": "signal",
            "source": "references",
            "fqdn": "sciona.atoms.signal.filter.atoms.apply",
            "canonical_fqdn": "sciona.atoms.signal.filter.apply",
            "method": "redundant_atoms_segment",
        }
    ]
    assert report["unresolved"] == [
        {
            "repo": "physics",
            "source": "cdg",
            "fqdn": "sciona.atoms.physics.pipeline.abstract_step",
        }
    ]


def test_reconciliation_prefers_unique_provenance_alias() -> None:
    report = classify_metadata_fqdns(
        [("physics", "audit_manifest", "sciona.atoms.physics.motion.velocitylaw")],
        seeded_fqdns={"sciona.atoms.physics.motion.velocity_law"},
        provenance_aliases={
            (
                "physics",
                "audit_manifest",
                "sciona.atoms.physics.motion.velocitylaw",
            ): (
                "sciona.atoms.physics.motion.velocity_law",
                "module_symbol_provenance",
            )
        },
    )

    assert report["counts"] == {"canonicalizable": 1}
    assert report["resolutions"][0]["canonical_fqdn"] == (
        "sciona.atoms.physics.motion.velocity_law"
    )


def test_apply_reconciliation_rewrites_idempotently_and_retires_orphans(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "sciona-atoms-signal"
    atom_path = repo / "src/sciona/atoms/signal/filter/atoms.py"
    atom_path.parent.mkdir(parents=True)
    atom_path.write_text(
        """
from sciona.ghost.registry import register_atom

def witness_apply_filter(values):
    return values

@register_atom(witness_apply_filter)
def apply_filter(values):
    return values
""".lstrip()
    )
    manifest_path = repo / "data/audit_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "atoms": [
                    {
                        "atom_name": "sciona.atoms.signal.filter.applyfilter",
                        "atom_key": "sciona.atoms.signal.filter.applyfilter",
                        "atom_id": "sciona.atoms.signal.filter.applyfilter",
                        "module_import_path": "sciona.atoms.signal.filter",
                        "wrapper_symbol": "applyfilter",
                    },
                    {
                        "atom_name": "sciona.atoms.signal.removed.legacy",
                        "module_import_path": "sciona.atoms.signal.removed",
                        "wrapper_symbol": "legacy",
                    },
                ]
            }
        )
    )
    references_path = atom_path.with_name("references.json")
    references_path.write_text(
        json.dumps(
            {
                "atoms": {
                    "sciona.atoms.signal.filter.atoms.apply_filter@sciona/atoms/signal/filter/atoms.py:7": {
                        "references": []
                    }
                }
            }
        )
    )

    initial = reconcile_provider_catalog(tmp_path)
    assert initial["counts"] == {"canonicalizable": 2, "unresolved": 1}

    applied = apply_provider_reconciliation(tmp_path)
    assert applied["rewritten"] == 2
    assert applied["after"]["counts"] == {"exact": 2, "unresolved": 1}
    assert apply_provider_reconciliation(tmp_path)["rewritten"] == 0

    retired = apply_provider_reconciliation(tmp_path, retire_unresolved=True)
    assert retired["retired"] == 1
    assert retired["after"]["counts"] == {"exact": 2}
    tombstones = json.loads(
        (repo / "data/retired_catalog_metadata.json").read_text()
    )
    assert tombstones["retired"][0]["fqdn"] == (
        "sciona.atoms.signal.removed.legacy"
    )
