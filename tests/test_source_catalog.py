"""Tests for deriving architect primitives from configured atom sources."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import sciona as matcher_sciona
import pytest

from sciona.architect.catalog import CatalogReport, PrimitiveCatalog
from sciona.architect.models import AlgorithmicPrimitive, ConceptType, IOSpec
from sciona.architect.source_catalog import (
    _alias_candidates,
    audit_source_registration_alignment,
    seed_catalog_from_manifest_sqlite,
    seed_catalog_from_sources,
)
from sciona.sources import AtomSource, SourcesConfig
from sciona.sources import discover_cdgs
from sciona.types import Declaration, Prover


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _create_manifest_db(
    path: Path,
    *,
    atoms: list[dict[str, object]],
    descriptions: list[dict[str, object]] | None = None,
    io_specs: list[dict[str, object]] | None = None,
    include_io_specs: bool = True,
) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE atoms (
            atom_id TEXT PRIMARY KEY,
            fqdn TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'approved',
            domain_tags TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            visibility_tier TEXT NOT NULL DEFAULT 'general',
            source_kind TEXT NOT NULL DEFAULT 'hand_written',
            stateful_kind TEXT NOT NULL DEFAULT 'none',
            is_stochastic INTEGER NOT NULL DEFAULT 0,
            is_ffi INTEGER NOT NULL DEFAULT 0,
            namespace_root TEXT NOT NULL DEFAULT 'sciona.atoms',
            namespace_path TEXT NOT NULL DEFAULT '',
            source_repo_id TEXT NOT NULL DEFAULT '',
            source_package TEXT NOT NULL DEFAULT '',
            source_module_path TEXT NOT NULL DEFAULT '',
            source_symbol TEXT NOT NULL DEFAULT '',
            is_publishable INTEGER NOT NULL DEFAULT 1,
            license_expression TEXT NOT NULL DEFAULT '',
            license_status TEXT NOT NULL DEFAULT 'unknown',
            license_family TEXT NOT NULL DEFAULT 'unknown'
        );
        CREATE TABLE descriptions (
            description_id TEXT PRIMARY KEY,
            atom_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT 'en',
            reviewed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT ''
        );
        """
    )
    if include_io_specs:
        con.executescript(
            """
            CREATE TABLE io_specs (
                atom_id TEXT NOT NULL,
                port_name TEXT NOT NULL,
                direction TEXT NOT NULL,
                ordinal INTEGER NOT NULL DEFAULT 0,
                type_desc TEXT NOT NULL DEFAULT 'Any',
                constraints TEXT NOT NULL DEFAULT '',
                data_kind TEXT NOT NULL DEFAULT '',
                required INTEGER NOT NULL DEFAULT 1,
                default_value_repr TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (atom_id, direction, port_name)
            );
            """
        )

    for atom in atoms:
        payload = {
            "license_expression": "",
            "license_status": "unknown",
            "license_family": "unknown",
            **atom,
        }
        con.execute(
            """
            INSERT INTO atoms (
                atom_id, fqdn, status, domain_tags, description, visibility_tier,
                source_kind, stateful_kind, is_stochastic, is_ffi, namespace_root,
                namespace_path, source_repo_id, source_package, source_module_path,
                source_symbol, is_publishable, license_expression, license_status,
                license_family
            )
            VALUES (
                :atom_id, :fqdn, :status, :domain_tags, :description, :visibility_tier,
                :source_kind, :stateful_kind, :is_stochastic, :is_ffi, :namespace_root,
                :namespace_path, :source_repo_id, :source_package, :source_module_path,
                :source_symbol, :is_publishable, :license_expression, :license_status,
                :license_family
            )
            """,
            payload,
        )

    for desc in descriptions or []:
        con.execute(
            """
            INSERT INTO descriptions (
                description_id, atom_id, kind, content, language, reviewed, updated_at
            )
            VALUES (
                :description_id, :atom_id, :kind, :content, :language, :reviewed, :updated_at
            )
            """,
            desc,
        )

    if include_io_specs:
        for spec in io_specs or []:
            con.execute(
                """
                INSERT INTO io_specs (
                    atom_id, port_name, direction, ordinal, type_desc, constraints,
                    data_kind, required, default_value_repr
                )
                VALUES (
                    :atom_id, :port_name, :direction, :ordinal, :type_desc, :constraints,
                    :data_kind, :required, :default_value_repr
                )
                """,
                spec,
            )

    con.commit()
    con.close()


def _purge_modules(prefixes: tuple[str, ...], baseline: set[str]) -> None:
    for name in set(sys.modules) - baseline:
        if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes):
            sys.modules.pop(name, None)


