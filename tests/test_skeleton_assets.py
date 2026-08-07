"""Tests for local skeleton-family assets and compatibility resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sciona.asset_atom_registry import clear_registered_atom_identifier_cache
from sciona.asset_migration import MigrationReadinessAsset
from sciona.architect.models import ConceptType
from sciona.architect.skeleton_assets import (
    load_local_skeleton_assets,
    load_local_skeleton_graphs,
    skeleton_asset_summary,
)
from sciona.architect.skeletons import get_skeleton


class TestLocalSkeletonAssets:
    def test_loads_local_assets_with_audit_metadata(self):
        assets = load_local_skeleton_assets()
        by_id = {asset.asset_id: asset for asset in assets}

        assert "family.divide_and_conquer.v1" in by_id
        assert "family.sequential_filter.v1" in by_id
        assert "kalman_filter" in by_id
        assert "particle_filter" in by_id
        assert "signal_detect_measure" in by_id
        assert "belief_propagation" in by_id
        assert by_id["family.divide_and_conquer.v1"].audit.review_status == "transitional"
        assert by_id["family.sequential_filter.v1"].family == "sequential_filter"
        assert by_id["kalman_filter"].family == "kalman_filter"
        assert by_id["particle_filter"].family == "particle_filter"
        assert by_id["belief_propagation"].paradigm == ConceptType.MESSAGE_PASSING
        assert by_id["belief_propagation"].canonical_for_paradigm is True
        assert by_id["family.sequential_filter.v1"].variant_hints == [
            "kalman_filter",
            "particle_filter",
        ]
        assert by_id["signal_detect_measure"].audit.rationale
        readiness = by_id["family.divide_and_conquer.v1"].audit.migration_readiness
        assert readiness.status == "in_progress"
        assert readiness.target_repository == "../sciona-atoms"
        assert readiness.required_check_count() == 3
        assert readiness.completed_required_check_count() == 2
        assert readiness.is_ready_for_migration() is False

    def test_skeleton_asset_summary_includes_migration_readiness(self):
        assets = load_local_skeleton_assets()
        asset = next(asset for asset in assets if asset.asset_id == "family.divide_and_conquer.v1")

        summary = skeleton_asset_summary(asset)

        assert summary["migration_readiness_status"] == "in_progress"
        assert summary["migration_readiness_ready"] is False
        assert summary["migration_readiness_required_check_count"] == 3
        assert "cross_family_portability" in summary["migration_readiness_check_ids"]

    def test_migration_readiness_asset_requires_satisfied_checks_when_ready(self):
        readiness = MigrationReadinessAsset.model_validate(
            {
                "status": "ready_for_migration",
                "target_repository": "../sciona-atoms",
                "checklist": [
                    {
                        "check_id": "schema",
                        "description": "Stable schema",
                        "required": True,
                        "satisfied": True,
                    },
                    {
                        "check_id": "docs",
                        "description": "Docs complete",
                        "required": True,
                        "satisfied": True,
                    },
                ],
            }
        )

        assert readiness.is_ready_for_migration() is True

    def test_canonical_family_asset_can_override_paradigm_default(self):
        skeleton = get_skeleton(ConceptType.DIVIDE_AND_CONQUER, variant="merge_sort")

        assert skeleton is not None
        assert skeleton.metadata["asset"]["asset_id"] == "family.divide_and_conquer.v1"
        assert skeleton.metadata["asset"]["asset_version"] == "v1"

    def test_subfamily_asset_requires_explicit_variant(self):
        default_signal = get_skeleton(ConceptType.SIGNAL_FILTER)
        hr_signal = get_skeleton(
            ConceptType.SIGNAL_FILTER,
            variant="bandpass_hr_detection",
        )

        assert default_signal is not None
        assert "asset" not in default_signal.metadata
        assert hr_signal is not None
        assert hr_signal.metadata["asset"]["asset_id"] == "signal_detect_measure"

    def test_sequential_filter_asset_is_discoverable_by_paradigm_and_variant_hint(self):
        by_paradigm, by_name = load_local_skeleton_graphs()

        sequential = by_paradigm[ConceptType.SEQUENTIAL_FILTER]

        assert sequential.metadata["asset"]["asset_id"] == "family.sequential_filter.v1"
        assert by_name["kalman_filter"].metadata["asset"]["asset_id"] == "kalman_filter"
        assert by_name["particle_filter"].metadata["asset"]["asset_id"] == "particle_filter"
        assert by_name["belief_propagation"].metadata["asset"]["asset_id"] == "belief_propagation"

    def test_message_passing_asset_is_first_class_skeleton_lookup(self):
        default_message_passing = get_skeleton(ConceptType.MESSAGE_PASSING)
        belief_hint = get_skeleton(
            ConceptType.MESSAGE_PASSING,
            variant="belief_propagation",
        )

        assert default_message_passing is not None
        assert belief_hint is not None
        assert default_message_passing.metadata["asset"]["asset_id"] == "belief_propagation"
        assert belief_hint.metadata["asset"]["asset_id"] == "belief_propagation"

    def test_sequential_filter_asset_is_first_class_skeleton_lookup(self):
        default_filter = get_skeleton(ConceptType.SEQUENTIAL_FILTER)
        kalman_hint = get_skeleton(ConceptType.SEQUENTIAL_FILTER, variant="kalman_filter")
        particle_hint = get_skeleton(
            ConceptType.SEQUENTIAL_FILTER,
            variant="particle_filter",
        )

        assert default_filter is not None
        assert kalman_hint is not None
        assert particle_hint is not None
        assert default_filter.metadata["asset"]["asset_id"] == "family.sequential_filter.v1"
        assert kalman_hint.metadata["asset"]["asset_id"] == "kalman_filter"
        assert particle_hint.metadata["asset"]["asset_id"] == "particle_filter"

    def test_signal_detect_measure_stages_carry_explicit_matched_primitives(self):
        asset = next(
            asset for asset in load_local_skeleton_assets() if asset.asset_id == "signal_detect_measure"
        )
        by_stage = {stage.stage_id: stage for stage in asset.stages}

        assert by_stage["tpl_filter_signal_for_detection"].matched_primitive == (
            "sciona.atoms.expansion.signal_event_rate.filter_signal_for_detection"
        )
        assert by_stage["tpl_detect_peaks_in_signal"].matched_primitive == (
            "sciona.atoms.expansion.signal_event_rate.detect_peaks_in_signal"
        )
        assert by_stage["tpl_compute_event_rate"].matched_primitive == (
            "sciona.atoms.expansion.signal_event_rate.compute_event_rate"
        )

        graph = asset.to_skeleton_graph()
        assert {
            node.node_id: node.matched_primitive for node in graph.template_nodes
        } == {
            "tpl_filter_signal_for_detection": "sciona.atoms.expansion.signal_event_rate.filter_signal_for_detection",
            "tpl_detect_peaks_in_signal": "sciona.atoms.expansion.signal_event_rate.detect_peaks_in_signal",
            "tpl_compute_event_rate": "sciona.atoms.expansion.signal_event_rate.compute_event_rate",
        }


def _write_registered_atom(provider_root: Path, *, module_name: str, atom_name: str) -> None:
    module_path = provider_root / "src" / "sciona" / "atoms" / f"{module_name}.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(
        "\n".join(
            [
                "from sciona.ghost.abstract import AbstractArray",
                "from sciona.ghost.registry import register_atom",
                "",
                f"def witness_{atom_name}(x: AbstractArray) -> AbstractArray:",
                "    return x",
                "",
                f"@register_atom(witness_{atom_name})",
                f"def {atom_name}(x):",
                "    return x",
                "",
            ]
        )
    )


@pytest.fixture
def isolated_skeleton_asset_layout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, Path]:
    local_dir = tmp_path / "local" / "skeletons"
    provider_root = tmp_path / "sciona-atoms-signal"
    monkeypatch.setattr("sciona.architect.skeleton_assets.ASSET_DIR", local_dir)
    monkeypatch.setattr(
        "sciona.asset_atom_registry.candidate_atom_provider_roots",
        lambda: (provider_root,),
    )
    clear_registered_atom_identifier_cache()
    load_local_skeleton_assets.cache_clear()
    load_local_skeleton_graphs.cache_clear()
    try:
        yield {"local_dir": local_dir, "provider_root": provider_root}
    finally:
        clear_registered_atom_identifier_cache()
        load_local_skeleton_assets.cache_clear()
        load_local_skeleton_graphs.cache_clear()


def test_skeleton_asset_rejects_unknown_stage_hint(
    isolated_skeleton_asset_layout: dict[str, Path],
) -> None:
    local_dir = isolated_skeleton_asset_layout["local_dir"]
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "bad.json").write_text(
        json.dumps(
            {
                "asset_id": "bad_fixture",
                "asset_version": "v1",
                "family": "signal_fixture",
                "paradigm": "signal_filter",
                "name": "Bad Fixture",
                "summary": "Fixture skeleton.",
                "dejargonized_summary": "Fixture skeleton.",
                "stages": [
                    {
                        "stage_id": "stage_0",
                        "name": "Stage 0",
                        "description": "Fixture stage.",
                        "dejargonized_description": "Fixture stage.",
                        "concept_type": "signal_filter",
                        "inputs": [],
                        "outputs": [],
                        "matched_primitive": "does_not_exist",
                    }
                ],
                "edges": [],
                "audit": {
                    "review_status": "draft",
                    "dejargonized_summary": "Fixture skeleton.",
                    "references": [{"title": "Fixture Reference"}],
                },
            }
        )
    )

    with pytest.raises(ValueError, match="unknown registered atoms: does_not_exist"):
        load_local_skeleton_assets()


def test_skeleton_asset_accepts_registered_stage_hint(
    isolated_skeleton_asset_layout: dict[str, Path],
) -> None:
    _write_registered_atom(
        isolated_skeleton_asset_layout["provider_root"],
        module_name="fixture_atoms",
        atom_name="normalize_records",
    )
    local_dir = isolated_skeleton_asset_layout["local_dir"]
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "good.json").write_text(
        json.dumps(
            {
                "asset_id": "good_fixture",
                "asset_version": "v1",
                "family": "signal_fixture",
                "paradigm": "signal_filter",
                "name": "Good Fixture",
                "summary": "Fixture skeleton.",
                "dejargonized_summary": "Fixture skeleton.",
                "stages": [
                    {
                        "stage_id": "stage_0",
                        "name": "Stage 0",
                        "description": "Fixture stage.",
                        "dejargonized_description": "Fixture stage.",
                        "concept_type": "signal_filter",
                        "inputs": [],
                        "outputs": [],
                        "matched_primitive": "normalize_records",
                    }
                ],
                "edges": [],
                "audit": {
                    "review_status": "draft",
                    "dejargonized_summary": "Fixture skeleton.",
                    "references": [{"title": "Fixture Reference"}],
                },
            }
        )
    )
    clear_registered_atom_identifier_cache()
    load_local_skeleton_assets.cache_clear()
    load_local_skeleton_graphs.cache_clear()

    assets = load_local_skeleton_assets()

    assert len(assets) == 1
    assert assets[0].stages[0].matched_primitive == "normalize_records"
