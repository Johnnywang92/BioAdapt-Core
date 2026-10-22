"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Reads always go through this hook so a screen shows what the API answered *now*.
 *
 * Section 5.4 puts the ceiling on a card that the gate re-derives, so a stale read is not a cosmetic
 * problem: a reviewer could act on a status another person has already moved. Keeping one loading
 * path means every screen can be asked to re-read, and none of them holds a copy of the truth.
 */
export interface AsyncState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

export function useAsync<T>(load: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setLoading(true);
    setError(null);
    loadRef
      .current()
      .then((value) => {
        if (live && !controller.signal.aborted) setData(value);
      })
      .catch((exc) => {
        if (live && !controller.signal.aborted && (exc as Error)?.name !== "AbortError") setError(exc);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
      controller.abort();
    };
    // The caller's array is the dependency contract; `tick` is the explicit re-read.
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, reload };
}

/**
 * The settled value of a free-text filter, a beat after the person stops typing it.
 *
 * A screen that re-reads on every keystroke pays for the whole typing session, and the expensive
 * reads here (§13.4 re-derives a queue from a run's products) are not cheap enough for that. The
 * value below is still what the field holds, so the input never lags the cursor.
 */
export function useDebounced<T>(value: T, delayMs = 400): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return settled;
}

/** A mutation that has to report the server's own refusal rather than a guessed one. */
export function useSubmit(): {
  busy: boolean;
  error: unknown;
  result: unknown;
  run: <T>(action: () => Promise<T>) => Promise<T | null>;
} {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<unknown>(null);

  const run = useCallback(async <T,>(action: () => Promise<T>): Promise<T | null> => {
    setBusy(true);
    setError(null);
    try {
      const value = await action();
      setResult(value);
      return value;
    } catch (exc) {
      setError(exc);
      return null;
    } finally {
      setBusy(false);
    }
  }, []);

  return { busy, error, result, run };
}

/**
 * A read that re-runs itself while a person is watching, so a tracker does not freeze at first paint.
 *
 * A run moves without the browser being told: the state machine answers when asked, and §9.2 keeps the
 * journal as the only record of why it moved. Polling is the honest shape here - the screen re-reads the
 * same endpoint it read the first time and shows whatever the API says now, rather than accumulating a
 * local copy of the state that could disagree with the next reviewer.
 */
export function useAutoRefresh(reload: () => void, intervalMs: number, live: boolean) {
  useEffect(() => {
    if (!live) return undefined;
    const timer = setInterval(reload, intervalMs);
    return () => clearInterval(timer);
  }, [reload, intervalMs, live]);
}
