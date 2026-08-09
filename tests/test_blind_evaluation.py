from __future__ import annotations

from pathlib import Path

from sciona.blind_evaluation import evaluate_solution, load_blind_suite
from sciona.api.routers.catalog import _fallback_text_filter
from sciona.catalog_query import expand_catalog_query_tokens
from sciona.api.models import CatalogEntry
from sciona.provider_runtime import RemoteCatalogClient


MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "evaluations"
    / "cross_disciplinary_blind.json"
)


def _task(evaluator: str):
    suite = load_blind_suite(MANIFEST)
    return next(task for task in suite.tasks if task.evaluator == evaluator)


def test_blind_manifest_covers_disciplines_and_prompt_disclosure_levels() -> None:
    suite = load_blind_suite(MANIFEST)

    assert len(suite.tasks) == 4
    assert len({task.discipline for task in suite.tasks}) == 4
    assert all(len(task.variants) == 3 for task in suite.tasks)
    assert {
        variant.variant for task in suite.tasks for variant in task.variants
    } == {"explicit", "masked", "nearby_domain"}


def test_no_key_fallback_searches_individual_intent_tokens() -> None:
    expression = _fallback_text_filter(
        "Find the least accumulated cost from one item",
        "fqdn",
        "technical_description",
    )

    assert "technical_description.ilike.%cost%" in expression
    assert "fqdn.ilike.%accumulated%" in expression
    assert "%find the least%" not in expression


def test_no_key_query_expansion_is_cross_domain_and_operational() -> None:
    assert {"shortest", "path", "node"} <= expand_catalog_query_tokens(
        "least cost to each reachable item"
    )
    assert {"convert", "transform", "translate"} <= expand_catalog_query_tokens(
        "translate coordinates"
    )
    assert {"predict", "advance", "project"} <= expand_catalog_query_tokens(
        "project vehicle state forward"
    )
    assert {"ecef", "lla", "latitude", "longitude"} <= expand_catalog_query_tokens(
        "map positions into an Earth-centered Cartesian frame"
    )
    assert {"rate", "frequency"} <= expand_catalog_query_tokens("measure cadence")


def test_hidden_dijkstra_evaluator_checks_independent_distances(tmp_path: Path) -> None:
    solution = tmp_path / "solution.py"
    solution.write_text(
        "import scipy.sparse.csgraph\n"
        "def solve(adj_matrix, source):\n"
        "    return scipy.sparse.csgraph.dijkstra(adj_matrix, indices=source)\n"
    )

    report = evaluate_solution(_task("dijkstra"), solution)

    assert report["passed"]
    assert report["max_abs_error"] == 0.0


def test_hidden_geo_evaluator_normalizes_longitude_and_checks_height(
    tmp_path: Path,
) -> None:
    solution = tmp_path / "solution.py"
    solution.write_text(
        "def solve(lat, lon, alt):\n"
        "    return lat, lon + 360.0, alt\n"
    )

    report = evaluate_solution(_task("wgs84_roundtrip"), solution)

    assert report["passed"]
    assert report["max_abs_roundtrip_error"] == 0.0


def test_hidden_robotics_evaluator_rejects_wrong_prediction(tmp_path: Path) -> None:
    solution = tmp_path / "solution.py"
    solution.write_text("def solve(*args):\n    return 10.5\n")

    report = evaluate_solution(_task("linear_state_prediction"), solution)

    assert not report["passed"]
    assert report["absolute_prediction_error"] == 0.5


def test_real_ecg_case_cannot_silently_fall_back_to_synthetic_data(
    tmp_path: Path,
) -> None:
    solution = tmp_path / "solution.py"
    solution.write_text("def solve(signal, sampling_rate):\n    return [], []\n")

    report = evaluate_solution(_task("ecg_rate"), solution)

    assert not report["passed"]
    assert "requires --ecg-edf" in report["error"]


async def test_selector_prefers_direct_output_over_an_unrequested_roundtrip(
    monkeypatch,
) -> None:
    client = RemoteCatalogClient("https://catalog.invalid")
    rows = [
        CatalogEntry(
            fqdn="cdg.geo.wgs84_ecef_roundtrip",
            artifact_kind="cdg",
            description="Convert WGS84 coordinates to ECEF and roundtrip back",
            trust_readiness="ready",
            score=0.5,
        ),
        CatalogEntry(
            fqdn="sciona.atoms.geo.lla_to_ecef",
            artifact_kind="atom",
            description="Convert WGS84 latitude longitude altitude to ECEF x y z",
            trust_readiness="ready",
            score=0.5,
        ),
    ]

    async def search(*args, **kwargs):
        return rows

    monkeypatch.setattr(client, "search_artifacts", search)

    selected = await client.select_artifact("Convert WGS84 GPS fixes to ECEF x y z")

    assert selected.artifact_kind == "atom"


async def test_selector_prefers_complete_graph_over_matching_leaf(monkeypatch) -> None:
    client = RemoteCatalogClient("https://catalog.invalid")
    rows = [
        CatalogEntry(
            fqdn="cdg.cs.dijkstra_shortest_path",
            artifact_kind="cdg",
            description="Validate weights and compute Dijkstra shortest paths",
            trust_readiness="ready",
            score=0.2,
        ),
        CatalogEntry(
            fqdn="sciona.atoms.cs.initialize_distances",
            artifact_kind="atom",
            description="Initialize distances for Dijkstra",
            trust_readiness="ready",
            score=0.9,
        ),
    ]

    async def search(*args, **kwargs):
        return rows

    monkeypatch.setattr(client, "search_artifacts", search)

    selected = await client.select_artifact("Compute Dijkstra distances")

    assert selected.artifact_kind == "cdg"
