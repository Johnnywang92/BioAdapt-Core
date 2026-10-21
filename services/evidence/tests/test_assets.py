"""The asset reader: an index row, the bytes behind it, and the contract that says what they are.

Section 7.3 makes every number on a card traceable to a tool record, so the reader is the first place a
drift has to be caught: a row that names the wrong bytes, bytes that changed after they were indexed, or
two rows that both claim to answer one slot must stop the card rather than be picked between.
"""

from __future__ import annotations

import pytest

import evidence_factories as E
from bioadapt_evidence import INTERMEDIATE_SLOTS, SCHEMA_BY_SLOT, AssetReader, ContractDrift, MissingProduct
from bioadapt_evidence.assets import SLOT_CLUSTERS, SLOT_QC_FLAGS, barcodes_of, specimen_of
from bioadapt_schemas.entities import AssetLineage
from bioadapt_schemas.enums import AssetRole
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_schemas.plan import AnalysisPlan
from bioadapt_storage.objectstore import InMemoryObjectStore

CELLS = {
    E.cell_key("spc_donor_a", "AAAC"): "c000",
    E.cell_key("spc_donor_a", "TTGG"): "c000",
    E.cell_key("spc_donor_b", "GGCT"): "c001",
    E.cell_key("spc_donor_c", "CCAA"): "c001",
}


@pytest.fixture
def objects():
    return InMemoryObjectStore()


@pytest.fixture
def catalog(objects):
    made = E.catalog_with_run(objects)
    yield made
    made.close()


def write_raw(catalog, objects, *, slot, schema_version, payload, node_id="clustering", run_id=E.RUN_ID, **extra):
    return E.write_product(
        catalog,
        objects,
        slot=slot,
        schema_version=schema_version,
        payload=payload,
        node_id=node_id,
        run_id=run_id,
        **extra,
    )


def write_clusters(catalog, objects, *, labels=CELLS, schema_version=None, run_id=E.RUN_ID, **payload):
    return write_raw(
        catalog,
        objects,
        slot=SLOT_CLUSTERS,
        schema_version=schema_version or SCHEMA_BY_SLOT[SLOT_CLUSTERS],
        payload=E.clusters_payload(labels, **payload),
        run_id=run_id,
        cell_keys=list(labels),
    )


def write_qc(catalog, objects, *, cells=None, run_id=E.RUN_ID, **payload):
    return write_raw(
        catalog,
        objects,
        slot=SLOT_QC_FLAGS,
        schema_version=SCHEMA_BY_SLOT[SLOT_QC_FLAGS],
        payload=E.qc_payload(cells or {key: [] for key in CELLS}, **payload),
        node_id="qc",
        run_id=run_id,
        cell_keys=list(cells or CELLS),
    )


def reindex(catalog, asset_id: str, **columns) -> None:
    """Rewrite an index row in place, the way a bad migration or a hand-edited row would."""
    sets = ", ".join(f"{name} = ?" for name in columns)
    catalog.conn.execute(
        f"update data_asset set {sets} where project_id = ? and asset_id = ?",
        [*columns.values(), E.PROJECT_ID, asset_id],
    )


