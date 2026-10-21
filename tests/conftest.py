"""The root test tree's import path, so one fixture is written once and read everywhere.

§9.5 keeps the layers apart: workflows and services depend only on packages, and this tree is the plan's own -
the place where the layers are joined and proved to still speak to each other. Joining them means a root test
has to reach the fixture a layer already wrote, because a second copy of a fixture is a weaker copy of the
thing the layer tested. So each layer's test directory is put on the import path here rather than re-created
per file: the scRNA-seq workflow's synthetic dataset and stage-0 parameter sets, the policy factories the
red-team corpus attacks through, the registry and evidence fixtures it replays against.

A bare-name import therefore resolves to the layer's own module. Where two layers would need the same module
name, the closer directory is searched first because pytest puts a test file's own directory ahead of this
table.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Every layer whose tests carry fixtures the root tree reads instead of copies.
FACTORY_DIRS = (
    "workflows/scrnaseq/tests",
    "packages/policies/tests",
    "packages/evaluation/tests",
    "packages/tool_registry/tests",
    "services/evidence/tests",
    "tests/integration",
    "tests/redteam",
)

for _relative in FACTORY_DIRS:
    _path = str(ROOT / _relative)
    if _path not in sys.path:
        sys.path.insert(0, _path)