def test_seed_catalog_from_sources_uses_shared_registry_and_cdg_metadata(tmp_path: Path):
    repo = tmp_path / "repo"

    _write(repo / "sharedpkg" / "__init__.py", "")
    _write(repo / "sharedpkg" / "ghost" / "__init__.py", "")
    _write(
        repo / "sharedpkg" / "ghost" / "registry.py",
        """
from __future__ import annotations

REGISTRY = {}

def register_atom(witness, *, name=None):
    def decorator(func):
        atom_name = name or func.__name__
        REGISTRY[atom_name] = {"impl": func, "witness": witness}
        return func
    return decorator
""".strip()
        + "\n",
    )

    _write(repo / "mypkg" / "__init__.py", "")
    _write(
        repo / "mypkg" / "atoms.py",
        """
from __future__ import annotations

from sharedpkg.ghost.registry import register_atom

def witness_detect_peaks(signal: "AbstractSignal") -> "AbstractPeaks":
    return signal

@register_atom(witness_detect_peaks)
def detect_peaks(signal: "np.ndarray") -> "np.ndarray":
    return signal
""".strip()
        + "\n",
    )

    _write(
        repo / "pipeline_cdg.json",
        """
{
  "nodes": [
    {
      "node_id": "detect_peaks",
      "name": "DetectPeaks",
      "description": "Detect salient peaks from a filtered waveform.",
      "concept_type": "signal_transform",
      "inputs": [{"name": "signal", "type_desc": "np.ndarray", "constraints": "1D waveform"}],
      "outputs": [{"name": "peaks", "type_desc": "np.ndarray", "constraints": "peak indices"}],
      "status": "atomic",
      "type_signature": "(signal: np.ndarray) -> np.ndarray"
    }
  ]
}
""".strip()
        + "\n",
    )

    config = SourcesConfig(
        sources=[AtomSource(name="demo-source", package="mypkg", path="repo")]
    )
    catalog = PrimitiveCatalog()
    report = CatalogReport()

    before_modules = set(sys.modules)
    try:
        added = seed_catalog_from_sources(catalog, config=config, base_dir=tmp_path, report=report)
        assert added == 1

        prim = catalog.get("detect_peaks")
        assert prim is not None
        assert prim.source == "demo-source"
        assert prim.category.value == "signal_transform"
        assert prim.description == "Detect salient peaks from a filtered waveform."
        assert prim.inputs[0].name == "signal"
        assert prim.outputs[0].name == "peaks"
        assert prim.type_signature == "(signal: np.ndarray) -> np.ndarray"

        aliased = catalog.get("DetectPeaks")
        assert aliased is not None
        assert aliased.name == "detect_peaks"
        assert report.source_live_registry_candidates == 1
        assert report.source_cdg_metadata_matches == 1
        assert report.source_breakdown["demo-source"]["live_registry_candidates"] == 1
        assert report.source_breakdown["demo-source"]["added"] == 1
    finally:
        for name in set(sys.modules) - before_modules:
            if name == "mypkg" or name.startswith("mypkg.") or name == "sharedpkg" or name.startswith("sharedpkg."):
                sys.modules.pop(name, None)


def test_seed_catalog_from_namespace_style_sciona_atoms_source(tmp_path: Path):
    repo = tmp_path / "repo"

    _write(
        repo / "src" / "sciona" / "atoms" / "demo" / "ghost" / "registry.py",
        """
from __future__ import annotations

REGISTRY = {}

def register_atom(witness, *, name=None):
    def decorator(func):
        atom_name = name or func.__name__
        REGISTRY[atom_name] = {"impl": func, "witness": witness}
        return func
    return decorator
""".strip()
        + "\n",
    )

    _write(
        repo / "src" / "sciona" / "atoms" / "demo" / "atoms.py",
        """
from __future__ import annotations

from sciona.atoms.demo.ghost.registry import register_atom

def witness_detect_demo(signal: "AbstractSignal") -> "AbstractDemoSignal":
    return signal

@register_atom(witness_detect_demo)
def detect_demo(signal: "np.ndarray") -> "np.ndarray":
    return signal
""".strip()
        + "\n",
    )

    _write(
        repo / "demo_cdg.json",
        """
{
  "nodes": [
    {
      "node_id": "detect_demo",
      "name": "DetectDemo",
      "description": "Detect demo events from a namespace-packaged waveform.",
      "concept_type": "signal_transform",
      "inputs": [{"name": "signal", "type_desc": "np.ndarray"}],
      "outputs": [{"name": "events", "type_desc": "np.ndarray"}],
      "status": "atomic",
      "type_signature": "(signal: np.ndarray) -> np.ndarray"
    }
  ]
}
""".strip()
        + "\n",
    )

    config = SourcesConfig(
        sources=[
            AtomSource(
                name="demo-namespace",
                package="sciona.atoms.demo",
                path="repo",
                python_path="src",
            )
        ]
    )
    catalog = PrimitiveCatalog()
    report = CatalogReport()

    before_modules = set(sys.modules)
    before_sciona_path = list(matcher_sciona.__path__)
    before_sciona_spec_path = list(matcher_sciona.__spec__.submodule_search_locations or [])
    try:
        added = seed_catalog_from_sources(catalog, config=config, base_dir=tmp_path, report=report)
        assert added == 1

        prim = catalog.get("detect_demo")
        assert prim is not None
        assert prim.source == "demo-namespace"
        assert prim.category.value == "signal_transform"
        assert prim.description == "Detect demo events from a namespace-packaged waveform."
        assert prim.inputs[0].name == "signal"
        assert prim.outputs[0].name == "events"
        assert prim.type_signature == "(signal: np.ndarray) -> np.ndarray"

        aliased = catalog.get("DetectDemo")
        assert aliased is not None
        assert aliased.name == "detect_demo"

        assert report.source_live_registry_candidates == 1
        assert report.source_cdg_metadata_matches == 1
        assert report.source_breakdown["demo-namespace"]["live_registry_candidates"] == 1
        assert report.source_breakdown["demo-namespace"]["added"] == 1
    finally:
        _purge_modules(("sciona.atoms",), before_modules)
        matcher_sciona.__path__[:] = before_sciona_path
        matcher_sciona.__spec__.submodule_search_locations[:] = before_sciona_spec_path