class TestReading:
    def test_a_product_is_read_by_the_slot_its_bytes_name(self, catalog, objects):
        clusters = write_clusters(catalog, objects)
        qc = write_qc(catalog, objects)
        reader = AssetReader(catalog, objects, run_id=E.RUN_ID)
        assert reader.slots() == (SLOT_CLUSTERS, "counts", SLOT_QC_FLAGS)
        assert reader.product(SLOT_CLUSTERS).asset_id == clusters
        assert reader.product(SLOT_QC_FLAGS).asset_id == qc
        assert reader.optional("stability") is None

    def test_rows_come_back_as_the_asset_stored_them(self, catalog, objects):
        write_clusters(catalog, objects)
        product = AssetReader(catalog, objects, run_id=E.RUN_ID).product(SLOT_CLUSTERS)
        assert {str(row["cell_key"]): str(row["cluster"]) for row in product.rows("labels")} == CELLS
        assert product.get("resolution") == 1.0

    def test_a_missing_slot_names_the_slots_the_run_has(self, catalog, objects):
        write_clusters(catalog, objects)
        with pytest.raises(MissingProduct) as raised:
            AssetReader(catalog, objects, run_id=E.RUN_ID).product("stability")
        assert SLOT_CLUSTERS in str(raised.value)

    def test_a_field_the_reader_needs_has_to_be_in_the_document(self, catalog, objects):
        write_raw(
            catalog,
            objects,
            slot=SLOT_CLUSTERS,
            schema_version=SCHEMA_BY_SLOT[SLOT_CLUSTERS],
            payload={"labels": [{"cell_key": key, "cluster": name} for key, name in CELLS.items()]},
        )
        product = AssetReader(catalog, objects, run_id=E.RUN_ID).product(SLOT_CLUSTERS)
        with pytest.raises(ContractDrift, match="does not carry 'resolution'"):
            product.get("resolution")

    def test_a_table_field_has_to_be_a_table(self, catalog, objects):
        write_raw(
            catalog,
            objects,
            slot=SLOT_CLUSTERS,
            schema_version=SCHEMA_BY_SLOT[SLOT_CLUSTERS],
            payload={"labels": {"c000": 2}},
        )
        product = AssetReader(catalog, objects, run_id=E.RUN_ID).product(SLOT_CLUSTERS)
        with pytest.raises(ContractDrift, match="not a table of rows"):
            product.rows("labels")


class TestRunAttribution:
    def test_a_root_input_is_found_through_the_manifest_not_through_a_producing_run(
        self, catalog, objects
    ):
        """Section 5.2: an uploaded asset is not attributable to a run, so only the manifest ties it to one."""
        root = next(iter(catalog.get_run(E.RUN_ID).input_hashes))
        assert catalog.get_asset(root).created_by_run_id is None
        reader = AssetReader(catalog, objects, run_id=E.RUN_ID)
        assert reader.product("counts").asset_id == root
        with pytest.raises(MissingProduct, match="no output lineage"):
            reader.tool_of(root)

    def test_an_asset_a_caller_names_explicitly_is_read_without_belonging_to_the_run(self, catalog, objects):
        """A reference product another run made is readable when the caller names it (§5.8 reuse)."""
        qc = write_qc(catalog, objects, run_id="run_reference")
        reader = AssetReader(catalog, objects, run_id=E.RUN_ID, extra_asset_ids=[qc])
        assert reader.product(SLOT_QC_FLAGS).asset_id == qc

    def test_a_product_from_another_run_is_not_read_as_this_runs_answer(self, catalog, objects):
        write_clusters(catalog, objects)
        write_qc(catalog, objects, run_id="run_other")
        assert AssetReader(catalog, objects, run_id=E.RUN_ID).slots() == (SLOT_CLUSTERS, "counts")


class TestSlotResolution:
    """One slot has one answer, and the reader's job is to find it without inventing one."""

    def test_naming_one_of_the_two_assets_a_slot_has_is_the_end_of_the_argument(self, catalog, objects):
        """The drift tells the caller to name one; a refusal that survived being named would be advice."""
        write_clusters(catalog, objects)
        second = write_clusters(
            catalog, objects, labels={**CELLS, E.cell_key("spc_donor_a", "GGGG"): "c002"}
        )
        with pytest.raises(ContractDrift, match="indexes both"):
            AssetReader(catalog, objects, run_id=E.RUN_ID)
        reader = AssetReader(catalog, objects, run_id=E.RUN_ID, extra_asset_ids=[second])
        assert reader.product(SLOT_CLUSTERS).asset_id == second

    def test_naming_two_assets_for_one_slot_leaves_the_reader_no_answer_to_cite(self, catalog, objects):
        first = write_clusters(catalog, objects)
        second = write_clusters(catalog, objects, labels={**CELLS, E.cell_key("spc_donor_a", "GGGG"): "c002"})
        with pytest.raises(ContractDrift, match="was named twice"):
            AssetReader(catalog, objects, run_id=E.RUN_ID, extra_asset_ids=[first, second])

    def test_the_run_answers_its_counts_slot_and_the_upload_is_set_aside(self, catalog, objects):
        """§5.2: a QC step publishes a filtered matrix over the uploaded one, and a card cites the run's."""
        root = next(iter(catalog.get_run(E.RUN_ID).input_hashes))
        produced = write_raw(
            catalog,
            objects,
            slot="counts",
            schema_version=SCHEMA_BY_SLOT["counts"],
            payload={"matrix": {"shape": [3, 3]}, "transform": "counts", "parameters": {"cells_kept": 3}},
            node_id="qc",
        )
        assert AssetReader(catalog, objects, run_id=E.RUN_ID).product("counts").asset_id == produced
        named = AssetReader(catalog, objects, run_id=E.RUN_ID, extra_asset_ids=[root])
        assert named.product("counts").asset_id == root, "the caller can still ask for the upload"

    def test_an_asset_a_caller_names_is_verified_before_it_is_allowed_to_answer(self, catalog, objects):
        qc = write_qc(catalog, objects, run_id="run_reference")
        objects.delete(catalog.get_asset(qc).uri[len("s3://") :])
        with pytest.raises(MissingProduct, match="its object is gone"):
            AssetReader(catalog, objects, run_id=E.RUN_ID, extra_asset_ids=[qc])


