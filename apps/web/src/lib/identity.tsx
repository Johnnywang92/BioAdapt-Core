/**
 * "Acting as" context (plan V2.0 sections 11.2, 13.1).
 *
 * The workbench deliberately has no login of its own: the platform's authorization is a property of
 * the token, so the only honest way to demo roles, ceilings and sign-off separation is to let a
 * person hold a token and say whose token it is. Two rules follow from that:
 * - The token lives in memory and in sessionStorage only. sessionStorage dies with the tab, so a
 *   shared machine keeps no one's credential behind (and it never reaches a URL or a log line).
 * - The decoded claims below are read for display only. Every permission decision is made by the
 *   API; the UI guessing a role would only produce a screen that lies about what the server allows.
 */

"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import type { Caller } from "@/lib/api";

const STORAGE_KEY = "bioadapt.workbench.identity";

export interface Identity {
  token: string;
  subjectId: string;
  roles: string[];
  projectIds: string[];
  isServiceAccount: boolean;
  mfaVerified: boolean;
}

/** The claims a screen may branch on; the API re-derives all of them from the signature. */
interface Claims {
  sub?: string;
  iss?: string;
  aud?: string;
  roles?: string[];
  project_ids?: string[];
  is_service_account?: boolean;
  amr?: string[];
  exp?: number;
}

const MFA_METHODS = new Set(["mfa", "totp", "pwd+mfa", "otp"]);

function decode(token: string): Claims {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("a compact JWS has three parts");
  const payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const padded = payload + "=".repeat((4 - (payload.length % 4)) % 4);
  const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes)) as Claims;
}

export function parseIdentity(token: string): Identity {
  const claims = decode(token);
  if (!claims.sub) throw new Error("the token carries no subject");
  return {
    token,
    subjectId: claims.sub,
    roles: claims.roles ?? [],
    projectIds: claims.project_ids ?? [],
    isServiceAccount: claims.is_service_account === true,
    mfaVerified: (claims.amr ?? []).some((method) => MFA_METHODS.has(method)),
  };
}

export function expiryNote(token: string): string | null {
  try {
    const exp = decode(token).exp;
    if (!exp) return null;
    const seconds = exp - Math.floor(Date.now() / 1000);
    // Only the API can say whether a token still works, and a seeded demo server runs its own clock,
    // so this reports what *this* machine's clock reads rather than predicting the answer.
    if (seconds <= 0) return `past its exp by this clock — whether the API still honours it is its own call`;
    return `expires in ${Math.round(seconds / 60)} min`;
  } catch {
    return null;
  }
}

interface IdentityContextValue {
  identity: Identity | null;
  projectId: string;
  error: string | null;
  setToken: (token: string) => void;
  setProjectId: (projectId: string) => void;
  clear: () => void;
}

const IdentityContext = createContext<IdentityContextValue | null>(null);

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState("");
  const [projectId, setProjectId] = useState("");
  const [error, setError] = useState<string | null>(null);

  // A token is not a preference, so it is never read at module scope and never sent to the server
  // rendered pass: the person enters it in their own tab.
  useEffect(() => {
    const storedToken = window.sessionStorage.getItem(STORAGE_KEY);
    const storedProject = window.sessionStorage.getItem(`${STORAGE_KEY}.project`);
    if (storedToken) {
      try {
        parseIdentity(storedToken);
        setTokenState(storedToken);
      } catch {
        window.sessionStorage.removeItem(STORAGE_KEY);
      }
    }
    if (storedProject) setProjectId(storedProject);
  }, []);

  const setToken = useCallback((next: string) => {
    const trimmed = next.trim();
    if (!trimmed) {
      window.sessionStorage.removeItem(STORAGE_KEY);
      setTokenState("");
      setError(null);
      return;
    }
    try {
      parseIdentity(trimmed);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "the token could not be read");
      return;
    }
    window.sessionStorage.setItem(STORAGE_KEY, trimmed);
    setTokenState(trimmed);
    setError(null);
  }, []);

  const rememberProject = useCallback((next: string) => {
    const trimmed = next.trim();
    setProjectId(trimmed);
    if (trimmed) window.sessionStorage.setItem(`${STORAGE_KEY}.project`, trimmed);
    else window.sessionStorage.removeItem(`${STORAGE_KEY}.project`);
  }, []);

  const clear = useCallback(() => {
    window.sessionStorage.removeItem(STORAGE_KEY);
    setTokenState("");
    setError(null);
  }, []);

  const identity = useMemo(() => {
    if (!token) return null;
    try {
      return parseIdentity(token);
    } catch {
      return null;
    }
  }, [token]);

  const value = useMemo(
    () => ({ identity, projectId, error, setToken, setProjectId: rememberProject, clear }),
    [identity, projectId, error, setToken, rememberProject, clear],
  );

  return <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>;
}

export function useIdentity(): IdentityContextValue {
  const context = useContext(IdentityContext);
  if (!context) throw new Error("useIdentity has to be used under IdentityProvider");
  return context;
}

/** Every read hook takes the caller from the context so no screen can ask with the wrong token. */
export interface Workbench {
  identity: Identity | null;
  projectId: string;
  setProjectId: (projectId: string) => void;
  caller: Caller | null;
}

/** The three things every screen needs: whom to ask as, and which project the path is scoped to. */
export function useWorkbench(): Workbench {
  const { identity, projectId, setProjectId } = useIdentity();
  return {
    identity,
    projectId,
    setProjectId,
    caller: identity ? { token: identity.token, subjectId: identity.subjectId } : null,
  };
}