def test_seed_catalog_attributes_candidates_before_cross_source_dedup(tmp_path: Path):
    repo = tmp_path / "repo"
    _write(
        repo / "src" / "sciona" / "atoms" / "demo" / "atoms.py",
        """
from sciona.ghost.registry import register_atom

def witness_detect_demo(signal: object) -> object:
    return signal

@register_atom(witness_detect_demo)
def detect_demo(signal: object) -> object:
    return signal
""".strip()
        + "\n",
    )
    config = SourcesConfig(
        sources=[
            AtomSource(
                name="specific-provider",
                package="sciona.atoms.demo",
                path="repo",
                python_path="src",
            ),
            AtomSource(
                name="aggregate-provider",
                package="sciona.atoms",
                path="repo",
                python_path="src",
            ),
        ]
    )
    report = CatalogReport()

    added = seed_catalog_from_sources(
        PrimitiveCatalog(),
        config=config,
        base_dir=tmp_path,
        include_live_registries=False,
        report=report,
    )

    assert added == 1
    assert report.source_breakdown["specific-provider"]["ast_candidates"] == 1
    assert report.source_breakdown["specific-provider"]["added"] == 1
    assert report.source_breakdown["aggregate-provider"]["ast_candidates"] == 1
    assert "added" not in report.source_breakdown["aggregate-provider"]


def test_alias_candidates_include_full_module_path_alias():
    def detect_peaks(signal):
        return signal

    aliases = _alias_candidates(
        "detect_peaks",
        detect_peaks,
        module_name="sciona.atoms.signal_processing.biosppy.ecg",
    )

    assert "sciona.atoms.signal_processing.biosppy.ecg.detect_peaks" in aliases


def test_seed_catalog_from_manifest_sqlite_builds_primitives_and_infers_category(
    tmp_path: Path,
):
    db = tmp_path / "manifest.sqlite"
    _create_manifest_db(
        db,
        atoms=[
            {
                "atom_id": "a1",
                "fqdn": "pkg.manifest.bandpass",
                "status": "approved",
                "domain_tags": "signal_filter",
                "description": "Generic manifest atom.",
                "visibility_tier": "general",
                "source_kind": "reviewed",
                "stateful_kind": "none",
                "is_stochastic": 0,
                "is_ffi": 0,
                "namespace_root": "sciona.atoms",
                "namespace_path": "",
                "source_repo_id": "repo-1",
                "source_package": "pkg.manifest",
                "source_module_path": "atoms",
                "source_symbol": "bandpass",
                "is_publishable": 1,
            }
        ],
        descriptions=[
            {
                "description_id": "d1",
                "atom_id": "a1",
                "kind": "dejargonized",
                "content": "Apply a bandpass filter from manifest metadata.",
                "language": "en",
                "reviewed": 1,
                "updated_at": "2026-04-14T00:00:00Z",
            }
        ],
        io_specs=[
            {
                "atom_id": "a1",
                "port_name": "signal",
                "direction": "input",
                "ordinal": 0,
                "type_desc": "np.ndarray",
                "constraints": "1D waveform",
                "data_kind": "signal",
                "required": 1,
                "default_value_repr": "",
            },
            {
                "atom_id": "a1",
                "port_name": "result",
                "direction": "output",
                "ordinal": 1,
                "type_desc": "np.ndarray",
                "constraints": "filtered waveform",
                "data_kind": "signal",
                "required": 1,
                "default_value_repr": "",
            },
        ],
    )

    catalog = PrimitiveCatalog()
    added = seed_catalog_from_manifest_sqlite(catalog, db)

    assert added == 1
    prim = catalog.get("pkg.manifest.bandpass")
    assert prim is not None
    assert prim.source == "manifest:repo-1"
    assert prim.category == ConceptType.SIGNAL_FILTER
    assert prim.description == "Apply a bandpass filter from manifest metadata."
    assert [port.name for port in prim.inputs] == ["signal"]
    assert [port.name for port in prim.outputs] == ["result"]
    assert prim.inputs[0].constraints == "1D waveform"
    assert prim.inputs[0].data_kind == "signal"
    assert prim.type_signature == "(signal: np.ndarray) -> np.ndarray"


