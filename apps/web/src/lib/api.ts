/**
 * The workbench's only route to data (plan V2.0 section 9.5: workflows and services depend on the
 * packages; this app depends on nothing but the HTTP surface those services expose).
 *
 * Three rules the client keeps so the screens cannot drift from the contract:
 * - The token is passed per request from the "acting as" context and never written into a URL, a
 *   log line or a stored query, so no screen can leak it into a shareable link (section 11.2).
 * - A failure is a Problem with seven keys, not a thrown string; screens branch on `class`, which is
 *   what the gate and policy layer means, rather than on wording.
 * - `POST /runs` and `POST /exports` require an Idempotency-Key, so the client generates one per
 *   intent and reuses it for a resend instead of starting a second job.
 */

import type {
  Alignment,
  AnalysisPlan,
  Approval,
  ApprovalKind,
  ApprovalState,
  CandidateSet,
  CardEvidenceRead,
  DataAsset,
  DraftResponse,
  ExpertQueue,
  ExportEnvelope,
  ExportKind,
  ExportRow,
  Page,
  Problem,
  Project,
  ProjectCreate,
  RecordedAsset,
  RecordedPlan,
  ReviewDecision,
  ReviewEvent,
  ReviewResponse,
  ReviewScope,
  RunEventFrame,
  RunListItem,
  RunState,
  RunSummary,
  SignOff,
  SignOffResponse,
} from "./types";

export const API_BASE = "/core-api/v1";

export class ApiProblem extends Error implements Problem {
  readonly code: string;
  readonly class: Problem["class"];
  readonly field: string | null;
  readonly retryable: boolean;
  readonly retry_after: number | null;
  readonly trace_id: string;
  readonly status: number;

  constructor(problem: Problem, status: number) {
    super(problem.message);
    this.name = "ApiProblem";
    this.code = problem.code;
    this.class = problem.class;
    this.field = problem.field;
    this.retryable = problem.retryable;
    this.retry_after = problem.retry_after;
    this.trace_id = problem.trace_id;
    this.status = status;
  }

  /** Section 13.1: a ceiling refusal is a gate reading, and the UI has to say so in gate words. */
  get isGateBlocked(): boolean {
    return this.class === "gate_blocked";
  }

  get isPolicyBlocked(): boolean {
    return this.class === "policy_blocked";
  }
}

export interface Caller {
  token: string;
  subjectId: string;
}

export interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

function newTraceId(): string {
  return `web_${Math.random().toString(36).slice(2, 8)}${Date.now().toString(36)}`;
}

