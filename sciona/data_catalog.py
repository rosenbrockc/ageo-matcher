"""Postgres-backed catalog for versioned data artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_LOCAL_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
COMPATIBILITY_KINDS = frozenset({"example", "validated", "benchmark", "incompatible"})
ASSET_FORMATS = frozenset(
    {"safetensors", "onnx", "json", "jsonl", "parquet", "npy", "npz", "txt", "vocab"}
)


def resolve_data_catalog_database_url(explicit: str | None = None) -> str:
    """Resolve the catalog database without requiring a hosted API credential."""
    if explicit:
        return explicit
    for name in (
        "SCIONA_DATA_CATALOG_DATABASE_URL",
        "SCIONA_POSTGRES_URI",
        "SUPABASE_DATABASE_URL",
        "POSTGRES_URI",
    ):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return DEFAULT_LOCAL_DATABASE_URL


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_dataset_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a dataset ingestion manifest."""
    normalized = dict(manifest)
    for field in ("fqn", "version", "description"):
        if not str(normalized.get(field, "")).strip():
            raise ValueError(f"dataset manifest requires non-empty {field!r}")

    assets = normalized.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("dataset manifest requires at least one asset")
    seen_paths: set[str] = set()
    for index, raw_asset in enumerate(assets):
        if not isinstance(raw_asset, Mapping):
            raise ValueError(f"assets[{index}] must be an object")
        asset = dict(raw_asset)
        for field in ("asset_path", "sha256", "format", "storage_uri"):
            if not str(asset.get(field, "")).strip():
                raise ValueError(f"assets[{index}] requires non-empty {field!r}")
        if asset["asset_path"] in seen_paths:
            raise ValueError(f"duplicate asset_path: {asset['asset_path']}")
        seen_paths.add(str(asset["asset_path"]))
        sha256 = str(asset["sha256"]).lower()
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError(f"assets[{index}].sha256 must be 64 lowercase hex characters")
        if str(asset["format"]) not in ASSET_FORMATS:
            raise ValueError(f"unsupported assets[{index}].format: {asset['format']}")
        if int(asset.get("byte_size", -1)) < 0:
            raise ValueError(f"assets[{index}].byte_size must be non-negative")

    compatibility = normalized.get("compatibility", [])
    if not isinstance(compatibility, list):
        raise ValueError("compatibility must be a list")
    for index, raw_link in enumerate(compatibility):
        if not isinstance(raw_link, Mapping):
            raise ValueError(f"compatibility[{index}] must be an object")
        if not str(raw_link.get("consumer_fqdn", "")).strip():
            raise ValueError(f"compatibility[{index}] requires consumer_fqdn")
        kind = str(raw_link.get("kind", "validated"))
        if kind not in COMPATIBILITY_KINDS:
            raise ValueError(f"unsupported compatibility[{index}].kind: {kind}")
        confidence = float(raw_link.get("confidence", 1.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"compatibility[{index}].confidence must be between 0 and 1")

    evaluation = normalized.get("evaluation", {})
    if not isinstance(evaluation, Mapping):
        raise ValueError("evaluation must be an object")

    normalized.setdefault("name", normalized["fqn"])
    normalized.setdefault("modality", "")
    normalized.setdefault("media_type", "application/octet-stream")
    normalized.setdefault("schema", {})
    normalized.setdefault("shape", [])
    normalized.setdefault("dtype", "")
    normalized.setdefault("sampling", {})
    normalized.setdefault("evaluation", {})
    normalized.setdefault("attribution", {})
    normalized.setdefault("license_expression", "")
    normalized.setdefault("source_uri", "")
    normalized.setdefault("intended_use", "")
    normalized.setdefault("limitations", [])
    normalized.setdefault("compatibility", [])
    versioned_content = {
        key: value
        for key, value in normalized.items()
        if key not in {"compatibility", "content_hash"}
    }
    normalized.setdefault("content_hash", _canonical_hash(versioned_content))
    return normalized


class PostgresDataCatalog:
    """Read and ingest data-artifact records through direct Postgres access."""

    def __init__(self, database_url: str | None = None, *, connect_timeout: int = 2):
        self.database_url = resolve_data_catalog_database_url(database_url)
        self.connect_timeout = connect_timeout

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("psycopg is required for the Postgres data catalog") from exc
        return psycopg.connect(
            self.database_url,
            connect_timeout=self.connect_timeout,
            row_factory=dict_row,
        )

    def list_datasets(
        self,
        *,
        consumer_fqdn: str | None = None,
        input_port: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM public.catalog_data_artifacts(%s, %s)",
                (consumer_fqdn, input_port),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_dataset(self, fqn: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM public.catalog_data_artifacts(NULL, NULL)
                WHERE fqn = %s
                LIMIT 1
                """,
                (fqn,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def ingest_manifest(
        self,
        manifest: Mapping[str, Any],
        *,
        apply: bool = False,
    ) -> dict[str, Any]:
        normalized = validate_dataset_manifest(manifest)
        summary = {
            "fqn": normalized["fqn"],
            "version": normalized["version"],
            "content_hash": normalized["content_hash"],
            "asset_count": len(normalized["assets"]),
            "compatibility_count": len(normalized["compatibility"]),
            "status": "dry_run" if not apply else "applied",
        }
        if not apply:
            return summary

        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("psycopg is required for dataset ingestion") from exc

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT artifact_id, artifact_kind FROM public.artifacts WHERE fqdn = %s",
                (normalized["fqn"],),
            )
            existing = cursor.fetchone()
            if existing and existing["artifact_kind"] != "data_artifact":
                raise ValueError(
                    f"catalog FQN {normalized['fqn']!r} is already a {existing['artifact_kind']}"
                )
            if existing:
                artifact_id = existing["artifact_id"]
                cursor.execute(
                    """
                    UPDATE public.artifacts
                    SET description = %s, status = 'approved', visibility_tier = 'general',
                        is_publishable = TRUE, updated_at = now()
                    WHERE artifact_id = %s
                    """,
                    (normalized["description"], artifact_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO public.artifacts (
                        artifact_kind, fqdn, description, status, visibility_tier,
                        source_kind, is_publishable
                    ) VALUES ('data_artifact', %s, %s, 'approved', 'general',
                              'hand_written', TRUE)
                    RETURNING artifact_id
                    """,
                    (normalized["fqn"], normalized["description"]),
                )
                artifact_id = cursor.fetchone()["artifact_id"]

            cursor.execute(
                "UPDATE public.artifact_versions SET is_latest = FALSE WHERE artifact_id = %s",
                (artifact_id,),
            )
            cursor.execute(
                """
                SELECT version_id, content_hash
                FROM public.artifact_versions
                WHERE artifact_id = %s AND semver = %s
                """,
                (artifact_id, normalized["version"]),
            )
            existing_version = cursor.fetchone()
            if existing_version:
                if existing_version["content_hash"] != normalized["content_hash"]:
                    raise ValueError(
                        f"dataset version {normalized['fqn']}@{normalized['version']} "
                        "already exists with different content"
                    )
                version_id = existing_version["version_id"]
                cursor.execute(
                    "UPDATE public.artifact_versions SET is_latest = TRUE WHERE version_id = %s",
                    (version_id,),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO public.artifact_versions (
                        artifact_id, content_hash, semver, is_latest, fingerprint
                    ) VALUES (%s, %s, %s, TRUE, %s)
                    RETURNING version_id
                    """,
                    (
                        artifact_id,
                        normalized["content_hash"],
                        normalized["version"],
                        normalized["content_hash"],
                    ),
                )
                version_id = cursor.fetchone()["version_id"]
            schema_json = dict(normalized["schema"])
            schema_json.setdefault("name", normalized["name"])
            cursor.execute(
                """
                INSERT INTO public.data_artifact_metadata (
                    version_id, modality, media_type, schema_json, shape, dtype,
                    sampling_metadata, evaluation_metadata, attribution,
                    license_expression, source_uri, intended_use, limitations
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (version_id) DO UPDATE SET
                    modality = EXCLUDED.modality,
                    media_type = EXCLUDED.media_type,
                    schema_json = EXCLUDED.schema_json,
                    shape = EXCLUDED.shape,
                    dtype = EXCLUDED.dtype,
                    sampling_metadata = EXCLUDED.sampling_metadata,
                    evaluation_metadata = EXCLUDED.evaluation_metadata,
                    attribution = EXCLUDED.attribution,
                    license_expression = EXCLUDED.license_expression,
                    source_uri = EXCLUDED.source_uri,
                    intended_use = EXCLUDED.intended_use,
                    limitations = EXCLUDED.limitations
                """,
                (
                    version_id,
                    normalized["modality"],
                    normalized["media_type"],
                    Jsonb(schema_json),
                    normalized["shape"],
                    normalized["dtype"],
                    Jsonb(normalized["sampling"]),
                    Jsonb(normalized["evaluation"]),
                    Jsonb(normalized["attribution"]),
                    normalized["license_expression"],
                    normalized["source_uri"],
                    normalized["intended_use"],
                    normalized["limitations"],
                ),
            )
            for asset in normalized["assets"]:
                cursor.execute(
                    """
                    INSERT INTO public.artifact_assets (
                        version_id, asset_path, byte_size, sha256, format, media_type,
                        storage_uri, compression, mmap_safe, loader_name
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (version_id, asset_path) DO UPDATE SET
                        byte_size = EXCLUDED.byte_size,
                        sha256 = EXCLUDED.sha256,
                        format = EXCLUDED.format,
                        media_type = EXCLUDED.media_type,
                        storage_uri = EXCLUDED.storage_uri,
                        compression = EXCLUDED.compression,
                        mmap_safe = EXCLUDED.mmap_safe,
                        loader_name = EXCLUDED.loader_name
                    """,
                    (
                        version_id,
                        asset["asset_path"],
                        int(asset["byte_size"]),
                        asset["sha256"],
                        asset["format"],
                        asset.get("media_type", "application/octet-stream"),
                        asset["storage_uri"],
                        asset.get("compression", ""),
                        bool(asset.get("mmap_safe", False)),
                        asset.get("loader_name", ""),
                    ),
                )

            for link in normalized["compatibility"]:
                cursor.execute(
                    "SELECT artifact_id FROM public.artifacts WHERE fqdn = %s",
                    (link["consumer_fqdn"],),
                )
                consumer_artifact = cursor.fetchone()
                consumer_atom = None
                if not consumer_artifact:
                    cursor.execute(
                        "SELECT atom_id FROM public.atoms WHERE fqdn = %s",
                        (link["consumer_fqdn"],),
                    )
                    consumer_atom = cursor.fetchone()
                if not consumer_artifact and not consumer_atom:
                    raise ValueError(
                        "compatibility consumer is not in the catalog: "
                        f"{link['consumer_fqdn']}"
                    )
                cursor.execute(
                    """
                    INSERT INTO public.artifact_data_compatibility (
                        consumer_fqdn, consumer_artifact_id, consumer_atom_id,
                        input_port, data_version_id,
                        compatibility_kind, evidence_json, confidence, verified_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                              CASE WHEN %s IN ('validated', 'benchmark') THEN now() ELSE NULL END)
                    ON CONFLICT (
                        consumer_fqdn,
                        (COALESCE(consumer_version_id,
                          '00000000-0000-0000-0000-000000000000'::uuid)),
                        (COALESCE(consumer_atom_version_id,
                          '00000000-0000-0000-0000-000000000000'::uuid)),
                        input_port, data_version_id, compatibility_kind
                    ) DO UPDATE SET
                        evidence_json = EXCLUDED.evidence_json,
                        confidence = EXCLUDED.confidence,
                        verified_at = EXCLUDED.verified_at
                    """,
                    (
                        link["consumer_fqdn"],
                        consumer_artifact["artifact_id"] if consumer_artifact else None,
                        consumer_atom["atom_id"] if consumer_atom else None,
                        link.get("input_port", ""),
                        version_id,
                        link.get("kind", "validated"),
                        Jsonb(link.get("evidence", {})),
                        float(link.get("confidence", 1.0)),
                        link.get("kind", "validated"),
                    ),
                )
        return summary


class HttpDataCatalog:
    """Read the public catalog API without an API key."""

    def __init__(self, base_url: str, *, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def list_datasets(
        self,
        *,
        consumer_fqdn: str | None = None,
        input_port: str | None = None,
    ) -> list[dict[str, Any]]:
        import httpx

        params = {
            key: value
            for key, value in {
                "consumer_fqdn": consumer_fqdn,
                "input_port": input_port,
            }.items()
            if value is not None
        }
        response = httpx.get(
            f"{self.base_url}/api/datasets",
            params=params,
            timeout=self.timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, list):
            raise ValueError("data catalog API returned a non-list response")
        return [dict(row) for row in value if isinstance(row, Mapping)]

    def get_dataset(self, fqn: str) -> dict[str, Any] | None:
        import httpx

        response = httpx.get(
            f"{self.base_url}/api/datasets/preview",
            params={"fqn": fqn},
            timeout=self.timeout,
            follow_redirects=True,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        value = response.json()
        return dict(value) if isinstance(value, Mapping) else None


def build_default_data_catalog() -> PostgresDataCatalog | HttpDataCatalog:
    """Prefer a configured keyless HTTP catalog, otherwise use Postgres directly."""
    public_url = os.environ.get("SCIONA_DATA_CATALOG_URL", "").strip()
    if public_url:
        return HttpDataCatalog(public_url)
    return PostgresDataCatalog()


def load_dataset_manifest(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("dataset manifest root must be an object")
    return validate_dataset_manifest(value)


def ingest_dataset_manifests(
    paths: Sequence[str | Path],
    *,
    database_url: str | None = None,
    apply: bool = False,
) -> list[dict[str, Any]]:
    catalog = PostgresDataCatalog(database_url)
    return [catalog.ingest_manifest(load_dataset_manifest(path), apply=apply) for path in paths]
