"""Reading the run's stored products (plan V2.0 sections 5.2, 5.6, 7.3, 9.7).

A card is built from what the tools measured, so this service reads the same immutable bytes a reviewer
would open: it goes to the index, fetches the object behind the URI, checks the digest, and only then
parses. Nothing here recomputes a verdict - the products store measurements and the criteria they were
measured against, and ``cards`` derives what they mean.

The slot and schema-version tables below are stated again rather than imported from the workflow that
writes them. That duplication is the point: it is the consumer side of the asset contract, and the root
integration test proves the two sides still name the same things.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional, Sequence

from bioadapt_schemas.entities import DataAsset
from bioadapt_schemas.enums import AssetRole
from bioadapt_schemas.identifiers import CELL_KEY_SEPARATOR, sha256_hex
from bioadapt_storage.catalog import Catalog
from bioadapt_storage.objectstore import ObjectStore

from .errors import ContractDrift, MissingProduct

#: Section 5.9's key layout: an asset URI is the object store's own address.
URI_SCHEME = "s3://"

SLOT_COUNTS = "counts"
SLOT_EXPRESSION = "expression"
SLOT_QC_FLAGS = "qc_flags"
SLOT_CLUSTERS = "clusters"
SLOT_MARKERS = "markers"
SLOT_DE_TABLE = "de_table"
SLOT_STABILITY = "stability"
SLOT_COVERAGE = "coverage"
SLOT_STATE_AXIS = "state_axis"

#: What each product contract is called, as the writing side declares it (section 4.1: a contract name
#: is agreed, never inferred from the shape of the bytes).
SCHEMA_BY_SLOT: Mapping[str, str] = {
    SLOT_COUNTS: "expression_v1",
    SLOT_EXPRESSION: "expression_v1",
    SLOT_QC_FLAGS: "qc_flags_v1",
    SLOT_CLUSTERS: "cluster_labels_v1",
    SLOT_MARKERS: "markers_v1",
    SLOT_DE_TABLE: "de_table_v1",
    SLOT_STABILITY: "stability_v1",
    SLOT_COVERAGE: "coverage_v1",
    SLOT_STATE_AXIS: "state_axis_v1",
}

#: The intermediates a finished run indexes because a later node reads them: no card cites them, but they
#: are named contracts, so their index rows and digests are still verified rather than skipped. A slot in
#: neither table is a product whose contract nobody agreed on, and that stays a refusal (§4.1).
INTERMEDIATE_SLOTS: Mapping[str, str] = {
    "features": "feature_selection_v1",
    "embedding": "embedding_v1",
    "graph": "neighborhood_graph_v1",
}


def specimen_of(cell_key: str) -> str:
    """The specimen a canonical cell key came from (section 5.6), which is how partitions are recovered."""
    specimen, separator, _barcode = cell_key.partition(CELL_KEY_SEPARATOR)
    if not separator:
        raise ContractDrift(f"cell key {cell_key!r} is not the canonical specimen+barcode form")
    return specimen


def barcodes_of(cell_keys: Sequence[str]) -> Iterator[str]:
    for key in cell_keys:
        yield key.partition(CELL_KEY_SEPARATOR)[2]


@dataclass(frozen=True)
class StoredProduct:
    """One asset's index row and the parsed document it points at, verified to be the same thing."""

    asset: DataAsset
    document: Mapping[str, Any]

    @property
    def slot(self) -> str:
        return str(self.document["slot"])

    @property
    def asset_id(self) -> str:
        return self.asset.asset_id

    @property
    def schema_version(self) -> str:
        return self.asset.schema_version

    def get(self, name: str) -> Any:
        try:
            return self.document[name]
        except KeyError:
            raise ContractDrift(
                f"{self.slot} asset {self.asset_id} does not carry {name!r}; the stored contract and the "
                "reader disagree"
            ) from None

    def rows(self, name: str) -> list[dict[str, Any]]:
        value = self.get(name)
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            raise ContractDrift(f"{self.slot} asset {self.asset_id}: {name!r} is not a table of rows")
        return value