def test_seed_catalog_from_manifest_sqlite_respects_exact_name_precedence(
    tmp_path: Path,
):
    db = tmp_path / "manifest.sqlite"
    _create_manifest_db(
        db,
        atoms=[
            {
                "atom_id": "a1",
                "fqdn": "pkg.manifest.lookup_value",
                "status": "approved",
                "domain_tags": "",
                "description": "Manifest version that should not replace local state.",
                "visibility_tier": "general",
                "source_kind": "reviewed",
                "stateful_kind": "none",
                "is_stochastic": 0,
                "is_ffi": 0,
                "namespace_root": "sciona.atoms",
                "namespace_path": "",
                "source_repo_id": "repo-2",
                "source_package": "pkg.manifest",
                "source_module_path": "atoms",
                "source_symbol": "lookup_value",
                "is_publishable": 1,
            }
        ],
        descriptions=[
            {
                "description_id": "d1",
                "atom_id": "a1",
                "kind": "technical",
                "content": "Manifest description that should be ignored.",
                "language": "en",
                "reviewed": 0,
                "updated_at": "2026-04-14T00:00:00Z",
            }
        ],
        io_specs=[
            {
                "atom_id": "a1",
                "port_name": "value",
                "direction": "input",
                "ordinal": 0,
                "type_desc": "int",
                "constraints": "",
                "data_kind": "",
                "required": 1,
                "default_value_repr": "",
            }
        ],
    )

    catalog = PrimitiveCatalog()
    catalog.add(
        AlgorithmicPrimitive(
            name="pkg.manifest.lookup_value",
            source="local",
            category=ConceptType.SEARCHING,
            description="Local primitive that must win.",
            inputs=[IOSpec(name="value", type_desc="int")],
            outputs=[IOSpec(name="result", type_desc="int")],
        )
    )

    added = seed_catalog_from_manifest_sqlite(catalog, db)

    assert added == 0
    prim = catalog.get("pkg.manifest.lookup_value")
    assert prim is not None
    assert prim.source == "local"
    assert prim.description == "Local primitive that must win."


def test_seed_catalog_from_manifest_sqlite_handles_partial_manifests(
    tmp_path: Path,
):
    db = tmp_path / "manifest.sqlite"
    _create_manifest_db(
        db,
        atoms=[
            {
                "atom_id": "a1",
                "fqdn": "pkg.manifest.helper",
                "status": "approved",
                "domain_tags": "",
                "description": "Fallback atom description.",
                "visibility_tier": "general",
                "source_kind": "reviewed",
                "stateful_kind": "none",
                "is_stochastic": 0,
                "is_ffi": 0,
                "namespace_root": "sciona.atoms",
                "namespace_path": "",
                "source_repo_id": "",
                "source_package": "",
                "source_module_path": "",
                "source_symbol": "",
                "is_publishable": 1,
            }
        ],
        descriptions=[],
        include_io_specs=False,
    )

    catalog = PrimitiveCatalog()
    added = seed_catalog_from_manifest_sqlite(catalog, db)

    assert added == 1
    prim = catalog.get("pkg.manifest.helper")
    assert prim is not None
    assert prim.description == "Fallback atom description."
    assert prim.category == ConceptType.CUSTOM
    assert prim.inputs == []
    assert prim.outputs == []
    assert prim.type_signature == "(void) -> Any"


def test_seed_catalog_from_manifest_sqlite_respects_license_policy_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db = tmp_path / "manifest.sqlite"
    _create_manifest_db(
        db,
        atoms=[
            {
                "atom_id": "a1",
                "fqdn": "pkg.manifest.mit_atom",
                "status": "approved",
                "domain_tags": "",
                "description": "MIT atom",
                "visibility_tier": "general",
                "source_kind": "reviewed",
                "stateful_kind": "none",
                "is_stochastic": 0,
                "is_ffi": 0,
                "namespace_root": "sciona.atoms",
                "namespace_path": "",
                "source_repo_id": "repo-1",
                "source_package": "pkg.manifest",
                "source_module_path": "atoms",
                "source_symbol": "mit_atom",
                "is_publishable": 1,
                "license_expression": "MIT",
                "license_status": "approved",
                "license_family": "permissive",
            },
            {
                "atom_id": "a2",
                "fqdn": "pkg.manifest.unknown_atom",
                "status": "approved",
                "domain_tags": "",
                "description": "Unknown atom",
                "visibility_tier": "general",
                "source_kind": "reviewed",
                "stateful_kind": "none",
                "is_stochastic": 0,
                "is_ffi": 0,
                "namespace_root": "sciona.atoms",
                "namespace_path": "",
                "source_repo_id": "repo-2",
                "source_package": "pkg.manifest",
                "source_module_path": "atoms",
                "source_symbol": "unknown_atom",
                "is_publishable": 1,
                "license_expression": "NOASSERTION",
                "license_status": "unknown",
                "license_family": "unknown",
            },
        ],
        include_io_specs=False,
    )

    monkeypatch.setenv("SCIONA_ALLOWED_LICENSES", "MIT")
    monkeypatch.setenv("SCIONA_ALLOW_UNKNOWN_LICENSES", "0")

    catalog = PrimitiveCatalog()
    added = seed_catalog_from_manifest_sqlite(catalog, db)

    assert added == 1
    assert catalog.get("pkg.manifest.mit_atom") is not None
    assert catalog.get("pkg.manifest.unknown_atom") is None


def test_seed_catalog_from_configured_sciona_atoms_source_discovers_pilot_cdgs() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if not (repo_root.parent / "sciona-atoms").exists():
        pytest.skip("sciona-atoms fixture repo is not present in this workspace")
    source = AtomSource(
        name="sciona-state-estimation-static-kf",
        package="sciona.atoms.state_estimation.kalman_filters.static_kf",
        path="../sciona-atoms",
        cdg_glob="src/sciona/atoms/state_estimation/kalman_filters/static_kf/cdg.json",
    )
    cdgs = discover_cdgs(source, base_dir=repo_root)

    assert cdgs
    assert cdgs[0].name == "cdg.json"


