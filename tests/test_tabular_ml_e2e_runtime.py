"""Focused regression tests for the public Tabular ML E2E harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from sciona.api.models import CatalogEntry, ProviderInstallInfo


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tabular_ml_e2e_runtime.py"
SPEC = importlib.util.spec_from_file_location("tabular_ml_e2e_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
RUNTIME = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNTIME
SPEC.loader.exec_module(RUNTIME)


def _entry(fqdn: str, description: str) -> CatalogEntry:
    module, symbol = fqdn.rsplit(".", 1)
    return CatalogEntry(
        fqdn=fqdn,
        description=description,
        provider=ProviderInstallInfo(
            provider_id="sciona-atoms-ml",
            distribution_name="sciona-atoms-ml",
            distribution_version="0.1.0",
            install_requirement="sciona-atoms-ml==0.1.0",
            import_module=module,
            import_symbol=symbol,
        ),
    )


def test_select_candidate_uses_generic_capability_text() -> None:
    unrelated = _entry("vendor.ml.random_split", "Randomly divide numeric values")
    matching = _entry(
        "vendor.ml.partition",
        "Create a reproducible stratified tabular train test split",
    )

    assert RUNTIME._select_candidate(
        [unrelated, matching], required_terms=("stratified", "tabular", "split")
    ) is matching


def test_select_candidate_prefers_context_complete_family_over_low_level_helper() -> None:
    low_level = _entry(
        "vendor.gaussian.classification.predict_binary_probabilities",
        "Predict binary probabilities from integrals",
    )
    workflow = _entry(
        "vendor.tabular.supervised_classification.predict_binary_probabilities",
        "Predict binary probabilities and aligned held-out targets",
    )

    assert RUNTIME._select_candidate(
        [low_level, workflow],
        required_terms=("predict", "binary", "probabilities"),
        preferred_terms=("tabular", "supervised", "classification"),
    ) is workflow


def test_public_tabular_versions_execute_and_improve_on_same_holdout() -> None:
    from sciona.atoms.ml.tabular.supervised_classification import (
        fit_cross_validated_logistic,
        fit_one_hot_logistic,
        fit_prior_probability,
        predict_binary_probabilities,
        predict_prior_probabilities,
        stratified_tabular_split,
    )

    rng = np.random.default_rng(8)
    feature = rng.normal(size=300)
    table = pd.DataFrame(
        {
            "amount": feature,
            "segment": np.where(feature > 0.0, "upper", "lower"),
            "target": np.where(feature + rng.normal(scale=0.4, size=300) > 0.0, "yes", "no"),
        }
    )
    metrics = RUNTIME._evaluate_functions(
        {
            "split": stratified_tabular_split,
            "prior_fit": fit_prior_probability,
            "prior_predict": predict_prior_probabilities,
            "model_fit": fit_one_hot_logistic,
            "cv_fit": fit_cross_validated_logistic,
            "predict": predict_binary_probabilities,
        },
        table,
    )

    assert metrics["test_count"] == 75
    assert metrics["loss_deltas"]["model_expansion"] < 0.0
    assert metrics["losses"]["cv_refinement"] < metrics["losses"]["prior_baseline"]


def test_cli_uses_a_repo_local_cache_by_default(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--api-url", "http://localhost"])
    captured = {}

    async def fake_run(args):
        captured["cache_dir"] = args.cache_dir
        return {"status": "passed"}

    monkeypatch.setattr(RUNTIME, "run", fake_run)

    assert RUNTIME.main() == 0
    assert captured["cache_dir"] == SCRIPT.parents[1] / ".sciona_datasets_cache" / "open-data"
