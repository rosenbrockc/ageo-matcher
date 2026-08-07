#!/usr/bin/env python3
"""Exercise catalog-discovered ECG atoms against one real EDF recording."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import inspect
import json
from pathlib import Path
from typing import Callable

import numpy as np

from sciona.api.models import CatalogEntry
from sciona.provider_runtime import ProviderInstaller, RemoteCatalogClient


def _parse_ascii_number(raw: bytes, cast: Callable[[str], float | int]) -> float | int:
    return cast(raw.decode("ascii", errors="strict").strip())


def _read_edf_channel(
    path: Path,
    channel: str,
    *,
    start_seconds: float,
    duration_seconds: float,
) -> tuple[np.ndarray, float]:
    """Read one EDF channel without adding an EDF library to the cold runtime."""
    with path.open("rb") as stream:
        fixed = stream.read(256)
        if len(fixed) != 256:
            raise ValueError(f"EDF header is truncated: {path}")
        header_bytes = int(_parse_ascii_number(fixed[184:192], int))
        record_count = int(_parse_ascii_number(fixed[236:244], int))
        record_duration = float(_parse_ascii_number(fixed[244:252], float))
        signal_count = int(_parse_ascii_number(fixed[252:256], int))
        if record_count <= 0 or record_duration <= 0 or signal_count <= 0:
            raise ValueError("EDF has invalid record metadata")

        signal_header = stream.read(header_bytes - 256)
        if len(signal_header) != header_bytes - 256:
            raise ValueError("EDF signal header is truncated")

        cursor = 0

        def fields(width: int) -> list[bytes]:
            nonlocal cursor
            end = cursor + width * signal_count
            block = signal_header[cursor:end]
            cursor = end
            return [block[index * width : (index + 1) * width] for index in range(signal_count)]

        labels = [value.decode("ascii").strip() for value in fields(16)]
        fields(80)  # transducer
        fields(8)  # physical dimension
        physical_min = [float(_parse_ascii_number(value, float)) for value in fields(8)]
        physical_max = [float(_parse_ascii_number(value, float)) for value in fields(8)]
        digital_min = [float(_parse_ascii_number(value, float)) for value in fields(8)]
        digital_max = [float(_parse_ascii_number(value, float)) for value in fields(8)]
        fields(80)  # prefilter
        samples_per_record = [int(_parse_ascii_number(value, int)) for value in fields(8)]
        fields(32)  # reserved

        try:
            channel_index = labels.index(channel)
        except ValueError as exc:
            raise ValueError(f"EDF channel {channel!r} not found; available={labels}") from exc

        samples = samples_per_record[channel_index]
        sampling_rate = samples / record_duration
        first_record = max(0, int(start_seconds // record_duration))
        final_record = min(
            record_count,
            int(np.ceil((start_seconds + duration_seconds) / record_duration)),
        )
        record_bytes = 2 * sum(samples_per_record)
        channel_offset = 2 * sum(samples_per_record[:channel_index])
        chunks: list[np.ndarray] = []
        for record_index in range(first_record, final_record):
            stream.seek(header_bytes + record_index * record_bytes + channel_offset)
            raw = np.frombuffer(stream.read(samples * 2), dtype="<i2").astype(float)
            if raw.size != samples:
                raise ValueError("EDF data record is truncated")
            chunks.append(raw)

    values = np.concatenate(chunks) if chunks else np.array([], dtype=float)
    scale = (physical_max[channel_index] - physical_min[channel_index]) / (
        digital_max[channel_index] - digital_min[channel_index]
    )
    values = (values - digital_min[channel_index]) * scale + physical_min[channel_index]
    trim_start = int(round((start_seconds - first_record * record_duration) * sampling_rate))
    trim_count = int(round(duration_seconds * sampling_rate))
    return values[trim_start : trim_start + trim_count], sampling_rate


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
        provider = candidate.provider
        if provider is None or not all(term in text for term in required_terms):
            continue
        score = sum(term in text for term in preferred_terms)
        eligible.append((score, -rank, candidate))
    if not eligible:
        returned = [candidate.fqdn for candidate in candidates]
        raise LookupError(
            f"No installable candidate contained terms {required_terms}; returned={returned}"
        )
    return max(eligible, key=lambda item: (item[0], item[1]))[2]


async def _discover(client: RemoteCatalogClient) -> tuple[dict[str, CatalogEntry], dict[str, list[str]]]:
    searches = {
        "filter": "ECG bandpass filter",
        "peaks": "ECG R peak detection",
        "rate": "heart rate computation from R peaks",
    }
    returned: dict[str, list[str]] = {}
    results: dict[str, list[CatalogEntry]] = {}
    for role, query in searches.items():
        candidates = await client.search(query, limit=40)
        results[role] = candidates
        returned[role] = [candidate.fqdn for candidate in candidates[:10]]
    selected = {
        "filter": _select_candidate(
            results["filter"],
            required_terms=("bandpass", "filter"),
            preferred_terms=("ecg", "waveform"),
        ),
        "peaks": _select_candidate(
            results["peaks"],
            required_terms=("peak",),
            preferred_terms=("ecg", "detect", "r peak"),
        ),
        "rate": _select_candidate(
            results["rate"],
            required_terms=("heart", "rate"),
            preferred_terms=("comput", "peak", "ecg"),
        ),
    }
    return selected, returned


def _assert_signature(role: str, function: Callable[..., object]) -> None:
    parameters = inspect.signature(function).parameters
    input_options = {
        "filter": ("signal",),
        "peaks": ("conditioned_signal", "filtered", "signal"),
        "rate": ("signal", "rpeaks"),
    }[role]
    if "sampling_rate" not in parameters or not any(name in parameters for name in input_options):
        raise TypeError(f"Selected {role} candidate has incompatible signature: {parameters}")


def _provider_installed(candidate: CatalogEntry) -> bool:
    assert candidate.provider is not None
    try:
        importlib.metadata.version(candidate.provider.distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


async def run(args: argparse.Namespace) -> dict[str, object]:
    client = RemoteCatalogClient(args.api_url)
    selected, returned = await _discover(client)
    providers = {candidate.provider.provider_id for candidate in selected.values() if candidate.provider}
    if len(providers) != 1:
        raise RuntimeError(f"ECG pipeline crossed unexpected provider boundaries: {providers}")
    if any(_provider_installed(candidate) for candidate in selected.values()):
        raise RuntimeError("ECG provider was already installed in the cold environment")

    installer = ProviderInstaller()
    functions: dict[str, Callable[..., object]] = {}
    for role in ("filter", "peaks", "rate"):
        functions[role] = installer.materialize(selected[role])
        _assert_signature(role, functions[role])

    signal, sampling_rate = _read_edf_channel(
        args.edf,
        args.ecg_channel,
        start_seconds=args.start_seconds,
        duration_seconds=args.duration_seconds,
    )
    reference, reference_rate = _read_edf_channel(
        args.edf,
        args.reference_channel,
        start_seconds=args.start_seconds,
        duration_seconds=args.duration_seconds,
    )
    filtered = np.asarray(functions["filter"](signal, sampling_rate=sampling_rate))
    peaks = np.asarray(functions["peaks"](filtered, sampling_rate=sampling_rate), dtype=int)
    rate_parameters = inspect.signature(functions["rate"]).parameters
    if "rpeaks" in rate_parameters:
        rate_indices, estimated = functions["rate"](peaks, sampling_rate=sampling_rate)
    else:
        rate_peaks, rate_indices, estimated = functions["rate"](
            signal, sampling_rate=sampling_rate
        )
        rate_peaks = np.asarray(rate_peaks, dtype=int)
        if rate_peaks.size < 10:
            raise AssertionError("Selected rate atom did not detect enough events")
    rate_indices = np.asarray(rate_indices, dtype=float)
    estimated = np.asarray(estimated, dtype=float)

    valid_reference = np.isfinite(reference) & (reference >= 30.0) & (reference <= 220.0)
    if estimated.size < 10 or valid_reference.sum() < 10:
        raise AssertionError("ECG execution did not produce enough rate/reference samples")
    reference_times = np.arange(reference.size, dtype=float) / reference_rate
    estimate_times = rate_indices / sampling_rate
    aligned_reference = np.interp(estimate_times, reference_times, reference)
    valid = np.isfinite(estimated) & (estimated >= 30.0) & (estimated <= 220.0)
    valid &= np.isfinite(aligned_reference) & (aligned_reference >= 30.0) & (aligned_reference <= 220.0)
    mae = float(np.mean(np.abs(estimated[valid] - aligned_reference[valid])))
    median_error = float(abs(np.median(estimated[valid]) - np.median(aligned_reference[valid])))
    if valid.sum() < 10 or mae > args.max_mae or median_error > args.max_median_error:
        raise AssertionError(
            f"ECG rate failed reference bounds: samples={valid.sum()}, mae={mae:.3f}, "
            f"median_error={median_error:.3f}"
        )

    return {
        "status": "passed",
        "edf": str(args.edf),
        "window": {"start_seconds": args.start_seconds, "duration_seconds": args.duration_seconds},
        "channels": {"ecg": args.ecg_channel, "reference": args.reference_channel},
        "sampling_rate_hz": sampling_rate,
        "selected": {role: candidate.fqdn for role, candidate in selected.items()},
        "search_top_10": returned,
        "provider": next(iter(providers)),
        "r_peak_count": int(peaks.size),
        "comparison_count": int(valid.sum()),
        "heart_rate_mae_bpm": mae,
        "heart_rate_median_error_bpm": median_error,
        "estimated_median_bpm": float(np.median(estimated[valid])),
        "reference_median_bpm": float(np.median(aligned_reference[valid])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--edf", type=Path, required=True)
    parser.add_argument("--ecg-channel", default="ECG1-ECG2")
    parser.add_argument("--reference-channel", default="Pulse")
    parser.add_argument("--start-seconds", type=float, default=3600.0)
    parser.add_argument("--duration-seconds", type=float, default=300.0)
    parser.add_argument("--max-mae", type=float, default=12.0)
    parser.add_argument("--max-median-error", type=float, default=8.0)
    args = parser.parse_args()
    if not args.edf.is_file():
        parser.error(f"EDF file does not exist: {args.edf}")
    print(json.dumps(asyncio.run(run(args)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
