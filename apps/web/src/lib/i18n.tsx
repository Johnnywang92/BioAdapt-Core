/**
 * Bilingual display for the workbench (Chinese / English).
 *
 * The English source string is the key: `t("No exports yet")` renders as written on an English screen
 * and looks its translation up in `@/lib/zh` on a Chinese one. A key missing from the dictionary falls
 * back to the English rather than blanking the screen, which keeps a half-translated build usable and
 * makes the gap visible where it is written.
 *
 * Two things are deliberately not translated here, because both are evidence rather than UI:
 * - What the API answers. A refusal is shown in the server's own words (see ProblemNote in ui.tsx), so a
 *   screen cannot disagree with the decision it is reporting.
 * - Identifiers: ids, states of a run as the index holds them, tool names. Enum *labels* get a Chinese
 *   display name; the stored value stays on screen wherever a person would read it back to the API.
 */

"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { zh } from "@/lib/zh";

export type Locale = "en" | "zh";

export const LOCALES: Locale[] = ["en", "zh"];

const STORAGE_KEY = "bioadapt.locale";

const HTML_LANG: Record<Locale, string> = { en: "en", zh: "zh-CN" };

export type Vars = Record<string, string | number>;

/** `{name}` placeholders work in both languages, so a count never depends on English word order. */
function fill(text: string, vars?: Vars): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (match, name: string) => (name in vars ? String(vars[name]) : match));
}

export function readStoredLocale(): Locale {
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored === "zh" || stored === "en" ? stored : "en";
}

interface LocaleContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (english: string, vars?: Vars) => string;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("en");

  // A preference rather than a credential, so it may outlive the tab; it is still only read here,
  // because the first render has to agree with what the server rendered.
  useEffect(() => {
    setLocaleState(readStoredLocale());
  }, []);

  useEffect(() => {
    document.documentElement.lang = HTML_LANG[locale];
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  }, []);

  const t = useCallback(
    (english: string, vars?: Vars) => fill(locale === "zh" ? zh[english] ?? english : english, vars),
    [locale],
  );

  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): LocaleContextValue {
  const context = useContext(LocaleContext);
  if (!context) throw new Error("useLocale has to be used under LocaleProvider");
  return context;
}

/** The one call sites import: `const { t } = useT()`. */
export function useT() {
  return useLocale().t;
}