def test_seed_catalog_marks_defaulted_parameters_optional(tmp_path: Path):
    repo = tmp_path / "repo"

    _write(repo / "sharedpkg" / "__init__.py", "")
    _write(repo / "sharedpkg" / "ghost" / "__init__.py", "")
    _write(
        repo / "sharedpkg" / "ghost" / "registry.py",
        """
from __future__ import annotations

REGISTRY = {}

def register_atom(witness, *, name=None):
    def decorator(func):
        atom_name = name or func.__name__
        REGISTRY[atom_name] = {"impl": func, "witness": witness}
        return func
    return decorator
""".strip()
        + "\n",
    )

    _write(repo / "mypkg" / "__init__.py", "")
    _write(
        repo / "mypkg" / "atoms.py",
        """
from __future__ import annotations

from sharedpkg.ghost.registry import register_atom

def witness_smooth(signal: "AbstractSignal", window: int = 5, method: str = "median") -> "AbstractSignal":
    return signal

@register_atom(witness_smooth)
def smooth(signal: "np.ndarray", window: int = 5, method: str = "median") -> "np.ndarray":
    return signal
""".strip()
        + "\n",
    )

    config = SourcesConfig(
        sources=[AtomSource(name="demo-source", package="mypkg", path="repo")]
    )
    catalog = PrimitiveCatalog()

    before_modules = set(sys.modules)
    try:
        added = seed_catalog_from_sources(catalog, config=config, base_dir=tmp_path)
        assert added == 1
        prim = catalog.get("smooth")
        assert prim is not None
        assert [port.name for port in prim.inputs] == ["signal", "window", "method"]
        assert prim.inputs[0].required is True
        assert prim.inputs[1].required is False
        assert prim.inputs[1].default_value_repr == "5"
        assert prim.inputs[2].required is False
        assert prim.inputs[2].default_value_repr == "'median'"
    finally:
        for name in set(sys.modules) - before_modules:
            if name == "mypkg" or name.startswith("mypkg.") or name == "sharedpkg" or name.startswith("sharedpkg."):
                sys.modules.pop(name, None)


def test_audit_source_registration_alignment_reports_registry_ast_drift(tmp_path: Path):
    repo = tmp_path / "repo"

    _write(repo / "sharedpkg" / "__init__.py", "")
    _write(repo / "sharedpkg" / "ghost" / "__init__.py", "")
    _write(
        repo / "sharedpkg" / "ghost" / "registry.py",
        """
from __future__ import annotations

REGISTRY = {}

def register_atom(witness, *, name=None):
    def decorator(func):
        atom_name = name or func.__name__
        REGISTRY[atom_name] = {"impl": func, "witness": witness}
        return func
    return decorator
""".strip()
        + "\n",
    )

    _write(repo / "mypkg" / "__init__.py", "")
    _write(
        repo / "mypkg" / "atoms.py",
        """
from __future__ import annotations

from sharedpkg.ghost.registry import register_atom

def witness_common(signal: "AbstractSignal") -> "AbstractSignal":
    return signal

def witness_registry(signal: "AbstractSignal") -> "AbstractSignal":
    return signal

@register_atom(witness_common)
def common_atom(signal: "np.ndarray") -> "np.ndarray":
    return signal

@register_atom(witness_registry)
def registry_only_atom(signal: "np.ndarray") -> "np.ndarray":
    return signal
""".strip()
        + "\n",
    )
    _write(
        repo / "mypkg" / "extra.py",
        """
from __future__ import annotations

from sharedpkg.ghost.registry import REGISTRY

def witness_live_only(signal: "AbstractSignal") -> "AbstractSignal":
    return signal

def live_only(signal: "np.ndarray") -> "np.ndarray":
    return signal

REGISTRY["live_only_atom"] = {"impl": live_only, "witness": witness_live_only}
""".strip()
        + "\n",
    )

    config = SourcesConfig(
        sources=[AtomSource(name="demo-source", package="mypkg", path="repo")]
    )

    before_modules = set(sys.modules)
    try:
        audit = audit_source_registration_alignment(config=config, base_dir=tmp_path)
        assert audit["source_count"] == 1
        assert audit["matched_total"] == 2
        assert audit["registry_only_total"] == 1
        assert audit["ast_only_total"] == 0
        assert audit["drift_sources"] == ["demo-source"]
        assert audit["highest_severity"] == "high"
        assert audit["severity_counts"]["high"] == 1
        row = audit["rows"][0]
        assert row["severity"] == "high"
        assert row["registry_only_examples"] == ["live_only_atom"]
        assert row["ast_only_examples"] == []
    finally:
        for name in set(sys.modules) - before_modules:
            if name == "mypkg" or name.startswith("mypkg.") or name == "sharedpkg" or name.startswith("sharedpkg."):
                sys.modules.pop(name, None)


