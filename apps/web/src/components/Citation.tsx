"use client";

import { useState } from "react";

import { ProblemNote } from "@/components/ui";
import { knowledge, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { ChunkReading, RetrievalHit } from "@/lib/types";

/**
 * A citation the person can open (§6.5 read against 第三批④).
 *
 * A retrieved sentence a reviewer cannot find is a sentence they have to take on trust, so the locator the
 * parse recorded is shown next to the text, and the surrounding pieces come from the index on request.
 * The index keeps chunks rather than the file: the context below is what was stored, never a re-reading of
 * the document, and a passage indexed before positions were recorded says so instead of inventing one.
 */
export function Citation({ caller, projectId, hit }: { caller: Caller; projectId: string; hit: RetrievalHit }) {
  const t = useT();
  const [reading, setReading] = useState<ChunkReading | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const chunk = hit.chunk;
  const located = chunk.page != null || chunk.char_start != null;

  async function open() {
    setBusy(true);
    setError(null);
    try {
      setReading(await knowledge.chunk(caller, projectId, chunk.chunk_id));
    } catch (exc) {
      setReading(null);
      setError(exc);
    } finally {
      setBusy(false);
    }
  }

  return (
    <blockquote>
      <div>
        <strong>{hit.source_doc?.title ?? chunk.doc_id}</strong> · {chunk.section_path}
        {chunk.page != null ? <span className="muted"> · {t("page {n}", { n: chunk.page })}</span> : null}
        {chunk.char_start != null && chunk.char_end != null ? (
          <span className="mono muted">
            {" "}
            · {t("characters {from} to {to}", { from: chunk.char_start, to: chunk.char_end })}
          </span>
        ) : null}
      </div>
      {!located ? (
        <p className="muted">
          {t(
            "this passage was indexed before positions were recorded, so the index cannot say where it sits; the text is what was stored",
          )}
        </p>
      ) : (
        <p className="muted">
          {t(
            "the offsets count characters in the text the parser read out of the file, not bytes of the file itself",
          )}
        </p>
      )}
      <p>{chunk.content}</p>
      <button type="button" className="secondary" disabled={busy} onClick={() => void open()}>
        {busy ? t("opening…") : reading ? t("re-read the passage") : t("open the cited passage")}
      </button>
      <ProblemNote error={error} />
      {reading ? (
        <div className="rows" style={{ marginTop: 8 }}>
          <p className="muted">
            {t("{n} stored pieces before and {m} after", { n: reading.before.length, m: reading.after.length })}
            {reading.document ? <> · <span className="mono">{reading.document.filename}</span></> : null}
          </p>
          {reading.before.map((one) => (
            <p key={one.chunk_id} className="muted">
              {one.content}
            </p>
          ))}
          <p>
            <strong>{t("cited")}</strong> {reading.chunk.content}
          </p>
          {reading.after.map((one) => (
            <p key={one.chunk_id} className="muted">
              {one.content}
            </p>
          ))}
          {reading.document ? (
            <p className="mono muted">
              sha256 {reading.document.file_sha256} · {t("licence")} {reading.document.license_class}
            </p>
          ) : null}
        </div>
      ) : null}
    </blockquote>
  );
}
