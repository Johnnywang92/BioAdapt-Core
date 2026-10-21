"use client";

import { useT } from "@/lib/i18n";
import { useWorkbench } from "@/lib/identity";
import type { Caller } from "@/lib/api";

export interface Scope {
  caller: Caller | null;
  projectId: string;
  /** Why the screen cannot read yet, in the words a person can act on. */
  blocked: string | null;
}

/**
 * The path carries the project and the token carries the caller; both have to be true before a read
 * makes sense (§11.2: the row-level scope is a property of the request, not of the screen).
 *
 * The route param wins over the id the tab remembers, because the URL is what a person clicked, and
 * silently reading a different project than the one in the address bar would be the kind of confusion
 * that ends up in a delivered report.
 *
 * The reason is translated here rather than at each screen: it names the two project ids a person has
 * to compare, so the wording belongs with the values.
 */
export function useProjectScope(routeProjectId: string): Scope {
  const t = useT();
  const { caller, projectId: remembered } = useWorkbench();
  const projectId = decodeURIComponent(routeProjectId);
  if (!caller) {
    return {
      caller: null,
      projectId,
      blocked: t("paste a platform token in the panel below; every read is made as someone."),
    };
  }
  if (!projectId) {
    return { caller, projectId, blocked: t("this screen is project scoped and needs a project in the path.") };
  }
  if (remembered && remembered !== projectId) {
    return {
      caller,
      projectId,
      blocked: t(
        "the tab remembers project {remembered}, but the address names {address}. Reading {address}.",
        { remembered, address: projectId },
      ),
    };
  }
  return { caller, projectId, blocked: null };
}
