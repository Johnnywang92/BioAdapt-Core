"use client";

import { Chip, Empty, Loading, Panel, ProblemNote } from "@/components/ui";
import { tools as toolsApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync } from "@/lib/useAsync";
import type { ToolRow } from "@/lib/types";

/**
 * The tool catalog, read from the registry this deployment hydrates (§4.1, §9.4).
 *
 * This screen writes nothing and offers nothing to write: a registered tool is decided by the deployment
 * that holds it, and the catalog is the same list the plan wizard builds nodes from and the executor
 * hydrates. So a row here is a promise - which image, which licence, what it assumes, what would make
 * its result stop meaning what it seems to mean - and the reader is here to check those promises, not to
 * change them.
 *
 * The snapshot at the top is the value a launch has to name. A run approved against another snapshot is
 * refused by the executor, so the number is worth reading before a plan is written rather than after.
 */
export default function ToolsPage({ params }: { params: { projectId: string } }) {
  const t = useT();
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  const read = useAsync(
    () => (caller ? toolsApi.read(caller, projectId) : Promise.resolve(null)),
    [caller?.token, projectId],
  );

  if (!caller) {
    return (
      <Panel title={t("Tool catalog")} note={t("nothing is read until a token is set")}>
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  const catalog = read.data;
  const conclusionOutputs = new Set(catalog?.conclusion_outputs ?? []);

  return (
    <>
      <Panel
        title={t("Tool catalog")}
        note={t(
          "the tool set this deployment can run for this project; a plan names a tool by name and version, and a run pins the snapshot below",
        )}
        actions={
          <button className="secondary" type="button" disabled={read.loading} onClick={read.reload}>
            {t("re-read")}
          </button>
        }
      >
        <ProblemNote error={read.error} />
        <Loading on={read.loading && !catalog} />
        {catalog ? (
          <>
            <dl className="facts">
              <dt>{t("registry snapshot")}</dt>
              <dd>
                <span className="mono">{catalog.snapshot_id}</span>
              </dd>
              <dt>{t("tools")}</dt>
              <dd>{catalog.tools.length}</dd>
              <dt>{t("conclusion outputs")}</dt>
              <dd>
                <span className="mono">{catalog.conclusion_outputs.join(", ")}</span>
              </dd>
            </dl>
            <p className="muted">
              {t(
                "a launch has to name this snapshot; a run approved against another one is refused rather than executed against a different tool set",
              )}
            </p>
          </>
        ) : null}
      </Panel>

      {catalog ? (
        <Panel
          title={t("Registered tools")}
          note={t(
            "one row per tool, read from the registry the executor hydrates; a tool that is not here is a tool no plan node can name",
          )}
        >
          {catalog.tools.length ? (
            <>
              <div className="table-scroll">
                <table className="tool-table">
                  <thead>
                    <tr>
                      <th>{t("tool and version")}</th>
                      <th>{t("task")}</th>
                      <th>{t("reads")}</th>
                      <th>{t("writes")}</th>
                      <th>{t("resources floor")}</th>
                      <th>{t("measured peak")}</th>
                      <th>{t("gate")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {catalog.tools.map((tool) => (
                      <tr key={`${tool.tool_name}@${tool.tool_version}`}>
                        <td>
                          <span className="mono">
                            {tool.tool_name}@{tool.tool_version}
                          </span>
                          <div className="muted mono">
                            {tool.container_image.repository}:{tool.container_image.tag}
                          </div>
                        </td>
                        <td className="mono">{tool.task}</td>
                        <td>
                          {tool.input_slots
                            .filter((one) => one.required)
                            .map((one) => one.slot)
                            .join(", ") || "—"}
                        </td>
                        <td>
                          {tool.output_slots.join(", ") || "—"}
                          {tool.output_slots.some((slot) => conclusionOutputs.has(slot)) ? (
                            <>
                              {" "}
                              <Chip status="provisional">{t("conclusion")}</Chip>
                            </>
                          ) : null}
                        </td>
                        <td className="mono">
                          {`${tool.min_resources.cpu} cpu · ${tool.min_resources.memory_gb} GB · ${tool.min_resources.max_minutes} min`}
                          {tool.min_resources.gpu ? ` · ${tool.min_resources.gpu} gpu` : ""}
                        </td>
                        <td className="mono">
                          {tool.measured_peak_memory_mb === null
                            ? "—"
                            : `${tool.measured_peak_memory_mb} MB`}
                          {tool.measured_on ? (
                            <div className="muted mono">{tool.measured_on}</div>
                          ) : null}
                        </td>
                        <td>
                          {tool.requires_approval ? (
                            <Chip status="provisional">{t("gated")}</Chip>
                          ) : tool.runnable ? (
                            <Chip status="supported">{t("runnable")}</Chip>
                          ) : (
                            <Chip status="unknown">{t("no runner")}</Chip>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {catalog.tools.map((tool) => (
                <ToolContract key={`${tool.tool_name}@${tool.tool_version}`} tool={tool} />
              ))}
            </>
          ) : (
            !read.loading && <Empty>{t("This deployment holds no tool for this project.")}</Empty>
          )}
        </Panel>
      ) : null}
    </>
  );
}

/**
 * One tool's whole contract, under the row that names it.
 *
 * The three lists are the tool's own words - what it assumes, what would make its result stop meaning
 * what it seems to mean, what a reader has to be able to check - and they are shown as the registry
 * holds them, because a tool that checks these is the whole point of registering it.
 */
function ToolContract({ tool }: { tool: ToolRow }) {
  const t = useT();
  return (
    <details className="advanced">
      <summary>
        <span className="mono">
          {tool.tool_name}@{tool.tool_version}
        </span>
        {" · "}
        {t("the full contract")}
      </summary>
      <dl className="facts">
        <dt>{t("image")}</dt>
        <dd className="mono">
          {tool.container_image.repository}:{tool.container_image.tag}
          <div className="mono">{tool.container_image.digest}</div>
        </dd>
        <dt>{t("class")}</dt>
        <dd className="mono">{tool.tool_class}</dd>
        <dt>{t("licence")}</dt>
        <dd>
          <span className="mono">{tool.license.spdx_id}</span>
          {" · "}
          {tool.license.display_name}
          {" · "}
          <span className="mono">{tool.license.review_status}</span>
          {tool.license.reviewed_at ? (
            <div className="muted mono">{tool.license.reviewed_at}</div>
          ) : null}
          {tool.license.note ? <div className="muted">{tool.license.note}</div> : null}
        </dd>
        <dt>{t("schemas")}</dt>
        <dd className="mono">
          {`${tool.input_schema.name}@${tool.input_schema.version} → ${tool.output_schema.name}@${tool.output_schema.version}`}
        </dd>
        <dt>{t("reference compatibility")}</dt>
        <dd className="mono">
          {`id_system ${tool.reference_compatibility.id_system ?? "—"}`}
          {" · "}
          {`requires_counts ${String(tool.reference_compatibility.requires_counts)}`}
          {" · "}
          {`needs_log ${String(tool.reference_compatibility.needs_log)}`}
          {tool.reference_compatibility.embedding_source ? (
            <div className="mono">{`embedding ${tool.reference_compatibility.embedding_source}`}</div>
          ) : null}
        </dd>
        <dt>{t("network")}</dt>
        <dd className="mono">
          {tool.network_mode}
          {tool.egress_allowlist.length ? (
            <div className="mono">{tool.egress_allowlist.join(", ")}</div>
          ) : null}
        </dd>
        <dt>{t("failure policy")}</dt>
        <dd className="mono">
          {tool.failure_policy}
          {" · "}
          {`retry ${tool.retry_limit}`}
          {" · "}
          {`reproducibility ${tool.reproducibility_level_target}`}
        </dd>
        <dt>{t("privacy policy")}</dt>
        <dd className="mono">{tool.privacy_policy}</dd>
      </dl>
      <h3>{t("quality checks")}</h3>
      <ul>
        {tool.quality_checks.map((one) => (
          <li key={one} className="mono">
            {one}
          </li>
        ))}
      </ul>
      <h3>{t("assumptions")}</h3>
      <ul>
        {tool.assumptions.map((one) => (
          <li key={one}>{one}</li>
        ))}
      </ul>
      <h3>{t("invalid when")}</h3>
      <ul>
        {tool.invalid_when.map((one) => (
          <li key={one}>{one}</li>
        ))}
      </ul>
    </details>
  );
}
