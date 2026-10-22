/**
 * Wire types for the platform API (plan V2.0 sections 9.3, 9.7).
 *
 * The API declares no response models, so these are descriptions of what the routes actually emit,
 * not mirrors of the Python contracts: optional keys arrive as explicit nulls, and every value that
 * is a Python enum arrives as its string. Only the fields a screen reads are typed; anything the
 * server may add later stays reachable through the index signatures on the looser records.
 */

export type CapabilityStatus = "configured" | "unavailable" | "unknown";
export interface DeploymentCapabilities {
  mode: "reference" | "configured" | "unknown";
  object_upload: CapabilityStatus;
  run_execution: CapabilityStatus;
  export_generation: CapabilityStatus;
}

export type PrivacyTier = "public" | "restricted" | "strict_local" | "hgr_controlled";
export type RegulatoryProfile = "hgr" | "pipl" | "gdpr" | "hipaa" | "commercial_only";
export type UsageScope =
  | "this_project_only"
  | "internal_improvement"
  | "cross_project_allowed"
  | "deliverable_visible";
export type RetentionClass = "ephemeral" | "run_linked" | "deliverable" | "archive";
export type FilteringState = "none" | "barcode_filtered" | "gene_filtered" | "both_filtered" | "unknown";
export type ReproducibilityLevel = "byte" | "numeric" | "conclusion";
export type FinalStatus = "supported" | "provisional" | "conflict" | "unknown" | "rejected";
export type SignOff = "full" | "conditional" | "not_signed";
export type ReviewScope = "card" | "field" | "membership";
export type ReviewDecision =
  | "approve"
  | "revise"
  | "return_for_analysis"
  | "reject"
  | "mark_conflict"
  | "mark_unknown";
export type FieldSource = "tool" | "retrieval" | "model" | "human";
export type HypothesisType = "cell_type" | "cell_state" | "continuous_state" | "technical_artifact" | "unknown";
export type UnknownReason =
  | "reference_not_covered"
  | "insufficient_evidence"
  | "unresolved_conflict"
  | "quality_limited";
export type LabelLevel = "unknown_coarse" | "cell_type_broad" | "cell_type_fine" | "cell_state" | "provisional_project";
export type CandidateType =
  | "C1_unassigned"
  | "C2_reference_conflict"
  | "C3_rare_stable"
  | "C4_cross_modal_discordant"
  | "C5_composition_outlier";
export type EvidenceRole = "supporting" | "contradicting" | "trigger";
export type EvidencePartition = "discovery" | "confirmation";
export type ArtifactRisk =
  | "doublet"
  | "ambient"
  | "low_quality"
  | "batch_specific"
  | "stress_cycle"
  | "over_split";
export type ScreeningResult = "pass" | "warn" | "fail" | "not_run";
export type CrossModalStatus = "not_yet_validated" | "consistent" | "discordant" | "partial";
export type RunState = string;
export type ApprovalKind = "plan" | "node" | "adhoc_tool" | "escalation";
export type ApprovalState = "pending" | "approved" | "rejected" | "expired";
export type ExportKind = "tables" | "figures" | "code_bundle" | "delivery_zip";
export type ExportStatus = "QUEUED" | "RUNNING" | "READY" | "FAILED";
export type ErrorClass =
  | "validation"
  | "retryable_failure"
  | "needs_input"
  | "policy_blocked"
  | "resource_exceeded"
  | "budget_exceeded"
  | "gate_blocked"
  | "not_found"
  | "conflict"
  | "failed";

/** Section 9.7: one shape for every 4xx and 5xx. */
export interface Problem {
  code: string;
  class: ErrorClass;
  field: string | null;
  message: string;
  retryable: boolean;
  retry_after: number | null;
  trace_id: string;
}

/** The only list shape the API uses. `next_offset` is present only when a full page came back. */
export interface Page<T> {
  items: T[];
  limit: number;
  offset: number;
  next_offset?: number;
}

