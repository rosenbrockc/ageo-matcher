"""Pinned public datasets used by reproducible evaluation suites."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Literal
import urllib.request
import zipfile

import numpy as np
from pydantic import BaseModel, Field


class OpenDataFile(BaseModel):
    path: str
    url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)


class OpenDataArchive(BaseModel):
    kind: Literal["zip_rar"]
    member: str
    required_path: str


class OpenDataSource(BaseModel):
    source_id: str
    title: str
    landing_url: str
    license_spdx: str
    license_url: str
    citation: str
    files: list[OpenDataFile] = Field(min_length=1)
    archive: OpenDataArchive | None = None


class OpenDataRegistry(BaseModel):
    schema_version: str
    sources: list[OpenDataSource] = Field(min_length=1)


def load_open_data_registry(path: Path) -> OpenDataRegistry:
    registry = OpenDataRegistry.model_validate_json(path.read_text())
    identifiers = [source.source_id for source in registry.sources]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Open-data source identifiers must be unique")
    return registry


def default_open_data_cache() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    return Path(root).expanduser() / "sciona" / "evaluation-data" if root else Path.home() / ".cache" / "sciona" / "evaluation-data"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified(path: Path, declaration: OpenDataFile) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == declaration.size_bytes
        and _sha256(path) == declaration.sha256
    )


def _download(declaration: OpenDataFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=f".{destination.name}.", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            request = urllib.request.Request(
                declaration.url, headers={"User-Agent": "sciona-open-evaluation/1"}
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                shutil.copyfileobj(response, temporary)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
    if not _verified(temporary_path, declaration):
        temporary_path.unlink(missing_ok=True)
        raise ValueError(f"Downloaded file failed integrity validation: {declaration.url}")
    temporary_path.replace(destination)


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    destination_root = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target = (destination / member.filename).resolve()
            if destination_root not in target.parents and target != destination_root:
                raise ValueError(f"Unsafe ZIP member path: {member.filename}")
        package.extractall(destination)


def _extract_archive(source: OpenDataSource, root: Path) -> None:
    archive = source.archive
    if archive is None or (root / archive.required_path).is_file():
        return
    if archive.kind != "zip_rar":
        raise ValueError(f"Unsupported archive type: {archive.kind}")
    zip_path = root / source.files[0].path
    _safe_extract_zip(zip_path, root)
    rar_path = (root / archive.member).resolve()
    if not rar_path.is_file():
        raise FileNotFoundError(f"ZIP archive did not contain {archive.member!r}")
    bsdtar = shutil.which("bsdtar")
    if bsdtar is None:
        raise RuntimeError("Extracting the UCI GPS archive requires bsdtar")
    subprocess.run(
        [bsdtar, "-xf", str(rar_path)],
        cwd=root,
        check=True,
        timeout=60,
    )
    if not (root / archive.required_path).is_file():
        raise FileNotFoundError(
            f"Archive did not produce required file {archive.required_path!r}"
        )


def fetch_open_data(
    registry: OpenDataRegistry,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Path]:
    cache = cache_dir or default_open_data_cache()
    roots: dict[str, Path] = {}
    for source in registry.sources:
        root = cache / source.source_id
        root.mkdir(parents=True, exist_ok=True)
        for declaration in source.files:
            destination = root / declaration.path
            if not _verified(destination, declaration):
                destination.unlink(missing_ok=True)
                _download(declaration, destination)
        _extract_archive(source, root)
        roots[source.source_id] = root
    return roots


def read_wfdb_212_channel(
    root: Path,
    record: str,
    *,
    channel: int = 0,
    start_seconds: float = 0.0,
    duration_seconds: float | None = None,
) -> tuple[np.ndarray, float]:
    lines = [
        line.strip()
        for line in (root / f"{record}.hea").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    record_fields = lines[0].split()
    signal_count = int(record_fields[1])
    sampling_rate = float(record_fields[2].split("/", 1)[0])
    sample_count = int(record_fields[3])
    if signal_count != 2 or not 0 <= channel < signal_count:
        raise ValueError("The dependency-light WFDB reader supports two-channel records")
    signal_fields = [line.split() for line in lines[1 : 1 + signal_count]]
    if any(fields[1] != "212" for fields in signal_fields):
        raise ValueError("The dependency-light WFDB reader supports format 212 only")
    if len({fields[0] for fields in signal_fields}) != 1:
        raise ValueError("The dependency-light WFDB reader requires one shared data file")

    raw = np.frombuffer((root / signal_fields[0][0]).read_bytes(), dtype=np.uint8)
    frames = raw[: (raw.size // 3) * 3].reshape(-1, 3)
    first = frames[:, 0].astype(np.int16) | (
        (frames[:, 1] & 0x0F).astype(np.int16) << 8
    )
    second = (frames[:, 1].astype(np.int16) >> 4) | (
        frames[:, 2].astype(np.int16) << 4
    )
    first = np.where(first >= 2048, first - 4096, first)
    second = np.where(second >= 2048, second - 4096, second)
    digital = (first if channel == 0 else second)[:sample_count].astype(float)
    gain = float(signal_fields[channel][2].split("/", 1)[0])
    baseline = float(signal_fields[channel][4])
    physical = (digital - baseline) / gain
    start = max(0, int(round(start_seconds * sampling_rate)))
    stop = sample_count if duration_seconds is None else start + int(
        round(duration_seconds * sampling_rate)
    )
    return physical[start:stop], sampling_rate


_WFDB_BEAT_TYPES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 25, 30, 34, 35, 38, 41}


def read_wfdb_annotations(root: Path, record: str) -> np.ndarray:
    stream = io.BytesIO((root / f"{record}.atr").read_bytes())
    sample = 0
    beats: list[int] = []
    while True:
        pair = stream.read(2)
        if len(pair) < 2:
            break
        interval = pair[0] | ((pair[1] & 0x03) << 8)
        annotation_type = pair[1] >> 2
        if annotation_type == 0:
            break
        if annotation_type == 59:
            skip = stream.read(4)
            if len(skip) < 4:
                raise ValueError("WFDB annotation SKIP record is truncated")
            sample += int.from_bytes(skip, byteorder="little", signed=True)
            continue
        if annotation_type == 63:
            stream.seek(interval + (interval % 2), io.SEEK_CUR)
            continue
        if annotation_type >= 60:
            continue
        sample += interval
        if annotation_type in _WFDB_BEAT_TYPES:
            beats.append(sample)
    return np.asarray(beats, dtype=np.int64)


def read_dimacs_graph(path: Path):
    from scipy.sparse import csr_array

    node_count = 0
    rows: list[int] = []
    columns: list[int] = []
    weights: list[float] = []
    with path.open() as stream:
        for line in stream:
            if line.startswith("p "):
                fields = line.split()
                node_count = int(fields[2])
            elif line.startswith("a "):
                _, source, target, weight = line.split()
                rows.append(int(source) - 1)
                columns.append(int(target) - 1)
                weights.append(float(weight))
    if node_count <= 0 or not weights:
        raise ValueError(f"No DIMACS graph was found in {path}")
    return csr_array((weights, (rows, columns)), shape=(node_count, node_count))


def read_uci_gps_points(root: Path) -> list[dict[str, str]]:
    path = root / "GPS Trajectory" / "go_track_trackspoints.csv"
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))
