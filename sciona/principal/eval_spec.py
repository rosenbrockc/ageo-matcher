"""Reference-based evaluation helpers for exported runners.

The evaluation spec is a JSON object describing how to compare a runner's
outputs against reference channels loaded from the benchmark dataset.

Example:
{
  "loss": "rmse",
  "prediction": {
    "value_output": 1,
    "time_output": 0,
    "time_kind": "index",
    "time_source": "h10_ecg_t"
  },
  "reference": {
    "value_source": "h10_hr_value",
    "time_source": "h10_hr_t"
  }
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np


def load_evaluation_spec(spec: str | None) -> dict[str, Any] | None:
    """Load an evaluation spec from a file path or inline JSON string."""
    if spec is None:
        return None
    candidate = Path(spec).expanduser()
    if candidate.exists():
        return json.loads(candidate.read_text())
    return json.loads(spec)


def compute_evaluation_payload(
    result: Any,
    flat_inputs: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> dict[str, float]:
    """Compute reference-based loss metrics from a runner result."""
    prediction_spec = spec.get("prediction", {})
    reference_spec = spec.get("reference", {})

    prediction_values = _as_float_array(
        _select_output(result, prediction_spec.get("value_output"))
    )
    if reference_spec.get("value_output") is not None:
        reference_values = _as_float_array(
            _select_output(result, reference_spec.get("value_output"))
        )
    else:
        reference_values = _resolve_input_array(
            flat_inputs,
            _require_str(reference_spec.get("value_source"), "reference.value_source"),
        )

    prediction_times = _resolve_prediction_times(result, flat_inputs, prediction_spec)
    reference_times = None
    if reference_spec.get("time_source") is not None:
        reference_times = _resolve_input_array(
            flat_inputs,
            _require_str(reference_spec.get("time_source"), "reference.time_source"),
        )

    aligned_prediction, aligned_reference = _align_series(
        prediction_values,
        reference_values,
        prediction_times=prediction_times,
        reference_times=reference_times,
    )

    if aligned_prediction.size == 0 or aligned_reference.size == 0:
        raise ValueError("evaluation alignment produced no comparable samples")

    finite = np.isfinite(aligned_prediction) & np.isfinite(aligned_reference)
    aligned_prediction = aligned_prediction[finite]
    aligned_reference = aligned_reference[finite]
    if aligned_prediction.size == 0:
        raise ValueError("evaluation contains no finite prediction/reference pairs")

    diff = aligned_prediction - aligned_reference
    mse = float(np.mean(diff ** 2))
    rmse = float(np.sqrt(mse))
    mae = float(np.mean(np.abs(diff)))

    loss_name = str(spec.get("loss", spec.get("metric", "mse"))).lower()
    threshold = float(spec.get("classification_threshold", 0.5))
    probabilities = np.clip(aligned_prediction, 1e-12, 1.0 - 1e-12)
    binary_targets = aligned_reference.astype(np.int64)
    binary_reference = bool(
        np.all(np.isin(binary_targets, [0, 1]))
        and np.allclose(aligned_reference, binary_targets)
    )
    log_loss = float("nan")
    brier = float("nan")
    accuracy = float("nan")
    if binary_reference:
        log_loss = float(
            -np.mean(
                binary_targets * np.log(probabilities)
                + (1 - binary_targets) * np.log1p(-probabilities)
            )
        )
        brier = float(np.mean((probabilities - binary_targets) ** 2))
        accuracy = float(np.mean((probabilities >= threshold) == binary_targets))

    if loss_name == "rmse":
        loss = rmse
    elif loss_name == "mae":
        loss = mae
    elif loss_name == "mse":
        loss = mse
    elif loss_name in {"log_loss", "binary_cross_entropy"}:
        if not binary_reference:
            raise ValueError("binary log loss requires reference labels encoded as 0 and 1")
        loss = log_loss
    elif loss_name == "brier":
        if not binary_reference:
            raise ValueError("Brier loss requires reference labels encoded as 0 and 1")
        loss = brier
    elif loss_name in {"classification_error", "error_rate"}:
        if not binary_reference:
            raise ValueError("classification error requires reference labels encoded as 0 and 1")
        loss = 1.0 - accuracy
    else:
        raise ValueError(f"unsupported evaluation loss {loss_name!r}")

    metrics = {
        "mse": mse,
        "rmse": rmse,
        "mae": mae,
        "loss": loss,
        "n_eval_samples": float(aligned_prediction.size),
    }
    if binary_reference:
        metrics.update(
            {
                "log_loss": log_loss,
                "brier": brier,
                "accuracy": accuracy,
                "classification_error": 1.0 - accuracy,
            }
        )
    return metrics


def _resolve_prediction_times(
    result: Any,
    flat_inputs: Mapping[str, Any],
    prediction_spec: Mapping[str, Any],
) -> np.ndarray | None:
    selector = prediction_spec.get("time_output")
    if selector is None:
        return None

    raw_times = _select_output(result, selector)
    time_kind = str(prediction_spec.get("time_kind", "timestamp")).lower()
    values = np.asarray(raw_times).reshape(-1)

    if time_kind in {"timestamp", "value"}:
        return _as_float_array(values)
    if time_kind == "index":
        source_name = _require_str(prediction_spec.get("time_source"), "prediction.time_source")
        time_source = _resolve_input_array(flat_inputs, source_name)
        indices = np.asarray(values, dtype=np.int64).reshape(-1)
        if indices.size == 0:
            return np.empty(0, dtype=np.float64)
        if indices.min(initial=0) < 0 or indices.max(initial=-1) >= time_source.size:
            raise IndexError(
                f"prediction index output is out of bounds for source {source_name!r}"
            )
        return time_source[indices]
    raise ValueError(f"unsupported prediction.time_kind {time_kind!r}")


def _align_series(
    prediction_values: np.ndarray,
    reference_values: np.ndarray,
    *,
    prediction_times: np.ndarray | None,
    reference_times: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray]:
    prediction_values = _finite_1d(prediction_values)
    reference_values = _finite_1d(reference_values)

    if prediction_times is None and reference_times is None:
        n = min(prediction_values.size, reference_values.size)
        return prediction_values[:n], reference_values[:n]

    if prediction_times is None or reference_times is None:
        raise ValueError("time-aware evaluation requires both prediction and reference times")

    prediction_times = _finite_1d(prediction_times)
    reference_times = _finite_1d(reference_times)
    n = min(prediction_values.size, prediction_times.size)
    prediction_values = prediction_values[:n]
    prediction_times = prediction_times[:n]
    n = min(reference_values.size, reference_times.size)
    reference_values = reference_values[:n]
    reference_times = reference_times[:n]

    if prediction_values.size == 0 or reference_values.size == 0:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)

    order = np.argsort(reference_times)
    reference_times = reference_times[order]
    reference_values = reference_values[order]

    unique_times, unique_idx = np.unique(reference_times, return_index=True)
    reference_times = unique_times
    reference_values = reference_values[unique_idx]

    lo = float(reference_times[0])
    hi = float(reference_times[-1])
    keep = (prediction_times >= lo) & (prediction_times <= hi)
    if not np.any(keep):
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)

    prediction_times = prediction_times[keep]
    prediction_values = prediction_values[keep]
    aligned_reference = np.interp(prediction_times, reference_times, reference_values)
    return prediction_values, aligned_reference


def _select_output(result: Any, selector: Any) -> Any:
    if selector is None:
        return result
    if isinstance(result, Mapping):
        if selector not in result:
            raise KeyError(f"output selector {selector!r} not found in mapping result")
        return result[selector]
    if isinstance(result, (list, tuple)):
        if not isinstance(selector, int):
            raise TypeError("list/tuple output selectors must be integers")
        return result[selector]
    raise TypeError("cannot select nested output from scalar result")


def _resolve_input_array(flat_inputs: Mapping[str, Any], name: str) -> np.ndarray:
    if name not in flat_inputs:
        raise KeyError(f"input source {name!r} not found in loaded dataset inputs")
    return _as_float_array(flat_inputs[name])


def _finite_1d(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    return array[np.isfinite(array)]


def _as_float_array(values: Any) -> np.ndarray:
    return np.asarray(values, dtype=np.float64).reshape(-1)


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value
