"""Dataset manifest and schema contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sciona.data_catalog import (
    ingest_dataset_manifests,
    validate_dataset_manifest,
)


def _manifest() -> dict:
    return {
        "fqn": "sciona.data.synthetic.signal.v1",
        "version": "1.0.0",
        "name": "Synthetic signal",
        "description": "Synthetic test data.",
        "modality": "time_series",
        "assets": [
            {
                "asset_path": "signal.npy",
                "byte_size": 128,
                "sha256": "a" * 64,
                "format": "npy",
                "storage_uri": "https://data.example.test/signal.npy",
            }
        ],
        "compatibility": [
            {
                "consumer_fqdn": "sciona.atoms.signal.rate",
                "input_port": "signal",
                "kind": "validated",
                "confidence": 0.95,
                "evidence": {"suite": "public-synthetic-v1"},
            }
        ],
    }


def test_manifest_validation_adds_deterministic_content_hash() -> None:
    first = validate_dataset_manifest(_manifest())
    second = validate_dataset_manifest(_manifest())

    assert first["content_hash"] == second["content_hash"]
    assert len(first["content_hash"]) == 64


def test_compatibility_can_change_without_mutating_dataset_version() -> None:
    first = _manifest()
    second = _manifest()
    second["compatibility"].append(
        {
            "consumer_fqdn": "sciona.atoms.signal.another_rate",
            "input_port": "samples",
            "kind": "example",
        }
    )

    assert (
        validate_dataset_manifest(first)["content_hash"]
        == validate_dataset_manifest(second)["content_hash"]
    )


def test_manifest_validation_rejects_unverified_assets() -> None:
    manifest = _manifest()
    del manifest["assets"][0]["sha256"]

    with pytest.raises(ValueError, match="sha256"):
        validate_dataset_manifest(manifest)


def test_ingestion_defaults_to_dry_run_without_connecting(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")

    result = ingest_dataset_manifests([path], apply=False)

    assert result == [
        {
            "fqn": "sciona.data.synthetic.signal.v1",
            "version": "1.0.0",
            "content_hash": validate_dataset_manifest(_manifest())["content_hash"],
            "asset_count": 1,
            "compatibility_count": 1,
            "status": "dry_run",
        }
    ]


def test_data_artifact_migration_is_mirrored_and_publicly_readable() -> None:
    root = Path(__file__).resolve().parents[1]
    relative = Path("supabase/migrations/20260808000000_data_artifact_catalog.sql")
    matcher_sql = (root / relative).read_text(encoding="utf-8")
    infra_sql = (root.parent / "sciona-infra" / relative).read_text(encoding="utf-8")

    assert matcher_sql == infra_sql
    assert "'data_artifact'" in matcher_sql
    assert "CREATE TABLE IF NOT EXISTS public.data_artifact_metadata" in matcher_sql
    assert "CREATE TABLE IF NOT EXISTS public.artifact_data_compatibility" in matcher_sql
    assert "CREATE OR REPLACE FUNCTION public.catalog_data_artifacts" in matcher_sql
    assert "TO anon, authenticated" in matcher_sql
