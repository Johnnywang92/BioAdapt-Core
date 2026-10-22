"use client";

import { useState, useEffect, useRef } from "react";
import { knowledge, type Caller } from "@/lib/api";
import { queryMarkers } from "@/lib/evidencePresentation";
import { useLocale } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { Citation } from "@/components/Citation";
import { Panel, ProblemNote } from "@/components/ui";
import type { BioSuggestion, DocumentType, KnowledgeDocument, RagAnswer } from "@/lib/types";

function getDocIcon(filename: string, docType: string): string {
  const ext = filename.split(".").pop()?.toLowerCase();
  if (ext === "pdf") return "📄";
  if (ext === "docx" || ext === "doc") return "📘";
  if (ext === "md") return "📝";
  if (docType === "lab_note") return "🧪";
  if (docType === "analysis_log") return "📊";
  return "📑";
}

export default function KnowledgePage({ params }: { params: { projectId: string } }) {
  const { t } = useLocale();
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  if (!caller) return <Panel title={t("private knowledge base")}><p>{blocked}</p></Panel>;
  return <KnowledgeWorkspace key={`${projectId}:${caller.token}`} caller={caller} projectId={projectId} />;
}

function KnowledgeWorkspace({ caller, projectId }: { caller: Caller; projectId: string }) {
  const { t } = useLocale();
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // Ingest mode: "dropzone" | "manual"
  const [uploadMode, setUploadMode] = useState<"dropzone" | "manual">("dropzone");
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Ingest form state (manual)
  const [title, setTitle] = useState("");
  const [filename, setFilename] = useState("");
  const [docType, setDocType] = useState<DocumentType>("paper");
  const [contentText, setContentText] = useState("");
  const [author, setAuthor] = useState("");
  const [tissue, setTissue] = useState("");
  const [ingesting, setIngesting] = useState(false);

  // RAG QA state
  const [query, setQuery] = useState("");
  const [asking, setAsking] = useState(false);
  const [ragAnswer, setRagAnswer] = useState<RagAnswer | null>(null);

  // Cluster suggestion state
  const [clusterId, setClusterId] = useState("");
  const [markersInput, setMarkersInput] = useState("");
  const [clusterTissue, setClusterTissue] = useState("");
  const [suggesting, setSuggesting] = useState(false);
  const [suggestion, setSuggestion] = useState<BioSuggestion | null>(null);

  const loadDocuments = async () => {
    if (!caller) return;
    setLoadingDocs(true);
    setError(null);
    try {
      const list = await knowledge.list(caller, projectId);
      setDocs(list);
    } catch (err: unknown) {
      setError(err);
    } finally {
      setLoadingDocs(false);
    }
  };

  useEffect(() => {
    loadDocuments();
  }, [caller?.token, projectId]);

  const processFiles = async (files: File[]) => {
    if (!caller || files.length === 0) return;
    setIngesting(true);
    setError(null);
    let successCount = 0;
    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        setUploadProgress(`正在解析并索引 (${i + 1}/${files.length}): ${file.name}...`);

        const ext = file.name.split(".").pop()?.toLowerCase() || "";
        const titleFromName = file.name.replace(/\.[^/.]+$/, "");

        // Infer doc_type
        let inferredType: DocumentType = "paper";
        const lower = file.name.toLowerCase();
        if (lower.includes("log") || lower.includes("analysis")) inferredType = "analysis_log";
        else if (lower.includes("lab") || lower.includes("note") || lower.includes("exp")) inferredType = "lab_note";
        else if (lower.includes("protocol") || lower.includes("sop")) inferredType = "protocol";
        else if (ext === "md" || ext === "txt") inferredType = "lab_note";

        if (ext === "txt" || ext === "md") {
          const text = await file.text();
          await knowledge.ingest(caller, projectId, {
            title: titleFromName,
            filename: file.name,
            doc_type: inferredType,
            content_text: text,
            privacy_tier: "restricted",
          });
        } else {
          // Binary files: PDF, DOCX
          const b64 = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
              const res = reader.result as string;
              const base64Data = res.includes(",") ? res.split(",")[1] : res;
              resolve(base64Data);
            };
            reader.onerror = (e) => reject(e);
            reader.readAsDataURL(file);
          });

          await knowledge.ingest(caller, projectId, {
            title: titleFromName,
            filename: file.name,
            doc_type: inferredType,
            content_b64: b64,
            privacy_tier: "restricted",
          });
        }
        successCount++;
      }
      await loadDocuments();
      setUploadProgress(`✅ 成功入库 ${successCount} 篇文献/实验记录！切片与混合索引已就绪。`);
      setTimeout(() => setUploadProgress(null), 6000);
    } catch (err: unknown) {
      setError(err);
      setUploadProgress(null);
    } finally {
      setIngesting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleIngest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!caller || !title || !filename || !contentText) return;
    setIngesting(true);
    setError(null);
    try {
      await knowledge.ingest(caller, projectId, {
        title,
        filename,
        doc_type: docType,
        content_text: contentText,
        author: author || undefined,
        tissue: tissue || undefined,
        privacy_tier: "restricted",
      });
      setTitle("");
      setFilename("");
      setContentText("");
      await loadDocuments();
      setUploadProgress("✅ 手输文档入库成功！已建立切片索引。");
      setTimeout(() => setUploadProgress(null), 4000);
    } catch (err: unknown) {
      setError(err);
    } finally {
      setIngesting(false);
    }
  };

  const handleDelete = async (docId: string) => {
    if (!caller || !confirm("确认删除该文档及其全部索引切片吗？")) return;
    try {
      await knowledge.delete(caller, projectId, docId);
      await loadDocuments();
    } catch (err: unknown) {
      setError(err);
    }
  };

  const handleAsk = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || asking) return;
    setAsking(true);
    setRagAnswer(null);
    setError(null);
    try {
      const res = await knowledge.ask(caller, projectId, query.trim());
      setRagAnswer(res);
    } catch (err: unknown) {
      setError(err);
    } finally {
      setAsking(false);
    }
  };

  const markers = queryMarkers(markersInput);
  const handleSuggest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!clusterId.trim() || !markers.length || suggesting) return;
    setSuggesting(true);
    setSuggestion(null);
    setError(null);
    try {
      const res = await knowledge.suggest(caller, projectId, clusterId.trim(), markers, clusterTissue.trim());
      setSuggestion(res);
    } catch (err: unknown) {
      setError(err);
    } finally {
      setSuggesting(false);
    }
  };

  return (
    <div style={{ padding: "1.5rem", maxWidth: "1200px", margin: "0 auto" }}>
      <header style={{ marginBottom: "2rem" }}>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 700, margin: "0 0 0.5rem" }}>
          📖 {t("private knowledge base")}
        </h1>
        <p style={{ color: "var(--muted)", margin: 0 }}>
          {t("Search project papers, lab notes and analysis reports. Parsing depends on the installed backends; inspect extracted citations before using a suggestion.")}
        </p>
      </header>

      <ProblemNote error={error} />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem", marginBottom: "2rem" }}>
        {/* RAG QA Panel */}
        <section style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "1.25rem", background: "var(--card-bg)" }}>
          <h2 style={{ fontSize: "1.25rem", fontWeight: 600, marginTop: 0, marginBottom: "1rem" }}>
            🧠 {t("rag question answering")}
          </h2>
          <form onSubmit={handleAsk} style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
            <input
              type="text"
              placeholder={t("ask question placeholder")}
              value={query}
              disabled={asking}
              onChange={(e) => { setQuery(e.target.value); setRagAnswer(null); }}
              style={{ flex: 1, padding: "0.5rem 0.75rem", borderRadius: "4px", border: "1px solid var(--border)" }}
            />
            <button type="submit" disabled={asking || !query.trim()}>
              {asking ? "检索问答中…" : t("ask button")}
            </button>
          </form>

          {ragAnswer && (
            <div style={{ background: "rgba(0,0,0,0.02)", border: "1px solid var(--border)", borderRadius: "6px", padding: "1rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.5rem" }}>
                <strong>💡 AI 回答</strong>
                <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
                  {t("Retrieval heuristic score")}: {(ragAnswer.confidence * 100).toFixed(0)}%
                </span>
              </div>
              <p className="muted">{t("This score reflects retrieved matches, not the probability that the answer is correct.")}</p>
              {!ragAnswer.citations.length ? <p className="notice">{t("No matching citations were found. This answer has no retrieved source support.")}</p> : null}
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.6, fontSize: "0.95rem", marginBottom: "1rem" }}>
                {ragAnswer.answer}
              </div>

              {ragAnswer.citations.length > 0 && (
                <div>
                  <h4 style={{ fontSize: "0.9rem", margin: "0.5rem 0", color: "var(--muted)" }}>
                    🔗 {t("citations")} ({ragAnswer.citations.length} 处匹配)
                  </h4>
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                    {ragAnswer.citations.map((hit, idx) => (
                      <Citation key={`${hit.chunk.chunk_id}:${idx}`} caller={caller} projectId={projectId} hit={hit} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </section>

        {/* Bio Suggestion Panel */}
        <section style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "1.25rem", background: "var(--card-bg)" }}>
          <h2 style={{ fontSize: "1.25rem", fontWeight: 600, marginTop: 0, marginBottom: "1rem" }}>
            🧬 {t("cluster marker suggestion")}
          </h2>
          <form onSubmit={handleSuggest} style={{ display: "flex", flexDirection: "column", gap: "0.75rem", marginBottom: "1rem" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem" }}>
              <input
                type="text"
                placeholder="聚类编号 (如 cluster_04)"
                value={clusterId}
                disabled={suggesting}
                onChange={(e) => { setClusterId(e.target.value); setSuggestion(null); }}
                style={{ padding: "0.5rem 0.75rem", borderRadius: "4px", border: "1px solid var(--border)" }}
              />
              <input
                type="text"
                placeholder={t("Tissue (optional)")}
                value={clusterTissue}
                disabled={suggesting}
                onChange={(e) => { setClusterTissue(e.target.value); setSuggestion(null); }}
                style={{ padding: "0.5rem 0.75rem", borderRadius: "4px", border: "1px solid var(--border)" }}
              />
            </div>
            <input
              type="text"
              placeholder={t("Markers for this query")}
              value={markersInput}
              disabled={suggesting}
              onChange={(e) => { setMarkersInput(e.target.value); setSuggestion(null); }}
              style={{ padding: "0.5rem 0.75rem", borderRadius: "4px", border: "1px solid var(--border)" }}
            />
            <p className="muted">{t("The first eight distinct genes are used. Check the exact query below before submitting.")}</p>
            <p>{t("Query preview")}: <span className="mono">{clusterId.trim() || "—"} · {markers.join(", ") || "—"} · {clusterTissue.trim() || t("Tissue not supplied")}</span></p>
            <button type="submit" disabled={suggesting || !clusterId.trim() || !markers.length}>
              {suggesting ? "知识库匹配中…" : t("get suggestions")}
            </button>
          </form>

          {suggestion && (
            <div style={{ background: "rgba(0,0,0,0.02)", border: "1px solid var(--border)", borderRadius: "6px", padding: "1rem" }}>
              <div style={{ marginBottom: "0.75rem" }}>
                <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>{t("suggested cell types")}:</span>
                <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.25rem" }}>
                  {suggestion.suggested_cell_types.map((ct, idx) => (
                    <span key={idx} style={{ padding: "0.2rem 0.5rem", background: "#10b98120", color: "#10b981", borderRadius: "4px", fontWeight: 600, fontSize: "0.9rem" }}>
                      🎯 {ct}
                    </span>
                  ))}
                </div>
              </div>
              <p>{t("Queried markers")}: <span className="mono">{suggestion.markers.join(", ")}</span></p>
              {!suggestion.citations.length ? <p className="notice">{t("No matching knowledge-base citations were found. Any proposed cell types are heuristic leads, not literature-backed conclusions.")}</p> : null}
              {!suggestion.suggested_cell_types.length ? <p className="notice">{t("The retrieved evidence is insufficient to suggest a cell label.")}</p> : null}
              <p className="muted">{t("Retrieval heuristic score")}: {Math.round(suggestion.confidence * 100)}% · {t("This score is based on retrieved matches, not a calibrated probability that a cell label is correct.")}</p>
              <div style={{ fontSize: "0.9rem", lineHeight: 1.5, marginBottom: "0.75rem" }}>
                <strong>{t("biological rationale")}:</strong>
                <div style={{ whiteSpace: "pre-wrap", marginTop: "0.25rem", color: "var(--muted)" }}>
                  {suggestion.rationale}
                </div>
              </div>
              {suggestion.citations.length ? <details>
                <summary>{t("citations")} ({suggestion.citations.length})</summary>
                {suggestion.citations.map((citation, index) => (
                  <Citation
                    key={`${citation.chunk.chunk_id}:${index}`}
                    caller={caller}
                    projectId={projectId}
                    hit={citation}
                  />
                ))}
              </details> : null}
            </div>
          )}
        </section>
      </div>

      {/* Document Ingestion & Document List */}
      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: "1.5rem" }}>
        {/* Document Ingestion Panel */}
        <section style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "1.25rem", background: "var(--card-bg)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
            <h2 style={{ fontSize: "1.25rem", fontWeight: 600, margin: 0 }}>
              📥 {t("upload paper or lab log")}
            </h2>
            <div style={{ display: "flex", gap: "4px" }}>
              <button
                type="button"
                className={uploadMode === "dropzone" ? undefined : "secondary"}
                onClick={() => setUploadMode("dropzone")}
                style={{ fontSize: "0.8rem", padding: "3px 8px" }}
              >
                📁 {t("Batch Document Upload")}
              </button>
              <button
                type="button"
                className={uploadMode === "manual" ? undefined : "secondary"}
                onClick={() => setUploadMode("manual")}
                style={{ fontSize: "0.8rem", padding: "3px 8px" }}
              >
                ✏️ {t("Manual Text Input")}
              </button>
            </div>
          </div>

          {uploadProgress && (
            <div
              style={{
                padding: "0.6rem 0.8rem",
                borderRadius: "6px",
                background: uploadProgress.startsWith("✅") ? "rgba(16, 185, 129, 0.12)" : "rgba(59, 130, 246, 0.12)",
                color: uploadProgress.startsWith("✅") ? "#059669" : "#2563eb",
                border: `1px solid ${uploadProgress.startsWith("✅") ? "#a7f3d0" : "#bfdbfe"}`,
                fontSize: "0.85rem",
                marginBottom: "0.85rem",
              }}
            >
              {uploadProgress}
            </div>
          )}

          {uploadMode === "dropzone" ? (
            <div>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.docx,.doc,.md,.txt"
                style={{ display: "none" }}
                onChange={async (e) => {
                  if (e.target.files && e.target.files.length > 0) {
                    await processFiles(Array.from(e.target.files));
                  }
                }}
              />
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setIsDragging(true);
                }}
                onDragLeave={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setIsDragging(false);
                }}
                onDrop={async (e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setIsDragging(false);
                  if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                    await processFiles(Array.from(e.dataTransfer.files));
                  }
                }}
                onClick={() => fileInputRef.current?.click()}
                style={{
                  border: isDragging ? "2px dashed #2563eb" : "2px dashed var(--border)",
                  borderRadius: "8px",
                  padding: "2.5rem 1.5rem",
                  textAlign: "center",
                  cursor: ingesting ? "wait" : "pointer",
                  background: isDragging ? "rgba(37, 99, 235, 0.05)" : "var(--bg)",
                  transition: "all 0.15s ease",
                }}
              >
                <div style={{ fontSize: "2.2rem", marginBottom: "0.5rem" }}>
                  {ingesting ? "⏳" : "📥"}
                </div>
                <div style={{ fontWeight: 600, fontSize: "1rem", marginBottom: "0.4rem" }}>
                  {ingesting ? t("Parsing & Indexing...") : t("Drop files here or click to upload")}
                </div>
                <div style={{ fontSize: "0.8rem", color: "var(--muted)" }}>
                  {t("Supported formats")}
                </div>
                <div style={{ marginTop: "1rem", display: "inline-flex", gap: "6px" }}>
                  <span style={{ fontSize: "0.75rem", padding: "2px 6px", background: "#f1f5f9", borderRadius: "4px" }}>PDF 论文</span>
                  <span style={{ fontSize: "0.75rem", padding: "2px 6px", background: "#f1f5f9", borderRadius: "4px" }}>Word (.docx) 实验记录</span>
                  <span style={{ fontSize: "0.75rem", padding: "2px 6px", background: "#f1f5f9", borderRadius: "4px" }}>Markdown (.md) 分析报告</span>
                </div>
              </div>
            </div>
          ) : (
            <form onSubmit={handleIngest} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
              <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "0.5rem" }}>
                <input
                  type="text"
                  placeholder={t("document title")}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  required
                  style={{ padding: "0.5rem 0.75rem", borderRadius: "4px", border: "1px solid var(--border)" }}
                />
                <select
                  value={docType}
                  onChange={(e) => setDocType(e.target.value as DocumentType)}
                  style={{ padding: "0.5rem 0.75rem", borderRadius: "4px", border: "1px solid var(--border)" }}
                >
                  <option value="paper">📄 学术论文</option>
                  <option value="lab_note">🧪 实验记录</option>
                  <option value="analysis_log">📊 数据分析日志</option>
                  <option value="protocol">📋 实验规程 SOP</option>
                  <option value="other">📑 其它文档</option>
                </select>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.5rem" }}>
                <input
                  type="text"
                  placeholder={t("document filename")}
                  value={filename}
                  onChange={(e) => setFilename(e.target.value)}
                  required
                  style={{ padding: "0.5rem 0.75rem", borderRadius: "4px", border: "1px solid var(--border)" }}
                />
                <input
                  type="text"
                  placeholder="作者 / 记录人员 (选填)"
                  value={author}
                  onChange={(e) => setAuthor(e.target.value)}
                  style={{ padding: "0.5rem 0.75rem", borderRadius: "4px", border: "1px solid var(--border)" }}
                />
                <input
                  type="text"
                  placeholder="组织 / 样本来源 (选填)"
                  value={tissue}
                  onChange={(e) => setTissue(e.target.value)}
                  style={{ padding: "0.5rem 0.75rem", borderRadius: "4px", border: "1px solid var(--border)" }}
                />
              </div>

              <textarea
                placeholder={t("document content")}
                value={contentText}
                onChange={(e) => setContentText(e.target.value)}
                rows={7}
                required
                style={{ width: "100%", padding: "0.75rem", borderRadius: "4px", border: "1px solid var(--border)", fontFamily: "monospace", fontSize: "0.85rem" }}
              />

              <button type="submit" disabled={ingesting}>
                {ingesting ? "切片向量化与入库中…" : `🚀 ${t("ingest document")}`}
              </button>
            </form>
          )}
        </section>

        {/* Existing Documents List */}
        <section style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "1.25rem", background: "var(--card-bg)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
            <h2 style={{ fontSize: "1.25rem", fontWeight: 600, margin: 0 }}>
              📚 已收录文献与记录 ({docs.length})
            </h2>
            <button className="secondary" onClick={loadDocuments} disabled={loadingDocs} style={{ padding: "0.25rem 0.5rem", fontSize: "0.85rem" }}>
              🔄 刷新
            </button>
          </div>

          {loadingDocs && <p style={{ color: "var(--muted)" }}>加载知识库文档中…</p>}

          {!loadingDocs && docs.length === 0 && (
            <p style={{ color: "var(--muted)", fontSize: "0.9rem" }}>
              当前项目知识库为空。可在左侧拖拽或输入学术论文/实验记录进行索引。
            </p>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", maxHeight: "380px", overflowY: "auto" }}>
            {docs.map((d) => (
              <div key={d.doc_id} style={{ padding: "0.75rem", border: "1px solid var(--border)", borderRadius: "6px", background: "var(--bg)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                  <div style={{ flex: 1, minWidth: 0, marginRight: "8px" }}>
                    <div style={{ fontWeight: 600, fontSize: "0.95rem", display: "flex", alignItems: "center", gap: "6px" }}>
                      <span>{getDocIcon(d.filename, d.doc_type)}</span>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={d.title}>
                        {d.title}
                      </span>
                    </div>
                    <div style={{ fontSize: "0.8rem", color: "var(--muted)", marginTop: "0.3rem", display: "flex", gap: "8px", flexWrap: "wrap" }}>
                      <span>📁 {d.filename}</span>
                      <span>📑 {d.chunk_count} 个切片</span>
                      <span style={{ color: "#059669" }}>🔒 {d.privacy_tier}</span>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "6px", alignItems: "center", flexShrink: 0 }}>
                    <button
                      className="secondary"
                      type="button"
                      title="以此文献为背景提问"
                      onClick={() => {
                        setQuery(`根据《${d.title}》，其核心实验结果与生物学结论是什么？`);
                        window.scrollTo({ top: 0, behavior: "smooth" });
                      }}
                      style={{ fontSize: "0.75rem", padding: "0.2rem 0.4rem" }}
                    >
                      💡 提问
                    </button>
                    <button
                      className="secondary"
                      type="button"
                      onClick={() => handleDelete(d.doc_id)}
                      style={{ fontSize: "0.75rem", padding: "0.2rem 0.4rem", color: "#ef4444" }}
                    >
                      {t("delete doc")}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
