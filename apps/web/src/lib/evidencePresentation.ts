import type { Evidence } from "./types";

export function evidenceMarkers(evidence: Record<string, Evidence>): string[] {
  const genes: string[] = [];
  for (const item of Object.values(evidence)) {
    if (item.kind !== "marker_specificity") continue;
    for (const values of [item.content.genes, item.content.qualifying_genes]) {
      if (!Array.isArray(values)) continue;
      for (const value of values) {
        const gene = typeof value === "string" ? value : value?.gene;
        if (typeof gene === "string" && gene.trim()) genes.push(gene.trim());
      }
    }
  }
  return [...new Set(genes)];
}

export function queryMarkers(input: string): string[] {
  return [...new Set(input.split(/[,，\s]+/).map((gene) => gene.trim()).filter(Boolean))].slice(0, 8);
}
