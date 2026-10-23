"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Chip, Empty, Field, Loading, Panel, ProblemNote } from "@/components/ui";
import { findings, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync } from "@/lib/useAsync";
import type { CandidateSet, Page } from "@/lib/types";

const PAGE_SIZE = 50;

/** The address-bar names this screen owns, so a filtered page can be handed over as a link. */
const FIELDS = ["cluster_id", "min_size", "max_size", "min_donors", "reviewed"] as const;

export default function RunCandidatesPage({ params }: { params: { projectId: string; runId: string } }) {
  return (
    <Suspense fallback={<Loading on />}>
      <Search projectId={params.projectId} runId={decodeURIComponent(params.runId)} />
    </Suspense>
  );
}

function Search({ projectId, runId }: { projectId: string; runId: string }) {
  const t = useT();
  const { caller, projectId: scoped, blocked } = useProjectScope(projectId);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // The fields hold what the person typed; the address bar holds what the screen actually asked for.
  // Applying is the step that moves one into the other, so a read is never paid for by half-entered
  // numbers and the page a colleague opens answers to the link, not to whatever was typed last.
  const [draft, setDraft] = useState<Record<string, string>>({});
  useEffect(() => {
    setDraft(Object.fromEntries(FIELDS.map((name) => [name, searchParams.get(name) ?? ""])));
  }, [searchParams]);

  const ask = (name: string) => (event: { target: { value: string } }) =>
    setDraft((current) => ({ ...current, [name]: event.target.value }));

  const applied = Object.fromEntries(
    FIELDS.map((name) => [name, searchParams.get(name) ?? ""]).filter(([, value]) => value !== ""),
  );
  const query = searchParams.toString();

  // The read has to be declared before the signed-out return: the identity arrives after the first
  // client render, and a hook that only exists once a token is set changes the hook count.
  const read = useAsync<Page<CandidateSet>>(
    () =>
      caller
        ? findings.listCandidates(caller, scoped, {
            run_id: runId,
            ...(applied.cluster_id ? { cluster_id: applied.cluster_id } : {}),
            ...(applied.min_size ? { min_size: Number(applied.min_size) } : {}),
            ...(applied.max_size ? { max_size: Number(applied.max_size) } : {}),
            ...(applied.min_donors ? { min_donors: Number(applied.min_donors) } : {}),
            ...(applied.reviewed ? { reviewed: applied.reviewed === "yes" } : {}),
            limit: PAGE_SIZE,
          })
        : Promise.resolve({ items: [], limit: 0, offset: 0 }),
    [caller?.token, scoped, runId, query],
  );

  if (!caller) {
    return (
      <Panel title={t("Find a population")} note={t("nothing is read until a token is set")}>
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  const apply = () => {
    const next = new URLSearchParams();
    FIELDS.forEach((name) => {
      const value = (draft[name] ?? "").trim();
      if (value) next.set(name, value);
    });
    const written = next.toString();
    router.replace(written ? `${pathname}?${written}` : pathname);
  };

  const clear = () => {
    setDraft(Object.fromEntries(FIELDS.map((name) => [name, ""])));
    router.replace(pathname);
  };

  const rows = read.data?.items ?? [];

  return (
    <>
      <Panel
        title={t("Find a population")}
        note={t(
          "these are the populations this run produced, and a cluster label is only the name it gave them (§5.6); every filter is answered by the index before the page is cut",
        )}
        actions={
          <div style={{ display: "flex", gap: 8 }}>
            <button className="secondary" type="button" onClick={clear}>
              {t("clear the filters")}
            </button>
            <button className="secondary" type="button" disabled={read.loading} onClick={read.reload}>
              {t("re-read")}
            </button>
          </div>
        }
      >
        <div className="grid">
          <Field label="cluster_id" hint={t("the label as this run wrote it; it names nothing outside this run")}>
            <input className="mono" value={draft.cluster_id ?? ""} onChange={ask("cluster_id")} />
          </Field>
          <Field label={t("reviewed")} hint={t("whether a person has already acted on the card behind it")}>
            <select value={draft.reviewed ?? ""} onChange={ask("reviewed")}>
              <option value="">{t("either")}</option>
              <option value="yes">{t("acted on")}</option>
              <option value="no">{t("nobody yet")}</option>
            </select>
          </Field>
        </div>
        <div className="grid">
          <Field label={t("min cells")} hint={t("smallest population to keep, in cells")}>
            <input value={draft.min_size ?? ""} onChange={ask("min_size")} inputMode="numeric" />
          </Field>
          <Field label={t("max cells")} hint={t("largest population to keep, in cells")}>
            <input value={draft.max_size ?? ""} onChange={ask("max_size")} inputMode="numeric" />
          </Field>
          <Field label={t("min donors")} hint={t("fewest donors the population has to span")}>
            <input value={draft.min_donors ?? ""} onChange={ask("min_donors")} inputMode="numeric" />
          </Field>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" onClick={apply}>
            {t("Apply")}
          </button>
          <Link href={`/projects/${scoped}/runs/${encodeURIComponent(runId)}`}>{t("back to the run")}</Link>
          <Link href={`/projects/${scoped}/queue?run_id=${encodeURIComponent(runId)}`}>{t("expert queue")}</Link>
        </div>
        <p className="muted">
          {t(
            "The address bar is the filter: apply it and the link you send opens this same page, not whatever you were last looking at.",
          )}
        </p>
        <ProblemNote error={read.error} />
        <Loading on={read.loading} />
        {rows.length ? (
          <>
            <p className="muted">
              {t("{n} populations on this page", { n: rows.length })}
              {applied.cluster_id ? ` · ${applied.cluster_id}` : ""}
              {applied.min_size ? ` · ${t("at least {n} cells", { n: applied.min_size })}` : ""}
              {applied.max_size ? ` · ${t("at most {n} cells", { n: applied.max_size })}` : ""}
              {applied.min_donors ? ` · ${t("at least {n} donors", { n: applied.min_donors })}` : ""}
              {applied.reviewed
                ? ` · ${applied.reviewed === "yes" ? t("acted on") : t("nobody yet")}`
                : ""}
            </p>
            <table>
              <thead>
                <tr>
                  <th>{t("population")}</th>
                  <th>{t("cells")}</th>
                  <th>{t("donors")}</th>
                  <th>{t("candidate type")}</th>
                  <th>{t("card")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.candidate_id}>
                    <td>
                      <span className="mono">{row.candidate_id}</span>
                      <div className="muted">{row.display_name}</div>
                    </td>
                    <td>{row.size}</td>
                    <td>{row.donor_count}</td>
                    <td>
                      {(row.candidate_types ?? []).map((one) => (
                        <Chip key={one} title={one}>
                          {t(one)}
                        </Chip>
                      ))}
                    </td>
                    <td>
                      <Link href={`/projects/${scoped}/candidates/${encodeURIComponent(row.candidate_id)}`}>
                        {t("open the card")}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : (
          !read.loading && (
            <Empty>
              {t(
                "No population in this run answers to that filter. The list is what the server returned, so the filter is not hiding rows it did not ask for.",
              )}
            </Empty>
          )
        )}
      </Panel>
    </>
  );
}
