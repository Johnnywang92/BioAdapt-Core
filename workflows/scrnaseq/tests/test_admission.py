"""From a converted h5ad to an asset a run can read, without the scientific stack (§4.1, §5.2).

The adapter is tested against duck-typed doubles elsewhere, so the join between a converted product
and the index can be tested the same way: nothing here needs ``anndata``. What is checked is the
promise README §6.4 says is missing - that the bytes a converter writes are an input the reference
executor accepts, that the row labels them the way the project rather than the tool decides, and that
the loss the conversion caused travels in the same record as the result instead of being a console
line that scrolls past.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from bioadapt_schemas.entities import DataAsset, Genome
from bioadapt_schemas.enums import FilteringState, GenomeIdSystem, PrivacyTier, RetentionClass, UsageScope
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.identifiers import sha256_hex

from bioadapt_scrnaseq.admission import DEFAULT_ASSET_DEFAULTS, FileNote, admit, note_of, write_admission
from bioadapt_scrnaseq.errors import StepFailure
from bioadapt_scrnaseq.products import read_product
from bioadapt_scrnaseq.runner import ASSET_FORMAT, URI_SCHEME
from bioadapt_scrnaseq.scanpy_adapter import DEFAULT_OBS_KEYS, from_anndata

GENOME = Genome(
    build="gencode-v44",
    annotation_source="GENCODE",
    version="44",
    id_system=GenomeIdSystem.HGNC_SYMBOL,
)
GENES = ("ACTB", "B2M", "MT-CO1", "RPL13A")
BARCODES = ("AAACCTG-1", "AAACCTG-2", "CTAACG-1")
OBS: dict[str, list[str]] = {
    "specimen_id": ["spc_a", "spc_a", "spc_b"],
    "donor_id": ["donor_1", "donor_1", "donor_2"],
    "group": ["control", "control", "case"],
}
DENSE = [[1.0, 0.0, 3.0, 0.0], [0.0, 2.0, 1.0, 0.0], [4.0, 0.0, 0.0, 5.0]]
CREATED = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
PROJECT = "prj_scrnaseq"


def adata(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "X": DENSE,
        "obs": OBS,
        "var": {"index": list(GENES)},
        "obs_names": list(BARCODES),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def converted(**overrides: Any):
    return from_anndata(adata(**overrides), genome=GENOME)


def admitted(**overrides: Any):
    return admit(converted(**overrides), project_id=PROJECT, created_at=CREATED, obs_keys=DEFAULT_OBS_KEYS)


# ------------------------------------------------------------------ the bytes are a node's input


def test_the_admitted_bytes_pass_every_check_a_node_makes_on_its_input() -> None:
    """runner._read's five checks, on a file the converter wrote rather than a node produced."""
    read = converted()
    admission = admit(read, project_id=PROJECT, created_at=CREATED, obs_keys=DEFAULT_OBS_KEYS)
    asset = admission.asset
    assert asset.uri.startswith(URI_SCHEME)
    assert asset.format == ASSET_FORMAT, "a run reads its own product assets, not a converted file"
    assert sha256_hex(admission.payload) == asset.file_sha256
    assert len(admission.payload) == asset.size_bytes
    assert json.loads(admission.payload)["slot"] == "counts"
    back = read_product(asset.schema_version, admission.payload)
    assert back.matrix.digest() == read.product.matrix.digest()
    assert asset.asset_id == f"as_{asset.file_sha256[:12]}"


def test_the_gene_universe_and_the_annotation_survive_the_round_trip() -> None:
    """§10.1: a run that cannot name the identifier space its genes live in is rejected at admission."""
    admission = admitted()
    document = json.loads(admission.payload)
    assert document["matrix"]["genes"] == sorted(GENES)
    assert admission.asset.genome == GENOME
    assert read_product(admission.asset.schema_version, admission.payload).matrix.genome == GENOME


def test_the_row_labels_the_asset_as_the_project_decides_not_as_the_converter_prefers() -> None:
    asset = admitted().asset
    assert asset.project_id == PROJECT
    assert (asset.privacy_tier, asset.usage_scope) == (PrivacyTier.RESTRICTED, UsageScope.THIS_PROJECT_ONLY)
    assert (asset.retention_class, asset.filtering_state) == (RetentionClass.RUN_LINKED, FilteringState.NONE)
    assert asset.created_by_run_id is None, "this asset came in from a person, not out of a node"
    assert asset.schema_version == "expression_v1"


def test_the_same_file_converted_twice_is_one_asset(tmp_path) -> None:
    """Byte-derived ids are what lets a rerun resolve to the existing row instead of duplicating §5.2."""
    first = admit(converted(), project_id=PROJECT, created_at=CREATED, obs_keys=DEFAULT_OBS_KEYS)
    second = admit(
        converted(),
        project_id=PROJECT,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        obs_keys=DEFAULT_OBS_KEYS,
    )
    assert first.asset.asset_id == second.asset.asset_id
    assert first.payload == second.payload


