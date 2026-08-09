"""Catalog discovery and verified local materialization for data artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import numpy as np

from sciona.data_catalog import build_default_data_catalog

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _default_cache_dir() -> Path:
    configured = os.environ.get("SCIONA_DATASET_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "sciona" / "datasets"


CACHE_DIR = _default_cache_dir()


class DatasetManager:
    """Discovers cataloged datasets and materializes verified assets on demand."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        catalog: Any | None = None,
        allow_synthetic_fallback: bool | None = None,
    ):
        self.cache_dir = Path(cache_dir or _default_cache_dir()).expanduser()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog or build_default_data_catalog()
        self.allow_synthetic_fallback = (
            _env_flag("SCIONA_DATASET_ALLOW_SYNTHETIC_FALLBACK")
            if allow_synthetic_fallback is None
            else allow_synthetic_fallback
        )

    def list_datasets(
        self,
        *,
        consumer_fqdn: str | None = None,
        input_port: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return Postgres catalog entries, with cached manifests as offline fallback."""
        try:
            rows = self.catalog.list_datasets(
                consumer_fqdn=consumer_fqdn,
                input_port=input_port,
            )
            if rows or consumer_fqdn is not None:
                return rows
        except Exception as exc:
            logger.info("Postgres data catalog unavailable: %s", exc)

        if consumer_fqdn is not None:
            return []
        return self._list_cached_manifests()

    def get_curated_inputs_for_primitive(
        self,
        primitive_name: str,
        *,
        input_port: str | None = None,
    ) -> list[str]:
        """Return data-artifact FQNs backed by compatibility records."""
        return [
            str(row["fqn"])
            for row in self.list_datasets(
                consumer_fqdn=primitive_name,
                input_port=input_port,
            )
        ]

    def get_dataset_path(self, fqn: str) -> Path:
        """Return the verified content-addressed path for a catalog dataset."""
        manifest = self.load_manifest(fqn)
        asset = self._primary_asset(manifest)
        return self._asset_cache_path(asset)

    def get_manifest_path(self, fqn: str) -> Path:
        digest = hashlib.sha256(fqn.encode("utf-8")).hexdigest()
        return self.cache_dir / "manifests" / f"{digest}.json"

    def download_dataset(self, fqn: str) -> bool:
        """Materialize a catalog dataset, returning false on retrieval failure."""
        try:
            self.materialize_dataset(fqn)
            return True
        except Exception as exc:
            logger.warning("Dataset materialization failed for %s: %s", fqn, exc)
            return False

    def materialize_dataset(self, fqn: str) -> Path:
        """Download and checksum a dataset's primary asset into the local cache."""
        manifest = self.load_manifest(fqn)
        asset = self._primary_asset(manifest)
        target = self._asset_cache_path(asset)
        if target.exists():
            try:
                self._verify_asset(target, asset)
                return target
            except ValueError:
                logger.warning("Discarding corrupt cached dataset asset: %s", target)
                target.unlink()

        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")
        partial.unlink(missing_ok=True)
        try:
            self._download_uri(str(asset["storage_uri"]), partial)
            self._verify_asset(partial, asset)
            partial.replace(target)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        self._cache_manifest(manifest)
        return target

    def load_dataset(self, fqn: str) -> Any:
        """Load the primary asset without silently substituting unrelated data."""
        try:
            manifest = self.load_manifest(fqn)
            asset = self._primary_asset(manifest)
            path = self.materialize_dataset(fqn)
            return self._load_path(path, str(asset["format"]))
        except Exception:
            if not self.allow_synthetic_fallback:
                raise
            logger.warning("Using explicitly enabled synthetic fallback for %s", fqn)
            return self._generate_mock_dataset(fqn)

    def load_manifest(self, fqn: str) -> dict[str, Any]:
        """Resolve a catalog manifest from Postgres or the offline cache."""
        try:
            manifest = self.catalog.get_dataset(fqn)
            if manifest:
                return dict(manifest)
        except Exception as exc:
            logger.info("Postgres data catalog lookup failed for %s: %s", fqn, exc)

        cached_path = self.get_manifest_path(fqn)
        if cached_path.exists():
            value = json.loads(cached_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        if self.allow_synthetic_fallback and fqn.startswith("s3://"):
            return self._generate_mock_manifest(fqn)
        raise LookupError(f"dataset is not present in the Postgres catalog or local cache: {fqn}")

    def _list_cached_manifests(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        manifests_dir = self.cache_dir / "manifests"
        if not manifests_dir.exists():
            return rows
        for path in sorted(manifests_dir.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict) and value.get("fqn"):
                    rows.append(value)
            except (OSError, json.JSONDecodeError):
                continue
        return rows

    def _cache_manifest(self, manifest: Mapping[str, Any]) -> None:
        path = self.get_manifest_path(str(manifest["fqn"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dict(manifest), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _primary_asset(manifest: Mapping[str, Any]) -> dict[str, Any]:
        assets = manifest.get("assets", [])
        if isinstance(assets, str):
            assets = json.loads(assets)
        if not isinstance(assets, list) or not assets:
            raise ValueError(f"dataset {manifest.get('fqn', '')!r} has no assets")
        asset = assets[0]
        if not isinstance(asset, Mapping):
            raise ValueError("dataset primary asset is malformed")
        required = ("sha256", "byte_size", "format", "storage_uri")
        missing = [field for field in required if asset.get(field) in (None, "")]
        if missing:
            raise ValueError(f"dataset asset is missing required fields: {', '.join(missing)}")
        return dict(asset)

    def _asset_cache_path(self, asset: Mapping[str, Any]) -> Path:
        digest = str(asset["sha256"]).lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("dataset asset has an invalid sha256")
        suffix = Path(str(asset.get("asset_path", ""))).suffix
        if not suffix:
            suffix = f".{asset['format']}"
        return self.cache_dir / "objects" / digest[:2] / f"{digest}{suffix}"

    @staticmethod
    def _verify_asset(path: Path, asset: Mapping[str, Any]) -> None:
        expected_size = int(asset["byte_size"])
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise ValueError(
                f"dataset asset size mismatch: expected {expected_size}, got {actual_size}"
            )
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != str(asset["sha256"]).lower():
            raise ValueError("dataset asset checksum mismatch")

    @staticmethod
    def _download_uri(uri: str, destination: Path) -> None:
        parsed = urlparse(uri)
        if parsed.scheme == "file":
            shutil.copyfile(Path(parsed.path), destination)
            return
        if parsed.scheme in {"http", "https"}:
            import httpx

            with httpx.stream("GET", uri, timeout=60.0, follow_redirects=True) as response:
                response.raise_for_status()
                with destination.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
            return
        if parsed.scheme == "s3":
            try:
                import boto3  # type: ignore[import-untyped]
                from botocore import UNSIGNED  # type: ignore[import-untyped]
                from botocore.config import Config  # type: ignore[import-untyped]
            except ImportError as exc:
                raise RuntimeError("boto3 is required to materialize s3:// assets") from exc
            key = parsed.path.lstrip("/")
            try:
                boto3.client("s3").download_file(parsed.netloc, key, str(destination))
            except Exception as signed_error:
                try:
                    boto3.client("s3", config=Config(signature_version=UNSIGNED)).download_file(
                        parsed.netloc, key, str(destination)
                    )
                except Exception as unsigned_error:
                    raise RuntimeError(
                        f"signed and anonymous S3 downloads failed: {signed_error}; {unsigned_error}"
                    ) from unsigned_error
            return
        raise ValueError(f"unsupported dataset storage URI scheme: {parsed.scheme or '(none)'}")

    @staticmethod
    def _load_path(path: Path, asset_format: str) -> Any:
        if asset_format == "npz":
            with np.load(path, allow_pickle=False) as data:
                keys = list(data.keys())
                if len(keys) == 1:
                    return data[keys[0]]
                return {key: data[key] for key in keys}
        if asset_format == "npy":
            return np.load(path, allow_pickle=False)
        if asset_format == "json":
            return json.loads(path.read_text(encoding="utf-8"))
        if asset_format in {"parquet", "jsonl"}:
            import pandas as pd

            return pd.read_parquet(path) if asset_format == "parquet" else pd.read_json(path, lines=True)
        if asset_format == "txt":
            return path.read_text(encoding="utf-8")
        raise ValueError(f"unsupported executable dataset format: {asset_format}")

    @staticmethod
    def _generate_mock_dataset(fqn: str) -> Any:
        name = fqn.lower()
        if "ecg" in name:
            t = np.linspace(0, 10, 36000)
            signal = 0.5 * np.sin(2 * np.pi * 0.1 * t)
            for peak_t in range(1, 10):
                signal += np.exp(-((t - peak_t) / 0.05) ** 2) * 1.5
            return signal
        if "matrix" in name or "dense" in name:
            return np.zeros((100, 100), dtype=np.float64)
        if "sinusoid" in name:
            t = np.linspace(0, 1, 1000)
            return np.sin(2 * np.pi * 50 * t)
        return np.zeros(100, dtype=np.float64)

    @staticmethod
    def _generate_mock_manifest(fqn: str) -> dict[str, Any]:
        return {
            "fqn": fqn,
            "name": Path(urlparse(fqn).path).name or fqn,
            "description": "Synthetic fallback explicitly enabled for local development.",
            "shape": [],
            "dtype": "float64",
            "assets": [],
            "attribution": {"source": "SCIONA synthetic development fixture"},
        }