def test_seed_catalog_from_sources_falls_back_to_ast_when_imports_are_broken(tmp_path: Path):
    repo = tmp_path / "repo"
    _write(repo / "brokenpkg" / "__init__.py", "from brokenpkg import missing_dependency\n")
    _write(
        repo / "brokenpkg" / "atoms.py",
        """
from __future__ import annotations

from sciona.ghost.registry import register_atom

def witness_derive_feature(signal: "AbstractSignal", threshold: float = 0.5) -> "AbstractSignal":
    return signal

@register_atom(witness_derive_feature)
def derive_feature(signal: "np.ndarray", threshold: float = 0.5) -> "np.ndarray":
    \"\"\"Compute a derived feature with an optional threshold.\"\"\"
    return signal
""".strip()
        + "\n",
    )

    config = SourcesConfig(
        sources=[AtomSource(name="broken-source", package="brokenpkg", path="repo")]
    )
    catalog = PrimitiveCatalog()

    before_modules = set(sys.modules)
    try:
        added = seed_catalog_from_sources(catalog, config=config, base_dir=tmp_path)
        assert added == 1
        prim = catalog.get("derive_feature")
        assert prim is not None
        assert prim.description == "Compute a derived feature with an optional threshold."
        assert prim.inputs[0].name == "signal"
        assert prim.inputs[0].required is True
        assert prim.inputs[1].name == "threshold"
        assert prim.inputs[1].required is False
        assert prim.inputs[1].default_value_repr == "0.5"
    finally:
        for name in set(sys.modules) - before_modules:
            if name == "brokenpkg" or name.startswith("brokenpkg."):
                sys.modules.pop(name, None)


def test_seed_catalog_prefers_witness_doc_for_live_registry_metadata(tmp_path: Path):
    repo = tmp_path / "repo"

    _write(repo / "sharedpkg" / "__init__.py", "")
    _write(repo / "sharedpkg" / "ghost" / "__init__.py", "")
    _write(
        repo / "sharedpkg" / "ghost" / "registry.py",
        """
from __future__ import annotations

REGISTRY = {}

def register_atom(witness, *, name=None):
    def decorator(func):
        atom_name = name or func.__name__
        REGISTRY[atom_name] = {"impl": func, "witness": witness}
        return func
    return decorator
""".strip()
        + "\n",
    )

    _write(repo / "richpkg" / "__init__.py", "")
    _write(
        repo / "richpkg" / "atoms.py",
        """
from __future__ import annotations

from sharedpkg.ghost.registry import register_atom

def witness_bandpass(signal: "AbstractSignal", lowcut: float, highcut: float) -> "AbstractSignal":
    \"\"\"Apply a stable bandpass stage to the signal.\"\"\"
    return signal

@register_atom(witness_bandpass)
def bandpass_filter(signal: "np.ndarray", lowcut: float, highcut: float) -> "np.ndarray":
    return signal
""".strip()
        + "\n",
    )

    config = SourcesConfig(
        sources=[AtomSource(name="rich-source", package="richpkg", path="repo")]
    )
    catalog = PrimitiveCatalog()
    report = CatalogReport()

    before_modules = set(sys.modules)
    try:
        added = seed_catalog_from_sources(catalog, config=config, base_dir=tmp_path, report=report)
        assert added == 1
        prim = catalog.get("bandpass_filter")
        assert prim is not None
        assert prim.description == "Apply a stable bandpass stage to the signal."
        assert [port.name for port in prim.inputs] == ["signal", "lowcut", "highcut"]
        assert report.source_live_registry_candidates == 1
        assert report.source_witness_doc_fallbacks == 1
    finally:
        for name in set(sys.modules) - before_modules:
            if name == "richpkg" or name.startswith("richpkg.") or name == "sharedpkg" or name.startswith("sharedpkg."):
                sys.modules.pop(name, None)


def test_seed_catalog_uses_witness_signature_for_ast_fallback_when_wrapper_is_generic(tmp_path: Path):
    repo = tmp_path / "repo"
    _write(repo / "brokenpkg" / "__init__.py", "from brokenpkg import missing_dependency\n")
    _write(
        repo / "brokenpkg" / "atoms.py",
        """
from __future__ import annotations

from sciona.ghost.registry import register_atom

def witness_fft_transform(signal: "AbstractSignal", axis: int = -1) -> "AbstractSpectrum":
    \"\"\"Compute a forward spectral transform on the signal.\"\"\"
    return signal

@register_atom(witness_fft_transform)
def fft_transform(*args, **kwargs) -> object:
    return None
""".strip()
        + "\n",
    )

    config = SourcesConfig(
        sources=[AtomSource(name="broken-source", package="brokenpkg", path="repo")]
    )
    catalog = PrimitiveCatalog()
    report = CatalogReport()

    before_modules = set(sys.modules)
    try:
        added = seed_catalog_from_sources(
            catalog,
            config=config,
            base_dir=tmp_path,
            include_live_registries=False,
            report=report,
        )
        assert added == 1
        prim = catalog.get("fft_transform")
        assert prim is not None
        assert prim.description == "Compute a forward spectral transform on the signal."
        assert [port.name for port in prim.inputs] == ["signal", "axis"]
        assert prim.inputs[1].required is False
        assert prim.inputs[1].default_value_repr == "-1"
        assert prim.outputs[0].type_desc == "AbstractSpectrum"
        assert report.source_ast_candidates == 1
        assert report.source_witness_doc_fallbacks == 1
        assert report.source_witness_signature_fallbacks == 1
    finally:
        for name in set(sys.modules) - before_modules:
            if name == "brokenpkg" or name.startswith("brokenpkg."):
                sys.modules.pop(name, None)


