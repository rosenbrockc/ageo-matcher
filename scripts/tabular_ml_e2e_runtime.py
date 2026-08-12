#!/usr/bin/env python3
"""Run catalog-discovered tabular atoms on pinned public UCI data."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from sciona.api.models import CatalogEntry
from sciona.open_datasets import OpenDataRegistry, fetch_open_data, load_open_data_registry
from sciona.provider_runtime import ProviderInstaller, RemoteCatalogClient


def _candidate_text(candidate: CatalogEntry) -> str:
    return f"{candidate.fqdn} {candidate.description}".lower().replace("_", " ")


def _select_candidate(
    candidates: list[CatalogEntry],
    *,
    required_terms: tuple[str, ...],
    preferred_terms: tuple[str, ...] = (),
) -> CatalogEntry:
    eligible: list[tuple[int, int, CatalogEntry]] = []
    for rank, candidate in enumerate(candidates):
        text = _candidate_text(candidate)
        if candidate.provider and all(term in text for term in required_terms):
            eligible.append((sum(term in text for term in preferred_terms), -rank, candidate))
    if eligible:
        return max(eligible, key=lambda item: (item[0], item[1]))[2]
    raise LookupError(
        f"No installable candidate contained {required_terms}; "
        f"returned={[candidate.fqdn for candidate in candidates[:10]]}"
    )


async def _discover(client: RemoteCatalogClient) -> tuple[dict[str, CatalogEntry], dict[str, list[str]]]:
    queries = {
        "split": "reproducible stratified train test split for a labeled mixed type table",
        "prior_fit": "fit empirical prior probability baseline for binary classification",
        "prior_predict": "predict constant prior probabilities for held out rows",
        "model_fit": "fit one hot logistic classifier for mixed categorical numeric tabular data",
        "cv_fit": "cross validated logistic regularization selected by log loss",
        "predict": "predict binary positive class probabilities from a fitted model",
    }
    required = {
        "split": ("stratified", "tabular", "split"),
        "prior_fit": ("fit", "prior", "probability"),
        "prior_predict": ("predict", "prior", "probabilities"),
        "model_fit": ("fit", "one", "hot", "logistic"),
        "cv_fit": ("fit", "cross", "validated", "logistic"),
        "predict": ("predict", "binary", "probabilities"),
    }
    selected: dict[str, CatalogEntry] = {}
    returned: dict[str, list[str]] = {}
    for role, query in queries.items():
        candidates = await client.search(query, limit=40)
        returned[role] = [candidate.fqdn for candidate in candidates[:10]]
        selected[role] = _select_candidate(
            candidates,
            required_terms=required[role],
            preferred_terms=("tabular", "supervised", "classification"),
        )
    return selected, returned


def _log_loss(probabilities: Any, targets: Any) -> float:
    values = np.clip(np.asarray(probabilities, dtype=float), 1e-12, 1.0 - 1e-12)
    labels = np.asarray(targets, dtype=int)
    return float(-np.mean(labels * np.log(values) + (1 - labels) * np.log1p(-values)))


def _evaluate_functions(functions: dict[str, Callable[..., Any]], dataset: pd.DataFrame) -> dict[str, Any]:
    X_train, X_test, y_train, y_test = functions["split"](dataset)
    prior_probability = functions["prior_fit"](y_train)
    prior, prior_targets = functions["prior_predict"](prior_probability, X_test, y_test)
    model = functions["model_fit"](X_train, y_train)
    expanded, expanded_targets = functions["predict"](model, X_test, y_test)
    cv_model = functions["cv_fit"](X_train, y_train)
    refined, refined_targets = functions["predict"](cv_model, X_test, y_test)

    if not (
        np.array_equal(prior_targets, expanded_targets)
        and np.array_equal(expanded_targets, refined_targets)
    ):
        raise AssertionError("model versions did not preserve the held-out evaluation partition")
    losses = {
        "prior_baseline": _log_loss(prior, prior_targets),
        "model_expansion": _log_loss(expanded, expanded_targets),
        "cv_refinement": _log_loss(refined, refined_targets),
    }
    if losses["model_expansion"] >= losses["prior_baseline"]:
        raise AssertionError(f"feature-dependent model did not improve over prior: {losses}")
    if losses["cv_refinement"] >= losses["prior_baseline"]:
        raise AssertionError(f"cross-validated model did not improve over prior: {losses}")
    return {
        "row_count": int(len(dataset)),
        "feature_count": int(dataset.shape[1] - 1),
        "train_count": int(len(X_train)),
        "test_count": int(len(X_test)),
        "positive_rate": float(np.mean(y_test)),
        "losses": losses,
        "loss_deltas": {
            "model_expansion": losses["model_expansion"] - losses["prior_baseline"],
            "cv_refinement": losses["cv_refinement"] - losses["model_expansion"],
        },
    }


def _load_public_table(registry_path: Path, cache_dir: Path | None) -> pd.DataFrame:
    registry = load_open_data_registry(registry_path)
    source = next(source for source in registry.sources if source.source_id == "uci-bank-marketing")
    selected_registry = OpenDataRegistry(schema_version=registry.schema_version, sources=[source])
    root = fetch_open_data(selected_registry, cache_dir=cache_dir)[source.source_id]
    return pd.read_csv(root / "bank.csv", sep=";")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    selected, returned = await _discover(RemoteCatalogClient(args.api_url))
    installer = ProviderInstaller()
    functions = {
        role: installer.materialize(candidate)
        for role, candidate in selected.items()
    }
    metrics = _evaluate_functions(
        functions,
        _load_public_table(args.registry, args.cache_dir),
    )
    return {
        "status": "passed",
        "dataset_id": "uci-bank-marketing",
        "selected": {role: candidate.fqdn for role, candidate in selected.items()},
        "search_top_10": returned,
        **metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", required=True)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "evaluations" / "open_data_sources.json",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".sciona_datasets_cache" / "open-data",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
