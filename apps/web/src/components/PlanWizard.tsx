"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { AdhocSpecForm, type AdhocSpec, type AdhocSubmission } from "@/components/AdhocSpecForm";
import { Field, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { assets as assetsApi, plans as plansApi, tools as toolsApi, type Caller } from "@/lib/api";
import { digestHex, distinct, donorsPerGroup, inspectDocument, looksH5ad, type CountsPreview } from "@/lib/countsDocument";
import { useT } from "@/lib/i18n";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type { AnalysisPlan, PlanNode, Project, ToolCatalog, ToolRow } from "@/lib/types";

/** A step as this wizard offers it: a registry row, or the project's own submission for that step (§9.6). */
interface Step extends ToolRow {
  adhoc_spec?: AdhocSpec;
}

/** One stage-0 number a step refuses to run without (§C1: these are inputs, never code defaults). */
interface ParamField {
  key: string;
  kind: "number" | "integer" | "text" | "genes";
  label?: string;
  hint?: string;
}

const QC_POLICY_FIELDS: ParamField[] = [
  { key: "policy_id", kind: "text", hint: "the id the plan's qc_policy_id repeats" },
  { key: "version", kind: "text" },
  { key: "species", kind: "text", hint: "defaults to the project's own" },
  { key: "tissue", kind: "text" },
  { key: "platform", kind: "text" },
];

/**
 * The parameters each reference step asks the driver for, read off the step's own `_require` calls.
 * A wizard that invented a value here would be the platform writing a threshold without its source,
 * which §7.7 forbids - so every field below is empty until a person types it.
 */
const STEP_PARAMS: Record<string, ParamField[]> = {
  qc: [
    { key: "thresholds_version", kind: "text", hint: "the cut-set version this run is judged against (§1.5)" },
    { key: "max_flagged_share", kind: "number", hint: "0 to 1: past this share of flagged cells the step stops" },
    { key: "mito_genes", kind: "genes", hint: "symbols this matrix calls mitochondrial; unknown names are refused" },
    { key: "ribosomal_genes", kind: "genes", hint: "symbols this matrix calls ribosomal" },
  ],
  normalize: [{ key: "target_sum", kind: "number", hint: "library size every cell is scaled to before log2(1+x)" }],
  feature_selection: [
    { key: "n_top_features", kind: "integer" },
    { key: "min_mean", kind: "number" },
    { key: "max_mean", kind: "number" },
    { key: "min_variance", kind: "number" },
    { key: "seed", kind: "integer", hint: "fixed, because the reproducibility claim rests on it" },
  ],
  embedding: [
    { key: "n_components", kind: "integer" },
    { key: "seed", kind: "integer", hint: "fixed, because the reproducibility claim rests on it" },
  ],
  neighbors: [
    { key: "k", kind: "integer" },
    { key: "seed", kind: "integer", hint: "fixed, because the reproducibility claim rests on it" },
  ],
  clustering: [
    { key: "resolution", kind: "number" },
    { key: "seed", kind: "integer", hint: "fixed, because the reproducibility claim rests on it" },
  ],
  markers: [{ key: "top_n", kind: "integer" }],
  pseudobulk_de: [
    { key: "permutation_replicates", kind: "integer", hint: "a finite number of shuffles: a p-value can never be zero" },
    { key: "seed", kind: "integer", hint: "fixed, because the reproducibility claim rests on it" },
  ],
};

/** §7.5 and §7.6 measure a candidate a finished run named, so no plan can be built for them beforehand. */
const NEEDS_A_CANDIDATE = new Set(["robustness", "coverage", "state_axis"]);

const METRICS = ["total_counts", "detected_genes", "mitochondrial_fraction", "ribosomal_fraction", "doublet_score", "ambient_score"];
const APPLIES = ["outside", "above", "below"];
const ACTIONS = ["mark", "drop"];
const SOURCES = ["knowledge_claim", "distribution_diagnostic", "manual"];

interface Rule {
  name: string;
  metric: string;
  apply: string;
  lower: string;
  upper: string;
  action: string;
  threshold_source: string;
  source_ref: string;
  reason: string;
}

const EMPTY_RULE: Rule = {
  name: "",
  metric: "total_counts",
  apply: "outside",
  lower: "",
  upper: "",
  action: "mark",
  threshold_source: "manual",
  source_ref: "",
  reason: "",
};

function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 32)
    .replace(/_+$/, "");
}