export interface Project {
  project_id: string;
  study_goal: string;
  species: string;
  privacy_tier: PrivacyTier;
  created_by: string;
  regulatory_profile: RegulatoryProfile[];
  annotation_granularity_target: string;
  review_load_budget_hours_per_week: number;
  thresholds: Record<string, unknown>;
  created_at: string | null;
}

/**
 * What a caller may send when opening a project. `thresholds` and `created_at` are left out: the
 * server fills them, and §7.3 keeps the cut set a deployment input rather than something a screen
 * can pick. `regulatory_profile` has a server-side default, so it is optional here.
 */
export type ProjectCreate = Omit<Project, "thresholds" | "created_at"> &
  Partial<Pick<Project, "regulatory_profile">>;

/**
 * A row of the identity directory (§11.2): the key an admin authorised, and what a login with it is
 * granted. The key material itself is absent on purpose - a screen compares fingerprints, and the PEM
 * is only ever read by the thing that checks a signature.
 */
export interface IamCredential {
  kid: string;
  subject_id: string;
  algorithm: string;
  fingerprint: string;
  roles: string[];
  project_ids: string[];
  amr: string[];
  solo_mode: boolean;
  is_service_account: boolean;
  label: string | null;
  created_by: string;
  created_at: string | null;
  revoked_by: string | null;
  revoked_at: string | null;
}

/** What an admin writes about a key. The kid and fingerprint are derived from the PEM, never typed. */
export interface CredentialRegistration {
  subject_id: string;
  public_key: string;
  roles: string[];
  project_ids: string[];
  amr: string[];
  solo_mode: boolean;
  is_service_account: boolean;
  label?: string | null;
}

/** What a provenance claim about filtering rests on (§5.4): the tool and the reference it read. */
export interface UpstreamPipeline {
  name: string;
  version: string;
  reference_build: string;
  reference_annotation_source: string;
}

export interface DataAsset {
  asset_id: string;
  project_id: string;
  uri: string;
  file_sha256: string;
  content_fingerprint: string;
  format: string;
  schema_version: string;
  size_bytes: number;
  privacy_tier: PrivacyTier;
  usage_scope: UsageScope;
  retention_class: RetentionClass;
  filtering_state: FilteringState;
  created_at: string;
  upstream_pipeline: UpstreamPipeline | null;
  genome: Record<string, unknown> | null;
  created_by_run_id: string | null;
}

export interface UploadGrant {
  asset_id: string;
  key: string;
  method: string;
  upload_url: string;
  expires_at: string;
  ttl_seconds: number;
}

export interface RecordedAsset {
  asset_id: string;
  key: string;
  uri: string;
  content_fingerprint: string;
}

export interface PlanNode {
  node_id: string;
  tool_name: string;
  tool_version: string;
  tool_class: string;
  requires_approval: boolean;
  inputs?: string[];
  outputs?: string[];
  parameters?: Record<string, unknown>;
  dependency_node_ids?: string[];
  resources?: { cpu: number; memory_gb: number; gpu: number; max_minutes: number };
  privacy_policy?: string;
  [extra: string]: unknown;
}

export interface AnalysisPlan {
  plan_id: string;
  plan_version: string;
  project_id: string;
  nodes: PlanNode[];
  [extra: string]: unknown;
}

export interface RecordedPlan {
  plan_id: string;
  plan_version: string;
  plan_sha256: string;
}

/** A row of `GET /projects/{id}/plans`: enough to pick a plan version to launch, and no more (§9.7). */
export interface PlanSummary {
  plan_id: string;
  plan_version: string;
  objective: string;
  plan_sha256: string;
  /** The slots a launch has to be handed an asset for (§9.2); the rest of the graph is produced inside the run. */
  root_inputs: string[];
  created_by: string;
  created_at: string | null;
  nodes: PlanNode[];
}

export interface ToolSlot {
  slot: string;
  required: boolean;
}

