"""Diagnostic tests for keyword retrieval against historical solution bindings.

For each bound stage in the solution CDGs, constructs an AlgorithmicNode from
the stage description and runs find_matching_primitives() against a catalog
populated from all atom repos. Asserts the expected atom appears in the top-k.

This tests the legacy deterministic keyword path (keyword + category bonus +
arity) without requiring embeddings, FAISS, or torch. It is not the provider
catalog acceptance gate; that path is exercised by run_provider_staging_e2e.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from sciona.architect.catalog import (
    PrimitiveCatalog,
    seed_builtin_primitives,
    seed_solution_retrieval_aliases,
)
from sciona.architect.stage_resolution import NON_ATOM_ACTION_CLASSES
from sciona.architect.models import (
    AlgorithmicNode,
    AlgorithmicPrimitive,
    CommonPattern,
    ConceptType,
    IOSpec,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PERSONAL = Path.home() / "personal"

ATOM_REPOS = [
    PERSONAL / repo
    for repo in [
        "sciona-atoms",
        "sciona-atoms-ml",
        "sciona-atoms-dl",
        "sciona-atoms-cs",
        "sciona-atoms-bio",
        "sciona-atoms-physics",
        "sciona-atoms-signal",
        "sciona-atoms-geo",
    ]
]

SOLUTION_CDG_DIR = PERSONAL / "sciona-atoms" / "data" / "solution_cdgs"

# Stages resolved without atom retrieval — skip them.
SKIP_ACTION_CLASSES = set(NON_ATOM_ACTION_CLASSES)

# Stages where the keyword path is expected to fail at top-5 because
# the atom name and stage description have near-zero lexical overlap.
# These require the embedding path (not tested here).
KNOWN_KEYWORD_MISMATCHES = {
    "dsb2017_1st/noisy_or_pooling",  # "noisy-OR" vs "case_probability_from_nodule_scores"
    # idct is an approved wrapper, but not an atomic node in the separately
    # scoped SciPy remediation CDG scanned by this legacy keyword fixture.
    "alaska2_steganalysis_1st/spatial_decompression",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_concept_type(raw: str) -> ConceptType:
    """Map a raw concept_type string to the enum, falling back to CUSTOM."""
    try:
        return ConceptType(raw)
    except ValueError:
        return ConceptType.CUSTOM


def _fqdn_to_atom_name(fqdn: str) -> str:
    """Extract the atom node_id from a fully qualified name."""
    return fqdn.rsplit(".", 1)[-1]


# ---------------------------------------------------------------------------
# Catalog builder: load all atoms from CDG JSON files across repos
# ---------------------------------------------------------------------------


def _load_all_atom_primitives() -> list[AlgorithmicPrimitive]:
    primitives: list[AlgorithmicPrimitive] = []
    for repo in ATOM_REPOS:
        if not repo.exists():
            continue
        for cdg_path in repo.rglob("cdg.json"):
            # Skip solution CDGs (those are in data/solution_cdgs/)
            if "solution_cdgs" in str(cdg_path):
                continue
            try:
                data = json.loads(cdg_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            for node in data.get("nodes", []):
                if node.get("status") != "atomic":
                    continue
                node_id = node.get("node_id") or node.get("name")
                if not node_id:
                    continue
                primitives.append(
                    AlgorithmicPrimitive(
                        name=node_id,
                        source=str(cdg_path.parent.relative_to(repo)),
                        category=_safe_concept_type(
                            node.get("concept_type", "custom")
                        ),
                        description=node.get("description", ""),
                        inputs=[
                            IOSpec(**inp) for inp in node.get("inputs", [])
                        ],
                        outputs=[
                            IOSpec(**out) for out in node.get("outputs", [])
                        ],
                        type_signature=node.get("type_signature", ""),
                        aliases=node.get("aliases", []),
                        common_patterns=[
                            CommonPattern(**p)
                            for p in node.get("common_patterns", [])
                        ],
                    )
                )
    return primitives


# ---------------------------------------------------------------------------
# Test case builder: load (stage, expected_atom) pairs from solution CDGs
# ---------------------------------------------------------------------------


@dataclass
class RetrievalTestCase:
    solution_id: str
    stage_id: str
    node: AlgorithmicNode
    expected_atom_name: str
    action_class: str


def _load_test_cases() -> list[RetrievalTestCase]:
    cases: list[RetrievalTestCase] = []
    for cdg_path in sorted(SOLUTION_CDG_DIR.glob("*.json")):
        if "_bindings" in cdg_path.name or cdg_path.suffix != ".json":
            continue
        sol_name = cdg_path.stem
        bindings_path = cdg_path.with_name(f"{sol_name}_bindings.json")
        if not bindings_path.exists():
            continue

        cdg = json.loads(cdg_path.read_text())
        bindings = json.loads(bindings_path.read_text())

        binding_map = {b["stage_id"]: b for b in bindings["bindings"]}
        stage_map = {s["stage_id"]: s for s in cdg.get("stages", [])}

        for stage_id, binding in binding_map.items():
            if binding.get("action_class") in SKIP_ACTION_CLASSES:
                continue
            if binding.get("status") not in (None, "active"):
                continue
            fqdns = []
            if binding.get("bound_artifact_fqdn"):
                fqdns.append(binding["bound_artifact_fqdn"])
            elif binding.get("bound_artifact_fqdns"):
                fqdns.extend(binding.get("bound_artifact_fqdns", []) or [])
            if not fqdns:
                continue

            stage = stage_map.get(stage_id)
            if not stage:
                continue

            node = AlgorithmicNode(
                node_id=stage_id,
                name=stage.get("name", stage_id),
                description=stage.get("description", ""),
                concept_type=_safe_concept_type(
                    stage.get("concept_type", "custom")
                ),
                inputs=[IOSpec(**inp) for inp in stage.get("inputs", [])],
                outputs=[IOSpec(**out) for out in stage.get("outputs", [])],
            )

            for fqdn in fqdns:
                cases.append(
                    RetrievalTestCase(
                        solution_id=sol_name,
                        stage_id=stage_id,
                        node=node,
                        expected_atom_name=_fqdn_to_atom_name(str(fqdn)),
                        action_class=binding.get("action_class", "replace_stage"),
                    )
                )
    return cases


# ---------------------------------------------------------------------------
# Fixtures and parametrized data
# ---------------------------------------------------------------------------

ALL_CASES = _load_test_cases()


def _case_id(case: RetrievalTestCase) -> str:
    return f"{case.solution_id}/{case.stage_id}"


@pytest.fixture(scope="module")
def full_catalog() -> PrimitiveCatalog:
    """Catalog populated with all atoms from all repos + builtins."""
    catalog = PrimitiveCatalog()
    seed_builtin_primitives(catalog)
    for prim in _load_all_atom_primitives():
        catalog.add(prim)
    seed_solution_retrieval_aliases(catalog)
    return catalog


# ---------------------------------------------------------------------------
# Core retrieval tests
# ---------------------------------------------------------------------------


class TestRetrievalTop5:
    """Every bound atom must appear in the top-5 keyword+category matches."""

    @pytest.mark.parametrize(
        "case",
        [c for c in ALL_CASES if _case_id(c) not in KNOWN_KEYWORD_MISMATCHES],
        ids=[_case_id(c) for c in ALL_CASES if _case_id(c) not in KNOWN_KEYWORD_MISMATCHES],
    )
    def test_correct_atom_in_top5(self, full_catalog, case):
        matches = full_catalog.find_matching_primitives(case.node, k=5)
        match_names = [m.name for m in matches]
        assert case.expected_atom_name in match_names, (
            f"Expected '{case.expected_atom_name}' in top-5 for "
            f"{case.solution_id}/{case.stage_id}, got: {match_names}"
        )

    @pytest.mark.parametrize(
        "case",
        [c for c in ALL_CASES if _case_id(c) in KNOWN_KEYWORD_MISMATCHES],
        ids=[_case_id(c) for c in ALL_CASES if _case_id(c) in KNOWN_KEYWORD_MISMATCHES],
    )
    def test_known_keyword_mismatch_documented(self, full_catalog, case):
        """Known mismatches: keyword path cannot find these — embedding path needed."""
        matches = full_catalog.find_matching_primitives(case.node, k=10)
        match_names = [m.name for m in matches]
        if case.expected_atom_name in match_names:
            pytest.xfail(
                f"Unexpectedly found '{case.expected_atom_name}' — "
                f"remove from KNOWN_KEYWORD_MISMATCHES"
            )


# ---------------------------------------------------------------------------
# Quality summary tests
# ---------------------------------------------------------------------------


class TestRetrievalQualitySummary:
    """Aggregate recall metrics across all retrievable stages."""

    def test_recall_at_5_excluding_known_mismatches(self, full_catalog):
        testable = [
            c for c in ALL_CASES if _case_id(c) not in KNOWN_KEYWORD_MISMATCHES
        ]
        failures = []
        for case in testable:
            matches = full_catalog.find_matching_primitives(case.node, k=5)
            if case.expected_atom_name not in [m.name for m in matches]:
                failures.append(_case_id(case))
        assert not failures, f"top-5 misses ({len(failures)}): {failures}"

    def test_recall_at_1_above_threshold(self, full_catalog):
        """At least 60% of stages should have the correct atom as top-1."""
        testable = [
            c for c in ALL_CASES if _case_id(c) not in KNOWN_KEYWORD_MISMATCHES
        ]
        top1_hits = sum(
            1
            for case in testable
            if (
                full_catalog.find_matching_primitives(case.node, k=1)[0].name
                == case.expected_atom_name
            )
        )
        recall = top1_hits / len(testable) if testable else 0
        # Report the actual recall regardless of pass/fail
        print(f"\nRecall@1: {top1_hits}/{len(testable)} = {recall:.1%}")
        assert recall >= 0.60, (
            f"top-1 recall {recall:.1%} ({top1_hits}/{len(testable)}) below 60%"
        )


# ---------------------------------------------------------------------------
# Specific regression tests for known retrieval gotchas
# ---------------------------------------------------------------------------


class TestVarianceThresholdFalsePositive:
    """B-002: fluorescence_hard_threshold must not be confused with
    variance_threshold_fit."""

    def test_fluorescence_outranks_variance_threshold(self, full_catalog):
        node = AlgorithmicNode(
            node_id="fluorescence_hard_threshold",
            name="Fluorescence Hard Threshold",
            description=(
                "Zero all values below a threshold on the derivative signal, "
                "keeping values above unchanged, to isolate co-activation events "
                "in calcium fluorescence traces."
            ),
            concept_type=ConceptType.SIGNAL_FILTER,
            inputs=[
                IOSpec(name="X", type_desc="NDArray[np.float64]"),
            ],
            outputs=[
                IOSpec(name="result", type_desc="NDArray[np.float64]"),
            ],
        )
        matches = full_catalog.find_matching_primitives(node, k=10)
        match_names = [m.name for m in matches]

        fluor_rank = (
            match_names.index("fluorescence_hard_threshold")
            if "fluorescence_hard_threshold" in match_names
            else 999
        )
        vt_rank = (
            match_names.index("variance_threshold_fit")
            if "variance_threshold_fit" in match_names
            else 999
        )

        assert fluor_rank < vt_rank, (
            f"fluorescence_hard_threshold (rank {fluor_rank}) should outrank "
            f"variance_threshold_fit (rank {vt_rank}). "
            f"Full ranking: {match_names}"
        )


class TestCrossRepoRetrieval:
    """Verify that atoms from different repos are found for cross-repo solutions."""

    def test_trackml_finds_physics_atoms(self, full_catalog):
        """TrackML stages should find atoms from sciona-atoms-physics."""
        node = AlgorithmicNode(
            node_id="helix_cylinder_intersection",
            name="Helix-Cylinder Intersection",
            description=(
                "Exploit that r^2 is a harmonic function of helix phase, "
                "so cylinder crossings reduce to solving cos(phi)=C analytically."
            ),
            concept_type=ConceptType.GEOMETRY,
            inputs=[
                IOSpec(name="helix_params", type_desc="tuple"),
                IOSpec(name="cylinder_radius", type_desc="float"),
            ],
            outputs=[
                IOSpec(name="intersection", type_desc="NDArray[np.float64]"),
            ],
        )
        matches = full_catalog.find_matching_primitives(node, k=5)
        match_names = [m.name for m in matches]
        assert "helix_cylinder_intersection" in match_names

    def test_barachant_finds_signal_atoms(self, full_catalog):
        """Barachant stages should find atoms from sciona-atoms-signal."""
        node = AlgorithmicNode(
            node_id="tangent_space_projection",
            name="Tangent Space Projection",
            description=(
                "Projects SPD matrices from Riemannian manifold to Euclidean "
                "tangent space at a reference point via matrix logarithm."
            ),
            concept_type=ConceptType.DIMENSIONALITY_REDUCTION,
            inputs=[
                IOSpec(name="covmats", type_desc="ndarray(n_matrices, n, n)"),
                IOSpec(name="ref", type_desc="ndarray(n, n)"),
            ],
            outputs=[
                IOSpec(name="T", type_desc="ndarray(n_matrices, n*(n+1)/2)"),
            ],
        )
        matches = full_catalog.find_matching_primitives(node, k=5)
        match_names = [m.name for m in matches]
        assert "tangent_space_projection" in match_names


class TestNewConceptTypes:
    """Verify that new concept_types (loss_function, external_knowledge) participate
    in retrieval correctly."""

    def test_loss_function_category_bonus(self, full_catalog):
        """A loss_function stage should get category_bonus for loss_function atoms."""
        node = AlgorithmicNode(
            node_id="miss_penalty",
            name="Miss Penalty Loss",
            description=(
                "Asymmetric conditional penalty that fires when a known "
                "positive gets very low predicted probability."
            ),
            concept_type=ConceptType.LOSS_FUNCTION,
            inputs=[
                IOSpec(name="predictions", type_desc="NDArray[np.float64]"),
                IOSpec(name="labels", type_desc="NDArray[np.float64]"),
            ],
            outputs=[IOSpec(name="loss", type_desc="float")],
        )
        matches = full_catalog.find_matching_primitives(node, k=5)
        match_names = [m.name for m in matches]
        assert "miss_penalty_loss" in match_names


# ---------------------------------------------------------------------------
# Catalog health check
# ---------------------------------------------------------------------------


class TestCatalogPopulation:
    """Verify the catalog is populated with a reasonable number of atoms."""

    def test_catalog_has_sufficient_atoms(self, full_catalog):
        assert full_catalog.size >= 100, (
            f"Catalog has only {full_catalog.size} atoms — "
            f"expected 100+. Check repo paths."
        )

    def test_catalog_has_atoms_from_each_repo(self, full_catalog):
        all_prims = full_catalog.all_primitives()
        sources = {p.source for p in all_prims}
        source_str = " ".join(sources)
        # Each repo should contribute at least one atom with a recognizable path
        for marker in [
            "particle_tracking",  # sciona-atoms-physics
            "riemannian_bci",  # sciona-atoms-signal
            "constrained_ml",  # sciona-atoms-ml
            "dl/training",  # sciona-atoms-dl
        ]:
            assert any(
                marker in s for s in sources
            ), f"No atoms from repo containing '{marker}'"

    def test_all_test_cases_loaded(self):
        assert len(ALL_CASES) >= 50, (
            f"Only {len(ALL_CASES)} test cases loaded — expected 50+. "
            f"Check solution CDG files."
        )
