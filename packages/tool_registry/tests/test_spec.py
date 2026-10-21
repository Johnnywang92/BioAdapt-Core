import pytest
from pydantic import ValidationError

from bioadapt_schemas.enums import LicenseReviewStatus
from bioadapt_tool_registry import (
    ContainerImage,
    DataAccessPolicy,
    LicenseInfo,
    NetworkPolicy,
    minimal_spec,
)
from bioadapt_tool_registry.enums import AssetScope, NetworkMode
from registry_factories import complete_spec


class TestContainerImage:
    def test_digest_pinning_is_mandatory(self):
        with pytest.raises(ValidationError, match="sha256 digest"):
            ContainerImage(repository="registry.internal/x", tag="1.2.3", digest="1.2.3")

    def test_floating_tags_are_rejected_even_with_a_digest(self):
        for tag in ("latest", "LATEST", "stable"):
            with pytest.raises(ValidationError, match="floating image tag"):
                ContainerImage(
                    repository="registry.internal/x", tag=tag, digest="sha256:" + "ab" * 32
                )

    def test_pinned_image_is_accepted(self):
        image = ContainerImage(
            repository="registry.internal/x", tag="1.2.3", digest="sha256:" + "0" * 64
        )
        assert image.tag == "1.2.3"


class TestNetworkAndDataAccess:
    def test_closed_network_cannot_list_hosts(self):
        with pytest.raises(ValidationError, match="closed network policy"):
            NetworkPolicy(mode=NetworkMode.CLOSED, allowed_hosts=["example.org"])

    def test_allowlist_needs_concrete_hosts(self):
        with pytest.raises(ValidationError, match="at least one host"):
            NetworkPolicy(mode=NetworkMode.ALLOWLIST)
        for host in ("*", "https://example.org", "example.org/path"):
            with pytest.raises(ValidationError, match="exact host"):
                NetworkPolicy(mode=NetworkMode.ALLOWLIST, allowed_hosts=[host])

    def test_tools_never_read_secrets_or_foreign_projects(self):
        for kwargs in (
            {"secrets_readable": True},
            {"reads_only_declared_inputs": False},
            {"writes_only_declared_outputs": False},
            {"asset_scope": AssetScope.PUBLIC},
        ):
            with pytest.raises(ValidationError):
                DataAccessPolicy(**kwargs)
        assert DataAccessPolicy()


class TestToolSpec:
    def test_versions_must_look_pinned(self):
        for bad in ("latest", "main", "v", ""):
            with pytest.raises(ValidationError, match="not a pinned version"):
                complete_spec(tool_version=bad)

    def test_egress_destinations_must_be_within_the_network_allowlist(self):
        with pytest.raises(ValidationError, match="outside the network allowlist"):
            complete_spec(
                network=NetworkPolicy(mode=NetworkMode.ALLOWLIST, allowed_hosts=["refs.internal"]),
                egress_allowlist=["other.internal"],
            )
        with pytest.raises(ValidationError, match="closed-network tool"):
            complete_spec(egress_allowlist=["refs.internal"])

    def test_gpu_claim_must_match_resources(self):
        with pytest.raises(ValidationError, match="zero-GPU"):
            complete_spec(gpu_required=True)

    def test_modalities_are_declared(self):
        with pytest.raises(ValidationError, match="modalities"):
            complete_spec(supported_modalities=[])

    def test_licence_approval_needs_a_date(self):
        with pytest.raises(ValidationError, match="review date"):
            LicenseInfo(spdx_id="MIT", display_name="x", review_status=LicenseReviewStatus.APPROVED)

    def test_admission_violations_list_every_missing_review_item(self):
        spec = minimal_spec(tool_version="1.0.0")
        violations = spec.admission_violations()
        assert violations
        assert any("assumptions" in v for v in violations)
        assert any("invalid_when" in v for v in violations)
        assert any("licence review" in v for v in violations)
        assert any("peak memory" in v for v in violations)
        assert any("provenance fields" in v for v in violations)
        assert any("input state" in v for v in violations)
        assert complete_spec().admission_violations() == []

    def test_wildcard_species_needs_a_scope_note(self):
        spec = complete_spec(supported_species=["*"])
        assert any("wildcard species" in v for v in spec.admission_violations())
        assert complete_spec(supported_species=["*"], supported_tissue_notes="any mammalian tissue").admission_violations() == []
