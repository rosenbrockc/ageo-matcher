"""Tests for Postgres discovery and verified dataset materialization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from sciona.visualizer.dataset_manager import DatasetManager
from sciona.visualizer.runner import parse_input_value


class FakeCatalog:
    def __init__(self, rows: list[dict] | None = None, *, error: Exception | None = None):
        self.rows = rows or []
        self.error = error
        self.queries: list[tuple[str | None, str | None]] = []

    def list_datasets(self, *, consumer_fqdn=None, input_port=None):
        if self.error:
            raise self.error
        self.queries.append((consumer_fqdn, input_port))
        if consumer_fqdn is None:
            return self.rows
        return [
            row
            for row in self.rows
            if row.get("consumer_fqdn") == consumer_fqdn
            and row.get("input_port", "") in {"", input_port}
        ]

    def get_dataset(self, fqn: str):
        if self.error:
            raise self.error
        return next((row for row in self.rows if row["fqn"] == fqn), None)


def _catalog_row(source: Path, *, sha256: str | None = None) -> dict:
    payload = source.read_bytes()
    return {
        "fqn": "sciona.data.synthetic.signal.v1",
        "name": "Synthetic signal fixture",
        "description": "Committed synthetic unit-test fixture.",
        "shape": [4],
        "dtype": "float64",
        "consumer_fqdn": "sciona.atoms.signal.rate",
        "input_port": "signal",
        "assets": [
            {
                "asset_path": "signal.npy",
                "byte_size": len(payload),
                "sha256": sha256 or hashlib.sha256(payload).hexdigest(),
                "format": "npy",
                "media_type": "application/x-npy",
                "storage_uri": source.as_uri(),
            }
        ],
    }


def test_listing_and_compatibility_are_catalog_backed(tmp_path: Path) -> None:
    source = tmp_path / "signal.npy"
    np.save(source, np.arange(4, dtype=np.float64))
    row = _catalog_row(source)
    catalog = FakeCatalog([row])
    manager = DatasetManager(cache_dir=tmp_path / "cache", catalog=catalog)

    assert manager.list_datasets() == [row]
    assert manager.get_curated_inputs_for_primitive(
        "sciona.atoms.signal.rate", input_port="signal"
    ) == [row["fqn"]]
    assert catalog.queries[-1] == ("sciona.atoms.signal.rate", "signal")


def test_materialization_is_content_addressed_and_verified(tmp_path: Path) -> None:
    source = tmp_path / "signal.npy"
    expected = np.arange(4, dtype=np.float64)
    np.save(source, expected)
    row = _catalog_row(source)
    manager = DatasetManager(
        cache_dir=tmp_path / "cache",
        catalog=FakeCatalog([row]),
    )

    loaded = manager.load_dataset(row["fqn"])
    cached = manager.get_dataset_path(row["fqn"])

    np.testing.assert_array_equal(loaded, expected)
    assert cached.name == f"{row['assets'][0]['sha256']}.npy"
    assert cached.exists()
    assert json.loads(manager.get_manifest_path(row["fqn"]).read_text())["fqn"] == row["fqn"]


def test_checksum_mismatch_is_rejected_without_synthetic_substitution(tmp_path: Path) -> None:
    source = tmp_path / "signal.npy"
    np.save(source, np.arange(4, dtype=np.float64))
    row = _catalog_row(source, sha256="0" * 64)
    manager = DatasetManager(
        cache_dir=tmp_path / "cache",
        catalog=FakeCatalog([row]),
    )

    with pytest.raises(ValueError, match="checksum mismatch"):
        manager.load_dataset(row["fqn"])


def test_corrupt_cached_asset_is_replaced_from_source(tmp_path: Path) -> None:
    source = tmp_path / "signal.npy"
    expected = np.arange(4, dtype=np.float64)
    np.save(source, expected)
    row = _catalog_row(source)
    manager = DatasetManager(cache_dir=tmp_path / "cache", catalog=FakeCatalog([row]))
    cached = manager.materialize_dataset(row["fqn"])
    cached.write_bytes(b"corrupt")

    np.testing.assert_array_equal(manager.load_dataset(row["fqn"]), expected)


def test_cached_manifest_remains_listable_when_postgres_is_offline(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "signal.npy"
    np.save(source, np.arange(4, dtype=np.float64))
    row = _catalog_row(source)
    online = DatasetManager(cache_dir=cache_dir, catalog=FakeCatalog([row]))
    online.materialize_dataset(row["fqn"])

    offline = DatasetManager(
        cache_dir=cache_dir,
        catalog=FakeCatalog(error=ConnectionError("offline")),
    )
    assert offline.list_datasets()[0]["fqn"] == row["fqn"]
    np.testing.assert_array_equal(
        offline.load_dataset(row["fqn"]),
        np.arange(4, dtype=np.float64),
    )


def test_synthetic_fallback_must_be_explicit(tmp_path: Path) -> None:
    unavailable = FakeCatalog(error=ConnectionError("offline"))
    strict = DatasetManager(cache_dir=tmp_path / "strict", catalog=unavailable)
    with pytest.raises(LookupError):
        strict.load_dataset("s3://public-example/signal/ecg.npz")

    development = DatasetManager(
        cache_dir=tmp_path / "development",
        catalog=unavailable,
        allow_synthetic_fallback=True,
    )
    assert development.load_dataset("s3://public-example/signal/ecg.npz").shape == (36000,)


def test_runner_resolves_structured_dataset_reference() -> None:
    expected = np.arange(3)
    with patch(
        "sciona.visualizer.dataset_manager.DatasetManager.load_dataset",
        return_value=expected,
    ) as load:
        result = parse_input_value(
            {"$dataset": "sciona.data.synthetic.signal.v1"},
            "NDArray[np.float64]",
        )

    np.testing.assert_array_equal(result, expected)
    load.assert_called_once_with("sciona.data.synthetic.signal.v1")