# ------------------------------------------------------------------ the record says what was lost


def test_the_manifest_is_the_body_the_upload_route_accepts() -> None:
    manifest = admitted().manifest()
    assert DataAsset.model_validate(manifest["asset"]) == admitted().asset
    assert manifest["upload"] == {"file": f"{manifest['asset']['asset_id']}.json", "slot": "counts"}
    assert manifest["source"] == {"h5ad": None}


def test_slots_the_contract_cannot_hold_are_written_down_next_to_the_asset() -> None:
    read = converted(obsm={"X_pca": None}, uns={"rank_genes_groups": {}})
    manifest = admit(read, project_id=PROJECT, created_at=CREATED, obs_keys=DEFAULT_OBS_KEYS).manifest()
    conversion = manifest["conversion"]
    assert conversion["dropped_slots"] == ["obsm[X_pca]", "uns"]
    assert conversion["lossy"] is True
    assert conversion["cells_with_counts"] == 3
    assert conversion["warnings"][0] == (
        "conversion h5ad->platform_contract is lossy: ['obsm[X_pca]', 'uns'] retained on the other asset"
    )
    assert "read 3 cells x 4 genes into slot 'counts'" in conversion["warnings"][1]


def test_an_original_that_was_not_kept_is_recorded_as_a_breach_not_a_detail() -> None:
    """§4.1: with nothing to re-read, the dropped slots are gone, and the manifest is where that shows."""
    read = from_anndata(adata(obsm={"X_pca": None}), genome=GENOME, both_sides_kept=False)
    admission = admit(read, project_id=PROJECT, created_at=CREATED, obs_keys=DEFAULT_OBS_KEYS)
    warnings = admission.manifest()["conversion"]["warnings"]
    assert warnings[0] == (
        "conversion h5ad->platform_contract dropped ['obsm[X_pca]'] and the pre-conversion asset was not kept"
    )
    assert json.loads(admission.payload)["conversion_warnings"] == list(read.product.conversion_warnings)


def test_the_obs_columns_that_fed_the_design_matrix_are_part_of_the_record() -> None:
    """Which column was read as the donor is a claim about the file, so it belongs with the asset."""
    keys = {"specimen_id": "sample", "donor_id": "subject", "group": "condition"}
    read = from_anndata(
        adata(obs={"sample": OBS["specimen_id"], "subject": OBS["donor_id"], "condition": OBS["group"]}),
        genome=GENOME,
        obs_keys=keys,
    )
    manifest = admit(read, project_id=PROJECT, created_at=CREATED, obs_keys=keys).manifest()
    assert manifest["obs_keys"] == keys
    assert read.product.matrix.cells[0].donor_id == "donor_1"


# ------------------------------------------------------------------ the drop zone on disk


def test_the_files_written_are_the_bytes_the_row_hashes(tmp_path) -> None:
    admission = admitted(original=FileNote(path="pbmc.h5ad", file_sha256="ab" * 32, size_bytes=7))
    paths = write_admission(admission, tmp_path / "admissions")
    assert [path.name for path in paths] == [f"{admission.asset_id}.json", f"{admission.asset_id}.manifest.json"]
    payload = paths[0].read_bytes()
    assert hashlib.sha256(payload).hexdigest() == admission.asset.file_sha256
    assert json.loads(paths[1].read_bytes()) == admission.manifest()


def test_a_file_that_is_not_there_stops_for_input_like_any_other_missing_input(tmp_path) -> None:
    with pytest.raises(StepFailure) as raised:
        note_of(str(tmp_path / "absent.h5ad"))
    failure = raised.value
    assert failure.error_class is ErrorClass.NEEDS_INPUT
    assert failure.code == "input_missing" and failure.field == "input"

    here = tmp_path / "sample.h5ad"
    here.write_bytes(b"not really an h5ad")
    noted = note_of(str(here))
    assert noted.size_bytes == 18
    assert noted.file_sha256 == hashlib.sha256(b"not really an h5ad").hexdigest()
    assert noted.to_payload()["path"] == str(here)


def test_the_digest_of_the_original_is_kept_so_it_can_be_admitted_as_the_other_asset(tmp_path) -> None:
    here = tmp_path / "sample.h5ad"
    here.write_bytes(b"counts")
    manifest = admit(converted(), project_id=PROJECT, created_at=CREATED, obs_keys=DEFAULT_OBS_KEYS,
                     original=note_of(str(here))).manifest()
    assert manifest["source"]["h5ad"]["file_sha256"] == hashlib.sha256(b"counts").hexdigest()
    assert DEFAULT_ASSET_DEFAULTS.filtering_state is FilteringState.NONE
