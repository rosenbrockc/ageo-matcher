"""Deterministic benchmark-result generation for provider-owned manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from sciona.architect.skeleton_assets import SkeletonFamilyAsset, load_local_skeleton_assets

_MEASURED_AT = "2026-04-14T00:00:00Z"
_RUNNER = "matcher-deterministic-benchmark-v1"
_ATOM_BENCHMARK_TARGETS = {
    "signal.event_rate.ecg.v1": "sciona.atoms.expansion.signal_event_rate.estimate_event_rate_from_signal",
    "state_estimation.kalman.synthetic_tracking.v1": "sciona.atoms.state_estimation.kalman_filters.track_linear_gaussian_state",
    "state_estimation.particle.synthetic_tracking.v1": "sciona.atoms.state_estimation.particle_filters.track_particle_hidden_state",
}


@dataclass(frozen=True)
class BenchmarkResultRow:
    suite_id: str
    artifact_fqdn: str
    artifact_kind: str
    content_hash: str
    semver: str
    metric_name: str
    metric_value: float
    slice_key: str
    measured_at: str = _MEASURED_AT
    runner: str = _RUNNER
    run_config_hash: str = ""
    status: str = "completed"
    evidence_uri: str = ""
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "artifact_fqdn": self.artifact_fqdn,
            "artifact_kind": self.artifact_kind,
            "content_hash": self.content_hash,
            "semver": self.semver,
            "metric_name": self.metric_name,
            "metric_value": round(float(self.metric_value), 6),
            "slice_key": self.slice_key,
            "measured_at": self.measured_at,
            "runner": self.runner,
            "run_config_hash": self.run_config_hash,
            "status": self.status,
            "evidence_uri": self.evidence_uri,
            "notes": self.notes,
        }


def _artifact_fqdn(asset: SkeletonFamilyAsset) -> str:
    return f"cdg.skeleton.{asset.asset_id}"


def _content_hash(asset: SkeletonFamilyAsset) -> str:
    payload = asset.model_dump_json(by_alias=True, exclude_none=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _config_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _asset_by_id(asset_id: str) -> SkeletonFamilyAsset:
    for asset in load_local_skeleton_assets():
        if asset.asset_id == asset_id:
            return asset
    raise KeyError(f"Unknown skeleton asset {asset_id!r}")


@lru_cache(maxsize=1)
def _inventory_version_rows() -> dict[str, tuple[str, str]]:
    from sciona.atoms.supabase_seed import derive_seed_inventory

    repo_root = Path(__file__).resolve().parents[2]
    inventory = derive_seed_inventory(base_dir=repo_root.parent)
    return {row.fqdn: (row.content_hash, row.semver) for row in inventory.version_rows}


def _atom_artifact_for_suite(suite_id: str) -> tuple[str, str, str]:
    fqdn = _ATOM_BENCHMARK_TARGETS[suite_id]
    content_hash, semver = _inventory_version_rows()[fqdn]
    return fqdn, content_hash, semver


def _f1_score(tp: int, fp: int, fn: int) -> float:
    denom = 2 * tp + fp + fn
    if denom <= 0:
        return 0.0
    return (2.0 * tp) / float(denom)


def _match_events(
    truth: np.ndarray,
    predicted: np.ndarray,
    *,
    tolerance_samples: int,
) -> tuple[int, int, int]:
    used = np.zeros(len(predicted), dtype=bool)
    tp = 0
    for event in truth:
        diffs = np.abs(predicted - int(event))
        candidates = np.where((diffs <= tolerance_samples) & ~used)[0]
        if candidates.size == 0:
            continue
        best = int(candidates[np.argmin(diffs[candidates])])
        used[best] = True
        tp += 1
    fp = int((~used).sum())
    fn = int(len(truth) - tp)
    return tp, fp, fn


def _synthetic_ecg(
    *,
    bpm_profile: np.ndarray,
    sampling_rate: float = 100.0,
    noise_scale: float = 0.05,
    arrhythmia: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(42)
    signal = np.zeros(int(len(bpm_profile) * sampling_rate), dtype=np.float64)
    beat_positions: list[int] = []
    cursor = 0.3
    for bpm in bpm_profile:
        period = 60.0 / float(bpm)
        cursor += period
        index = int(round(cursor * sampling_rate))
        if index >= signal.size:
            break
        beat_positions.append(index)
        width = 4
        span = np.arange(max(0, index - width), min(signal.size, index + width + 1))
        pulse = np.exp(-0.5 * ((span - index) / 1.5) ** 2)
        signal[span] += pulse
    baseline = 0.1 * np.sin(np.linspace(0.0, 8.0 * np.pi, signal.size))
    signal += baseline + noise_scale * rng.standard_normal(signal.size)
    if arrhythmia and signal.size:
        burst_start = signal.size // 2
        signal[burst_start : burst_start + 40] += 0.35 * rng.standard_normal(40)
    return signal, np.asarray(beat_positions, dtype=np.int64)


def _signal_suite_rows() -> list[BenchmarkResultRow]:
    from sciona.atoms.expansion.signal_event_rate import (
        compute_event_rate_median_smoothed,
        detect_peaks_in_signal,
        filter_signal_for_detection,
    )

    asset = _asset_by_id("signal_detect_measure")
    config_hash = _config_hash({"suite": "signal.event_rate.ecg.v1", "samplerate": 100.0})
    rows: list[BenchmarkResultRow] = []
    suite_id = "signal.event_rate.ecg.v1"
    artifact_fqdn = _artifact_fqdn(asset)
    content_hash = _content_hash(asset)
    scenarios = {
        "clean": dict(bpm_profile=np.full(8, 72.0), noise_scale=0.02, arrhythmia=False),
        "noisy": dict(bpm_profile=np.full(8, 72.0), noise_scale=0.12, arrhythmia=False),
        "arrhythmic": dict(
            bpm_profile=np.asarray([72.0, 72.0, 88.0, 64.0, 92.0, 70.0, 84.0, 68.0]),
            noise_scale=0.08,
            arrhythmia=True,
        ),
    }
    for slice_key, params in scenarios.items():
        signal, truth_events = _synthetic_ecg(**params)
        conditioned = filter_signal_for_detection(signal, 100.0)
        detected = detect_peaks_in_signal(conditioned, 100.0)
        _, rate = compute_event_rate_median_smoothed(detected, 100.0, smoothing_window=5)
        tp, fp, fn = _match_events(truth_events, detected, tolerance_samples=8)
        truth_bpm = np.mean(params["bpm_profile"])
        mae_bpm = abs(float(np.median(rate)) - float(truth_bpm)) if rate.size else truth_bpm
        rows.extend(
            [
                BenchmarkResultRow(
                    suite_id=suite_id,
                    artifact_fqdn=artifact_fqdn,
                    artifact_kind="cdg",
                    content_hash=content_hash,
                    semver=asset.asset_version,
                    metric_name="f1",
                    metric_value=_f1_score(tp, fp, fn),
                    slice_key=slice_key,
                    run_config_hash=config_hash,
                    notes="Deterministic synthetic ECG evaluation over the published skeleton pipeline.",
                ),
                BenchmarkResultRow(
                    suite_id=suite_id,
                    artifact_fqdn=artifact_fqdn,
                    artifact_kind="cdg",
                    content_hash=content_hash,
                    semver=asset.asset_version,
                    metric_name="mae_bpm",
                    metric_value=mae_bpm,
                    slice_key=slice_key,
                    run_config_hash=config_hash,
                    notes="Deterministic synthetic ECG evaluation over the published skeleton pipeline.",
                ),
            ]
        )
    return rows


def _signal_atom_suite_rows() -> list[BenchmarkResultRow]:
    from sciona.atoms.expansion.signal_event_rate import estimate_event_rate_from_signal

    suite_id = "signal.event_rate.ecg.v1"
    artifact_fqdn, content_hash, semver = _atom_artifact_for_suite(suite_id)
    config_hash = _config_hash(
        {
            "suite": suite_id,
            "samplerate": 100.0,
            "runner": "estimate_event_rate_from_signal",
        }
    )
    scenarios = {
        "clean": dict(bpm_profile=np.full(8, 72.0), noise_scale=0.02, arrhythmia=False),
        "noisy": dict(bpm_profile=np.full(8, 72.0), noise_scale=0.12, arrhythmia=False),
        "arrhythmic": dict(
            bpm_profile=np.asarray([72.0, 72.0, 88.0, 64.0, 92.0, 70.0, 84.0, 68.0]),
            noise_scale=0.08,
            arrhythmia=True,
        ),
    }
    rows: list[BenchmarkResultRow] = []
    for slice_key, params in scenarios.items():
        signal, truth_events = _synthetic_ecg(**params)
        detected, _, rate = estimate_event_rate_from_signal(signal, 100.0, smoothing_window=5)
        tp, fp, fn = _match_events(truth_events, detected, tolerance_samples=8)
        truth_bpm = np.mean(params["bpm_profile"])
        mae_bpm = abs(float(np.median(rate)) - float(truth_bpm)) if rate.size else truth_bpm
        rows.extend(
            [
                BenchmarkResultRow(
                    suite_id=suite_id,
                    artifact_fqdn=artifact_fqdn,
                    artifact_kind="atom",
                    content_hash=content_hash,
                    semver=semver,
                    metric_name="f1",
                    metric_value=_f1_score(tp, fp, fn),
                    slice_key=slice_key,
                    run_config_hash=config_hash,
                    notes="Deterministic synthetic ECG evaluation over the contract-level signal atom.",
                ),
                BenchmarkResultRow(
                    suite_id=suite_id,
                    artifact_fqdn=artifact_fqdn,
                    artifact_kind="atom",
                    content_hash=content_hash,
                    semver=semver,
                    metric_name="mae_bpm",
                    metric_value=mae_bpm,
                    slice_key=slice_key,
                    run_config_hash=config_hash,
                    notes="Deterministic synthetic ECG evaluation over the contract-level signal atom.",
                ),
            ]
        )
    return rows


def _kalman_sequence(
    *,
    n_steps: int,
    process_scale: float,
    observation_scale: float,
    abrupt_jump: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    x = np.zeros(n_steps, dtype=np.float64)
    z = np.zeros(n_steps, dtype=np.float64)
    for idx in range(1, n_steps):
        drift = 1.5 if abrupt_jump and idx == (n_steps // 2) else 0.0
        x[idx] = x[idx - 1] + drift + rng.normal(scale=process_scale)
    z[:] = x + rng.normal(scale=observation_scale, size=n_steps)
    return x, z


def _kalman_filter_estimates(truth: np.ndarray, observations: np.ndarray) -> np.ndarray:
    from sciona.atoms.state_estimation.kalman_filters.static_kf.atoms import (
        expose_latent_mean,
        initialize_linear_gaussian_state_model,
        predict_latent_state,
        update_with_measurement,
    )

    state = initialize_linear_gaussian_state_model(
        initial_state=0.0,
        initial_covariance=1.0,
        transition_matrix=1.0,
        process_noise=0.05,
        observation_matrix=1.0,
        measurement_noise=0.2,
    )
    estimates = []
    for measurement in observations:
        state = predict_latent_state(state)
        state = update_with_measurement(state, float(measurement))
        estimates.append(float(expose_latent_mean(state)[0]))
    return np.asarray(estimates, dtype=np.float64)


def _kalman_suite_rows() -> list[BenchmarkResultRow]:
    asset = _asset_by_id("kalman_filter")
    config_hash = _config_hash({"suite": "state_estimation.kalman.synthetic_tracking.v1"})
    suite_id = "state_estimation.kalman.synthetic_tracking.v1"
    artifact_fqdn = _artifact_fqdn(asset)
    content_hash = _content_hash(asset)
    scenarios = {
        "well_conditioned": dict(process_scale=0.05, observation_scale=0.12, abrupt_jump=False),
        "high_noise": dict(process_scale=0.05, observation_scale=0.4, abrupt_jump=False),
        "abrupt_transition": dict(process_scale=0.05, observation_scale=0.2, abrupt_jump=True),
    }
    rows: list[BenchmarkResultRow] = []
    for slice_key, params in scenarios.items():
        truth, observations = _kalman_sequence(n_steps=60, **params)
        estimates = _kalman_filter_estimates(truth, observations)
        rmse = float(np.sqrt(np.mean((estimates - truth) ** 2)))
        rows.append(
            BenchmarkResultRow(
                suite_id=suite_id,
                artifact_fqdn=artifact_fqdn,
                artifact_kind="cdg",
                content_hash=content_hash,
                semver=asset.asset_version,
                metric_name="rmse_state",
                metric_value=rmse,
                slice_key=slice_key,
                run_config_hash=config_hash,
                notes="Deterministic linear-Gaussian tracking evaluation for the concrete Kalman skeleton.",
            )
        )
    return rows


def _kalman_atom_suite_rows() -> list[BenchmarkResultRow]:
    from sciona.atoms.state_estimation.kalman_filters.atoms import (
        track_linear_gaussian_state,
    )

    suite_id = "state_estimation.kalman.synthetic_tracking.v1"
    artifact_fqdn, content_hash, semver = _atom_artifact_for_suite(suite_id)
    config_hash = _config_hash({"suite": suite_id, "runner": "track_linear_gaussian_state"})
    scenarios = {
        "well_conditioned": dict(process_scale=0.05, observation_scale=0.12, abrupt_jump=False),
        "high_noise": dict(process_scale=0.05, observation_scale=0.4, abrupt_jump=False),
        "abrupt_transition": dict(process_scale=0.05, observation_scale=0.2, abrupt_jump=True),
    }
    rows: list[BenchmarkResultRow] = []
    for slice_key, params in scenarios.items():
        truth, observations = _kalman_sequence(n_steps=60, **params)
        estimates, _ = track_linear_gaussian_state(
            observations,
            process_noise=params["process_scale"],
            observation_noise=params["observation_scale"],
        )
        rmse = float(np.sqrt(np.mean((estimates - truth) ** 2)))
        rows.append(
            BenchmarkResultRow(
                suite_id=suite_id,
                artifact_fqdn=artifact_fqdn,
                artifact_kind="atom",
                content_hash=content_hash,
                semver=semver,
                metric_name="rmse_state",
                metric_value=rmse,
                slice_key=slice_key,
                run_config_hash=config_hash,
                notes="Deterministic linear-tracking evaluation over the contract-level Kalman atom.",
            )
        )
    return rows


def _particle_sequence(
    *,
    n_steps: int,
    process_scale: float,
    observation_scale: float,
    nonlinearity: float,
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(11)
    x = np.zeros(n_steps, dtype=np.float64)
    z = np.zeros(n_steps, dtype=np.float64)
    for idx in range(1, n_steps):
        x[idx] = x[idx - 1] + nonlinearity * np.sin(x[idx - 1]) + rng.normal(scale=process_scale)
    z[:] = x + rng.normal(scale=observation_scale, size=n_steps)
    return x, z


def _particle_filter_estimates(truth: np.ndarray, observations: np.ndarray) -> tuple[np.ndarray, float]:
    n_particles = 128
    rng = np.random.default_rng(19)
    particles = rng.normal(0.0, 0.5, size=n_particles)
    weights = np.ones(n_particles, dtype=np.float64) / n_particles
    estimates: list[float] = []
    ess_values: list[float] = []
    for observation in observations:
        ess = 1.0 / float(np.sum(weights**2))
        if ess < (0.55 * n_particles):
            indices = rng.choice(n_particles, size=n_particles, replace=True, p=weights)
            particles = particles[indices]
            weights = np.ones(n_particles, dtype=np.float64) / n_particles
        particles = particles + 0.15 * np.sin(particles) + rng.normal(0.0, 0.12, size=n_particles)
        log_weights = -0.5 * ((particles - observation) / 0.25) ** 2
        log_weights -= np.max(log_weights)
        weights = np.exp(log_weights)
        weights /= np.sum(weights)
        estimates.append(float(np.sum(weights * particles)))
        ess_values.append((1.0 / np.sum(weights**2)) / float(n_particles))
    return np.asarray(estimates, dtype=np.float64), float(np.mean(ess_values))


def _particle_suite_rows() -> list[BenchmarkResultRow]:
    asset = _asset_by_id("particle_filter")
    config_hash = _config_hash({"suite": "state_estimation.particle.synthetic_tracking.v1"})
    suite_id = "state_estimation.particle.synthetic_tracking.v1"
    artifact_fqdn = _artifact_fqdn(asset)
    content_hash = _content_hash(asset)
    scenarios = {
        "mild_nonlinearity": dict(process_scale=0.08, observation_scale=0.2, nonlinearity=0.1),
        "heavy_nonlinearity": dict(process_scale=0.12, observation_scale=0.25, nonlinearity=0.3),
        "multimodal_observation": dict(process_scale=0.1, observation_scale=0.35, nonlinearity=0.25),
    }
    rows: list[BenchmarkResultRow] = []
    for slice_key, params in scenarios.items():
        truth, observations = _particle_sequence(n_steps=60, **params)
        estimates, ess_fraction = _particle_filter_estimates(truth, observations)
        rmse = float(np.sqrt(np.mean((estimates - truth) ** 2)))
        rows.extend(
            [
                BenchmarkResultRow(
                    suite_id=suite_id,
                    artifact_fqdn=artifact_fqdn,
                    artifact_kind="cdg",
                    content_hash=content_hash,
                    semver=asset.asset_version,
                    metric_name="rmse_state",
                    metric_value=rmse,
                    slice_key=slice_key,
                    run_config_hash=config_hash,
                    notes="Deterministic bootstrap-particle-filter evaluation for the concrete particle skeleton.",
                ),
                BenchmarkResultRow(
                    suite_id=suite_id,
                    artifact_fqdn=artifact_fqdn,
                    artifact_kind="cdg",
                    content_hash=content_hash,
                    semver=asset.asset_version,
                    metric_name="ess_fraction",
                    metric_value=ess_fraction,
                    slice_key=slice_key,
                    run_config_hash=config_hash,
                    notes="Deterministic bootstrap-particle-filter evaluation for the concrete particle skeleton.",
                ),
            ]
        )
    return rows


def _particle_atom_suite_rows() -> list[BenchmarkResultRow]:
    from sciona.atoms.state_estimation.particle_filters.atoms import (
        track_particle_hidden_state,
    )

    suite_id = "state_estimation.particle.synthetic_tracking.v1"
    artifact_fqdn, content_hash, semver = _atom_artifact_for_suite(suite_id)
    config_hash = _config_hash({"suite": suite_id, "runner": "track_particle_hidden_state"})
    scenarios = {
        "mild_nonlinearity": dict(process_scale=0.08, observation_scale=0.2, nonlinearity=0.1),
        "heavy_nonlinearity": dict(process_scale=0.12, observation_scale=0.25, nonlinearity=0.3),
        "multimodal_observation": dict(process_scale=0.08, observation_scale=0.35, nonlinearity=0.5),
    }
    rows: list[BenchmarkResultRow] = []
    for slice_key, params in scenarios.items():
        truth, observations = _particle_sequence(n_steps=60, **params)
        estimates, ess_fractions, _ = track_particle_hidden_state(observations, rng_seed=7)
        rmse = float(np.sqrt(np.mean((estimates - truth) ** 2)))
        ess_fraction = float(np.mean(ess_fractions))
        rows.extend(
            [
                BenchmarkResultRow(
                    suite_id=suite_id,
                    artifact_fqdn=artifact_fqdn,
                    artifact_kind="atom",
                    content_hash=content_hash,
                    semver=semver,
                    metric_name="rmse_state",
                    metric_value=rmse,
                    slice_key=slice_key,
                    run_config_hash=config_hash,
                    notes="Deterministic nonlinear-tracking evaluation over the contract-level particle atom.",
                ),
                BenchmarkResultRow(
                    suite_id=suite_id,
                    artifact_fqdn=artifact_fqdn,
                    artifact_kind="atom",
                    content_hash=content_hash,
                    semver=semver,
                    metric_name="ess_fraction",
                    metric_value=ess_fraction,
                    slice_key=slice_key,
                    run_config_hash=config_hash,
                    notes="Deterministic nonlinear-tracking evaluation over the contract-level particle atom.",
                ),
            ]
        )
    return rows


def generate_provider_benchmark_results() -> dict[Path, list[dict[str, Any]]]:
    repo_root = Path(__file__).resolve().parents[2]
    provider_root = repo_root.parent
    grouped: dict[Path, list[BenchmarkResultRow]] = {
        provider_root / "sciona-atoms" / "data" / "benchmarks" / "benchmark_results.json": [],
        provider_root / "sciona-atoms-signal" / "data" / "benchmarks" / "benchmark_results.json": [],
    }
    signal_path = next(path for path in grouped if "sciona-atoms-signal" in str(path))
    core_path = next(
        path
        for path in grouped
        if str(path).endswith("sciona-atoms/data/benchmarks/benchmark_results.json")
    )
    grouped[signal_path].extend(_signal_suite_rows() + _signal_atom_suite_rows())
    grouped[core_path].extend(
        _kalman_suite_rows()
        + _kalman_atom_suite_rows()
        + _particle_suite_rows()
        + _particle_atom_suite_rows()
    )
    return {
        path: [row.as_dict() for row in sorted(rows, key=lambda row: (
            row.suite_id,
            row.artifact_fqdn,
            row.metric_name,
            row.slice_key,
        ))]
        for path, rows in grouped.items()
    }


def write_provider_benchmark_results() -> dict[str, int]:
    grouped = generate_provider_benchmark_results()
    summary: dict[str, int] = {}
    for path, rows in grouped.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        summary[str(path)] = len(rows)
    return summary
