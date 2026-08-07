"""Focused tests for the real-data ECG E2E harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np

from sciona.api.models import CatalogEntry, ProviderInstallInfo


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ecg_data_e2e_runtime.py"
SPEC = importlib.util.spec_from_file_location("ecg_data_e2e_runtime", SCRIPT)
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
            provider_id="sciona-atoms-signal",
            distribution_name="sciona-atoms-signal",
            distribution_version="1.0.0",
            install_requirement="sciona-atoms-signal==1.0.0",
            import_module=module,
            import_symbol=symbol,
        ),
    )


def test_select_candidate_uses_capability_text_not_fixed_fqdn() -> None:
    unrelated = _entry("vendor.signal.lowpass", "Low-pass a generic signal")
    matching = _entry(
        "vendor.cardiac.condition",
        "Apply a bandpass filter to an ECG signal waveform",
    )

    selected = RUNTIME._select_candidate(
        [unrelated, matching],
        required_terms=("bandpass", "filter"),
        preferred_terms=("ecg",),
    )

    assert selected is matching


def test_read_edf_channel_applies_calibration_and_window(tmp_path: Path) -> None:
    path = tmp_path / "sample.edf"
    signal_count = 2
    header_bytes = 256 + signal_count * 256

    def field(values: list[str], width: int) -> bytes:
        return b"".join(value.encode().ljust(width) for value in values)

    fixed = bytearray(b" " * 256)
    fixed[0:8] = b"0       "
    fixed[184:192] = str(header_bytes).encode().ljust(8)
    fixed[236:244] = b"2       "
    fixed[244:252] = b"1       "
    fixed[252:256] = b"2   "
    signals = b"".join(
        (
            field(["ECG", "Pulse"], 16),
            field(["", ""], 80),
            field(["mV", "bpm"], 8),
            field(["-1", "0"], 8),
            field(["1", "100"], 8),
            field(["-100", "0"], 8),
            field(["100", "100"], 8),
            field(["", ""], 80),
            field(["2", "1"], 8),
            field(["", ""], 32),
        )
    )
    records = b"".join(
        (
            np.array([-100, 0], dtype="<i2").tobytes(),
            np.array([50], dtype="<i2").tobytes(),
            np.array([100, 0], dtype="<i2").tobytes(),
            np.array([60], dtype="<i2").tobytes(),
        )
    )
    path.write_bytes(bytes(fixed) + signals + records)

    values, rate = RUNTIME._read_edf_channel(
        path, "ECG", start_seconds=0.5, duration_seconds=1.0
    )

    assert rate == 2.0
    assert np.allclose(values, [0.0, 1.0])
