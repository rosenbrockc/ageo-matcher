"""CLI commands for catalog management."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_MANIFEST_BUCKET = "sciona-platform"

from sciona.api.snapshot import (
    DEFAULT_MANIFEST_TIER,
    DEVELOPER_MANIFEST_TIER,
    MANIFEST_TIERS,
    manifest_artifact_key,
)


def _developer_mode_enabled() -> bool:
    return os.environ.get("SCIONA_DEVELOPER_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _resolve_manifest_url(args: argparse.Namespace) -> str:
    """Resolve the published manifest artifact URL."""
    explicit_url = getattr(args, "manifest_url", None)
    if explicit_url:
        return str(explicit_url).rstrip("/")

    env_url = os.environ.get("SCIONA_MANIFEST_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")

    tier = (
        str(getattr(args, "tier", "") or "").strip()
        or os.environ.get("SCIONA_MANIFEST_TIER", "").strip()
    )
    if not tier and _developer_mode_enabled():
        tier = DEVELOPER_MANIFEST_TIER
    valid_tiers = set(MANIFEST_TIERS)
    valid_tiers.add(DEVELOPER_MANIFEST_TIER)
    if tier not in valid_tiers:
        tier = DEFAULT_MANIFEST_TIER

    bucket = (
        os.environ.get("SCIONA_S3_BUCKET", "").strip()
        or os.environ.get("SCIONA_CATALOG_BUCKET", "").strip()
        or DEFAULT_MANIFEST_BUCKET
    )
    key = os.environ.get("SCIONA_MANIFEST_KEY", manifest_artifact_key(tier)).lstrip("/")
    return f"https://{bucket}.s3.amazonaws.com/{key}"


async def _download_manifest_bytes(manifest_url: str) -> bytes:
    """Fetch the published manifest bytes."""
    import httpx

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(manifest_url)
        response.raise_for_status()
        return response.content


async def _cmd_catalog_sync(args: argparse.Namespace) -> None:
    """Download the published manifest.sqlite artifact."""
    output_path = Path(args.output) if args.output else Path.home() / ".sciona" / "manifest.sqlite"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_url = _resolve_manifest_url(args)

    try:
        payload = await _download_manifest_bytes(manifest_url)
    except ImportError:
        print("Error: httpx is required. Install with: pip install httpx", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: failed to download manifest from {manifest_url}: {exc}", file=sys.stderr)
        sys.exit(1)

    output_path.write_bytes(payload)
    print(f"Manifest written to {output_path}")


async def _cmd_catalog_search(args: argparse.Namespace) -> None:
    from sciona.provider_runtime import RemoteCatalogClient

    rows = await RemoteCatalogClient(args.api_url).search(
        args.query,
        domain_tag=args.domain_tag,
        limit=args.limit,
    )
    print(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))


async def _cmd_catalog_install(args: argparse.Namespace) -> None:
    from sciona.provider_runtime import ProviderInstaller, RemoteCatalogClient

    candidate = await RemoteCatalogClient(args.api_url).find(args.fqdn)
    callable_object = ProviderInstaller().materialize(candidate)
    print(f"Installed {candidate.provider.distribution_name} for {candidate.fqdn}")
    print(f"Resolved {callable_object.__module__}.{callable_object.__name__}")


def _cmd_catalog_publish_providers(args: argparse.Namespace) -> None:
    from sciona.provider_publication import publish_provider_catalog

    result = publish_provider_catalog(
        workspace_root=Path(args.workspace_root) if args.workspace_root else None,
        apply=args.apply,
        ensure_owner=args.ensure_owner,
        database_url=args.database_url,
        allow_duplicate_fqdns=args.allow_duplicate_fqdns,
        include_backfills=not args.skip_backfills,
        include_embeddings=not args.skip_embeddings,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def _cmd_catalog_reconcile_providers(args: argparse.Namespace) -> None:
    from sciona.provider_reconciliation import (
        apply_provider_reconciliation,
        reconcile_provider_catalog,
    )

    workspace_root = Path(args.workspace_root or Path.cwd())
    if args.apply or args.retire_unresolved:
        result = apply_provider_reconciliation(
            workspace_root,
            retire_unresolved=args.retire_unresolved,
        )
        final_report = result["after"]
    else:
        result = reconcile_provider_catalog(workspace_root)
        final_report = result
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.strict and final_report["counts"].get("unresolved", 0):
        raise SystemExit(1)


def _cmd_catalog_validate_provider_release(args: argparse.Namespace) -> None:
    from sciona.provider_release import validate_provider_release

    report = validate_provider_release(args.repo, build=not args.no_build)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    if not report.ok:
        raise SystemExit(1)


def _cmd_catalog_audit_providers(args: argparse.Namespace) -> None:
    from sciona.catalog_audit import audit_catalog

    database_url = (
        args.database_url
        or os.environ.get("SUPABASE_DATABASE_URL", "").strip()
        or "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    )
    report = audit_catalog(database_url)
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.strict and report["totals"]["audit_gap"]:
        raise SystemExit(1)


def _cmd_catalog_ingest_dataset(args: argparse.Namespace) -> None:
    """Validate or publish versioned dataset manifests and compatibility evidence."""
    from sciona.data_catalog import ingest_dataset_manifests

    database_url = (
        args.database_url
        or os.environ.get("SCIONA_DATA_CATALOG_DATABASE_URL", "").strip()
        or os.environ.get("SCIONA_POSTGRES_URI", "").strip()
        or os.environ.get("SUPABASE_DATABASE_URL", "").strip()
        or None
    )
    result = ingest_dataset_manifests(
        args.manifest,
        database_url=database_url,
        apply=args.apply,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
