"""Versioned, provider-neutral quality gates for deployed catalog retrieval."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class CatalogIntentCase:
    case_id: str
    provider: str
    query: str
    acceptable_fqdns: tuple[str, ...]
    top_k: int = 10


@dataclass(frozen=True)
class CatalogQualityReport:
    cases: int
    hits: int
    recall: float
    providers: tuple[str, ...]
    misses: tuple[dict[str, object], ...]

    @property
    def ok(self) -> bool:
        return not self.misses

    def as_dict(self) -> dict[str, object]:
        return {
            "cases": self.cases,
            "hits": self.hits,
            "recall": self.recall,
            "providers": list(self.providers),
            "misses": list(self.misses),
            "ok": self.ok,
        }


def load_catalog_intent_cases(path: str | Path) -> tuple[CatalogIntentCase, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("cases")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Catalog intent benchmark must contain non-empty cases")
    cases = tuple(
        CatalogIntentCase(
            case_id=str(row["id"]),
            provider=str(row["provider"]),
            query=str(row["query"]).strip(),
            acceptable_fqdns=tuple(str(value) for value in row["acceptable_fqdns"]),
            top_k=int(row.get("top_k", 10)),
        )
        for row in rows
    )
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Catalog intent benchmark case IDs must be unique")
    for case in cases:
        if not case.query or not case.acceptable_fqdns or case.top_k < 1:
            raise ValueError(f"Invalid catalog intent case {case.case_id!r}")
        if case.query in case.acceptable_fqdns:
            raise ValueError(f"Case {case.case_id!r} uses an exact FQDN as its query")
    return cases


async def evaluate_catalog_intents(
    cases: tuple[CatalogIntentCase, ...],
    search: Callable[[str, int], Awaitable[list[Any]]],
) -> CatalogQualityReport:
    misses: list[dict[str, object]] = []
    for case in cases:
        rows = await search(case.query, case.top_k)
        returned = [
            str(row.get("fqdn", "") if isinstance(row, dict) else row.fqdn)
            for row in rows
        ]
        if not set(case.acceptable_fqdns).intersection(returned):
            misses.append(
                {
                    "id": case.case_id,
                    "provider": case.provider,
                    "query": case.query,
                    "acceptable_fqdns": list(case.acceptable_fqdns),
                    "returned_fqdns": returned,
                }
            )
    hits = len(cases) - len(misses)
    return CatalogQualityReport(
        cases=len(cases),
        hits=hits,
        recall=hits / len(cases),
        providers=tuple(sorted({case.provider for case in cases})),
        misses=tuple(misses),
    )