def test_seed_catalog_adds_suffix_aliases_for_dotted_registration_names(tmp_path: Path):
    repo = tmp_path / "repo"
    _write(repo / "brokenpkg" / "__init__.py", "from brokenpkg import missing_dependency\n")
    _write(
        repo / "brokenpkg" / "atoms.py",
        """
from __future__ import annotations

from sciona.ghost.registry import register_atom

def witness_linear_solve(matrix: "AbstractMatrix", rhs: "AbstractVector") -> "AbstractVector":
    return rhs

@register_atom(witness_linear_solve, name="scipy.linalg.solve")
def solve_impl(matrix: object, rhs: object) -> object:
    return rhs
""".strip()
        + "\n",
    )

    config = SourcesConfig(
        sources=[AtomSource(name="broken-source", package="brokenpkg", path="repo")]
    )
    catalog = PrimitiveCatalog()

    before_modules = set(sys.modules)
    try:
        added = seed_catalog_from_sources(
            catalog,
            config=config,
            base_dir=tmp_path,
            include_live_registries=False,
        )
        assert added == 1
        assert catalog.get("scipy.linalg.solve") is not None
        assert catalog.get("solve") is not None
        assert catalog.get("linalg.solve") is not None
        assert catalog.get("linalg solve") is not None
        assert catalog.get("linalg_solve") is not None
        assert catalog.get("witness_linear_solve") is not None
        assert catalog.get("linear_solve") is not None
        assert catalog.get("solve").name == "scipy.linalg.solve"
    finally:
        for name in set(sys.modules) - before_modules:
            if name == "brokenpkg" or name.startswith("brokenpkg."):
                sys.modules.pop(name, None)


def test_seed_catalog_adds_module_derived_aliases_for_simple_atom_names(tmp_path: Path):
    repo = tmp_path / "repo"

    _write(repo / "sharedpkg" / "__init__.py", "")
    _write(repo / "sharedpkg" / "ghost" / "__init__.py", "")
    _write(
        repo / "sharedpkg" / "ghost" / "registry.py",
        """
from __future__ import annotations

REGISTRY = {}

def register_atom(witness, *, name=None):
    def decorator(func):
        atom_name = name or func.__name__
        REGISTRY[atom_name] = {"impl": func, "witness": witness}
        return func
    return decorator
""".strip()
        + "\n",
    )

    _write(repo / "mypkg" / "__init__.py", "")
    _write(repo / "mypkg" / "scipy" / "__init__.py", "")
    _write(
        repo / "mypkg" / "scipy" / "signal.py",
        """
from __future__ import annotations

from sharedpkg.ghost.registry import register_atom

def witness_butter(signal: "AbstractSignal", order: int = 4) -> "AbstractSignal":
    return signal

@register_atom(witness_butter)
def butter(signal: "np.ndarray", order: int = 4) -> "np.ndarray":
    return signal
""".strip()
        + "\n",
    )

    config = SourcesConfig(
        sources=[AtomSource(name="demo-source", package="mypkg", path="repo")]
    )
    catalog = PrimitiveCatalog()

    before_modules = set(sys.modules)
    try:
        added = seed_catalog_from_sources(catalog, config=config, base_dir=tmp_path)
        assert added == 1
        assert catalog.get("butter") is not None
        assert catalog.get("signal.butter") is not None
        assert catalog.get("signal butter") is not None
        assert catalog.get("signal_butter") is not None
        assert catalog.get("scipy.signal.butter") is not None
        assert catalog.get("scipy signal butter") is not None
        assert catalog.get("scipy_signal_butter") is not None
        assert catalog.get("signal.butter").name == "butter"
    finally:
        for name in set(sys.modules) - before_modules:
            if name == "mypkg" or name.startswith("mypkg.") or name == "sharedpkg" or name.startswith("sharedpkg."):
                sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# Mock SkillIndex for dedup integration tests
# ---------------------------------------------------------------------------


class _MockSkillIndex:
    """Returns high similarity for a specific incumbent name."""

    def __init__(self, incumbent_name: str, score: float):
        self._name = incumbent_name
        self._score = score

    def search_by_embedding(self, query_text: str, k: int = 1):
        decl = Declaration(name=self._name, type_signature="", prover=Prover.LEAN4)
        return [(decl, self._score)]

    def _primitive_to_text(self, prim):
        return f"{prim.name}: {prim.description}"

    def add_primitive(self, primitive):
        pass


# ---------------------------------------------------------------------------
# Task 4: dedup integration in seed_catalog_from_sources
# ---------------------------------------------------------------------------


