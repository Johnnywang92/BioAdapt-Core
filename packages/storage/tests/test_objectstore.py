import hashlib
from datetime import timedelta

import pytest
from pydantic import ValidationError

from bioadapt_schemas.enums import RetentionClass
from bioadapt_storage import (
    BUCKET,
    EPHEMERAL_TTL_DAYS,
    REPLICA_SURFACES,
    DeletionCertificate,
    HmacSigner,
    InMemoryObjectStore,
    deletion_certificate,
    issue_signed_url,
    key_project,
    object_key,
    plan_gc,
)
from storage_factories import FINGERPRINT, NOW, PROJECT_ID, SHA, asset

OTHER = "prj_002"


def sha_of(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def key_for(asset_id: str = "as_0001", retention: RetentionClass = RetentionClass.EPHEMERAL) -> str:
    return object_key(
        project_id=PROJECT_ID,
        asset_id=asset_id,
        file_sha256=SHA,
        retention_class=retention,
        extension="h5ad",
    )


def self_asset():
    return asset(asset_id="as_0001", file_sha256=SHA, content_fingerprint=FINGERPRINT)


class TestKeys:
    def test_keys_are_content_addressed_inside_the_project_prefix(self):
        key = key_for()
        assert key == f"{BUCKET}/prj_001/ephemeral/as_0001/{SHA[:16]}.h5ad"
        assert key_project(key) == PROJECT_ID
        assert SHA not in key

    def test_path_traversal_and_unsafe_segments_are_refused(self):
        for bad in ("../prj_002", "prj 001", "prj_001/../002", "", "a" * 81):
            with pytest.raises(ValueError, match="safe path segment"):
                object_key(
                    project_id=bad,
                    asset_id="as_0001",
                    file_sha256=SHA,
                    retention_class=RetentionClass.EPHEMERAL,
                    extension="h5ad",
                )

    def test_asset_id_segment_is_validated_too(self):
        with pytest.raises(ValueError, match="asset_id"):
            object_key(
                project_id=PROJECT_ID,
                asset_id="as/../x",
                file_sha256=SHA,
                retention_class=RetentionClass.EPHEMERAL,
                extension="h5ad",
            )

    def test_extension_is_sanitised_and_digest_validated(self):
        cleaned = object_key(
            project_id=PROJECT_ID,
            asset_id="as_0001",
            file_sha256=SHA,
            retention_class=RetentionClass.EPHEMERAL,
            extension="h5*ad",
        )
        assert cleaned.endswith(".h5ad")
        with pytest.raises(ValueError, match="hex digest"):
            object_key(
                project_id=PROJECT_ID,
                asset_id="as_0001",
                file_sha256="not-a-sha",
                retention_class=RetentionClass.EPHEMERAL,
                extension="h5ad",
            )

    def test_malformed_keys_are_rejected(self):
        for bad in ("other/x", f"{BUCKET}/prj_001", f"{BUCKET}/prj_001/ephemeral"):
            with pytest.raises(ValueError, match="not a platform object key"):
                key_project(bad)


class TestSignedUrls:
    def issue(self, key=None, **overrides):
        fields = {"key": key or key_for(), "project_id": PROJECT_ID, "now": NOW, "signature": "s" * 16}
        fields.update(overrides)
        return issue_signed_url(fields.pop("key"), **fields)

    def test_url_is_scoped_to_the_requesting_project(self):
        url = self.issue(ttl_seconds=300)
        assert url.project_id == PROJECT_ID
        assert url.ttl_seconds == 300
        assert url.expires_at == NOW + timedelta(seconds=300)
        assert f"{BUCKET}/{PROJECT_ID}/" in url.url

    def test_a_key_from_another_project_is_a_permission_error(self):
        with pytest.raises(PermissionError, match="belongs to project prj_002"):
            self.issue(key=key_for().replace(PROJECT_ID, OTHER))

    def test_ttl_is_capped_at_ten_minutes(self):
        for ttl in (0, -1, 601, 3600):
            with pytest.raises(ValueError, match="expire within 600"):
                self.issue(ttl_seconds=ttl)

    def test_default_ttl_is_the_ceiling(self):
        assert self.issue().ttl_seconds == 600

    def test_signed_urls_are_frozen(self):
        url = self.issue()
        with pytest.raises(ValidationError, match="frozen"):
            url.key = f"{BUCKET}/{OTHER}/ephemeral/as_0001/{SHA[:16]}.h5ad"


class TestRetentionAndGc:
    def make(self, asset_id, retention, age_days):
        return asset(
            asset_id=asset_id,
            file_sha256=sha_of(asset_id),
            content_fingerprint=sha_of(asset_id + "c"),
            retention_class=retention,
            created_at=NOW - timedelta(days=age_days),
        )

    def test_only_expired_ephemeral_assets_are_collected(self):
        items = [
            self.make("as_old", RetentionClass.EPHEMERAL, EPHEMERAL_TTL_DAYS + 1),
            self.make("as_new", RetentionClass.EPHEMERAL, 3),
        ]
        plan = plan_gc(items, today=NOW)
        assert [r.asset_id for r in plan.deletable] == ["as_old"]
        assert [r.asset_id for r in plan.blocked] == ["as_new"]
        assert "ephemeral ttl 30d exceeded" in plan.deletable[0].reason

    def test_assets_referenced_by_a_deliverable_survive_the_collector(self):
        plan = plan_gc([self.make("as_old", RetentionClass.EPHEMERAL, 400)], today=NOW, referenced_by_deliverable={"as_old"})
        assert plan.deletable == []
        assert "deliverable" in plan.blocked[0].reason

    def test_run_linked_and_later_classes_are_cold_tiered_not_deleted(self):
        items = [
            self.make("as_run", RetentionClass.RUN_LINKED, 900),
            self.make("as_del", RetentionClass.DELIVERABLE, 900),
            self.make("as_arc", RetentionClass.ARCHIVE, 900),
        ]
        plan = plan_gc(items, today=NOW)
        assert plan.deletable == []
        assert [r.asset_id for r in plan.cold_tiered] == ["as_run", "as_del", "as_arc"]

    def test_every_asset_ends_up_in_exactly_one_bucket(self):
        items = [self.make(f"as_{i}", RetentionClass.EPHEMERAL, i * 100) for i in range(4)]
        plan = plan_gc(items, today=NOW)
        assert len(plan.deletable) + len(plan.blocked) + len(plan.cold_tiered) == len(items)

    def test_ttl_is_configurable_for_measured_values(self):
        items = [self.make("as_old", RetentionClass.EPHEMERAL, 8)]
        assert [r.asset_id for r in plan_gc(items, today=NOW, ttl_days=7).deletable] == ["as_old"]


class TestObjectPlane:
    def test_put_reports_the_digest_and_size_the_api_checks(self):
        store = InMemoryObjectStore()
        head = store.put(key_for(), b"counts")
        assert head.sha256 == hashlib.sha256(b"counts").hexdigest()
        assert head.size_bytes == 6
        assert store.get(key_for()) == b"counts"
        store.delete(key_for())
        assert store.head(key_for()) is None
        assert store.keys() == []

    def test_a_head_is_the_digest_of_the_bytes_under_the_key_right_now(self):
        store = InMemoryObjectStore()
        head = store.put(key_for(), b"counts")
        assert store.head(key_for()) == head
        #: The reference store keeps the head instead of re-hashing the payload, so rewriting a key
        #: must replace it - a stale head would let a completed upload attest to bytes that are gone.
        replaced = store.put(key_for(), b"counts v2")
        assert store.head(key_for()).sha256 == replaced.sha256 == hashlib.sha256(b"counts v2").hexdigest()
        assert store.head(key_for()).size_bytes == len(b"counts v2")

    def test_the_signer_is_stable_for_key_method_and_expiry(self):
        signer = HmacSigner(b"test-secret-material")
        expires = NOW + timedelta(seconds=600)
        put = signer.sign(key=key_for(), method="PUT", expires_at=expires)
        get = signer.sign(key=key_for(), method="GET", expires_at=expires)
        assert put != get
        assert put == signer.sign(key=key_for(), method="PUT", expires_at=expires)

    def test_a_short_signing_secret_is_refused(self):
        with pytest.raises(ValueError, match="too short"):
            HmacSigner(b"tiny")

    def test_upload_grants_are_put_only_and_project_scoped(self):
        signer = HmacSigner(b"test-secret-material")
        signature = signer.sign(key=key_for(), method="PUT", expires_at=NOW + timedelta(seconds=600))
        grant = issue_signed_url(
            key_for(), project_id=PROJECT_ID, now=NOW, signature=signature, method="PUT"
        )
        assert grant.method == "PUT"
        assert "method=PUT" in grant.url
        with pytest.raises(PermissionError, match="belongs to project prj_002"):
            issue_signed_url(
                key_for().replace(PROJECT_ID, OTHER),
                project_id=PROJECT_ID,
                now=NOW,
                signature=signature,
                method="PUT",
            )

    def test_only_read_and_write_signatures_are_handed_out(self):
        with pytest.raises(ValueError, match="GET and PUT"):
            issue_signed_url(key_for(), project_id=PROJECT_ID, now=NOW, signature="s" * 16, method="DELETE")


class TestDeletionCertificate:
    def certificate(self, **overrides):
        fields = {
            "asset": self_asset(),
            "deleted_at": NOW,
            "operator_id": "user_platform_admin_01",
            "surfaces_cleared": REPLICA_SURFACES,
        }
        fields.update(overrides)
        return deletion_certificate(**fields)

    def test_certificate_lists_every_replica_surface(self):
        cert = self.certificate()
        assert cert.complete
        assert set(cert.surfaces) == set(REPLICA_SURFACES)
        assert cert.verified_no_cross_project_sharing
        assert isinstance(cert, DeletionCertificate)

    def test_a_partial_deletion_is_refused(self):
        with pytest.raises(ValueError, match="copies remain on"):
            self.certificate(surfaces_cleared=["object_store", "postgres_index"])

    def test_cross_project_copies_void_the_certificate(self):
        cert = self.certificate(shared_with_projects=[OTHER])
        assert not cert.complete
        assert cert.cross_project_sharing == [OTHER]
        assert not cert.verified_no_cross_project_sharing

    def test_certificate_is_the_proof_recorded_for_regulators(self):
        cert = self.certificate(operator_id="user_pi_01")
        assert cert.project_id == PROJECT_ID
        assert cert.asset_id == "as_0001"
        assert cert.deleted_at == NOW
        assert cert.operator_id == "user_pi_01"
