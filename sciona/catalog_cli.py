"""Lightweight catalog commands available from the minimal Sciona install."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from collections.abc import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sciona catalog")
    subparsers = parser.add_subparsers(dest="catalog_command", required=True)

    search = subparsers.add_parser(
        "search", help="Search the Postgres-backed atom catalog"
    )
    search.add_argument("query")
    search.add_argument("--domain-tag", default=None)
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--api-url", default=None)

    artifact_search = subparsers.add_parser(
        "search-artifacts", help="Search published CDGs and other catalog artifacts"
    )
    artifact_search.add_argument("query")
    artifact_search.add_argument("--domain-tag", default=None)
    artifact_search.add_argument("--limit", type=int, default=20)
    artifact_search.add_argument("--api-url", default=None)

    plan = subparsers.add_parser(
        "plan", help="Select and print the best context-compatible CDG"
    )
    plan.add_argument("query")
    plan.add_argument("--domain-tag", default=None)
    plan.add_argument("--limit", type=int, default=40)
    plan.add_argument("--api-url", default=None)

    install = subparsers.add_parser(
        "install", help="Install the provider for one selected atom"
    )
    install.add_argument("fqdn")
    install.add_argument("--api-url", default=None)

    install_artifact = subparsers.add_parser(
        "install-artifact", help="Install providers bound by one published CDG"
    )
    install_artifact.add_argument("fqdn")
    install_artifact.add_argument("--api-url", default=None)

    build = subparsers.add_parser(
        "build", help="Select, install, and deterministically assemble a CDG"
    )
    build.add_argument("query")
    build.add_argument("--artifact-fqdn", default=None)
    build.add_argument("--domain-tag", default=None)
    build.add_argument("--limit", type=int, default=40)
    build.add_argument("--api-url", default=None)
    build.add_argument("--output", type=Path, default=Path("solution.py"))
    build.add_argument("--function-name", default="solve")
    return parser


def _api_url(value: str | None) -> str:
    return value or os.environ.get("SCIONA_API_URL", "http://127.0.0.1:8000")


async def _search(args: argparse.Namespace) -> None:
    from sciona.provider_runtime import RemoteCatalogClient

    rows = await RemoteCatalogClient(_api_url(args.api_url)).search(
        args.query,
        domain_tag=args.domain_tag,
        limit=args.limit,
    )
    print(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))


async def _install(args: argparse.Namespace) -> None:
    from sciona.provider_runtime import ProviderInstaller, RemoteCatalogClient

    candidate = await RemoteCatalogClient(_api_url(args.api_url)).find(args.fqdn)
    callable_object = ProviderInstaller().materialize(candidate)
    print(f"Installed {candidate.provider.distribution_name} for {candidate.fqdn}")
    print(f"Resolved {callable_object.__module__}.{callable_object.__name__}")


async def _search_artifacts(args: argparse.Namespace) -> None:
    from sciona.provider_runtime import RemoteCatalogClient

    rows = await RemoteCatalogClient(_api_url(args.api_url)).search_artifacts(
        args.query,
        domain_tag=args.domain_tag,
        limit=args.limit,
    )
    print(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))


async def _plan(args: argparse.Namespace) -> None:
    from sciona.provider_runtime import RemoteCatalogClient

    client = RemoteCatalogClient(_api_url(args.api_url))
    selected = await client.select_artifact(
        args.query,
        domain_tag=args.domain_tag,
        limit=args.limit,
    )
    document = await client.artifact_document(selected.fqdn)
    print(
        json.dumps(
            {
                "selected": selected.model_dump(mode="json"),
                "document": document,
            },
            indent=2,
        )
    )


async def _install_artifact(args: argparse.Namespace) -> None:
    from sciona.provider_runtime import ProviderInstaller, RemoteCatalogClient

    client = RemoteCatalogClient(_api_url(args.api_url))
    document = await client.artifact_document(args.fqdn)
    fqdns = sorted(
        {
            str(row.get("bound_artifact_fqdn", ""))
            for row in (document.get("cdg_bindings") or [])
            if str(row.get("bound_artifact_fqdn", ""))
        }
    )
    if not fqdns:
        raise RuntimeError(f"Artifact {args.fqdn!r} has no bound atoms")
    installer = ProviderInstaller()
    for fqdn in fqdns:
        installer.materialize(await client.find(fqdn))
    print(json.dumps({"artifact_fqdn": args.fqdn, "installed_atoms": fqdns}, indent=2))


async def _build(args: argparse.Namespace) -> None:
    from sciona.deterministic_builder import build_catalog_artifact
    from sciona.provider_runtime import RemoteCatalogClient

    client = RemoteCatalogClient(_api_url(args.api_url))
    artifact_fqdn = args.artifact_fqdn
    if not artifact_fqdn:
        artifact_fqdn = (
            await client.select_artifact(
                args.query,
                domain_tag=args.domain_tag,
                limit=args.limit,
            )
        ).fqdn
    result = await build_catalog_artifact(
        client=client,
        artifact_fqdn=artifact_fqdn,
        output_path=args.output,
        function_name=args.function_name,
    )
    print(
        json.dumps(
            {
                "artifact_fqdn": result.artifact_fqdn,
                "output_path": str(result.output_path),
                "function_name": result.function_name,
                "selected_fqdns": list(result.selected_fqdns),
            },
            indent=2,
        )
    )


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.catalog_command == "search":
        asyncio.run(_search(args))
    elif args.catalog_command == "search-artifacts":
        asyncio.run(_search_artifacts(args))
    elif args.catalog_command == "plan":
        asyncio.run(_plan(args))
    elif args.catalog_command == "install":
        asyncio.run(_install(args))
    elif args.catalog_command == "install-artifact":
        asyncio.run(_install_artifact(args))
    else:
        asyncio.run(_build(args))


if __name__ == "__main__":
    main()
