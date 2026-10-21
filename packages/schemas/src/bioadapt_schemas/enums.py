from enum import StrEnum


class PrivacyTier(StrEnum):
    PUBLIC = "public"
    RESTRICTED = "restricted"
    STRICT_LOCAL = "strict_local"
    HGR_CONTROLLED = "hgr_controlled"

    @property
    def egress_allowed_by_default(self) -> bool:
        return self in (PrivacyTier.PUBLIC, PrivacyTier.RESTRICTED)


class UsageScope(StrEnum):
    THIS_PROJECT_ONLY = "this_project_only"
    INTERNAL_IMPROVEMENT = "internal_improvement"
    CROSS_PROJECT_ALLOWED = "cross_project_allowed"
    DELIVERABLE_VISIBLE = "deliverable_visible"


class RegulatoryProfile(StrEnum):
    HGR = "hgr"
    PIPL = "pipl"
    GDPR = "gdpr"
    HIPAA = "hipaa"
    COMMERCIAL_ONLY = "commercial_only"


class CandidateType(StrEnum):
    C1_UNASSIGNED = "C1_unassigned"
    C2_REFERENCE_CONFLICT = "C2_reference_conflict"
    C3_RARE_STABLE = "C3_rare_stable"
    C4_CROSS_MODAL_DISCORDANT = "C4_cross_modal_discordant"
    C5_COMPOSITION_OUTLIER = "C5_composition_outlier"


class HypothesisType(StrEnum):
    CELL_TYPE = "cell_type"
    CELL_STATE = "cell_state"
    CONTINUOUS_STATE = "continuous_state"
    TECHNICAL_ARTIFACT = "technical_artifact"
    UNKNOWN = "unknown"


class UnknownReason(StrEnum):
    REFERENCE_NOT_COVERED = "reference_not_covered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNRESOLVED_CONFLICT = "unresolved_conflict"
    QUALITY_LIMITED = "quality_limited"


class FinalStatus(StrEnum):
    SUPPORTED = "supported"
    PROVISIONAL = "provisional"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    REJECTED = "rejected"


class EvidenceRole(StrEnum):
    SUPPORTING = "supporting"
    CONTRADICTING = "contradicting"
    TRIGGER = "trigger"


class EvidencePartition(StrEnum):
    DISCOVERY = "discovery"
    CONFIRMATION = "confirmation"


class FieldSource(StrEnum):
    TOOL = "tool"
    RETRIEVAL = "retrieval"
    MODEL = "model"
    HUMAN = "human"


class ReproducibilityLevel(StrEnum):
    BYTE = "byte"
    NUMERIC = "numeric"
    CONCLUSION = "conclusion"


class PairingMethod(StrEnum):
    EXPERIMENTAL_WET_PAIRING = "experimental_wet_pairing"
    BARCODE_INFERENCE = "barcode_inference"
    ST_REGISTRATION = "st_registration"
    UNPAIRED = "unpaired"


class PerturbationKind(StrEnum):
    RESOLUTION = "resolution"
    SEED = "seed"
    FEATURE_SUBSET = "feature_subset"
    LEAVE_ONE_DONOR = "leave_one_donor"
    SUBSAMPLING = "subsampling"

    @classmethod
    def mandatory_protocol(cls) -> tuple["PerturbationKind", ...]:
        return (cls.RESOLUTION, cls.SEED, cls.FEATURE_SUBSET, cls.LEAVE_ONE_DONOR, cls.SUBSAMPLING)


class ArtifactRisk(StrEnum):
    DOUBLET = "doublet"
    AMBIENT = "ambient"
    LOW_QUALITY = "low_quality"
    BATCH_SPECIFIC = "batch_specific"
    STRESS_CYCLE = "stress_cycle"
    OVER_SPLIT = "over_split"


