"""Bioinformatics-aware RAG pipeline and assistant.

Provides:
- KnowledgeAssistant: performs offline RAG QA and cluster-level bioinformatics suggestions.
- Strict citation tracking back to original documents and sections via RetrievalHit.
- Context assembly and deterministic grounded reasoning (plan V2.0 section 6).
"""

from __future__ import annotations

from typing import Callable, Sequence

from bioadapt_schemas.enums import PrivacyTier

from .entities import BioSuggestion, DocumentType, RagAnswer, RetrievalHit
from .index import KnowledgeIndex


class KnowledgeAssistant:
    """Offline Assistant for scientific document QA and single-cell data analysis suggestions."""

    def __init__(
        self,
        index: KnowledgeIndex,
        llm_generate_fn: Callable[[str, str], str] | None = None,
    ) -> None:
        """Initialize the assistant.

        Parameters
        ----------
        index : KnowledgeIndex
            The hybrid search knowledge base index.
        llm_generate_fn : Callable[[str, str], str] | None
            Optional text generation function `(system_prompt, user_prompt) -> response_text`.
            If None, a deterministic offline synthesis engine is used (STRICT_LOCAL default).
        """
        self._index = index
        self._llm_fn = llm_generate_fn

    def ask(
        self,
        query: str,
        project_id: str | None = None,
        top_k: int = 4,
        doc_type: DocumentType | None = None,
        allowed_privacy_tiers: Sequence[PrivacyTier] | None = None,
    ) -> RagAnswer:
        """Answer a scientific or experiment question with citation tracking."""
        hits = self._index.search(
            query=query,
            project_id=project_id,
            top_k=top_k,
            mode="hybrid",
            doc_type=doc_type,
            allowed_privacy_tiers=allowed_privacy_tiers,
        )

        if not hits:
            return RagAnswer(
                query=query,
                answer="抱歉，在本地私有知识库中未检索到与该问题高度相关的文献、实验记录或分析报告。请检查知识库是否已导入相关文档，或尝试更换关键词（如基因官方名或细胞类型）。",
                citations=[],
                confidence=0.0,
                grounded_facts=[],
            )

        grounded_facts = [
            f"《{h.source_doc.title if h.source_doc else h.chunk.doc_id}》[{h.chunk.section_path}]: {h.chunk.content[:160]}..."
            for h in hits
        ]

        if self._llm_fn:
            context_blocks: list[str] = []
            for i, hit in enumerate(hits, start=1):
                doc_title = hit.source_doc.title if hit.source_doc else hit.chunk.doc_id
                context_blocks.append(
                    f"[{i}] 《{doc_title}》 (章节: {hit.chunk.section_path})\n{hit.chunk.content}"
                )
            context_str = "\n\n".join(context_blocks)
            sys_prompt = (
                "你是一个精通单细胞多组学与生物信息学的严谨科研助手。请基于提供的本地知识库参考资料回答问题。"
                "回答必须忠实于参考资料，标明引用出处 [1], [2] 等，不得无中生有。"
            )
            user_prompt = f"参考资料：\n{context_str}\n\n问题：{query}\n\n请给出严谨的专业回答与分析："
            answer_text = self._llm_fn(sys_prompt, user_prompt)
        else:
            answer_text = self._synthesize_offline_answer(query, hits)

        confidence = min(0.95, 0.45 + 0.12 * len(hits))

        return RagAnswer(
            query=query,
            answer=answer_text,
            citations=hits,
            confidence=round(confidence, 2),
            grounded_facts=grounded_facts,
        )

    def suggest_for_cluster(
        self,
        cluster_id: str,
        markers: Sequence[str],
        tissue: str = "",
        project_id: str | None = None,
        top_k: int = 3,
    ) -> BioSuggestion:
        """Retrieve historical lab records and papers to generate cell type annotations and biological advice."""
        top_markers = list(markers[:8])
        marker_query = " ".join(top_markers)
        search_query = f"{tissue} {marker_query} cell type marker"

        hits = self._index.search(
            query=search_query,
            project_id=project_id,
            top_k=top_k,
            mode="hybrid",
        )

        predicted_types = self._infer_cell_types(top_markers, hits)

        bullets: list[str] = []
        if hits:
            bullets.append(
                f"在私有知识库中匹配到 {len(hits)} 篇文献/实验记录，涉及标记基因: {', '.join(top_markers)}。"
            )
            for h in hits[:2]:
                title = h.source_doc.title if h.source_doc else h.chunk.doc_id
                snippet = h.chunk.content.replace("\n", " ")[:140]
                bullets.append(f"- 参考《{title}》({h.chunk.section_path}): {snippet}...")
        else:
            bullets.append(f"知识库中未见完全同源 Marker 组合 ({', '.join(top_markers)}) 的历史记录，建议结合公开参考集校验。")

        rationale = "\n".join(bullets)
        confidence = min(0.95, 0.50 + 0.15 * len(hits)) if hits else 0.30

        return BioSuggestion(
            cluster_id=cluster_id,
            markers=top_markers,
            suggested_cell_types=predicted_types,
            rationale=rationale,
            citations=hits,
            confidence=round(confidence, 2),
        )

    def _synthesize_offline_answer(self, query: str, hits: Sequence[RetrievalHit]) -> str:
        """Synthesize an informative answer using matched chunks without an external LLM."""
        sections: list[str] = [
            f"根据本地私有知识库检索，找到 {len(hits)} 处高相关内容：\n"
        ]

        for i, hit in enumerate(hits, start=1):
            title = hit.source_doc.title if hit.source_doc else hit.chunk.doc_id
            sec = f" > {hit.chunk.section_path}" if hit.chunk.section_path else ""
            summary_lines = [line.strip() for line in hit.chunk.content.split("\n") if line.strip()]
            preview = " ".join(summary_lines[:4])
            if len(preview) > 280:
                preview = preview[:277] + "..."
            sections.append(f"**[{i}] 《{title}》{sec}** (匹配度: {hit.score:.2f})")
            sections.append(f"> {preview}\n")

        sections.append("💡 **综合建议**：")
        sections.append(
            "上述记录来源于本地实验日志或文献，已建立可溯源的证据链。如需将该结论固化为分析依据，可一键将其转入单细胞证据卡。"
        )
        return "\n".join(sections)

    def _infer_cell_types(self, markers: Sequence[str], hits: Sequence[RetrievalHit]) -> list[str]:
        """Heuristically infer potential cell type names from markers and literature."""
        marker_set = {m.upper() for m in markers}
        cell_type_signatures = [
            ("CD4+ T Cell", {"CD4", "IL7R", "CD3D", "CD3E"}),
            ("CD8+ T Cell", {"CD8A", "CD8B", "GZMA", "GZMB"}),
            ("Regulatory T Cell (Treg)", {"FOXP3", "IL2RA", "CTLA4"}),
            ("B Cell", {"CD19", "MS4A1", "CD79A", "CD79B"}),
            ("Plasma Cell", {"SDC1", "CD38", "IGHG1"}),
            ("NK Cell", {"NCAM1", "NKG7", "KLRD1", "GNLY"}),
            ("Macrophage / Monocyte", {"CD14", "CD68", "FCGR3A", "LYZ"}),
            ("Dendritic Cell (DC)", {"ITGAX", "HLA-DRA", "CD1C", "CLEC9A"}),
            ("Endothelial Cell", {"PECAM1", "VWF", "CDH5"}),
            ("Fibroblast", {"COL1A1", "COL1A2", "ACTA2", "DCN"}),
            ("Epithelial Cell", {"EPCAM", "KRT18", "KRT19", "CDH1"}),
        ]

        scored: list[tuple[int, str]] = []
        for ct_name, ct_markers in cell_type_signatures:
            overlap = len(marker_set & ct_markers)
            if overlap > 0:
                scored.append((overlap, ct_name))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [ct for _, ct in scored[:2]]

        # Check in text content if empty
        if not results:
            for hit in hits:
                for ct_name, _ in cell_type_signatures:
                    if ct_name.lower() in hit.chunk.content.lower() and ct_name not in results:
                        results.append(ct_name)
                        if len(results) >= 2:
                            break

        return results or ["Candidate Cluster (Pending Orthogonal Validation)"]
