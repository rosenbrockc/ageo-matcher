from __future__ import annotations

from pathlib import Path

import pytest

from sciona.catalog_quality import evaluate_catalog_intents, load_catalog_intent_cases


FIXTURE = Path(__file__).parent / "fixtures/provider_intent_benchmark.json"


def test_intent_benchmark_is_multidisciplinary_and_not_exact_fqdn() -> None:
    cases = load_catalog_intent_cases(FIXTURE)

    assert len(cases) == 10
    assert len({case.provider for case in cases}) == 10
    assert all(case.query not in case.acceptable_fqdns for case in cases)


@pytest.mark.asyncio
async def test_quality_report_exposes_ranked_misses() -> None:
    cases = load_catalog_intent_cases(FIXTURE)[:2]

    async def search(query: str, top_k: int) -> list[dict[str, str]]:
        assert top_k == 10
        if "shortest path" in query:
            return [{"fqdn": cases[0].acceptable_fqdns[0]}]
        return [{"fqdn": "sciona.atoms.unrelated"}]

    report = await evaluate_catalog_intents(cases, search)

    assert report.recall == 0.5
    assert report.misses[0]["id"] == "biology-interaction-graph"
    assert report.misses[0]["returned_fqdns"] == ["sciona.atoms.unrelated"]