export async function call<T>(caller: Caller, path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    Authorization: `Bearer ${caller.token}`,
    "X-Trace-Id": newTraceId(),
  };
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(`${API_BASE}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
    cache: "no-store",
  });
  const text = await response.text();
  if (!response.ok) {
    let problem: Problem;
    try {
      problem = JSON.parse(text) as Problem;
    } catch {
      problem = {
        code: `http_${response.status}`,
        class: "failed",
        field: null,
        message: text || response.statusText,
        retryable: false,
        retry_after: null,
        trace_id: response.headers.get("X-Trace-Id") ?? "trace_unassigned",
      };
    }
    throw new ApiProblem(problem, response.status);
  }
  return (text ? JSON.parse(text) : null) as T;
}

const projectPath = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`;

/** Projects (section 5.8). Creation is the one route that carries its own project id in the body. */
export const projects = {
  read: (caller: Caller, projectId: string) => call<Project>(caller, projectPath(projectId)),
  create: (caller: Caller, body: ProjectCreate) =>
    call<{ project_id: string; privacy_tier: string }>(caller, "/projects", {
      method: "POST",
      // The request models forbid unknown keys, so a blank optional list is left out rather than sent empty.
      body: body.regulatory_profile?.length ? body : { ...body, regulatory_profile: undefined },
    }),
};

/** Data registration (section 5.9): declare, upload to the signed PUT, then complete with the same body. */
export const assets = {
  list: (caller: Caller, projectId: string, params: { retention_class?: string; run_id?: string; limit?: number; offset?: number } = {}) =>
    call<Page<DataAsset>>(caller, `${projectPath(projectId)}/assets${queryString(params)}`),
  read: (caller: Caller, projectId: string, assetId: string) =>
    call<DataAsset>(caller, `${projectPath(projectId)}/assets/${encodeURIComponent(assetId)}`),
  declare: (caller: Caller, asset: DataAsset) =>
    call<{ asset_id: string; key: string; method: string; upload_url: string; expires_at: string; ttl_seconds: number }>(
      caller,
      `${projectPath(asset.project_id)}/assets`,
      { method: "POST", body: asset },
    ),
  complete: (caller: Caller, asset: DataAsset) =>
    call<RecordedAsset>(caller, `${projectPath(asset.project_id)}/assets/${encodeURIComponent(asset.asset_id)}/complete`, {
      method: "POST",
      body: asset,
    }),
  download: (caller: Caller, projectId: string, assetId: string) =>
    call<{ asset_id: string; key: string; method: string; download_url: string; expires_at: string; ttl_seconds: number }>(
      caller,
      `${projectPath(projectId)}/assets/${encodeURIComponent(assetId)}/download`,
    ),
  /**
   * The whole registration in the three calls the API defines. The PUT goes straight to the signed URL
   * the API handed out, with no Authorization header: the signature is the only grant it needs, and
   * the bearer token must not travel to the object plane.
   */
  register: async (caller: Caller, asset: DataAsset, bytes: Blob | Uint8Array): Promise<RecordedAsset> => {
    const grant = await assets.declare(caller, asset);
    await putSigned(grant.upload_url, grant.method, bytes);
    return assets.complete(caller, asset);
  },
};

async function putSigned(url: string, method: string, bytes: Blob | Uint8Array): Promise<void> {
  // Either way nothing is recorded: the row is only written at completion, and the PUT never got there.
  const refused = (code: string, message: string, status: number) =>
    new ApiProblem(
      {
        code,
        class: "retryable_failure",
        field: "upload_url",
        message,
        retryable: true,
        retry_after: null,
        trace_id: "trace_object_plane",
      },
      status,
    );

  let response: Response;
  try {
    response = await fetch(url, { method, body: bytes as BodyInit });
  } catch {
    throw refused(
      "object_plane_unreachable",
      `the signed host ${new URL(url).host} could not be reached from this browser, so the upload never started and no row was written`,
      502,
    );
  }
  if (!response.ok) {
    throw refused(
      "upload_failed",
      `the object plane refused the upload with ${response.status}`,
      response.status,
    );
  }
}

/** Plans and the approval of a plan before anything runs (sections 9.6, 11.6). */
export const plans = {
  record: (caller: Caller, projectId: string, plan: AnalysisPlan) =>
    call<RecordedPlan>(caller, `${projectPath(projectId)}/plans`, { method: "POST", body: plan }),
};

export const approvals = {
  list: (
    caller: Caller,
    projectId: string,
    params: { state?: ApprovalState; run_id?: string; overdue?: boolean; limit?: number; offset?: number } = {},
  ) => call<Page<Approval>>(caller, `${projectPath(projectId)}/approvals${queryString(params)}`),
  request: (
    caller: Caller,
    projectId: string,
    runId: string,
    body: { kind: ApprovalKind; node_id?: string; assigned_to?: string; plan_version?: string },
  ) =>
    call<Approval>(caller, `${projectPath(projectId)}/runs/${encodeURIComponent(runId)}/approvals`, {
      method: "POST",
      body: { kind: body.kind, node_id: body.node_id ?? null, assigned_to: body.assigned_to ?? null, plan_version: body.plan_version ?? null },
    }),
  decide: (caller: Caller, projectId: string, approvalId: string, approved: boolean, reason: string) =>
    call<Approval>(caller, `${projectPath(projectId)}/approvals/${encodeURIComponent(approvalId)}/decision`, {
      method: "POST",
      body: { approved, reason },
    }),
  delegate: (caller: Caller, projectId: string, approvalId: string, toSubject: string) =>
    call<Approval>(caller, `${projectPath(projectId)}/approvals/${encodeURIComponent(approvalId)}/delegation`, {
      method: "POST",
      body: { to_subject: toSubject },
    }),
};

/** Runs (section 9.7): a launch is accepted with 202 and a queue position, never with results. */
export const runs = {
  list: (caller: Caller, projectId: string, params: { state?: RunState; limit?: number; offset?: number } = {}) =>
    call<Page<RunListItem>>(caller, `${projectPath(projectId)}/runs${queryString(params)}`),
  read: (caller: Caller, projectId: string, runId: string) =>
    call<RunSummary>(caller, `${projectPath(projectId)}/runs/${encodeURIComponent(runId)}`),
  launch: (
    caller: Caller,
    projectId: string,
    body: {
      plan_id: string;
      plan_version: string;
      input_asset_ids: string[];
      tool_registry_snapshot: string;
      parameters?: Record<string, unknown>;
      reproducibility_level_target?: string;
      priority?: number;
    },
    idempotencyKey: string,
  ) => call<RunSummary>(caller, `${projectPath(projectId)}/runs`, { method: "POST", body, idempotencyKey }),
  cancel: (caller: Caller, projectId: string, runId: string, reason: string) =>
    call<unknown>(caller, `${projectPath(projectId)}/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      body: { reason },
    }),
  budget: (caller: Caller, projectId: string, runId: string) =>
    call<{ run_id: string; usage: Record<string, number>; ceiling: Record<string, number>; exhausted: string[] }>(
      caller,
      `${projectPath(projectId)}/runs/${encodeURIComponent(runId)}/budget`,
    ),
};

/** Candidates, cards, the queue and review (sections 5.4, 5.6, 9.3, 13.1, 13.4). */
export const findings = {
  listCandidates: (caller: Caller, projectId: string, params: { run_id?: string; cluster_id?: string; limit?: number; offset?: number } = {}) =>
    call<Page<CandidateSet>>(caller, `${projectPath(projectId)}/candidates${queryString(params)}`),
  /** Section 5.6: a cluster label is only an alias, so it is answered only together with its run. */
  evidence: (
    caller: Caller,
    projectId: string,
    by: { candidate_id: string } | { cluster_id: string; run_id: string },
  ) => call<CardEvidenceRead>(caller, `${projectPath(projectId)}/evidence${queryString(by as Record<string, string>)}`),
  alignment: (caller: Caller, projectId: string, candidateId: string) =>
    call<Alignment>(caller, `${projectPath(projectId)}/candidates/${encodeURIComponent(candidateId)}/alignment`),
  draft: (caller: Caller, projectId: string, runId: string, body: { candidate_ids?: string[]; annotation_version_id?: string } = {}) =>
    call<DraftResponse>(caller, `${projectPath(projectId)}/runs/${encodeURIComponent(runId)}/cards/draft`, {
      method: "POST",
      body,
    }),
  queue: (caller: Caller, projectId: string, runId: string, params: { client_ask?: string[]; limit?: number } = {}) =>
    call<ExpertQueue>(
      caller,
      `${projectPath(projectId)}/runs/${encodeURIComponent(runId)}/cards/queue${queryString({
        ...params,
        client_ask: params.client_ask?.join(",") ?? undefined,
      })}`,
    ),
  reviews: (caller: Caller, projectId: string, cardId: string) =>
    call<{ card_id: string; reviews: ReviewEvent[] }>(
      caller,
      `${projectPath(projectId)}/cards/${encodeURIComponent(cardId)}/reviews`,
    ),
  review: (
    caller: Caller,
    projectId: string,
    cardId: string,
    body: {
      scope: ReviewScope;
      decision: ReviewDecision;
      rationale: string;
      supersedes_review_id?: string;
      carry_over_from_candidate_id?: string;
      retraction_reason?: string;
      new_value?: Record<string, unknown>;
      previous_value?: Record<string, unknown>;
    },
  ) =>
    call<ReviewResponse>(caller, `${projectPath(projectId)}/cards/${encodeURIComponent(cardId)}/reviews`, {
      method: "POST",
      body: {
        scope: body.scope,
        decision: body.decision,
        rationale: body.rationale,
        supersedes_review_id: body.supersedes_review_id ?? null,
        carry_over_from_candidate_id: body.carry_over_from_candidate_id ?? null,
        retraction_reason: body.retraction_reason ?? null,
        new_value: body.new_value ?? null,
        previous_value: body.previous_value ?? null,
      },
    }),
  signOff: (caller: Caller, projectId: string, cardId: string, signOff: SignOff, reason?: string) =>
    call<SignOffResponse>(caller, `${projectPath(projectId)}/cards/${encodeURIComponent(cardId)}/sign_off`, {
      method: "POST",
      body: { sign_off: signOff, reason: reason ?? null },
    }),
};

/** Report export (sections 9.3, 9.7, 11.2): queueing is a job, and a download URL exists only at READY. */
export const exports_ = {
  list: (caller: Caller, projectId: string, params: { status?: string; limit?: number; offset?: number } = {}) =>
    call<Page<ExportRow>>(caller, `${projectPath(projectId)}/exports${queryString(params)}`),
  queue: (caller: Caller, projectId: string, kind: ExportKind, filters: Record<string, unknown>, idempotencyKey: string) =>
    call<ExportEnvelope>(caller, `${projectPath(projectId)}/exports`, {
      method: "POST",
      body: { kind, filters },
      idempotencyKey,
    }),
  read: (caller: Caller, projectId: string, exportId: string) =>
    call<ExportRow>(caller, `${projectPath(projectId)}/exports/${encodeURIComponent(exportId)}`),
};

/** Audit and traces (section 11.2): the workbench shows a person their own trail. */
export const compliance = {
  audit: (caller: Caller, projectId: string, params: { limit?: number; offset?: number } = {}) =>
    call<Page<Record<string, unknown>>>(caller, `${projectPath(projectId)}/audit${queryString(params)}`),
};

export function queryString(params: Record<string, unknown>): string {
  const pairs = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  return pairs.length ? `?${pairs.join("&")}` : "";
}

/** An idempotency key per user intent, so a double click cannot start a second run or export. */
export function intentKey(intent: string): string {
  return `${intent}_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}

export type { RunEventFrame };
