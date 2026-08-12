from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sciona.architect.handoff import CDGExport
from sciona.architect.models import AlgorithmicNode, ConceptType, DependencyEdge, IOSpec, NodeStatus
from sciona.visualizer.runner import CDGExecutionSession
from sciona.visualizer.runner_api import _evaluate_persisted_run


PREFIX = "sciona.atoms.ml.tabular.supervised_classification."


def _node(node_id: str, primitive: str, inputs: list[tuple[str, str]], outputs: list[tuple[str, str]]) -> AlgorithmicNode:
    return AlgorithmicNode(
        node_id=node_id,
        name=node_id,
        description=node_id.replace("_", " "),
        concept_type=ConceptType.CUSTOM,
        status=NodeStatus.ATOMIC,
        matched_primitive=PREFIX + primitive,
        inputs=[IOSpec(name=name, type_desc=type_desc) for name, type_desc in inputs],
        outputs=[IOSpec(name=name, type_desc=type_desc) for name, type_desc in outputs],
    )


def _edge(source: str, target: str, output: str, input_name: str, type_desc: str) -> DependencyEdge:
    return DependencyEdge(
        source_id=source,
        target_id=target,
        output_name=output,
        input_name=input_name,
        source_type=type_desc,
        target_type=type_desc,
    )


def _graph(fit_primitive: str, *, baseline: bool) -> CDGExport:
    split = _node(
        "split",
        "stratified_tabular_split",
        [("dataset", "DataFrame")],
        [("X_train", "DataFrame"), ("X_test", "DataFrame"), ("y_train", "NDArray[int64]"), ("y_test", "NDArray[int64]")],
    )
    if baseline:
        fit = _node("fit", "fit_prior_probability", [("y_train", "NDArray[int64]")], [("class_probability", "float")])
        predict = _node(
            "predict",
            "predict_prior_probabilities",
            [("class_probability", "float"), ("X_test", "DataFrame"), ("y_test", "NDArray[int64]")],
            [("probabilities", "NDArray[float64]"), ("targets", "NDArray[int64]")],
        )
        edges = [_edge("split", "fit", "y_train", "y_train", "NDArray[int64]"), _edge("fit", "predict", "class_probability", "class_probability", "float")]
    else:
        fit = _node(
            "fit",
            fit_primitive,
            [("X_train", "DataFrame"), ("y_train", "NDArray[int64]")],
            [("model", "estimator")],
        )
        predict = _node(
            "predict",
            "predict_binary_probabilities",
            [("model", "estimator"), ("X_test", "DataFrame"), ("y_test", "NDArray[int64]")],
            [("probabilities", "NDArray[float64]"), ("targets", "NDArray[int64]")],
        )
        edges = [
            _edge("split", "fit", "X_train", "X_train", "DataFrame"),
            _edge("split", "fit", "y_train", "y_train", "NDArray[int64]"),
            _edge("fit", "predict", "model", "model", "estimator"),
        ]
    edges.extend(
        [
            _edge("split", "predict", "X_test", "X_test", "DataFrame"),
            _edge("split", "predict", "y_test", "y_test", "NDArray[int64]"),
        ]
    )
    return CDGExport(nodes=[split, fit, predict], edges=edges, metadata={"repo": "showcase/public-tabular-classification"})


@pytest.mark.asyncio
async def test_visualizer_executes_and_scores_all_tabular_versions(monkeypatch, tmp_path) -> None:
    import sciona.visualizer.runner as runner
    import sciona.visualizer.runner_api as runner_api

    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(runner_api, "RUNS_DIR", tmp_path)
    rng = np.random.default_rng(44)
    signal = rng.normal(size=260)
    dataset = pd.DataFrame(
        {
            "value": signal,
            "group": np.where(signal > 0, "positive", "negative"),
            "target": np.where(signal + rng.normal(scale=0.4, size=260) > 0, "yes", "no"),
        }
    )
    manifest = {
        "fqn": "sciona.data.synthetic.tabular.v1",
        "evaluation": {
            "objective": "log_loss",
            "prediction_node_id": "predict",
            "spec": {
                "loss": "log_loss",
                "prediction": {"value_output": "probabilities"},
                "reference": {"value_output": "targets"},
            },
            "reference_data": {},
        },
    }
    versions = [
        ("prior", _graph("", baseline=True)),
        ("expanded", _graph("fit_one_hot_logistic", baseline=False)),
        ("refined", _graph("fit_cross_validated_logistic", baseline=False)),
    ]
    losses = {}
    for version_id, graph in versions:
        await CDGExecutionSession(None, graph.metadata["repo"], version_id).execute(
            {"dataset": dataset}, cdg=graph, execution_id="tabular-test", version_id=version_id
        )
        losses[version_id] = _evaluate_persisted_run(
            version_id, manifest, version_id=version_id
        )["loss"]

    assert losses["expanded"] < losses["prior"]
    assert losses["refined"] < losses["prior"]
    assert (tmp_path / "refined" / "predict" / "out_probabilities.npy").is_file()
    assert (tmp_path / "refined" / "predict" / "out_targets.npy").is_file()
