"""Side-effect-free reconciliation of provider metadata and seeded atom identity."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Iterable, Mapping


MetadataKey = tuple[str, str, str]


def _canonical_candidates(fqdn: str) -> set[str]:
    parts = fqdn.split(".")
    return {
        ".".join((*parts[:index], *parts[index + 1 :]))
        for index, part in enumerate(parts)
        if part == "atoms" and index > 1
    }


def classify_metadata_fqdns(
    metadata_fqdns: Iterable[tuple[str, str, str]],
    *,
    seeded_fqdns: set[str],
    provenance_aliases: Mapping[MetadataKey, tuple[str, str]] | None = None,
) -> dict[str, object]:
    """Classify ``(repo, source, fqdn)`` rows against the seed inventory."""
    provenance_aliases = provenance_aliases or {}
    counts: Counter[str] = Counter()
    by_repo: dict[str, Counter[str]] = {}
    by_source: dict[str, Counter[str]] = {}
    resolutions: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    for repo, source, fqdn in metadata_fqdns:
        status = "exact"
        canonical_fqdn = fqdn
        method = "exact"
        if fqdn not in seeded_fqdns:
            provenance = provenance_aliases.get((repo, source, fqdn))
            matches = sorted(_canonical_candidates(fqdn) & seeded_fqdns)
            if provenance is not None:
                status = "canonicalizable"
                canonical_fqdn, method = provenance
            elif len(matches) == 1:
                status = "canonicalizable"
                canonical_fqdn = matches[0]
                method = "redundant_atoms_segment"
            else:
                status = "unresolved"
                canonical_fqdn = ""
                method = "unresolved"
        counts[status] += 1
        by_repo.setdefault(repo, Counter())[status] += 1
        by_source.setdefault(source, Counter())[status] += 1
        if status == "canonicalizable":
            resolutions.append(
                {
                    "repo": repo,
                    "source": source,
                    "fqdn": fqdn,
                    "canonical_fqdn": canonical_fqdn,
                    "method": method,
                }
            )
        if status == "unresolved":
            unresolved.append({"repo": repo, "source": source, "fqdn": fqdn})
    return {
        "counts": dict(sorted(counts.items())),
        "by_repo": {
            repo: dict(sorted(repo_counts.items()))
            for repo, repo_counts in sorted(by_repo.items())
        },
        "by_source": {
            source: dict(sorted(source_counts.items()))
            for source, source_counts in sorted(by_source.items())
        },
        "resolutions": sorted(
            resolutions, key=lambda row: (row["repo"], row["source"], row["fqdn"])
        ),
        "unresolved": sorted(
            unresolved, key=lambda row: (row["repo"], row["source"], row["fqdn"])
        ),
    }


def _normalized_module(module: str) -> str:
    module = module.strip().removesuffix(".atoms")
    return ".".join(part.replace("_", "").casefold() for part in module.split("."))


def _normalized_symbol(symbol: str) -> str:
    return symbol.replace("_", "").casefold()


def _module_from_locator(locator: str) -> str:
    path = locator.rsplit(":", 1)[0].strip().replace("\\", "/")
    match = re.search(r"(?:^|/)sciona/atoms/(.+)\.py$", path)
    if match is None:
        return ""
    suffix = match.group(1).replace("/", ".")
    if suffix.endswith(".__init__"):
        suffix = suffix.removesuffix(".__init__")
    return f"sciona.atoms.{suffix}"


def _module_for_references_path(path: Path) -> str:
    parts = path.parent.parts
    for index in range(len(parts) - 1):
        if parts[index : index + 2] == ("sciona", "atoms"):
            suffix = ".".join(parts[index + 2 :])
            return ".".join(part for part in ("sciona.atoms", suffix) if part)
    return ""


def _provider_name(path: Path) -> str:
    for parent in (path.parent, *path.parents):
        if parent.name.startswith("sciona-atoms"):
            return parent.name
    return "unknown"


def _identity_fqdn(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    for field in ("atom_name", "atom_key", "atom_id"):
        value = str(payload.get(field) or "").strip()
        if value.startswith("sciona.atoms."):
            return value
    return ""


def _iter_identity_dicts(payload: object) -> Iterable[dict[str, object]]:
    if isinstance(payload, dict):
        if _identity_fqdn(payload):
            yield payload
        for value in payload.values():
            yield from _iter_identity_dicts(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _iter_identity_dicts(value)


def reconcile_provider_catalog(workspace_root: Path) -> dict[str, object]:
    """Compare all provider audit/reference identities with the seed inventory."""
    try:
        from sciona.atoms.provider_inventory import (
            discover_audit_manifest_paths,
            discover_audit_review_bundle_paths,
            iter_provider_artifact_files,
        )
        from sciona.atoms.supabase_backfill import extract_fqdn
        from sciona.atoms.supabase_seed import derive_seed_inventory
    except ImportError as exc:
        raise RuntimeError(
            "Provider reconciliation requires the sciona-atoms operator package"
        ) from exc

    workspace_root = workspace_root.expanduser().resolve()
    inventory = derive_seed_inventory(base_dir=workspace_root)
    metadata: list[tuple[str, str, str]] = []
    provenance_aliases: dict[MetadataKey, tuple[str, str]] = {}
    identity_index: dict[tuple[str, str, str], set[str]] = {}
    for row in inventory.atom_rows:
        identity_index.setdefault(
            (
                row.repo_name,
                _normalized_module(row.import_module),
                _normalized_symbol(row.source_symbol),
            ),
            set(),
        ).add(row.fqdn)

    def add_provenance_alias(
        *, repo: str, source: str, fqdn: str, modules: Iterable[str], symbol: str
    ) -> None:
        matches: set[str] = set()
        for module in modules:
            if not module:
                continue
            matches.update(
                identity_index.get(
                    (repo, _normalized_module(module), _normalized_symbol(symbol)), set()
                )
            )
        if len(matches) == 1:
            provenance_aliases[(repo, source, fqdn)] = (
                next(iter(matches)),
                "module_symbol_provenance",
            )

    for path in discover_audit_manifest_paths(workspace_root):
        payload = json.loads(path.read_text())
        for entry in payload.get("atoms", []):
            fqdn = str(entry.get("atom_name") or "").strip()
            if fqdn:
                repo = _provider_name(path)
                source = "audit_manifest"
                metadata.append((repo, source, fqdn))
                add_provenance_alias(
                    repo=repo,
                    source=source,
                    fqdn=fqdn,
                    modules=(
                        str(entry.get("module_import_path") or ""),
                        str(entry.get("module") or ""),
                    ),
                    symbol=str(entry.get("wrapper_symbol") or fqdn.rsplit(".", 1)[-1]),
                )
    for path in discover_audit_review_bundle_paths(workspace_root):
        payload = json.loads(path.read_text())
        for entry in _iter_identity_dicts(payload):
            fqdn = _identity_fqdn(entry)
            repo = _provider_name(path)
            source = "review_bundle"
            metadata.append((repo, source, fqdn))
            add_provenance_alias(
                repo=repo,
                source=source,
                fqdn=fqdn,
                modules=(fqdn.rpartition(".")[0],),
                symbol=fqdn.rsplit(".", 1)[-1],
            )
    for path in iter_provider_artifact_files("references.json", base_dir=workspace_root):
        payload = json.loads(path.read_text())
        atoms = payload.get("atoms", {})
        if not isinstance(atoms, dict):
            continue
        for atom_key in atoms:
            fqdn = extract_fqdn(str(atom_key)).strip()
            if fqdn:
                repo = _provider_name(path)
                source = "references"
                metadata.append((repo, source, fqdn))
                locator = str(atom_key).split("@", 1)[1] if "@" in str(atom_key) else ""
                add_provenance_alias(
                    repo=repo,
                    source=source,
                    fqdn=fqdn,
                    modules=(
                        _module_from_locator(locator),
                        _module_for_references_path(path),
                    ),
                    symbol=fqdn.rsplit(".", 1)[-1],
                )

    result = classify_metadata_fqdns(
        metadata,
        seeded_fqdns={row.fqdn for row in inventory.atom_rows},
        provenance_aliases=provenance_aliases,
    )
    inventory_by_fqdn = {row.fqdn: row for row in inventory.atom_rows}
    for resolution in result["resolutions"]:
        row = inventory_by_fqdn[str(resolution["canonical_fqdn"])]
        resolution["canonical_import_module"] = row.import_module
        resolution["canonical_source_symbol"] = row.source_symbol
    return {
        "seeded_atoms": len(inventory.atom_rows),
        "metadata_identifiers": len(metadata),
        **result,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _merge_reference_payloads(left: object, right: object) -> object:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return right
    merged = {**left, **right}
    left_refs = left.get("references")
    right_refs = right.get("references")
    if isinstance(left_refs, list) and isinstance(right_refs, list):
        deduped: dict[str, object] = {}
        for item in (*left_refs, *right_refs):
            deduped[json.dumps(item, sort_keys=True, default=str)] = item
        merged["references"] = [deduped[key] for key in sorted(deduped)]
    return merged


def apply_provider_reconciliation(
    workspace_root: Path,
    *,
    retire_unresolved: bool = False,
) -> dict[str, object]:
    """Rewrite only one-to-one aliases and optionally retire orphaned metadata."""
    from sciona.atoms.provider_inventory import (
        discover_audit_manifest_paths,
        discover_audit_review_bundle_paths,
        iter_provider_artifact_files,
    )
    from sciona.atoms.supabase_backfill import extract_fqdn

    workspace_root = workspace_root.expanduser().resolve()
    before = reconcile_provider_catalog(workspace_root)
    resolutions = {
        (str(row["repo"]), str(row["source"]), str(row["fqdn"])): row
        for row in before["resolutions"]
    }
    unresolved = {
        (str(row["repo"]), str(row["source"]), str(row["fqdn"]))
        for row in before["unresolved"]
    }
    changed_files: list[str] = []
    rewritten = 0
    retired = 0
    retired_by_repo: dict[str, list[dict[str, str]]] = {}

    for path in discover_audit_manifest_paths(workspace_root):
        repo = _provider_name(path)
        payload = json.loads(path.read_text())
        atoms = payload.get("atoms", [])
        if not isinstance(atoms, list):
            continue
        output: list[object] = []
        changed = False
        for entry in atoms:
            if not isinstance(entry, dict):
                output.append(entry)
                continue
            fqdn = str(entry.get("atom_name") or "").strip()
            key = (repo, "audit_manifest", fqdn)
            resolution = resolutions.get(key)
            if resolution is not None:
                canonical = str(resolution["canonical_fqdn"])
                for field in ("atom_name", "atom_key", "atom_id"):
                    if field in entry and str(entry[field]) == fqdn:
                        entry[field] = canonical
                entry["module_import_path"] = str(
                    resolution["canonical_import_module"]
                )
                entry["wrapper_symbol"] = str(resolution["canonical_source_symbol"])
                rewritten += 1
                changed = True
            elif retire_unresolved and key in unresolved:
                retired_by_repo.setdefault(repo, []).append(
                    {
                        "source": "audit_manifest",
                        "fqdn": fqdn,
                        "module_import_path": str(entry.get("module_import_path") or ""),
                        "module_path": str(entry.get("module_path") or ""),
                        "wrapper_symbol": str(entry.get("wrapper_symbol") or ""),
                        "reason": "no_installable_atom_in_provider_inventory",
                    }
                )
                retired += 1
                changed = True
                continue
            output.append(entry)
        if changed:
            payload["atoms"] = output
            _write_json(path, payload)
            changed_files.append(str(path))

    def rewrite_bundle_value(value: object, repo: str) -> tuple[object, bool, int, int]:
        if isinstance(value, list):
            output: list[object] = []
            changed = False
            rewritten_count = 0
            retired_count = 0
            for item in value:
                item_fqdn = _identity_fqdn(item)
                item_key = (repo, "review_bundle", item_fqdn)
                if retire_unresolved and item_fqdn and item_key in unresolved:
                    assert isinstance(item, dict)
                    retired_by_repo.setdefault(repo, []).append(
                        {
                            "source": "review_bundle",
                            "fqdn": item_fqdn,
                            "module_import_path": item_fqdn.rpartition(".")[0],
                            "module_path": "",
                            "wrapper_symbol": item_fqdn.rsplit(".", 1)[-1],
                            "reason": "no_installable_atom_in_provider_inventory",
                        }
                    )
                    changed = True
                    retired_count += 1
                    continue
                rewritten_item, item_changed, item_rewritten, item_retired = (
                    rewrite_bundle_value(item, repo)
                )
                output.append(rewritten_item)
                changed = changed or item_changed
                rewritten_count += item_rewritten
                retired_count += item_retired
            return output, changed, rewritten_count, retired_count
        if not isinstance(value, dict):
            return value, False, 0, 0

        output = dict(value)
        changed = False
        rewritten_count = 0
        retired_count = 0
        fqdn = _identity_fqdn(output)
        resolution = resolutions.get((repo, "review_bundle", fqdn))
        if resolution is not None:
            canonical = str(resolution["canonical_fqdn"])
            for field in ("atom_name", "atom_key", "atom_id"):
                if field in output and str(output[field]) == fqdn:
                    output[field] = canonical
            if "module_import_path" in output:
                output["module_import_path"] = str(
                    resolution["canonical_import_module"]
                )
            if "wrapper_symbol" in output:
                output["wrapper_symbol"] = str(
                    resolution["canonical_source_symbol"]
                )
            changed = True
            rewritten_count += 1
        for field, item in tuple(output.items()):
            rewritten_item, item_changed, item_rewritten, item_retired = (
                rewrite_bundle_value(item, repo)
            )
            output[field] = rewritten_item
            changed = changed or item_changed
            rewritten_count += item_rewritten
            retired_count += item_retired
        return output, changed, rewritten_count, retired_count

    for path in discover_audit_review_bundle_paths(workspace_root):
        repo = _provider_name(path)
        payload = json.loads(path.read_text())
        output, changed, file_rewritten, file_retired = rewrite_bundle_value(
            payload, repo
        )
        if changed:
            _write_json(path, output)
            changed_files.append(str(path))
        rewritten += file_rewritten
        retired += file_retired

    for path in iter_provider_artifact_files("references.json", base_dir=workspace_root):
        repo = _provider_name(path)
        payload = json.loads(path.read_text())
        atoms = payload.get("atoms", {})
        if not isinstance(atoms, dict):
            continue
        output: dict[str, object] = {}
        changed = False
        for atom_key, atom_payload in atoms.items():
            fqdn = extract_fqdn(str(atom_key)).strip()
            key = (repo, "references", fqdn)
            resolution = resolutions.get(key)
            if resolution is not None:
                canonical = str(resolution["canonical_fqdn"])
                suffix = str(atom_key)[len(fqdn) :]
                if suffix.startswith("@"):
                    locator = suffix[1:]
                    line = locator.rsplit(":", 1)[1] if ":" in locator else ""
                    module_path = str(resolution["canonical_import_module"]).replace(
                        ".", "/"
                    )
                    suffix = f"@{module_path}.py" + (f":{line}" if line else "")
                new_key = canonical + suffix
                output[new_key] = _merge_reference_payloads(
                    output.get(new_key, {}), atom_payload
                )
                rewritten += 1
                changed = True
                continue
            if retire_unresolved and key in unresolved:
                retired_by_repo.setdefault(repo, []).append(
                    {
                        "source": "references",
                        "fqdn": fqdn,
                        "module_import_path": "",
                        "module_path": str(atom_key).split("@", 1)[-1],
                        "wrapper_symbol": fqdn.rsplit(".", 1)[-1],
                        "reason": "no_installable_atom_in_provider_inventory",
                    }
                )
                retired += 1
                changed = True
                continue
            output[str(atom_key)] = _merge_reference_payloads(
                output.get(str(atom_key), {}), atom_payload
            )
        if changed:
            payload["atoms"] = output
            _write_json(path, payload)
            changed_files.append(str(path))

    for repo, rows in sorted(retired_by_repo.items()):
        retired_path = workspace_root / repo / "data" / "retired_catalog_metadata.json"
        existing: list[dict[str, str]] = []
        if retired_path.is_file():
            existing_payload = json.loads(retired_path.read_text())
            if isinstance(existing_payload.get("retired"), list):
                existing = list(existing_payload["retired"])
        deduped = {
            (row["source"], row["fqdn"]): row for row in (*existing, *rows)
        }
        _write_json(
            retired_path,
            {
                "schema_version": "1.0",
                "retired": [deduped[key] for key in sorted(deduped)],
            },
        )
        changed_files.append(str(retired_path))

    after = reconcile_provider_catalog(workspace_root)
    return {
        "rewritten": rewritten,
        "retired": retired,
        "changed_files": sorted(set(changed_files)),
        "before": before,
        "after": after,
    }
