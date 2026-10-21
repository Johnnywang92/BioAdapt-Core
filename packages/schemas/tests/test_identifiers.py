import hashlib
import re

from bioadapt_schemas.identifiers import (
    IdentifierError,
    candidate_id,
    canonical_cell_key,
    content_fingerprint,
    derive_candidate,
    file_sha256,
    index_hash,
    member_fingerprint,
    normalize_barcode,
    short_run_id,
)
import pytest


def test_cell_key_is_specimen_scoped_and_normalized():
    key = canonical_cell_key("spc_01", "AAACGG")
    assert key == "spc_01\x1faaacgg"
    assert key != canonical_cell_key("spc_02", "AAACGG")


def test_cell_key_rejects_unsafe_characters():
    with pytest.raises(IdentifierError):
        canonical_cell_key("spc\x1f01", "AAAC")
    with pytest.raises(IdentifierError):
        canonical_cell_key("spc_01", "AA\nAC")
    with pytest.raises(IdentifierError):
        canonical_cell_key("spc_01", "")


def test_barcode_normalization_version_is_pinned():
    assert normalize_barcode("AAACGG") == "aaacgg"
    with pytest.raises(IdentifierError):
        normalize_barcode("AAAC", "v2-strip")


def test_member_fingerprint_is_a_set_not_a_sequence():
    ordered = ["spc_01\x1fa", "spc_01\x1fb", "spc_02\x1fc"]
    shuffled = ["spc_02\x1fc", "spc_01\x1fb", "spc_01\x1fa", "spc_01\x1fa"]
    assert member_fingerprint(ordered) == member_fingerprint(shuffled)
    assert member_fingerprint(ordered[:2]) != member_fingerprint(ordered)


def test_member_fingerprint_rejects_empty_membership():
    with pytest.raises(IdentifierError):
        member_fingerprint([])


def test_candidate_id_is_deterministic_and_run_separated():
    fingerprint = member_fingerprint(["spc_01\x1fa"])
    first = candidate_id(fingerprint, "run_project001_02")
    assert re.fullmatch(r"cand_[a-z2-7]{12}_[0-9a-f]{8}", first)
    assert first == candidate_id(fingerprint, "run_project001_02")
    assert first != candidate_id(fingerprint, "run_project001_03")
    assert first != candidate_id(member_fingerprint(["spc_01\x1fb"]), "run_project001_02")


def test_candidate_id_rejects_non_sha256_fingerprint():
    with pytest.raises(IdentifierError):
        candidate_id("not-a-digest", "run_x")
    with pytest.raises(IdentifierError):
        short_run_id("")


def test_content_fingerprint_ignores_summary_order_and_rounds_noise():
    common = {
        "n_rows": 100,
        "n_cols": 50,
        "obs_index_hash": index_hash(["spc_01\x1fa"]),
        "var_index_hash": index_hash(["ENSG000001"]),
        "nnz": 42,
    }
    base = content_fingerprint(**common, numeric_summaries=[("row_sum", 1.0), ("col_max", 2.5)])
    reordered = content_fingerprint(**common, numeric_summaries=[("col_max", 2.5000001), ("row_sum", 1.0)])
    assert base == reordered
    assert content_fingerprint(**common, numeric_summaries=[("row_sum", 1.0), ("col_max", 2.51)]) != base
    assert content_fingerprint(**{**common, "nnz": 43}) != base
    assert content_fingerprint(**{**common, "n_rows": 101}) != base


def test_content_fingerprint_rejects_empty_matrix():
    with pytest.raises(IdentifierError):
        content_fingerprint(n_rows=0, n_cols=10, obs_index_hash="x", var_index_hash="y", nnz=0)


def test_derive_candidate_deduplicates_and_is_stable():
    pairs = [("spc_01", "AAAC"), ("spc_01", "aaac"), ("spc_02", "GGCT")]
    cid, fingerprint, size = derive_candidate(pairs, "run_x")
    assert size == 2
    assert (cid, fingerprint) == derive_candidate(list(reversed(pairs)), "run_x")[:2]


def test_file_sha256_matches_hashlib(tmp_path):
    path = tmp_path / "counts.h5ad"
    payload = b"matrix-bytes" * 1024
    path.write_bytes(payload)
    assert file_sha256(path, chunk_size=64) == hashlib.sha256(payload).hexdigest()
