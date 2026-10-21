"use client";

import Link from "next/link";

import { Empty, Loading, Panel, ProblemNote } from "@/components/ui";
import { findings, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync } from "@/lib/useAsync";
import type { Alignment } from "@/lib/types";

/**
 * What this label met in other runs (plan V2.0 section 5.6).
 *
 * The history is reported against the thresholds in force, so a reclassification is visible rather
 * than silently restated: each row carries both the recorded kind and what it counts as today.
 */
export default function AlignmentPage({ params }: { params: { projectId: string; candidateId: string } }) {
  const t = useT();
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  const candidateId = decodeURIComponent(params.candidateId);

  if (!caller) {
    return (
      <Panel title={t("Label alignment")} note={t("nothing is read until a token is set")}>
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return <AlignmentView caller={caller} projectId={projectId} candidateId={candidateId} />;
}

function AlignmentView({ caller, projectId, candidateId }: { caller: Caller; projectId: string; candidateId: string }) {
  const t = useT();
  const read = useAsync<Alignment>(
    () => findings.alignment(caller, projectId, candidateId),
    [caller.token, projectId, candidateId],
  );
  const alignment = read.data;

  return (
    <Panel
      title={t("Label alignment")}
      note={t(
        "the same label in another run is a comparison, not a verdict: only a review can carry a conclusion across (§5.6)",
      )}
    >
      <ProblemNote error={read.error} />
      {!alignment ? <Loading on={read.loading} /> : null}
      {alignment ? (
        <>
          <p className="muted mono">
            {alignment.cluster_id} · {alignment.candidate_id} · {t("thresholds")} {alignment.thresholds_version} ·{" "}
            {t("member fingerprint")} {alignment.member_fingerprint}
          </p>
          <p>
            <Link href={`/projects/${projectId}/candidates/${encodeURIComponent(candidateId)}`}>
              {t("back to the card")}
            </Link>
          </p>
          <h3>{t("Alignment history")}</h3>
          {alignment.alignment_history.length === 0 ? (
            <Empty>{t("no alignment has been recorded for this label.")}</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{t("OTHER CANDIDATE")}</th>
                  <th>{t("OTHER RUN")}</th>
                  <th>{t("JACCARD")}</th>
                  <th>{t("KIND")}</th>
                  <th>{t("KIND TODAY")}</th>
                  <th>{t("CARRY OVER")}</th>
                  <th>{t("DECIDED BY")}</th>
                </tr>
              </thead>
              <tbody>
                {alignment.alignment_history.map((record) => (
                  <tr key={`${record.other_run_id}/${record.other_candidate_id}`}>
                    <td className="mono">{record.other_candidate_id}</td>
                    <td className="mono">{record.other_run_id}</td>
                    <td>{record.jaccard}</td>
                    <td>{t(record.kind)}</td>
                    <td>{t(record.kind_under_current_thresholds)}</td>
                    <td>{record.carry_over ? t("yes") : t("no")}</td>
                    <td>{record.decided_by ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <h3>{t("Same label in other runs")}</h3>
          {alignment.same_label_in_other_runs.length === 0 ? (
            <Empty>{t("no other candidate carries this label.")}</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{t("CANDIDATE")}</th>
                  <th>{t("RUN")}</th>
                  <th>{t("SIZE")}</th>
                  <th>{t("DONORS")}</th>
                  <th>{t("ALIGNED")}</th>
                </tr>
              </thead>
              <tbody>
                {alignment.same_label_in_other_runs.map((other) => (
                  <tr key={`${other.run_id}/${other.candidate_id}`}>
                    <td>
                      <Link
                        className="mono"
                        href={`/projects/${projectId}/candidates/${encodeURIComponent(other.candidate_id)}`}
                      >
                        {other.candidate_id}
                      </Link>
                    </td>
                    <td className="mono">{other.run_id}</td>
                    <td>{other.size}</td>
                    <td>{other.donor_count}</td>
                    <td>{other.aligned ? t("yes") : t("no")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      ) : null}
    </Panel>
  );
}
