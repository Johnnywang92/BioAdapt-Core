"""The shared harness fixtures for the closed-loop tests.

The import path is set once, in ``tests/conftest.py``; this file only wires the objects a finished run leaves
behind. ``harness_factory`` is per test because those tests each break the plan or the registry they run,
while ``carded`` is module-scoped because the follow-up chain it runs is the same run every reader in this
directory measures.
"""

import pytest

from driver_factories import Harness, harness

import card_factories


@pytest.fixture(scope="module")
def carded() -> card_factories.Carded:
    """The plan's whole product set, drafted. The robustness arm re-runs the chain, so one run is shared.

    Both suites that read a finished run ask for this: the card's contract parity and the metric rows the
    run can actually produce. Building it twice would let the two files measure different runs.
    """
    made = card_factories.carded_run()
    yield made
    made.harness.catalog.close()


@pytest.fixture
def harness_factory():
    """A factory fixture, so each test asks for the plan and the registry it means to break."""
    built: list[Harness] = []

    def make(**kwargs) -> Harness:
        made = harness(**kwargs)
        built.append(made)
        return made

    yield make
    for made in built:
        made.catalog.close()