class AssetReader:
    """The run's products, keyed by the slot each one answers for.

    An intermediate a card never cites is verified and set aside, so ``slots()`` names only the products a
    card can cite: a run that indexes an embedding has not thereby answered a coverage question.
    """

    def __init__(
        self,
        catalog: Catalog,
        objects: ObjectStore,
        *,
        run_id: str,
        extra_asset_ids: Sequence[str] = (),
    ) -> None:
        self.run_id = run_id
        self.catalog = catalog
        self._by_slot: dict[str, StoredProduct] = {}
        self._intermediate: dict[str, StoredProduct] = {}
        produced, named, inputs = self._collect(catalog, run_id, extra_asset_ids)
        #: Every row is fetched and digest-checked before a slot is settled. Naming an asset is a person's
        #: answer to a disagreement between two products, and the reader cannot accept that answer before it
        #: has read both sides of it (§5.8).
        chosen: dict[str, StoredProduct] = {}
        for asset in named:
            product = self._load(catalog, objects, asset)
            if product.slot in chosen:
                raise ContractDrift(
                    f"slot {product.slot!r} was named twice, by {chosen[product.slot].asset_id} and "
                    f"{product.asset_id}; naming a slot means naming the one asset to read"
                )
            chosen[product.slot] = product
        for asset in produced:
            product = self._load(catalog, objects, asset)
            found = self._table(product.slot).get(product.slot)
            if found is None:
                self._table(product.slot)[product.slot] = product
            elif product.slot not in chosen:
                #: Two products the run itself made for one question; which one a card should cite is a
                #: person's decision, not something a reader picks by timestamp.
                raise ContractDrift(
                    f"run {run_id} indexes both {found.asset_id} and {product.asset_id} as slot "
                    f"{product.slot!r}; name the one to read explicitly"
                )
        for slot, product in chosen.items():
            self._table(slot)[slot] = product
        for asset in inputs:
            product = self._load(catalog, objects, asset)
            #: A root input speaks only where the run stayed silent: the QC step publishes a filtered counts
            #: matrix over the uploaded one, and the matrix a card cites is the run's own (§5.2). The upload
            #: is still fetched and digest-checked, then set aside - unless a caller names it, above.
            if self._table(product.slot).get(product.slot) is None:
                self._table(product.slot)[product.slot] = product

    def _table(self, slot: str) -> dict[str, StoredProduct]:
        """A card cites the slot's answer; an intermediate is held apart so it cannot answer one."""
        return self._by_slot if slot in SCHEMA_BY_SLOT else self._intermediate

    @staticmethod
    def _collect(
        catalog: Catalog, run_id: str, extra_asset_ids: Sequence[str]
    ) -> tuple[list[DataAsset], list[DataAsset], list[DataAsset]]:
        """The three groups a run's index rows fall into, in the order a reader has to resolve them.

        A named asset leaves whichever group it would otherwise sit in: being named is what tells the reader
        to read it as the caller's answer instead of as one more product the run left indexed.
        """
        named: list[DataAsset] = []
        taken: set[str] = set()
        for asset_id in extra_asset_ids:
            if asset_id not in taken:
                named.append(catalog.get_asset(asset_id))
                taken.add(asset_id)
        produced = [asset for asset in catalog.list_assets(run_id=run_id) if asset.asset_id not in taken]
        taken |= {asset.asset_id for asset in produced}
        #: A run's root inputs are indexed without ``created_by_run_id`` (§5.2: an uploaded asset is not
        #: distinguishable from a produced one), so the manifest is what ties them to this run.
        inputs = [
            catalog.get_asset(asset_id)
            for asset_id in catalog.get_run(run_id).input_hashes
            if asset_id not in taken
        ]
        return produced, named, inputs

    @staticmethod
    def _load(catalog: Catalog, objects: ObjectStore, asset: DataAsset) -> StoredProduct:
        if not asset.uri.startswith(URI_SCHEME):
            raise ContractDrift(f"asset {asset.asset_id} has a non-platform URI {asset.uri!r}")
        try:
            raw = objects.get(asset.uri[len(URI_SCHEME) :])
        except KeyError:
            raise MissingProduct(
                f"asset {asset.asset_id} is indexed but its object is gone; the card cannot be built "
                "from an index row alone",
                field="asset_id",
            ) from None
        digest = sha256_hex(raw)
        if digest != asset.file_sha256:
            raise ContractDrift(
                f"asset {asset.asset_id} bytes digest {digest} does not match the indexed "
                f"{asset.file_sha256}",
                field="file_sha256",
            )
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ContractDrift(f"asset {asset.asset_id} is not a JSON object", field="document")
        stored = str(document.get("schema_version", ""))
        slot = str(document.get("slot", ""))
        if stored != asset.schema_version:
            raise ContractDrift(
                f"asset {asset.asset_id} is indexed as {asset.schema_version!r} but its bytes declare "
                f"{stored!r}"
            )
        expected = SCHEMA_BY_SLOT.get(slot) or INTERMEDIATE_SLOTS.get(slot)
        if expected is None:
            raise ContractDrift(f"slot {slot!r} is not a product this service knows how to read")
        if stored != expected:
            raise ContractDrift(
                f"slot {slot!r} is read as {expected!r}, but asset {asset.asset_id} stores {stored!r}"
            )
        return StoredProduct(asset=asset, document=document)

    # ------------------------------------------------------------------ access

    def slots(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_slot))

    def intermediate_slots(self) -> tuple[str, ...]:
        """The slots read and verified but held back, because no card cites an ephemeral product (§5.9)."""
        return tuple(sorted(self._intermediate))

    def has(self, slot: str) -> bool:
        return slot in self._by_slot

    def product(self, slot: str) -> StoredProduct:
        found = self._by_slot.get(slot)
        if found is None:
            raise MissingProduct(
                f"run {self.run_id} produced no {slot!r} asset, which a card needs to cite a measurement "
                f"for that step (have: {list(self.slots())})",
                field="slot",
            )
        return found

    def optional(self, slot: str) -> Optional[StoredProduct]:
        return self._by_slot.get(slot)

    def tool_of(self, asset_id: str) -> tuple[str, str, str]:
        return node_tool_of(self.catalog, self.run_id, asset_id)

    def __iter__(self) -> Iterator[StoredProduct]:
        return iter([self._by_slot[slot] for slot in sorted(self._by_slot)])


def node_tool_of(catalog: Catalog, run_id: str, asset_id: str) -> tuple[str, str, str]:
    """``(node_id, tool_name, tool_version)`` for a produced asset.

    Section 7.7 requires a screening record to name the tool and version that ran it. The lineage edge
    says which node wrote which asset, and the run's pinned plan version says what that node was
    registered as - so the answer comes from the record, not from whatever the registry holds today.
    """
    for lineage in catalog.lineage(asset_id):
        if lineage.asset_id != asset_id or lineage.role is not AssetRole.OUTPUT:
            continue
        if lineage.run_id != run_id:
            continue
        manifest = catalog.get_run(run_id)
        plan = catalog.get_plan(manifest.plan_id, manifest.plan_version)
        for node in plan.nodes:
            if node.node_id == lineage.node_id:
                return node.node_id, node.tool_name, node.tool_version
        raise ContractDrift(
            f"asset {asset_id} is attributed to node {lineage.node_id!r}, which plan "
            f"{plan.plan_id}@{plan.plan_version} does not contain"
        )
    raise MissingProduct(
        f"asset {asset_id} has no output lineage in run {run_id}, so no tool can be credited for it",
        field="asset_id",
    )