/** A row of `GET /projects/{id}/tools`: the tool set this deployment both advertises and executes (§4.1, §9.4). */
export interface ToolRow {
  tool_name: string;
  tool_version: string;
  tool_class: string;
  task: string;
  runnable: boolean;
  input_slots: ToolSlot[];
  output_slots: string[];
  min_resources: { cpu: number; memory_gb: number; gpu: number; max_minutes: number };
  measured_peak_memory_mb: number | null;
  measured_on: string | null;
  quality_checks: string[];
  assumptions: string[];
  invalid_when: string[];
  network_mode: string;
  egress_allowlist: string[];
  failure_policy: string;
  retry_limit: number;
  reproducibility_level_target: string;
  requires_approval: boolean;
  privacy_policy: string;
}

export interface ToolCatalog {
  snapshot_id: string;
  tools: ToolRow[];
  conclusion_outputs: string[];
}

/** What one execute call reports: how far the run got, and what it stopped to wait for (§9.1). */
export interface RunExecution {
  run_id: string;
  completed: boolean;
  state: RunState;
  nodes_executed: string[];
  assets_produced: string[];
  candidate_count: number;
  draft_count: number;
  warnings: string[];
  stopped_at: string | null;
  reason: string | null;
  requires_human_action: boolean;
  gated_nodes: string[];
}

export interface RunSummary {
  run_id: string;
  project_id: string;
  state: RunState;
  plan_id: string;
  plan_version: string;
  reproducibility_level_target: ReproducibilityLevel;
  tool_versions: Record<string, string>;
  tool_registry_snapshot: string;
  input_hashes: Record<string, string>;
  parameters: Record<string, unknown>;
  queue_position: number | null;
}

export interface RunListItem {
  run_id: string;
  state: RunState;
  plan_id: string;
  plan_version: string;
  started_at: string | null;
  queue_position: number | null;
}

export interface RunEventFrame {
  run_id: string;
  state: RunState;
  queue_position: number | null;
  frame: number;
}

/**
 * One line of the run's journal (§9.2). `reason` is the server's own sentence for the move - a
 * transition without one is refused at the route, so a tracker never has to invent an explanation.
 */
export interface RunJournalEvent {
  event_id: string;
  run_id: string;
  kind: string;
  from_state: RunState;
  to_state: RunState;
  actor_id: string;
  actor_kind: string;
  reason: string;
  input_hash: string;
  versions: Record<string, unknown>;
  attempt: number;
  node_id: string | null;
  loop_iteration: number;
  compute_minutes: number;
  model_cost_usd: number;
  at: string | null;
}

export interface Approval {
  approval_id: string;
  run_id: string;
  kind: ApprovalKind;
  subject_digest: string;
  state: ApprovalState;
  node_id: string | null;
  requested_by: string;
  assigned_to: string;
  requested_at: string;
  sla_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  delegated_from: string | null;
  delegated_at: string | null;
  overdue: boolean;
  overdue_reason: string | null;
}

export interface CandidateSet {
  candidate_id: string;
  run_id: string;
  display_name: string;
  candidate_types: CandidateType[];
  size: number;
  donor_count: number;
  member_fingerprint: string;
  [extra: string]: unknown;
}

export interface OntologyRef {
  term_id: string | null;
  ontology_name: string | null;
  ontology_version: string | null;
  mapping_source: string | null;
  mapping_confidence: number | null;
}

export interface FieldProvenance {
  source: FieldSource;
  object_id: string;
  note: string | null;
}

export interface EvidenceSource {
  claim_id: string | null;
  dataset_id: string | null;
  version: string | null;
  run_id: string | null;
  asset_id: string | null;
  doi: string | null;
  pmid: string | null;
  locator: string | null;
  license_class: string | null;
  usage_scope: string | null;
}

export interface Evidence {
  evidence_id: string;
  kind: string;
  role: EvidenceRole;
  partition: EvidencePartition;
  source: EvidenceSource;
  provenance: FieldProvenance;
  independence_group: string | null;
  used_cell_set_fingerprint: string | null;
  content: Record<string, unknown>;
}