/** A number as the step reads it, or absent: an empty field is never coerced into a zero. */
function number(value: string): number | undefined {
  const text = value.trim();
  if (!text) return undefined;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function geneList(value: string, genes: string[]): string[] | undefined {
  const names = value
    .split(/[,\s]+/)
    .map((one) => one.trim())
    .filter(Boolean);
  return names.length ? names : undefined;
}

/**
 * Building a plan from what the project actually has (§5.3, §9.6).
 *
 * The tool rows come from `GET /projects/{id}/tools`, which is the same construction the executor
 * hydrates, so a node this screen offers is a node the run can run. Slots, resources and the approval
 * requirement are read from those rows rather than typed. What stays a person's own claim - the design,
 * the partitions, the stage-0 numbers, whether a confounding check ran - is asked for in words, and
 * nothing is filled in on their behalf.
 */
export function PlanWizard({ caller, projectId, project }: { caller: Caller; projectId: string; project: Project }) {
  const t = useT();
  const submit = useSubmit();
  const catalog = useAsync<ToolCatalog>(() => toolsApi.read(caller, projectId), [caller.token, projectId]);
  const listed = useAsync(() => assetsApi.list(caller, projectId, { limit: 100 }), [caller.token, projectId]);

  const [form, setForm] = useState({
    objective: "",
    plan_id: "",
    plan_version: "1",
    group_variable: "group",
    formula: "~ group",
    min_n_per_group: String((project.thresholds.min_n_per_group as number) ?? 3),
    partition_rule: "",
    root_slot: "counts",
    discovery: "",
    confirmation: "",
    confounding_name: "",
    confounding_tool: "",
    confounding_status: "NOT_RUN",
    confounding_detail: "",
    max_iterations: "1",
    max_compute_minutes: "",
    max_token_cost_usd: "0",
    expected_deliverables: "candidate_sets, evidence_cards",
    provenance_policy: "every numeric field is tool output or a human revision; no model writes a value",
    stop_conditions: "input_schema_invalid, critical_quality_failure, unresolved_design_confounding",
  });
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const [params, setParams] = useState<Record<string, Record<string, string>>>({});
  const [policy, setPolicy] = useState<Record<string, string>>({ species: project.species });
  const [rules, setRules] = useState<Rule[]>([]);
  const [resourceEdits, setResourceEdits] = useState<Record<string, Record<string, string>>>({});
  const [contrast, setContrast] = useState({ group_a: "", group_b: "" });
  const [preview, setPreview] = useState<CountsPreview | null>(null);
  const [previewName, setPreviewName] = useState("");
  const [previewProblem, setPreviewProblem] = useState<string | null>(null);
  const [submissions, setSubmissions] = useState<Record<string, AdhocSubmission>>({});
  const [adhocTask, setAdhocTask] = useState("");

  const set = (name: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [name]: event.target.value }));

  const tools = catalog.data?.tools ?? [];
  const registered = tools.filter((one) => one.runnable && !NEEDS_A_CANDIDATE.has(one.task));
  const deferred = tools.filter((one) => NEEDS_A_CANDIDATE.has(one.task));
  /** A step is the registered row, or the project's own submission standing in its place (§9.6). */
  const steps: Step[] = registered.map((row) =>
    submissions[row.task] ? adhocStep(row, submissions[row.task]) : row,
  );
  const included = steps.filter((one) => chosen[one.task]);
  const conclusionOutputs = new Set(catalog.data?.conclusion_outputs ?? []);
  const adhocRow = registered.find((one) => one.task === adhocTask) ?? null;

  /** Reading the design off the bytes: this screen uploads nothing, it only looks at what was picked. */
  async function readData(file: File | undefined) {
    setPreview(null);
    setPreviewProblem(null);
    setPreviewName("");
    if (!file) return;
    const bytes = new Uint8Array(await file.arrayBuffer());
    setPreviewName(`${file.name} (${await digestHex(bytes).then((one) => one.slice(0, 12))})`);
    if (looksH5ad(file.name, bytes)) {
      setPreviewProblem("h5ad");
      return;
    }
    if (bytes.byteLength > 40_000_000) {
      setPreviewProblem("too_big");
      return;
    }
    try {
      const document = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as Record<string, unknown>;
      const made = inspectDocument(document);
      if ("preview" in made) {
        setPreview(made.preview);
        if (made.preview.slot) setForm((current) => ({ ...current, root_slot: made.preview.slot! }));
      } else setPreviewProblem(made.problem);
    } catch {
      setPreviewProblem("not_json");
    }
  }

  const specimens = preview ? distinct(preview.cells, "specimen_id") : [];
  const groups = preview ? distinct(preview.cells, "group") : [];

  function toggle(tool: Step) {
    setChosen((current) => {
      const next = { ...current, [tool.task]: !current[tool.task] };
      if (next[tool.task]) {
        setParams((older) => ({ ...older, [tool.task]: older[tool.task] ?? {} }));
        setResourceEdits((older) => ({ ...older, [tool.task]: older[tool.task] ?? {} }));
      }
      return next;
    });
  }

  /**
   * The project's own submission takes the step's place, so the node it builds carries the bytes (§9.6).
   *
   * The step's resources start from the submission's own, because those are the facts the engine will
   * measure the node against; an edit left over from the registered step would ask for less than the
   * submission declares, which is the one thing the driver refuses.
   */
  function attach(submission: AdhocSubmission) {
    setSubmissions((current) => ({ ...current, [submission.task]: submission }));
    setResourceEdits((current) => ({ ...current, [submission.task]: {} }));
  }

  function detach(task: string) {
    setSubmissions((current) => {
      const next = { ...current };
      delete next[task];
      return next;
    });
  }

  const setParam = (task: string, key: string) => (event: { target: { value: string } }) =>
    setParams((current) => ({ ...current, [task]: { ...(current[task] ?? {}), [key]: event.target.value } }));

  const setResource = (task: string, key: string) => (event: { target: { value: string } }) =>
    setResourceEdits((current) => ({ ...current, [task]: { ...(current[task] ?? {}), [key]: event.target.value } }));

  const setRule = (index: number, key: keyof Rule) => (event: { target: { value: string } }) =>
    setRules((current) => current.map((one, position) => (position === index ? { ...one, [key]: event.target.value } : one)));

  /**
   * Slots the chosen set produces, and the slots it still has to be handed (§4.1: nothing is inferred).
   *
   * The root slot is the one the run is handed as an asset, and it is named rather than discovered: a
   * QC step re-emits the counts it screened, so "who produces counts" has two honest answers and the
   * plan has to say which one the run starts from.
   */
  const graph = useMemo(() => {
    const rootSlot = form.root_slot.trim() || "counts";
    const producer = new Map<string, string>();
    for (const tool of included)
      for (const slot of tool.output_slots) if (slot !== rootSlot) producer.set(slot, tool.task);
    const roots = new Set<string>([rootSlot]);
    const dependencies: Record<string, string[]> = {};
    const optional: Record<string, string[]> = {};
    for (const tool of included) {
      dependencies[tool.task] = [];
      optional[tool.task] = [];
      for (const entry of tool.input_slots) {
        const from = producer.get(entry.slot);
        if (from && from !== tool.task) {
          if (!dependencies[tool.task].includes(from)) dependencies[tool.task].push(from);
          if (!entry.required) optional[tool.task].push(entry.slot);
        } else if (!from && entry.required) {
          roots.add(entry.slot);
        }
      }
    }
    return { rootSlot, roots: [...roots].sort(), dependencies, optional };
  }, [included, form.root_slot]);

  const nodes: PlanNode[] = included.map((tool) => {
    const required = tool.input_slots.filter((one) => one.required).map((one) => one.slot);
    const gated = tool.requires_approval || tool.output_slots.some((one) => conclusionOutputs.has(one));
    const min = tool.min_resources;
    const edited = resourceEdits[tool.task] ?? {};
    return {
      node_id: tool.task,
      tool_name: tool.tool_name,
      tool_version: tool.tool_version,
      tool_class: tool.tool_class,
      inputs: [...required, ...(graph.optional[tool.task] ?? [])],
      outputs: tool.output_slots,
      parameters: buildParameters(tool.task),
      dependency_node_ids: graph.dependencies[tool.task] ?? [],
      resources: {
        cpu: Number(edited.cpu ?? min.cpu),
        memory_gb: Number(edited.memory_gb ?? min.memory_gb),
        gpu: Number(edited.gpu ?? min.gpu),
        max_minutes: Number(edited.max_minutes ?? min.max_minutes),
      },
      privacy_policy: tool.privacy_policy,
      requires_approval: gated,
      ...(tool.adhoc_spec ? { adhoc_spec: tool.adhoc_spec } : {}),
    };
  });

  function buildParameters(task: string): Record<string, unknown> {
    const raw = params[task] ?? {};
    const spec = STEP_PARAMS[task] ?? [];
    const out: Record<string, unknown> = {};
    for (const field of spec) {
      const value = raw[field.key] ?? "";
      if (field.kind === "genes") {
        const names = geneList(value, preview?.genes ?? []);
        if (names) out[field.key] = names;
        continue;
      }
      if (field.kind === "text") {
        if (value.trim()) out[field.key] = value.trim();
        continue;
      }
      const parsed = number(value);
      if (parsed !== undefined) out[field.key] = parsed;
    }
    if (task === "qc") out.qc_policy = qcPolicy();
    if (task === "pseudobulk_de") {
      out.thresholds = project.thresholds;
      if (contrast.group_a && contrast.group_b)
        out.contrasts = [{ group_a: contrast.group_a, group_b: contrast.group_b, arms: "condition" }];
    }
    return out;
  }

  /** §7.7: a policy is a versioned object with a source for every bound, so the form is its own record. */
  function qcPolicy(): Record<string, unknown> {
    return {
      policy_id: policy.policy_id ?? "",
      version: policy.version ?? "",
      species: policy.species || project.species,
      tissue: policy.tissue ?? "",
      platform: policy.platform ?? "",
      policy_kind: "qc_policy_v1",
      rules: rules.map((one) => ({
        name: one.name,
        metric: one.metric,
        apply: one.apply,
        lower: number(one.lower) ?? null,
        upper: number(one.upper) ?? null,
        action: one.action,
        threshold_source: one.threshold_source,
        source_ref: one.source_ref.trim() || null,
        reason: one.reason.trim() || null,
        ...(one.threshold_source === "knowledge_claim" ? { claim_reviewed: true } : {}),
      })),
    };
  }

  const body: AnalysisPlan = {
    plan_id: form.plan_id || `plan_${slug(form.objective || project.study_goal) || projectId}`,
    plan_version: form.plan_version,
    project_id: projectId,
    objective: form.objective,
    design_matrix: {
      formula: form.formula,
      group_variable: form.group_variable,
      min_n_per_group: Number(form.min_n_per_group) || 1,
      confounding_checks: form.confounding_name.trim()
        ? [
            {
              name: form.confounding_name.trim(),
              tool: form.confounding_tool.trim(),
              status: form.confounding_status as "PASS" | "FAIL" | "NOT_RUN",
              ...(form.confounding_detail.trim() ? { detail: form.confounding_detail.trim() } : {}),
            },
          ]
        : [],
    },
    data_partitions: {
      rule: form.partition_rule,
      discovery_specimens: form.discovery.split(/[,\s]+/).filter(Boolean),
      confirmation_specimens: form.confirmation.split(/[,\s]+/).filter(Boolean),
    },
    qc_policy_id: policy.policy_id ?? "",
    loop_budget: {
      max_iterations: Number(form.max_iterations) || 0,
      max_compute_minutes: Number(form.max_compute_minutes) || 1,
      max_token_cost_usd: Number(form.max_token_cost_usd) || 0,
    },
    root_inputs: graph.roots,
    nodes,
    stop_conditions: form.stop_conditions.split(/[,\s]+/).filter(Boolean),
    expected_deliverables: form.expected_deliverables.split(/[,\s]+/).filter(Boolean),
    provenance_policy: form.provenance_policy,
  };

  /** Everything this screen can see that the server will refuse, named before the person clicks. */
  const problems: string[] = [];
  const notes: string[] = [];
  if (!form.objective.trim()) problems.push(t("the objective is the sentence an expert reads first; it is empty"));
  if (!form.partition_rule.trim()) problems.push(t("the partition rule names how discovery was separated from confirmation"));
  if (!form.discovery.trim()) problems.push(t("the discovery partition has to hold at least one specimen"));
  const discoveryList = form.discovery.split(/[,\s]+/).filter(Boolean);
  const overlap = discoveryList.filter((one) => form.confirmation.split(/[,\s]+/).includes(one));
  if (overlap.length) problems.push(`${t("a specimen cannot sit in both partitions")}: ${overlap.join(", ")}`);
  if (form.confounding_name.trim() && !form.confounding_tool.trim())
    problems.push(t("a confounding check has to name the tool that ran it"));
  if (!form.confounding_name.trim() || form.confounding_status === "NOT_RUN")
    problems.push(
      t("the contract refuses a design matrix whose confounding check has not run: record the check and its result"),
    );
  if (!nodes.length) problems.push(t("a plan with no nodes runs nothing"));
  if (graph.roots.length > 1)
    problems.push(`${t("this workflow fills one root slot and these choices leave")} ${graph.roots.join(", ")}`);
  for (const node of nodes) {
    const tool = included.find((one) => one.task === node.node_id)!;
    for (const field of STEP_PARAMS[tool.task] ?? []) {
      const value = (params[tool.task] ?? {})[field.key] ?? "";
      if (!value.trim() && field.key !== "mito_genes" && field.key !== "ribosomal_genes")
        problems.push(`${node.node_id}: ${field.key} ${t("is the number the step refuses to run without")}`);
    }
    if (tool.task === "qc") {
      if (!policy.policy_id?.trim() || !policy.version?.trim() || !policy.tissue?.trim() || !policy.platform?.trim())
        problems.push(t("qc: the policy has to name its id, version, tissue and platform"));
      if (!rules.length) problems.push(t("qc: a policy with no rules screens nothing; say why in the objective"));
      for (const rule of rules) {
        if (!rule.name.trim()) problems.push(t("qc: every rule needs a name the card can cite"));
        if (rule.apply === "outside" && !rule.lower.trim() && !rule.upper.trim())
          problems.push(`${t("qc rule")} ${rule.name || "?"} ${t("needs a bound")}`);
        if (rule.apply === "above" && !rule.upper.trim()) problems.push(`${t("qc rule")} ${rule.name || "?"} ${t("needs an upper bound")}`);
        if (rule.apply === "below" && !rule.lower.trim()) problems.push(`${t("qc rule")} ${rule.name || "?"} ${t("needs a lower bound")}`);
        if (rule.threshold_source === "manual" && !rule.reason.trim())
          problems.push(`${t("qc rule")} ${rule.name || "?"} ${t("is set by hand, so §7.7 requires the reason")}`);
        if (rule.threshold_source === "knowledge_claim" && !rule.source_ref.trim())
          problems.push(`${t("qc rule")} ${rule.name || "?"} ${t("cites a claim it does not name")}`);
        if (rule.threshold_source === "distribution_diagnostic" && !rule.source_ref.trim())
          problems.push(`${t("qc rule")} ${rule.name || "?"} ${t("must name the diagnostic its quantile came from")}`);
      }
    }
    if (tool.task === "pseudobulk_de" && !(contrast.group_a && contrast.group_b))
      problems.push(t("pseudobulk_de: a contrast needs two different arms to compare"));
    for (const [key, floor] of Object.entries(tool.min_resources)) {
      const requested = Number((resourceEdits[tool.task] ?? {})[key] ?? floor);
      if (requested < Number(floor)) {
        const peak = key === "memory_gb" && tool.measured_peak_memory_mb ? ` (${tool.measured_peak_memory_mb} MB ${t("measured on")} ${tool.measured_on ?? "?"})` : "";
        problems.push(`${node.node_id}: ${key}${peak} ${t("is below the registry's floor for this tool")}`);
      }
    }
  }
  if (preview) {
    const perGroup = donorsPerGroup(preview.cells, discoveryList);
    const under = Object.entries(perGroup).filter(([, count]) => count < (Number(form.min_n_per_group) || 1));
    if (under.length)
      notes.push(
        `${t("under-powered against min_n_per_group in the discovery partition")}: ${under.map(([g, n]) => `${g}=${n}`).join(", ")}`,
      );
    for (const field of ["mito_genes", "ribosomal_genes"]) {
      const names = geneList((params.qc ?? {})[field] ?? "", preview.genes) ?? [];
      const outside = names.filter((one) => !preview.genes.includes(one));
      if (outside.length) notes.push(`${field}: ${outside.slice(0, 5).join(", ")} ${t("is not in this matrix's gene list")}`);
    }
    if (preview.transform && preview.transform !== "counts")
      notes.push(t("the pseudobulk step sums counts and refuses a matrix declared as something else"));
  }
  if (deferred.length)
    notes.push(
      `${deferred.map((one) => one.task).join(", ")} ${t("measure a candidate a finished run named, so they are not offered here")}`,
    );
  if (!listed.loading && !(listed.data?.items ?? []).length)
    notes.push(t("nothing is registered in this project yet, so a run launched from this plan has no input asset to name"));

  function record() {
    void submit.run(() => plansApi.record(caller, projectId, body));
  }

  const recorded = submit.result as { plan_id: string; plan_version: string; plan_sha256: string } | null;

  return (
    <Panel
      title={t("Build a plan")}
      note={t(
        "the tool rows, their slots and their resource floors are read from the same registry the run executes against; the design, the partitions and the stage-0 numbers stay yours to state",
      )}
    >
      <ProblemNote error={catalog.error ?? listed.error ?? submit.error} />
      {recorded ? (
        <Notice>
          {t("Recorded")} <span className="mono">{recorded.plan_id}</span>
          {t(", version")} <span className="mono">{recorded.plan_version}</span>
          {t(" as digest")} <span className="mono">{recorded.plan_sha256}</span>.{" "}
          <Link href={`/projects/${encodeURIComponent(projectId)}`}>{t("launch it from the overview")}</Link>
        </Notice>
      ) : null}
      <Loading on={catalog.loading} />
      {previewProblem ? (
        <Notice>
          {previewProblem === "h5ad"
            ? t("an h5ad cannot be read in a browser; convert it first, then point this screen at the JSON it writes")
            : previewProblem === "too_big"
              ? t("this file is too large to read here, so the design below has to be typed")
              : t("this file is not a counts document, so no design could be read from it")}{" "}
          <span className="mono">{previewProblem}</span>
        </Notice>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          record();
        }}
      >
        <Field
          label={t("read the design from the data file")}
          hint={t("this uploads nothing: it is the same bytes the data screen registered, opened here to list specimens, donors and groups")}
        >
          <input type="file" onChange={(event) => void readData(event.target.files?.[0])} />
        </Field>
        {preview ? (
          <p className="muted mono">
            {previewName} · {preview.cells.length} {t("cells")} · {distinct(preview.cells, "specimen_id").length}
            {t("specimens")} · {distinct(preview.cells, "donor_id").length} {t("donors")}
          </p>
        ) : null}

        <div className="grid">
          <Field label={t("objective")} hint={t("what the run is being asked to settle, in one sentence")}>
            <input value={form.objective} onChange={set("objective")} />
          </Field>
          <Field label="plan_id" hint={t("derived from the objective when left empty")}>
            <input className="mono" value={form.plan_id} onChange={set("plan_id")} />
          </Field>
        </div>

        <fieldset>
          <legend>{t("Design")}</legend>
          <div className="grid">
            <Field label="group_variable" hint={preview ? t("the column this matrix actually carries") : t("the design column named on every cell")}>
              <input value={form.group_variable} onChange={set("group_variable")} list="observed-groups" />
            </Field>
            <Field label="formula">
              <input className="mono" value={form.formula} onChange={set("formula")} />
            </Field>
          </div>
          {preview ? (
            <>
              <datalist id="observed-groups">
                {groups.map((one) => (
                  <option key={one} value={one} />
                ))}
              </datalist>
              <p className="muted mono">
                {t("donors per group in the whole matrix")}:{" "}
                {Object.entries(donorsPerGroup(preview.cells))
                  .map(([group, count]) => `${group}=${count}`)
                  .join(", ")}
              </p>
            </>
          ) : null}
          <div className="grid">
            <Field label="min_n_per_group" hint={t("defaults to the cut set in force; a run reports the groups under it rather than dropping them")}>
              <input value={form.min_n_per_group} onChange={set("min_n_per_group")} />
            </Field>
            <Field label="plan_version">
              <input value={form.plan_version} onChange={set("plan_version")} />
            </Field>
          </div>
          <div className="grid">
            <Field label={t("confounding check name")} hint={t("what was looked at, e.g. group_vs_donor")}>
              <input value={form.confounding_name} onChange={set("confounding_name")} />
            </Field>
            <Field label={t("which tool ran it")}>
              <input value={form.confounding_tool} onChange={set("confounding_tool")} />
            </Field>
          </div>
          <div className="grid">
            <Field label={t("its result")} hint={t("NOT_RUN is honest and the contract refuses it: a check that has not run is a gap, not a placeholder")}>
              <select value={form.confounding_status} onChange={set("confounding_status")}>
                {["NOT_RUN", "PASS", "FAIL"].map((one) => (
                  <option key={one} value={one}>
                    {one}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("detail (optional)")}>
              <input value={form.confounding_detail} onChange={set("confounding_detail")} />
            </Field>
          </div>
          {form.confounding_status === "PASS" ? (
            <Notice>
              {t(
                "Declaring PASS is a claim about a check that ran and passed. This screen cannot run one, so it leaves the field at NOT_RUN and believes what you type.",
              )}
            </Notice>
          ) : null}
        </fieldset>

        <fieldset>
          <legend>{t("Partitions")}</legend>
          <Field label="rule" hint={t("the rule that separated discovery from confirmation, named so a reviewer can check it")}>
            <input value={form.partition_rule} onChange={set("partition_rule")} />
          </Field>
          <Field label="discovery_specimens" hint={t("ids separated by commas; pick them from the list the data file gave")}>
            <textarea rows={2} value={form.discovery} onChange={set("discovery")} className="mono" />
          </Field>
          {specimens.length ? (
            <div className="chips">
              {specimens.map((one) => (
                <button
                  className="chip secondary"
                  key={one}
                  type="button"
                  onClick={() => setForm((current) => ({ ...current, discovery: current.discovery ? `${current.discovery}, ${one}` : one }))}
                >
                  {one}
                </button>
              ))}
            </div>
          ) : null}
          <Field label="confirmation_specimens" hint={t("leave empty when the study has no independent confirmation set; say so in the objective")}>
            <textarea rows={2} value={form.confirmation} onChange={set("confirmation")} className="mono" />
          </Field>
        </fieldset>

        <fieldset>
          <legend>{t("Steps")}</legend>
          <Field
            label="root_inputs"
            hint={t("the slot the run is handed one registered asset for; this workflow fills exactly one")}
          >
            <input className="mono" value={form.root_slot} onChange={set("root_slot")} />
          </Field>
          <p className="muted">
            {t(
              "the name, version, slots and resource floor of every step below come from the registry this deployment hydrates; a node that emits a conclusion asset or is ad-hoc is gated whatever this screen thinks",
            )}
          </p>
          {steps.map((tool) => {
            const on = !!chosen[tool.task];
            const own = !!tool.adhoc_spec;
            return (
              <div className="step-row" key={tool.task}>
                <label className="check">
                  <input type="checkbox" checked={on} onChange={() => toggle(tool)} />
                  <span className="mono">
                    {tool.tool_name}@{tool.tool_version}
                  </span>
                  <span className="muted">
                    {` ${t("reads")} ${tool.input_slots.filter((one) => one.required).map((one) => one.slot).join(", ") || "—"}`}
                    {` · ${t("writes")} ${tool.output_slots.join(", ") || "—"}`}
                  </span>
                  {tool.requires_approval || tool.output_slots.some((one) => conclusionOutputs.has(one)) ? (
                    <span
                      className="chip chip--provisional"
                      title={gateReason(conclusionOutputs, tool.output_slots, tool.tool_class).join(", ")}
                    >
                      {t("gated")}
                    </span>
                  ) : null}
                </label>
                {own ? (
                  <div className="step-body">
                    <p className="muted">
                      {t(
                        "this step runs the tool your project declared instead of the registered one; it runs once, inside the cage, and its outputs are recorded as produced by that tool, so a PI has to approve exactly these bytes",
                      )}
                    </p>
                    <button className="secondary" type="button" onClick={() => setAdhocTask(tool.task)}>
                      {t("edit the submission")}
                    </button>
                    <button className="secondary" type="button" onClick={() => detach(tool.task)}>
                      {t("keep the registered step")}
                    </button>
                  </div>
                ) : null}
                {on ? (
                  <div className="step-body">
                    <div className="grid">
                      {(["cpu", "memory_gb", "gpu", "max_minutes"] as const).map((key) => (
                        <Field key={key} label={key} hint={`${t("at least")} ${tool.min_resources[key]}`}>
                          <input
                            value={resourceEdits[tool.task]?.[key] ?? String(tool.min_resources[key])}
                            onChange={setResource(tool.task, key)}
                          />
                        </Field>
                      ))}
                    </div>
                    {(STEP_PARAMS[tool.task] ?? []).map((field) => (
                      <div className="grid" key={field.key}>
                        <Field
                          label={field.label ?? field.key}
                          hint={
                            field.hint
                              ? t(field.hint)
                              : undefined
                          }
                        >
                          <input
                            className={field.kind === "text" ? "mono" : undefined}
                            value={(params[tool.task] ?? {})[field.key] ?? ""}
                            onChange={setParam(tool.task, field.key)}
                          />
                        </Field>
                        {field.key === "mito_genes" || field.key === "ribosomal_genes" ? (
                          <p className="muted mono">
                            {preview
                              ? `${preview.genes.length} ${t("gene symbols in this matrix")}`
                              : t("no matrix read here, so the names cannot be checked against one")}
                          </p>
                        ) : null}
                      </div>
                    ))}
                    {tool.task === "pseudobulk_de" ? (
                      <div className="grid">
                        <Field label="group_a" hint={t("the contrast's first arm")}>
                          <input value={contrast.group_a} list="observed-groups" onChange={(event) => setContrast((c) => ({ ...c, group_a: event.target.value }))} />
                        </Field>
                        <Field label="group_b" hint={t("the contrast's second arm")}>
                          <input value={contrast.group_b} list="observed-groups" onChange={(event) => setContrast((c) => ({ ...c, group_b: event.target.value }))} />
                        </Field>
                        <p className="muted">
                          {t("the thresholds handed to this step are the set in force on the project, verbatim:")}{" "}
                          <span className="mono">{String(project.thresholds.version ?? "?")}</span>
                          {project.thresholds.confirmed ? null : (
                            <>
                              {" "}
                              {t("which §1.5 reports as unconfirmed, so a finding computed on it cannot be signed off or exported")} —{" "}
                              <Link href={`/projects/${encodeURIComponent(projectId)}#thresholds`}>{t("attest the measured cuts")}</Link>
                            </>
                          )}
                        </p>
                      </div>
                    ) : null}
                    {tool.task === "qc" ? (
                      <fieldset>
                        <legend>{t("The QC policy this node runs (§7.7)")}</legend>
                        <div className="grid">
                          {QC_POLICY_FIELDS.map((field) => (
                            <Field key={field.key} label={field.key} hint={field.hint ? t(field.hint) : t("the step refuses to run without it")}>
                              <input value={policy[field.key] ?? ""} onChange={(event) => setPolicy((c) => ({ ...c, [field.key]: event.target.value }))} />
                            </Field>
                          ))}
                        </div>
                        {rules.map((rule, index) => (
                          <div className="rule-row" key={index}>
                            <input className="mono" placeholder="name" value={rule.name} onChange={setRule(index, "name")} />
                            <select value={rule.metric} onChange={setRule(index, "metric")}>
                              {METRICS.map((one) => (
                                <option key={one} value={one}>
                                  {one}
                                </option>
                              ))}
                            </select>
                            <select value={rule.apply} onChange={setRule(index, "apply")}>
                              {APPLIES.map((one) => (
                                <option key={one} value={one}>
                                  {one}
                                </option>
                              ))}
                            </select>
                            <input placeholder="lower" value={rule.lower} onChange={setRule(index, "lower")} />
                            <input placeholder="upper" value={rule.upper} onChange={setRule(index, "upper")} />
                            <select value={rule.action} onChange={setRule(index, "action")}>
                              {ACTIONS.map((one) => (
                                <option key={one} value={one}>
                                  {one}
                                </option>
                              ))}
                            </select>
                            <select value={rule.threshold_source} onChange={setRule(index, "threshold_source")}>
                              {SOURCES.map((one) => (
                                <option key={one} value={one}>
                                  {one}
                                </option>
                              ))}
                            </select>
                            <input
                              className="mono"
                              placeholder={rule.threshold_source === "manual" ? "reason" : "source_ref"}
                              value={rule.threshold_source === "manual" ? rule.reason : rule.source_ref}
                              onChange={setRule(index, rule.threshold_source === "manual" ? "reason" : "source_ref")}
                            />
                            <button className="secondary" type="button" onClick={() => setRules((c) => c.filter((_, i) => i !== index))}>
                              {t("remove")}
                            </button>
                          </div>
                        ))}
                        <button className="secondary" type="button" onClick={() => setRules((c) => [...c, { ...EMPTY_RULE }])}>
                          {t("add a rule")}
                        </button>
                        <p className="muted">
                          {t(
                            "mark keeps the cell and labels it at risk; drop deletes it, and §7.7 only allows that with a named approver, a reason and a report annotation - which this form refuses rather than assuming.",
                          )}
                        </p>
                      </fieldset>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </fieldset>

        <fieldset>
          <legend>{t("Your own tool for a step")}</legend>
          <p className="muted">
            {t(
              "when the registered step does not answer this project's question, the project may declare its own tool for that step (§9.6). It is stated once, here: the plan pins the bytes, and a PI approves exactly them before the step may run once, inside the cage",
            )}
          </p>
          <Field
            label={t("the step to put your own tool in")}
            hint={t("the registered step for it stays available until you use your own")}
          >
            <select value={adhocTask} onChange={(event) => setAdhocTask(event.target.value)}>
              <option value="">{t("choose a step")}</option>
              {registered.map((row) => (
                <option key={row.task} value={row.task}>
                  {`${row.task} (${row.tool_name}@${row.tool_version})`}
                </option>
              ))}
            </select>
          </Field>
          {adhocRow ? (
            <AdhocSpecForm
              key={adhocRow.task}
              row={adhocRow}
              limits={catalog.data?.adhoc_limits ?? null}
              initial={submissions[adhocRow.task]?.spec}
              onSubmit={attach}
              onClear={() => {
                detach(adhocRow.task);
                setAdhocTask("");
              }}
            />
          ) : null}
          {Object.keys(submissions).length ? (
            <ul className="steps">
              {Object.entries(submissions).map(([task, one]) => (
                <li key={task}>
                  {`${task}: ${one.spec.tool_name}@${one.spec.tool_version}`}
                </li>
              ))}
            </ul>
          ) : null}
        </fieldset>

        <fieldset>
          <legend>{t("Budget and stops")}</legend>
          <div className="grid">
            <Field label="max_iterations">
              <input value={form.max_iterations} onChange={set("max_iterations")} />
            </Field>
            <Field label="max_compute_minutes" hint={t("the quota the project was given is what the server compares this to")}>
              <input value={form.max_compute_minutes} onChange={set("max_compute_minutes")} />
            </Field>
            <Field label="max_token_cost_usd">
              <input value={form.max_token_cost_usd} onChange={set("max_token_cost_usd")} />
            </Field>
          </div>
          <Field label="stop_conditions" hint={t("the three mandatory stops are already here; add more, do not remove them")}>
            <input className="mono" value={form.stop_conditions} onChange={set("stop_conditions")} />
          </Field>
          <Field label="expected_deliverables" hint={t("what this plan promises to produce; conclusion assets are the ones a person has to sign")}>
            <input className="mono" value={form.expected_deliverables} onChange={set("expected_deliverables")} />
          </Field>
          <Field label="provenance_policy">
            <input value={form.provenance_policy} onChange={set("provenance_policy")} />
          </Field>
        </fieldset>

        {problems.length ? (
          <Notice>
            <strong>{t("This screen can see the plan is not complete")}</strong>
            <ul className="steps">
              {problems.map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
            <p>{t("Recording is still allowed: the server's own validators have the last word, and a refusal it gives is shown here in its words.")}</p>
          </Notice>
        ) : null}
        {notes.length ? (
          <p className="muted">
            <ul className="steps">
              {notes.map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
          </p>
        ) : null}

        <details>
          <summary>{t("the body this form sends")}</summary>
          <textarea rows={18} spellCheck={false} className="mono" readOnly value={JSON.stringify(body, null, 2)} />
        </details>

        <button type="submit" disabled={submit.busy || !nodes.length}>
          {submit.busy ? t("recording…") : t("Record this plan")}
        </button>
      </form>
    </Panel>
  );
}

/** The chip's tooltip names the output that makes the node gated, so the requirement is traceable. */
function gateReason(outputs: Set<string>, slots: string[], toolClass: string): string[] {
  if (toolClass === "project_adhoc") return ["ad-hoc"];
  const hit = slots.filter((one) => outputs.has(one));
  return hit.length ? hit : ["ad-hoc"];
}

/**
 * The step as it reads once the project has put its own tool in its place (§9.6).
 *
 * The slots stay the ones the task reads and writes - the step is the same step - and everything that
 * names the tool changes to the submission's own: the identity the outputs are recorded under, the
 * resource floor the engine measures the node against, and the approval it carries.
 */
function adhocStep(row: ToolRow, submission: AdhocSubmission): Step {
  const spec = submission.spec;
  const measured = spec.resources.measured_peak_memory_mb;
  return {
    ...row,
    tool_name: spec.tool_name,
    tool_version: spec.tool_version,
    tool_class: "project_adhoc",
    requires_approval: true,
    min_resources: {
      cpu: spec.resources.cpu,
      memory_gb: Math.max(1, Math.ceil(measured / 1024)),
      gpu: 0,
      max_minutes: spec.resources.max_minutes,
    },
    measured_peak_memory_mb: measured,
    measured_on: spec.resources.measured_on,
    quality_checks: spec.quality_checks,
    assumptions: spec.assumptions,
    invalid_when: spec.invalid_when,
    input_schema: spec.input_schema,
    output_schema: spec.output_schema,
    container_image: spec.container_image,
    reference_compatibility: spec.reference_compatibility,
    license: spec.license,
    network_mode: "closed",
    egress_allowlist: [],
    failure_policy: spec.failure_policy,
    retry_limit: 0,
    reproducibility_level_target: spec.reproducibility_level_target,
    adhoc_spec: spec,
  };
}