class ScreeningResult(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_RUN = "not_run"


class CrossModalStatus(StrEnum):
    NOT_YET_VALIDATED = "not_yet_validated"
    CONSISTENT = "consistent"
    DISCORDANT = "discordant"
    PARTIAL = "partial"


class DisagreementState(StrEnum):
    OPEN = "open"
    RESOLVED_BY_EXPERTS = "resolved_by_experts"
    SUPERSEDED = "superseded"


class ReviewScope(StrEnum):
    CARD = "card"
    FIELD = "field"
    MEMBERSHIP = "membership"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    RETURN_FOR_ANALYSIS = "return_for_analysis"
    REJECT = "reject"
    MARK_CONFLICT = "mark_conflict"
    MARK_UNKNOWN = "mark_unknown"


class SignOff(StrEnum):
    FULL = "full"
    CONDITIONAL = "conditional"
    NOT_SIGNED = "not_signed"


class ToolClass(StrEnum):
    REGISTERED = "registered"
    PROJECT_ADHOC = "project_adhoc"


class LicenseReviewStatus(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"
    NOT_REQUIRED = "not_required"


class LabelProvenance(StrEnum):
    CURATED_EXPERT = "curated_expert"
    PREDICTED = "predicted"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class RetentionClass(StrEnum):
    EPHEMERAL = "ephemeral"
    RUN_LINKED = "run_linked"
    DELIVERABLE = "deliverable"
    ARCHIVE = "archive"


class FilteringState(StrEnum):
    NONE = "none"
    BARCODE_FILTERED = "barcode_filtered"
    GENE_FILTERED = "gene_filtered"
    BOTH_FILTERED = "both_filtered"
    UNKNOWN = "unknown"


class ReplicateType(StrEnum):
    BIOLOGICAL = "biological"
    TECHNICAL = "technical"


class AlignmentKind(StrEnum):
    CONTINUATION = "continuation"
    UNCERTAIN = "uncertain_alignment"
    DISTINCT = "distinct"
    SPLIT = "split"
    MERGE = "merge"


class SubjectKind(StrEnum):
    """Section 11.2: the audit log must tell a human action from a service account one."""

    HUMAN = "human"
    SERVICE = "service"


class RunEventKind(StrEnum):
    """Section 9.2: the state machine and the workflow engine both report into one event log."""

    STATE = "state"
    NODE_RETRY = "node_retry"
    BUSINESS_RETRY = "business_retry"
    USAGE = "usage"


class ApprovalKind(StrEnum):
    PLAN = "plan"
    NODE = "node"
    ADHOC_TOOL = "adhoc_tool"
    ESCALATION = "escalation"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    #: The approval window lapsed. Only ever removes permission - it can never let a run advance.
    EXPIRED = "expired"


class RunState(StrEnum):
    CREATED = "CREATED"
    PREFLIGHT_RUNNING = "PREFLIGHT_RUNNING"
    PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
    PLAN_DRAFTED = "PLAN_DRAFTED"
    PLAN_APPROVED = "PLAN_APPROVED"
    ANALYSIS_RUNNING = "ANALYSIS_RUNNING"
    QC_REVIEW = "QC_REVIEW"
    CANDIDATE_DISCOVERY = "CANDIDATE_DISCOVERY"
    EVIDENCE_BUILDING = "EVIDENCE_BUILDING"
    TARGETED_ANALYSIS = "TARGETED_ANALYSIS"
    VALIDATION = "VALIDATION"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    REPORT_READY = "REPORT_READY"
    ARCHIVED = "ARCHIVED"
    BLOCKED = "BLOCKED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    NEEDS_INPUT = "NEEDS_INPUT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    RESOURCE_EXCEEDED = "RESOURCE_EXCEEDED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    ESCALATED_TO_HUMAN = "ESCALATED_TO_HUMAN"
    FAILED = "FAILED"


class AssetRole(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class GenomeIdSystem(StrEnum):
    ENSEMBL = "ensembl"
    REFSEQ = "refseq"
    GENCODE = "gencode"
    HGNC_SYMBOL = "hgnc_symbol"
    OTHER = "other"


class LabelLevel(StrEnum):
    UNKNOWN_COARSE = "unknown_coarse"
    CELL_TYPE_BROAD = "cell_type_broad"
    CELL_TYPE_FINE = "cell_type_fine"
    CELL_STATE = "cell_state"
    PROVISIONAL_PROJECT = "provisional_project"


class EvidenceKind(StrEnum):
    DIFFERENTIAL_EXPRESSION = "differential_expression"
    MARKER_SPECIFICITY = "marker_specificity"
    DOUBLET_SCREENING = "doublet_screening"
    AMBIENT_SCREENING = "ambient_screening"
    QUALITY_SCREENING = "quality_screening"
    BATCH_SCREENING = "batch_screening"
    STRESS_CYCLE_SCREENING = "stress_cycle_screening"
    OVER_SPLIT_SCREENING = "over_split_screening"
    REFERENCE_MAPPING = "reference_mapping"
    REFERENCE_COVERAGE = "reference_coverage"
    STABILITY = "stability"
    CONTINUOUS_STATE = "continuous_state"
    COMPOSITION = "composition"
    CROSS_MODAL = "cross_modal"
    LITERATURE_CLAIM = "literature_claim"
    CASE_STUDY = "case_study"
    EXPERT_REVISION = "expert_revision"


class AnnotationStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class Severity(StrEnum):
    BLOCK = "block"
    WARN = "warn"
    INFO = "info"
