from datetime import date, datetime, timedelta, timezone

from bioadapt_schemas.enums import GenomeIdSystem, LicenseReviewStatus, ReproducibilityLevel, ToolClass
from bioadapt_schemas.errors import ApiError, ErrorClass
from bioadapt_tool_registry.enums import FailurePolicy, ToolResultStatus
from bioadapt_tool_registry import (
    AdhocApproval,
    LicenseInfo,
    ReferenceCompatibility,
    ResourceProfile,
    ToolExecution,
    ToolResult,
    minimal_spec,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
D64 = "cd" * 32
DIGEST = "sha256:" + "ab" * 32


def complete_spec(**overrides):
    fields = {
        "assumptions": ["input is a counts matrix with Ensembl gene ids"],
        "invalid_when": ["normalised input", "cross-species mapping required"],
        "quality_checks": ["matrix_dimensions_match_obs_var", "no_all_zero_cells"],
        "provenance_fields": ["input_hashes", "parameters", "container_image_digest", "tool_version"],
        "resources": ResourceProfile(
            cpu=4,
            memory_mb=16384,
            max_minutes=60,
            measured_peak_memory_mb=12100,
            measured_on="run_prj_001_01",
        ),
        "license": LicenseInfo(
            spdx_id="BSD-3-Clause",
            display_name="Scanpy",
            review_status=LicenseReviewStatus.APPROVED,
            reviewed_at=date(2026, 10, 1),
        ),
        "reference_compatibility": ReferenceCompatibility(
            requires_counts=True, id_system=GenomeIdSystem.ENSEMBL, needs_log=False
        ),
    }
    fields.update(overrides)
    return minimal_spec(**fields)


def adhoc_spec(**overrides):
    fields = {
        "tool_name": "local_marker_scan",
        "tool_version": "0.0.1",
        "task": "marker_scan",
        "tool_class": ToolClass.PROJECT_ADHOC,
        "retry_limit": 0,
        "failure_policy": FailurePolicy.HALT,
        "resources": ResourceProfile(cpu=2, memory_mb=8192, max_minutes=60),
    }
    fields.update(overrides)
    return complete_spec(**fields)


def approval(**overrides):
    fields = {
        "approver_id": "user_lead_01",
        "project_id": "prj_001",
        "plan_id": "plan_001",
        "node_id": "adhoc_001",
        "reason": "project-specific marker scan before registration",
        "approved_at": NOW,
        "expires_at": NOW + timedelta(days=7),
    }
    fields.update(overrides)
    return AdhocApproval(**fields)


def execution(tool_version: str = "1.11.0", **overrides):
    fields = {
        "container_image_digest": DIGEST,
        "tool_version": tool_version,
        "parameters_sha256": D64,
        "seed": 7,
    }
    fields.update(overrides)
    return ToolExecution(**fields)


def result(**overrides):
    fields = {
        "run_id": "run_prj_001_01",
        "node_id": "qc_001",
        "project_id": "prj_001",
        "tool_name": "scanpy_qc",
        "tool_version": "1.11.0",
        "status": ToolResultStatus.SUCCESS,
        "execution": execution(),
        "input_asset_ids": ["asset_counts"],
        "input_hashes": {"asset_counts": D64},
        "parameters": {"min_genes": 200},
        "output_asset_ids": ["asset_qc"],
        "metrics": {"median_genes_after_qc": 1420.0},
        "reproducibility_level_achieved": ReproducibilityLevel.NUMERIC,
    }
    fields.update(overrides)
    return ToolResult(**fields)


def classified_error(**overrides):
    fields = {
        "code": "out_of_memory",
        "error_class": ErrorClass.RESOURCE_EXCEEDED,
        "message": "container exceeded its memory quota",
    }
    fields.update(overrides)
    return ApiError(**fields)