class TestIntermediates:
    """A product no card cites is still an index row, so it is still checked - and still not an answer."""

    def embedded(self, catalog, objects, **payload):
        return write_raw(
            catalog,
            objects,
            slot="embedding",
            schema_version=INTERMEDIATE_SLOTS["embedding"],
            payload={"n_components": 2, "seed": 0, **payload},
        )

    def test_an_intermediate_is_read_but_names_no_slot_a_card_can_cite(self, catalog, objects):
        write_clusters(catalog, objects)
        self.embedded(catalog, objects)
        reader = AssetReader(catalog, objects, run_id=E.RUN_ID)
        assert reader.slots() == (SLOT_CLUSTERS, "counts")
        with pytest.raises(MissingProduct, match="produced no 'embedding'"):
            reader.product("embedding")

    def test_an_intermediate_whose_bytes_moved_stops_the_card(self, catalog, objects):
        write_clusters(catalog, objects)
        embedding = self.embedded(catalog, objects)
        reindex(catalog, embedding, schema_version="embedding_v2")
        with pytest.raises(ContractDrift, match="is indexed as 'embedding_v2'"):
            AssetReader(catalog, objects, run_id=E.RUN_ID)


class TestDrift:
    def test_bytes_that_changed_after_indexing_are_refused(self, catalog, objects):
        asset_id = write_clusters(catalog, objects)
        uri = catalog.get_asset(asset_id).uri
        objects.put(
            uri[len("s3://") :],
            E.product_document(SLOT_CLUSTERS, SCHEMA_BY_SLOT[SLOT_CLUSTERS], E.clusters_payload(CELLS, resolution=2.0)),
        )
        with pytest.raises(ContractDrift, match="does not match the indexed"):
            AssetReader(catalog, objects, run_id=E.RUN_ID)

    def test_an_object_that_is_gone_leaves_the_index_row_unreadable(self, catalog, objects):
        asset_id = write_clusters(catalog, objects)
        objects.delete(catalog.get_asset(asset_id).uri[len("s3://") :])
        with pytest.raises(MissingProduct, match="its object is gone"):
            AssetReader(catalog, objects, run_id=E.RUN_ID)

    def test_bytes_that_declare_another_contract_than_the_row_names_are_refused(self, catalog, objects):
        """The version is agreed, never inferred from the shape of the bytes (section 4.1)."""
        asset_id = write_clusters(catalog, objects)
        raw = E.product_document(SLOT_CLUSTERS, "cluster_labels_v2", E.clusters_payload(CELLS))
        objects.put(catalog.get_asset(asset_id).uri[len("s3://") :], raw)
        reindex(catalog, asset_id, file_sha256=sha256_hex(raw), schema_version="cluster_labels_v2")
        #: The row and the bytes now agree on a contract this reader does not know how to read.
        with pytest.raises(ContractDrift, match="is read as"):
            AssetReader(catalog, objects, run_id=E.RUN_ID)

    def test_a_row_indexed_under_a_contract_the_bytes_do_not_agree_with_is_refused(self, catalog, objects):
        asset_id = write_clusters(catalog, objects)
        reindex(catalog, asset_id, schema_version="cluster_labels_v0")
        with pytest.raises(ContractDrift, match="its bytes declare"):
            AssetReader(catalog, objects, run_id=E.RUN_ID)

    def test_a_slot_this_service_cannot_read_is_refused(self, catalog, objects):
        write_raw(
            catalog,
            objects,
            slot="spatial_bins",
            schema_version="spatial_bins_v1",
            payload={"bins": []},
        )
        with pytest.raises(ContractDrift, match="not a product this service knows how to read"):
            AssetReader(catalog, objects, run_id=E.RUN_ID)

    def test_two_assets_naming_one_slot_are_a_drift_not_a_choice(self, catalog, objects):
        first = write_clusters(catalog, objects)
        second = write_clusters(catalog, objects, labels={**CELLS, E.cell_key("spc_donor_a", "GGGG"): "c002"})
        assert first != second
        with pytest.raises(ContractDrift, match="indexes both"):
            AssetReader(catalog, objects, run_id=E.RUN_ID)

    def test_a_non_platform_uri_is_refused(self, catalog, objects):
        asset_id = write_clusters(catalog, objects)
        reindex(catalog, asset_id, uri="file:///tmp/clusters.json")
        with pytest.raises(ContractDrift, match="non-platform URI"):
            AssetReader(catalog, objects, run_id=E.RUN_ID)


