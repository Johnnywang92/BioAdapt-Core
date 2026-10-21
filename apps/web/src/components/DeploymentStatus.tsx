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
  run_execution: "No analysis worker is connected. A run request can be recorded, but it will not execute. Use an existing demonstration run to inspect results, or ask the deployment administrator to connect a worker.",
  export_generation: "No export worker is connected. An export request can be recorded, but it will not generate a file. Ask the deployment administrator to connect a worker; polling cannot resolve this.",
};
const STATUS_LABELS = {
  configured: "Declared connected (not health-checked)",
  unavailable: "Not connected",
  unknown: "Not confirmed",
};

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

export function DeploymentStatus() {
  const t = useT();
  const { data, error, loading, reload } = useDeployment();
  const known = !error && !loading ? data : null;
  return (
    <section className="deployment-status" aria-label={t("Deployment capabilities")}>
      <div className="deployment-heading">
        <strong>{t("Deployment capabilities")}</strong>
        <Chip>{t(known?.mode === "reference" ? "Reference demonstration" : known?.mode === "configured" ? "Configured deployment" : "Deployment not confirmed")}</Chip>
        <button type="button" className="secondary" onClick={reload} disabled={loading}>{t("re-read")}</button>
      </div>
      <div className="deployment-capabilities">
        {(Object.keys(LABELS) as Capability[]).map((capability) => (
          <span key={capability}>{t(LABELS[capability])}: {t(STATUS_LABELS[known?.[capability] ?? "unknown"])}</span>
        ))}
      </div>
      <p className="muted" role="status">
        {t(loading ? "Reading deployment declaration…" : error ? "Deployment information could not be read. Capabilities remain unconfirmed; ask the deployment administrator to check the API, then retry." : "These are deployment declarations, not live health checks or permission grants. Project policies still apply.")}
      </p>
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