export interface ScreeningItem {
  result: ScreeningResult;
  tool: string | null;
  tool_version: string | null;
  threshold_source: string | null;
  detail: string | null;
}

export interface ArtifactScreening {
  doublet: ScreeningItem;
  ambient: ScreeningItem;
  low_quality: ScreeningItem;
  batch_specific: ScreeningItem;
  stress_cycle: ScreeningItem;
  over_split: ScreeningItem;
}

export interface EvidenceCard {
  card_id: string;
  candidate_id: string;
  run_id: string;
  display_name: string;
  candidate_types: CandidateType[];
  hypothesis_type: HypothesisType;
  unknown_reason: UnknownReason | null;
  candidate_label: string | null;
  label_level: LabelLevel;
  ontology: OntologyRef;
  identity_vs_state: Record<string, unknown>;
  evidence: Record<string, Evidence>;
  supporting_evidence_ids: string[];
  confirmation_evidence_ids: string[];
  contradicting_evidence_ids: string[];
  trigger_signal_ids: string[];
  unresolved_conflict: boolean;
  artifact_screening: ArtifactScreening | null;
  novelty_vs_coverage: Record<string, unknown> | null;
  stability_summary: Record<string, unknown> | null;
  composition_check: Record<string, unknown>;
  cross_modal_status: CrossModalStatus;
  field_provenance: Record<string, FieldProvenance>;
  support_rule_version: string;
  thresholds_version: string;
  final_status: FinalStatus;
  status_reason: string | null;
  reviewer_id: string | null;
  review_reason: string | null;
  sign_off: SignOff;
  reviewed_at: string | null;
}

export interface ReviewerMemo {
  summary: string;
  confidence?: number | null;
  power_warning?: string | null;
  confounding_risks: string[];
  stability_verdict?: string | null;
  recommended_action?: string | null;
}

export interface CardEvidenceRead {
  candidate_id: string;
  run_id: string;
  cluster_id: string;
  evidence_card: EvidenceCard;
  ceiling: FinalStatus;
  gate_reasons: string[];
  reviewer_memo?: ReviewerMemo | null;
}

export interface CardDraftRead {
  card_id: string;
  candidate_id: string;
  ceiling: FinalStatus;
  gate_reasons: string[];
  notes: string[];
  support_met: boolean;
  underpowered_design: boolean;
  blocking_risks: string[];
  evidence_count: number;
  card: EvidenceCard;
  reviewer_memo?: ReviewerMemo | null;
}

export interface DraftResponse {
  run_id: string;
  thresholds_version: string;
  cards: CardDraftRead[];
}

export interface CardScore {
  card_id: string;
  candidate_id: string;
  display_name: string;
  impact: number;
  uncertainty: number;
  client_ask: boolean;
  score: number;
  reasons: string[];
}

/** §9.1: what the filed row says about one card. The draft is always unknown, so this is the only
 * place that knows whether a person has acted - and null means nobody has filed the card yet. */
export interface CardStatus {
  card_id: string;
  candidate_id: string;
  final_status: FinalStatus | null;
  reviewer_id: string | null;
}

/** Section 13.4: the short list, what was filed instead, and the two numbers behind every place. */
export interface ExpertQueue {
  run_id: string;
  drafted: number;
  card_statuses: CardStatus[];
  queue: string[];
  observed: string[];
  scores: CardScore[];
  limit: number;
  thresholds_version: string;
  rule_version: string;
}

export interface ReviewEvent {
  review_id: string;
  object_id: string;
  object_type: string;
  scope: ReviewScope;
  reviewer_id: string;
  decision: ReviewDecision;
  rationale: string;
  timestamp: string;
  supersedes_review_id: string | null;
  carry_over_from_candidate_id: string | null;
  retraction_reason: string | null;
  previous_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
}

