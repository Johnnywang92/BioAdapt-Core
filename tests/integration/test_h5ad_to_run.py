"""From an h5ad-shaped file to a finished run - the path README §6.4 said did not exist (§4.1, §4.4).

``anndata`` is not installed here and this test does not need it: the adapter is duck-typed, so the
object built below is what ``read_h5ad`` would hand back, and everything after it is the platform's own
code - the admitted row, the upload-shaped staging, the five checks a node makes on its input, the
chain. What is insisted on is that the door does not change the room. Counts that arrive as a converted
file and counts that arrive as a product produce the same downstream assets, digest for digest, and a
file that declares the wrong state is stopped by the node that would have misread it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dataset import synthetic_dataset

import driver_factories as F
from bioadapt_schemas.identifiers import CELL_KEY_SEPARATOR
from bioadapt_scrnaseq import SLOT_CLUSTERS, SLOT_COUNTS, SLOT_EXPRESSION, SLOT_QC_FLAGS, write_asset
from bioadapt_scrnaseq.admission import admit
from bioadapt_scrnaseq.runner import URI_SCHEME
from bioadapt_scrnaseq.scanpy_adapter import DEFAULT_OBS_KEYS, from_anndata


def as_anndata(data: Any) -> SimpleNamespace:
    """The block anndata would return for this dataset: X, obs, var, and the barcodes it read."""
    matrix = data.matrix
    barcodes = [cell.cell_key.partition(CELL_KEY_SEPARATOR)[2] for cell in matrix.cells]
    return SimpleNamespace(
        X=[[matrix.row(cell.cell_key).get(gene, 0.0) for gene in matrix.genes] for cell in matrix.cells],
        obs={
            "specimen_id": [cell.specimen_id for cell in matrix.cells],
            "donor_id": [cell.donor_id for cell in matrix.cells],
            "group": [cell.group for cell in matrix.cells],
        },
        var={"index": list(matrix.genes)},
        obs_names=barcodes,
    )


def admitted(made: F.Harness, **kwargs: Any):
    data = made.data
    read = from_anndata(as_anndata(data), genome=data.matrix.genome, **kwargs)
    return admit(read, project_id=F.PROJECT_ID, created_at=made.clock(), obs_keys=DEFAULT_OBS_KEYS)


def register(made: F.Harness, admission: Any) -> None:
    """Put the object, then index its row: the two halves the upload routes split across three calls."""
    made.objects.put(admission.asset.uri[len(URI_SCHEME) :], admission.payload)
    made.catalog.record_asset(admission.asset)


def test_a_converted_file_runs_the_chain_to_the_same_assets_as_a_product(harness_factory: Any) -> None:
    """The run's conclusions do not depend on which door the counts came in through."""
    data = synthetic_dataset()
    baseline = harness_factory(data=data)
    first = baseline.run()
    assert first.completed, first.stopped_reason

    made = harness_factory(data=data)
    admission = admitted(made)
    register(made, admission)
    made.root[SLOT_COUNTS] = admission.asset_id
    second = made.run()
    assert second.completed, second.stopped_reason
    for slot in (SLOT_QC_FLAGS, SLOT_EXPRESSION, SLOT_CLUSTERS):
        assert F.digest(made.catalog, second.assets[slot]) == F.digest(baseline.catalog, first.assets[slot])
    consumed = {
        row.node_id for row in made.catalog.lineage(admission.asset_id) if row.role.value == "input"
    }
    assert "qc" in consumed, "the counts node read the converted bytes as the plan's counts input"


def test_the_admitted_row_is_the_id_the_platform_would_have_minted_for_those_bytes(
    harness_factory: Any,
) -> None:
    """Nothing about a platform asset depends on who produced it (§5.2), converter included."""
    made = harness_factory()
    admission = admitted(made)
    staged = write_asset(
        made.catalog,
        made.objects,
        from_anndata(as_anndata(made.data), genome=made.data.matrix.genome).product,
        project_id=F.PROJECT_ID,
        defaults=F.ASSET_DEFAULTS,
        created_at=made.clock(),
    )
    assert staged == admission.asset_id == admission.asset.asset_id
    assert admission.asset.format == "json" and admission.asset.schema_version == "expression_v1"


def test_a_file_that_declares_the_wrong_state_is_stopped_by_the_node_that_would_misread_it(
    harness_factory: Any,
) -> None:
    """``--transform`` is a declaration the driver enforces, not a label: DE sums counts (§4.1)."""
    made = harness_factory()
    admission = admitted(made, transform="log2_cpm")
    register(made, admission)
    made.root[SLOT_COUNTS] = admission.asset_id
    stopped = made.run()
    assert not stopped.completed and stopped.stopped_at == "qc"
    assert "sums or measures raw counts" in stopped.stopped_reason
    assert "is 'log2_cpm'" in stopped.stopped_reason