class TestToolAttribution:
    def test_a_produced_asset_is_credited_to_the_node_and_the_version_the_plan_pinned(self, catalog, objects):
        asset_id = write_clusters(catalog, objects)
        node_id, tool, version = AssetReader(catalog, objects, run_id=E.RUN_ID).tool_of(asset_id)
        assert (node_id, tool, version) == ("clustering", "scrnaseq_cluster", "1.0.0")

    def test_attributing_an_asset_to_a_node_the_pinned_plan_does_not_have_is_a_drift(self, catalog, objects):
        asset_id = write_clusters(catalog, objects)
        pinned = E.plan()
        replacement = AnalysisPlan.model_validate(
            {
                **pinned.model_dump(),
                "plan_version": "2",
                "nodes": [n.model_dump() for n in pinned.nodes if n.node_id != "clustering"],
            }
        )
        catalog.record_plan(replacement, created_by=E.ANALYST)
        catalog.set_run_plan(
            E.RUN_ID, plan_version="2", tool_versions={n.tool_name: n.tool_version for n in replacement.nodes}
        )
        with pytest.raises(ContractDrift, match="does not contain"):
            AssetReader(catalog, objects, run_id=E.RUN_ID).tool_of(asset_id)

    def test_an_asset_indexed_only_as_an_input_credits_no_tool(self, catalog, objects):
        """A node consumed the asset; nothing in this run produced it, so no tool can be credited for it."""
        asset_id = write_raw(
            catalog,
            objects,
            slot=SLOT_CLUSTERS,
            schema_version=SCHEMA_BY_SLOT[SLOT_CLUSTERS],
            payload=E.clusters_payload({**CELLS, E.cell_key("spc_donor_b", "TTTT"): "c002"}),
            run_id="",
        )
        catalog.record_lineage(
            AssetLineage(run_id=E.RUN_ID, node_id="markers", asset_id=asset_id, role=AssetRole.INPUT, ordinal=0)
        )
        with pytest.raises(MissingProduct, match="no output lineage"):
            AssetReader(catalog, objects, run_id=E.RUN_ID).tool_of(asset_id)

    def test_lineage_from_another_run_does_not_credit_this_run(self, catalog, objects):
        asset_id = write_clusters(catalog, objects, run_id="run_other")
        catalog.record_lineage(
            AssetLineage(run_id=E.RUN_ID, node_id="markers", asset_id=asset_id, role=AssetRole.INPUT, ordinal=0)
        )
        with pytest.raises(MissingProduct, match="no output lineage"):
            AssetReader(catalog, objects, run_id=E.RUN_ID).tool_of(asset_id)


class TestCellKeys:
    def test_a_canonical_key_yields_its_specimen_and_barcode(self):
        key = E.cell_key("spc_donor_a", "AAAC")
        assert specimen_of(key) == "spc_donor_a"
        assert list(barcodes_of([key])) == ["aaac"]

    def test_a_key_that_is_not_the_canonical_form_is_refused(self):
        """Section 5.6's partition rules read the specimen out of the key, so a loose key cannot be trusted."""
        with pytest.raises(ContractDrift, match="not the canonical"):
            specimen_of("spc_donor_a/AAAC")
