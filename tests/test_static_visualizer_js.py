import shutil
import subprocess
from pathlib import Path

import pytest

from sciona.architect.models import ConceptType


ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is required for static visualizer JS checks")
def test_static_visualizer_js_syntax():
    subprocess.run(
        [NODE, "frontend/scripts/check_static_visualizer.mjs"],
        cwd=ROOT,
        check=True,
    )


@pytest.mark.skipif(NODE is None, reason="node is required for static visualizer JS checks")
def test_static_visualizer_js_smoke():
    subprocess.run(
        [NODE, "--test", "frontend/tests/static_visualizer.test.mjs"],
        cwd=ROOT,
        check=True,
    )


def test_tabular_showcase_uses_valid_cdg_concept_types():
    app_source = (ROOT / "sciona" / "static" / "app.js").read_text()
    tabular_source = app_source.split("function buildTabularTutorialGraph", 1)[1].split(
        "var TUTORIAL_C_CDG", 1
    )[0]

    allowed = {member.value for member in ConceptType}
    declared = {
        fragment.split('"', 1)[0]
        for fragment in tabular_source.split('concept_type: "')[1:]
    }

    assert declared
    assert declared <= allowed