export interface ReviewResponse {
  review_id: string;
  card_id: string;
  final_status: FinalStatus;
  ceiling: FinalStatus;
  gate_reasons: string[];
  card: EvidenceCard;
}

export interface AlignmentRecordRow {
  other_candidate_id: string;
  other_run_id: string;
  kind: string;
  jaccard: number;
  carry_over: boolean;
  decided_by: string | null;
  decided_at: string | null;
  kind_under_current_thresholds: string;
}

export interface Alignment {
  candidate_id: string;
  run_id: string;
  cluster_id: string;
  member_fingerprint: string;
  thresholds_version: string;
  alignment_history: AlignmentRecordRow[];
  same_label_in_other_runs: {
    candidate_id: string;
    run_id: string;
    size: number;
    donor_count: number;
    aligned: boolean;
  }[];
}

export interface SignOffResponse {
  card_id: string;
  sign_off: SignOff;
  final_status: FinalStatus;
  ceiling: FinalStatus;
  gate_reasons: string[];
}

export interface ExportEnvelope {
  export_id: string;
  status: ExportStatus;
  kind: ExportKind;
  poll: string;
}

export interface ExportRow {
  export_id: string;
  status: ExportStatus;
  kind: ExportKind;
  filters: Record<string, unknown>;
  requested_by: string;
  artifact_asset_id: string | null;
  failure_reason: string | null;
  created_at: string | null;
  updated_at: string | null;
  download_url?: string;
  expires_at?: string;
  ttl_seconds?: number;
}

/**
 * How far the two export filters reach in the catalog before a job exists (§9.7, 第三批⑤).
 *
 * The counts are stored rows, not the artefact: a delivery is written by a worker, and until one runs
 * nobody can show its contents. `by_status` is cut by the chosen run alone, so a screen can say what a
 * status pick leaves the other statuses rather than only what it keeps.
 */
export interface ExportPreview {
  kind: ExportKind;
  filters: { run_id: string | null; final_status: string | null };
  thresholds_version: string;
  evidence_cards: { matched: number; by_status: Partial<Record<FinalStatus, number>> };
  candidate_sets: { matched: number };
  data_assets: { matched: number; size_bytes: number };
}

export type DocumentType = "paper" | "lab_note" | "analysis_log" | "protocol" | "other";

export interface KnowledgeDocument {
  doc_id: string;
  project_id: string;
  filename: string;
  doc_type: DocumentType;
  title: string;
  author?: string | null;
  species?: string | null;
  tissue?: string | null;
  privacy_tier: PrivacyTier;
  license_class: string;
  file_sha256: string;
  chunk_count: number;
  created_at: string;
  summary?: string | null;
  metadata: Record<string, unknown>;
}

/** One piece the knowledge index stored. The locator is what the parse recorded, so it can be absent:
 * a page only when the format has pages, and offsets only when the section was sliced out of the text. */
export interface DocumentChunk {
  chunk_id: string;
  doc_id: string;
  chunk_index: number;
  section_path: string;
  content: string;
  token_count: number;
  page?: number | null;
  char_start?: number | null;
  char_end?: number | null;
  metadata: Record<string, unknown>;
}

export interface RetrievalHit {
  chunk: DocumentChunk;
  score: number;
  dense_rank?: number | null;
  sparse_rank?: number | null;
  source_doc?: KnowledgeDocument | null;
}

/** §6.5: a cited passage, where it sits, and the pieces the same index kept on either side of it. */
export interface ChunkReading {
  chunk: DocumentChunk;
  document?: KnowledgeDocument | null;
  before: DocumentChunk[];
  after: DocumentChunk[];
}

export interface RagAnswer {
  query: string;
  answer: string;
  citations: RetrievalHit[];
  confidence: number;
  grounded_facts: string[];
}

export interface BioSuggestion {
  cluster_id: string;
  markers: string[];
  suggested_cell_types: string[];
  rationale: string;
  citations: RetrievalHit[];
  confidence: number;
}