def test_seed_catalog_dedup_merges_similar_primitives(tmp_path: Path):
    """Two sources with semantically identical atoms under different names."""
    repo = tmp_path / "repo"

    # Source A: atom named "detect_peaks"
    _write(repo / "pkga" / "__init__.py", "")
    _write(
        repo / "pkga" / "atoms.py",
        (
            "from __future__ import annotations\n"
            "from sciona.ghost.registry import register_atom\n"
            "@register_atom(None)\n"
            "def detect_peaks(signal: 'np.ndarray') -> 'np.ndarray':\n"
            "    '''Detect salient peaks from a waveform.'''\n"
            "    return signal\n"
        ),
    )

    # Source B: atom named "find_peaks" — semantically same
    _write(repo / "pkgb" / "__init__.py", "")
    _write(
        repo / "pkgb" / "atoms.py",
        (
            "from __future__ import annotations\n"
            "from sciona.ghost.registry import register_atom\n"
            "@register_atom(None)\n"
            "def find_peaks(signal: 'np.ndarray') -> 'np.ndarray':\n"
            "    '''Find peaks.'''\n"
            "    return signal\n"
        ),
    )

    config = SourcesConfig(
        sources=[
            AtomSource(name="source-a", package="pkga", path="repo"),
            AtomSource(name="source-b", package="pkgb", path="repo"),
        ]
    )
    catalog = PrimitiveCatalog()
    report = CatalogReport()

    # Pre-add detect_peaks so the mock index can find it as incumbent
    from sciona.architect.models import AlgorithmicPrimitive, ConceptType, IOSpec

    catalog.add(
        AlgorithmicPrimitive(
            name="detect_peaks",
            source="source-a",
            category=ConceptType.CUSTOM,
            description="Detect salient peaks from a waveform.",
            inputs=[IOSpec(name="signal", type_desc="np.ndarray")],
            outputs=[IOSpec(name="result", type_desc="np.ndarray")],
        )
    )

    # Mock index returns detect_peaks as the incumbent with high similarity
    mock_idx = _MockSkillIndex("detect_peaks", 0.92)

    before_modules = set(sys.modules)
    try:
        added = seed_catalog_from_sources(
            catalog,
            config=config,
            base_dir=tmp_path,
            include_live_registries=False,
            skill_index=mock_idx,
            report=report,
        )
        # find_peaks should be merged into detect_peaks
        assert catalog.get("find_peaks") is not None
        assert catalog.get("find_peaks").name == catalog.get("detect_peaks").name
        assert report.merged >= 1
    finally:
        for name in set(sys.modules) - before_modules:
            for prefix in ("pkga", "pkgb"):
                if name == prefix or name.startswith(prefix + "."):
                    sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# Task 5: structural dedup via topo_hash
# ---------------------------------------------------------------------------


def test_seed_catalog_reports_structural_duplicates(tmp_path: Path):
    """Two sources with CDGs that have identical structure produce structural_skips."""
    repo = tmp_path / "repo"

    # Build two CDGs with identical topology: parent -> child_a, child_b
    cdg_template = {
        "nodes": [
            {
                "node_id": "root",
                "name": "Root",
                "description": "Root node",
                "status": "decomposed",
                "children": ["c1", "c2"],
                "parent_id": None,
            },
            {
                "node_id": "c1",
                "name": "Step1",
                "description": "First step",
                "status": "atomic",
                "parent_id": "root",
                "concept_type": "sorting",
                "inputs": [{"name": "x", "type_desc": "int"}],
                "outputs": [{"name": "y", "type_desc": "int"}],
                "type_signature": "int -> int",
            },
            {
                "node_id": "c2",
                "name": "Step2",
                "description": "Second step",
                "status": "atomic",
                "parent_id": "root",
                "concept_type": "sorting",
                "inputs": [{"name": "y", "type_desc": "int"}],
                "outputs": [{"name": "z", "type_desc": "int"}],
                "type_signature": "int -> int",
            },
        ],
        "edges": [
            {"source_id": "c1", "target_id": "c2"},
        ],
    }

    # Source A
    _write(repo / "pkga" / "__init__.py", "")
    _write(repo / "pkga" / "atoms.py", "")
    src_a_cdg = tmp_path / "repo" / "pkga" / "pipeline_cdg.json"
    _write(src_a_cdg, json.dumps(cdg_template))

    # Source B — identical structure
    _write(repo / "pkgb" / "__init__.py", "")
    _write(repo / "pkgb" / "atoms.py", "")
    src_b_cdg = tmp_path / "repo" / "pkgb" / "pipeline_cdg.json"
    _write(src_b_cdg, json.dumps(cdg_template))

    config = SourcesConfig(
        sources=[
            AtomSource(name="source-a", package="pkga", path="repo/pkga", cdg_glob="*.json"),
            AtomSource(name="source-b", package="pkgb", path="repo/pkgb", cdg_glob="*.json"),
        ]
    )
    catalog = PrimitiveCatalog()
    report = CatalogReport()

    before_modules = set(sys.modules)
    try:
        seed_catalog_from_sources(
            catalog,
            config=config,
            base_dir=tmp_path,
            include_live_registries=False,
            report=report,
        )
        # Both sources have the same topo_hash, so the second should be flagged
        assert report.structural_skips >= 1
    finally:
        for name in set(sys.modules) - before_modules:
            for prefix in ("pkga", "pkgb"):
                if name == prefix or name.startswith(prefix + "."):
                    sys.modules.pop(name, None)
