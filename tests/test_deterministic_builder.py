from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sciona.deterministic_builder import build_catalog_artifact


class _Client:
    async def artifact_document(self, fqdn: str):
        assert fqdn == "cdg.test.rate"
        return {
            "io_specs": [
                {"direction": "input", "name": "signal", "ordinal": 0},
                {"direction": "input", "name": "sampling_rate", "ordinal": 1},
                {"direction": "output", "name": "rate", "ordinal": 0},
            ],
            "cdg_nodes": [
                {"node_id": "filter"},
                {"node_id": "detect"},
                {"node_id": "rate"},
            ],
            "cdg_edges": [
                {
                    "source_id": "filter",
                    "target_id": "detect",
                    "output_name": "filtered",
                    "input_name": "filtered",
                },
                {
                    "source_id": "detect",
                    "target_id": "rate",
                    "output_name": "events",
                    "input_name": "events",
                },
            ],
            "cdg_bindings": [
                {"node_id": "filter", "bound_artifact_fqdn": "pkg.filter"},
                {"node_id": "detect", "bound_artifact_fqdn": "pkg.detect"},
                {"node_id": "rate", "bound_artifact_fqdn": "pkg.rate"},
            ],
        }

    async def find(self, fqdn: str):
        return SimpleNamespace(fqdn=fqdn)


class _Installer:
    def materialize(self, candidate):
        def filter_atom(signal, *, sampling_rate):
            return np.asarray(signal) + sampling_rate * 0.0

        def detect_atom(filtered, *, sampling_rate):
            return np.flatnonzero(np.asarray(filtered) > 0)

        def rate_atom(events, *, sampling_rate):
            events = np.asarray(events)
            return events[1:], 60.0 * sampling_rate / np.diff(events)

        return {
            "pkg.filter": filter_atom,
            "pkg.detect": detect_atom,
            "pkg.rate": rate_atom,
        }[candidate.fqdn]


@pytest.mark.asyncio
async def test_build_catalog_artifact_emits_executable_composition(tmp_path: Path) -> None:
    output = tmp_path / "solution.py"
    result = await build_catalog_artifact(
        client=_Client(),
        artifact_fqdn="cdg.test.rate",
        output_path=output,
        function_name="estimate_rate",
        installer=_Installer(),
    )

    assert result.selected_fqdns == ("pkg.detect", "pkg.filter", "pkg.rate")
    source = output.read_text()
    # Replace imports because this unit test supplies in-memory callables.
    source = source.replace(
        "from pkg import detect as _atom_0\n"
        "from pkg import filter as _atom_1\n"
        "from pkg import rate as _atom_2",
        "_atom_0 = detect_atom\n_atom_1 = filter_atom\n_atom_2 = rate_atom",
    )
    namespace = {
        "detect_atom": _Installer().materialize(SimpleNamespace(fqdn="pkg.detect")),
        "filter_atom": _Installer().materialize(SimpleNamespace(fqdn="pkg.filter")),
        "rate_atom": _Installer().materialize(SimpleNamespace(fqdn="pkg.rate")),
    }
    exec(compile(source, str(output), "exec"), namespace)
    indices, rates = namespace["estimate_rate"](
        np.array([1.0, 0.0, 1.0, 0.0, 1.0]), 2.0
    )
    np.testing.assert_array_equal(indices, np.array([2, 4]))
    np.testing.assert_allclose(rates, np.array([60.0, 60.0]))
