"use client";

import { createContext, useContext, type ReactNode } from "react";

import { Chip, Notice } from "@/components/ui";
import { deployment } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { DeploymentCapabilities } from "@/lib/types";
import { useAsync, type AsyncState } from "@/lib/useAsync";

type Capability = "object_upload" | "run_execution" | "export_generation";
const LABELS: Record<Capability, string> = {
  object_upload: "Object uploads",
  run_execution: "Analysis execution",
  export_generation: "Export generation",
};
const UNAVAILABLE: Record<Capability, string> = {
  object_upload: "Object storage is not connected. Uploads cannot complete in this deployment. Ask the deployment administrator to connect storage before uploading.",
  run_execution: "No automatic analysis worker is connected. Recording a run request does not start analysis. Use an existing demonstration run to inspect results, or ask the deployment administrator how to execute the run.",
  export_generation: "No export worker is connected. An export request can be recorded, but it will not generate a file. Ask the deployment administrator to connect a worker; polling cannot resolve this.",
};
const STATUS_LABELS = {
  configured: "Declared connected (not health-checked)",
  unavailable: "Not connected",
  unknown: "Not confirmed",
};
const CAPABILITIES: Capability[] = ["object_upload", "run_execution", "export_generation"];

const DeploymentContext = createContext<AsyncState<DeploymentCapabilities> | null>(null);

export function DeploymentProvider({ children }: { children: ReactNode }) {
  const state = useAsync(deployment.read, []);
  return <DeploymentContext.Provider value={state}>{children}</DeploymentContext.Provider>;
}

export function useDeployment() {
  const state = useContext(DeploymentContext);
  if (!state) throw new Error("DeploymentProvider is required");
  return state;
}

/**
 * A one-line declaration strip. The three capabilities are still named on every screen, because a
 * person has to know that uploading or starting a run will not complete here, but the explanation is
 * kept in the disclosure instead of occupying the top of every page.
 */
export function DeploymentStatus() {
  const t = useT();
  const { data, error, loading, reload } = useDeployment();
  const known = !error && !loading ? data : null;
  const statusOf = (capability: Capability) => known?.[capability] ?? "unknown";
  const statuses = CAPABILITIES.map(statusOf);
  const overall = statuses.includes("unavailable")
    ? "unavailable"
    : statuses.includes("unknown")
      ? "unknown"
      : "configured";
  return (
    <section className="deployment-status" aria-label={t("Deployment capabilities")}>
      <div className="deployment-heading">
        <span className={`status-dot status-dot--${overall}`} aria-hidden="true" />
        <strong>{t("Deployment capabilities")}</strong>
        <Chip>
          {t(
            known?.mode === "reference"
              ? "Reference demonstration"
              : known?.mode === "configured"
                ? "Configured deployment"
                : "Deployment not confirmed",
          )}
        </Chip>
        <span className="deployment-capabilities">
          {CAPABILITIES.map((capability) => (
            <span className="capability" key={capability}>
              <span className={`status-dot status-dot--${statusOf(capability)}`} aria-hidden="true" />
              {t(LABELS[capability])}: {t(STATUS_LABELS[statusOf(capability) as keyof typeof STATUS_LABELS])}
            </span>
          ))}
        </span>
        <button type="button" className="small-button secondary" onClick={reload} disabled={loading}>
          {t("re-read")}
        </button>
      </div>
      <details className="deployment-details">
        <summary>{t("what this declaration does and does not mean")}</summary>
        <p className="muted" role="status">
          {t(
            loading
              ? "Reading deployment declaration…"
              : error
                ? "Deployment information could not be read. Capabilities remain unconfirmed; ask the deployment administrator to check the API, then retry."
                : "These are deployment declarations, not live health checks or permission grants. Project policies still apply.",
          )}
        </p>
      </details>
    </section>
  );
}

export function DeploymentNotice({ capability }: { capability: Capability }) {
  const t = useT();
  const { data, error, loading } = useDeployment();
  const status = !error && !loading ? data?.[capability] : "unknown";
  if (status === "configured") return null;
  return (
    <Notice>
      {t(status === "unavailable" ? UNAVAILABLE[capability] : "This capability has not been confirmed. Check the deployment status above before submitting; a recorded request does not guarantee execution.")}
    </Notice>
  );
}
