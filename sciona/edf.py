"""Minimal EDF channel reader for dependency-light evaluation paths."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np


def _parse_ascii_number(
    raw: bytes, cast: Callable[[str], float | int]
) -> float | int:
    return cast(raw.decode("ascii", errors="strict").strip())


def read_edf_channel(
    path: Path,
    channel: str,
    *,
    start_seconds: float,
    duration_seconds: float,
) -> tuple[np.ndarray, float]:
    """Read and calibrate one EDF channel without requiring an EDF library."""
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
            return [
                block[index * width : (index + 1) * width]
                for index in range(signal_count)
            ]

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
            raise ValueError(
                f"EDF channel {channel!r} not found; available={labels}"
            ) from exc

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
