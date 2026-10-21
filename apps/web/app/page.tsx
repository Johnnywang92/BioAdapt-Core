"use client";

import Link from "next/link";
import { useState } from "react";

import { Chip, Field, Notice, Panel, ProblemNote } from "@/components/ui";
import { projects, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useWorkbench } from "@/lib/identity";
import { useSubmit } from "@/lib/useAsync";
import type { PrivacyTier, ProjectCreate, RegulatoryProfile } from "@/lib/types";

const TIERS: PrivacyTier[] = ["public", "restricted", "strict_local", "hgr_controlled"];
const PROFILES: RegulatoryProfile[] = ["hgr", "pipl", "gdpr", "hipaa", "commercial_only"];

const TIER_NOTE: Record<PrivacyTier, string> = {
  public: "model calls and exports are allowed within the provider terms",
  restricted: "no raw egress; aggregate derivation only, and any cloud model call is checked field by field",
  strict_local: "raw data and sensitive derivations stay local; no cloud model call at all",
  hgr_controlled: "human genetic resource controlled: local compute, export refused, approval required",
};

/**
 * Starting a project (plan V2.0 section 5.8).
 *
 * The tier is asked for here, once, because every later screen inherits its limits from it: a
 * strict-local project has no cloud path to choose from, and the export screen will refuse rather
 * than offer one (§11.2). Choosing the tier is a person's decision, so this form has no default.
 */
export default function StartPage() {
  const t = useT();
  const { identity, projectId, setProjectId } = useWorkbench();
  const caller: Caller | null = identity ? { token: identity.token, subjectId: identity.subjectId } : null;
  const submit = useSubmit();
  const [form, setForm] = useState({
    project_id: "",
    study_goal: "",
    species: "",
    privacy_tier: "" as PrivacyTier | "",
    annotation_granularity_target: "",
    review_load_budget_hours_per_week: "4",
  });
  const [profiles, setProfiles] = useState<RegulatoryProfile[]>([]);

  const set = (key: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

  function create() {
    if (!caller) return;
    const body: ProjectCreate = {
      project_id: form.project_id,
      study_goal: form.study_goal,
      species: form.species,
      privacy_tier: form.privacy_tier as PrivacyTier,
      created_by: caller.subjectId,
      regulatory_profile: profiles,
      annotation_granularity_target: form.annotation_granularity_target,
      review_load_budget_hours_per_week: Number(form.review_load_budget_hours_per_week),
    };
    void submit.run(async () => {
      const created = await projects.create(caller, body);
      setProjectId(created.project_id);
      return created;
    });
  }

  return (
    <>
      <Panel
        title={t("Start or open a project")}
        note={t(
          "A project is the scope of every read: the API resolves the caller from the token and the project from the path.",
        )}
      >
        {projectId ? (
          <p>
            {t("Open")}{" "}
            <Link href={`/projects/${encodeURIComponent(projectId)}`}>
              <span className="mono">{projectId}</span>
            </Link>{" "}
            {t("to register data, approve a plan, read the expert queue and export the report.")}
          </p>
        ) : (
          <p className="muted">
            {t("No project open. Create one below, or set a project id in the panel at the bottom.")}
          </p>
        )}
      </Panel>

      <Panel
        title={t("New project")}
        note={t(
          "created_by is taken from the token you are holding, never typed in: an audit row that names a person has to name the person acting.",
        )}
      >
        <ProblemNote error={submit.error} />
        {submit.result ? (
          <Notice>
            {t("Created")}{" "}
            <span className="mono">{(submit.result as { project_id: string }).project_id}</span> —{" "}
            <Link
              href={`/projects/${encodeURIComponent((submit.result as { project_id: string }).project_id)}`}
            >
              {t("open it")}
            </Link>
          </Notice>
        ) : null}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            create();
          }}
        >
          <div className="grid">
            <Field label="project_id">
              <input value={form.project_id} onChange={set("project_id")} placeholder="prj_reference_pbmc" />
            </Field>
            <Field label="species">
              <input value={form.species} onChange={set("species")} placeholder="Homo sapiens" />
            </Field>
          </div>
          <Field
            label="study_goal"
            hint={t("what the delivery has to answer; it scopes which candidates matter")}
          >
            <textarea rows={2} value={form.study_goal} onChange={set("study_goal")} />
          </Field>
          <div className="grid">
            <Field label="annotation_granularity_target">
              <input
                value={form.annotation_granularity_target}
                onChange={set("annotation_granularity_target")}
                placeholder="cell_type_broad"
              />
            </Field>
            <Field
              label="review_load_budget_hours_per_week"
              hint={t("§13.4: this number is what the queue size has to fit inside")}
            >
              <input
                value={form.review_load_budget_hours_per_week}
                onChange={set("review_load_budget_hours_per_week")}
                inputMode="decimal"
              />
            </Field>
          </div>
          <Field
            label="privacy_tier"
            hint={form.privacy_tier ? t(TIER_NOTE[form.privacy_tier as PrivacyTier]) : t("no default on purpose")}
          >
            <select value={form.privacy_tier} onChange={set("privacy_tier")}>
              <option value="">{t("choose")}</option>
              {TIERS.map((tier) => (
                <option key={tier} value={tier}>
                  {`${t(tier)} (${tier})`}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="regulatory_profile"
            hint={t("an hgr profile forces the tier to hgr_controlled or strict_local")}
          >
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              {PROFILES.map((profile) => (
                <label key={profile} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    style={{ width: "auto" }}
                    checked={profiles.includes(profile)}
                    onChange={(event) =>
                      setProfiles((current) =>
                        event.target.checked ? [...current, profile] : current.filter((one) => one !== profile),
                      )
                    }
                  />
                  <Chip title={profile}>{t(profile)}</Chip>
                </label>
              ))}
            </div>
          </Field>
          <button type="submit" disabled={!caller || submit.busy}>
            {submit.busy ? t("creating…") : t("Create project")}
          </button>
          {!caller ? <p className="muted">{t("paste a token first")}</p> : null}
        </form>
      </Panel>
    </>
  );
}
