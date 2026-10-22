/**
 * Reading a counts document in the browser (§5.2, §5.6).
 *
 * Both wizards need the same thing: what the bytes actually hold. A plan is built from the design the
 * data carries, not from ids someone retyped, and a registration declares a filtering state the file
 * cannot contradict. So the read lives here once, and each screen reports from it rather than guessing.
 *
 * This is a reader, not a validator. Every claim it surfaces is shown to a person and the server's own
 * contract check stays the last word - a screen that quietly repaired a document would be the platform
 * editing evidence.
 */

export interface CellDesign {
  cell_key: string;
  specimen_id: string;
  donor_id: string;
  group: string;
}

export interface CountsPreview {
  kind: "counts" | "product" | "other";
  slot: string | null;
  schemaVersion: string | null;
  transform: string | null;
  cells: CellDesign[];
  genes: string[];
  missingDesign: string[];
  idSystem: string | null;
  genomeVersion: string | null;
  warnings: string[];
  duplicateKeys: number;
}

export type Inspection = { preview: CountsPreview } | { problem: string };

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((one): one is string => typeof one === "string") : [];
}

/**
 * The shape the reference workflow reads: a slot, a contract version, a declared transform, and a
 * matrix whose cells each carry the design row that every rarity and robustness judgement counts on.
 */
export function inspectDocument(document: Record<string, unknown>): Inspection {
  const matrix = document.matrix as
    | { cells?: unknown; genes?: unknown; genome?: { version?: unknown; id_system?: unknown } }
    | undefined;
  const raw = Array.isArray(matrix?.cells) ? (matrix!.cells as Record<string, unknown>[]) : [];
  if (!raw.length) return { problem: "no_matrix" };
  const cells: CellDesign[] = raw.map((one) => ({
    cell_key: typeof one.cell_key === "string" ? one.cell_key : "",
    specimen_id: typeof one.specimen_id === "string" ? one.specimen_id : "",
    donor_id: typeof one.donor_id === "string" ? one.donor_id : "",
    group: typeof one.group === "string" ? one.group : "",
  }));
  const keys = cells.map((one) => one.cell_key);
  const design: [keyof CellDesign, string][] = [
    ["specimen_id", "specimen_id"],
    ["donor_id", "donor_id"],
    ["group", "group"],
  ];
  const slot = typeof document.slot === "string" ? document.slot : null;
  const genome = matrix?.genome;
  return {
    preview: {
      kind: slot === "counts" ? "counts" : slot ? "product" : "other",
      slot,
      schemaVersion: typeof document.schema_version === "string" ? document.schema_version : null,
      transform: typeof document.transform === "string" ? document.transform : null,
      cells,
      genes: strings(matrix?.genes),
      missingDesign: design.filter(([field]) => cells.some((one) => !one[field])).map(([, name]) => name),
      idSystem: typeof genome?.id_system === "string" ? genome.id_system : null,
      genomeVersion: typeof genome?.version === "string" ? genome.version : null,
      warnings: strings(document.conversion_warnings),
      duplicateKeys: keys.length - new Set(keys).size,
    },
  };
}

/** hdf5 begins with a fixed byte signature, so an h5ad is recognised from the bytes, not the name. */
export function looksH5ad(name: string, bytes: Uint8Array): boolean {
  if (name.toLowerCase().endsWith(".h5ad")) return true;
  return String.fromCharCode(...bytes.subarray(0, 4)) === "\x89HDF";
}

export async function digestHex(bytes: Uint8Array): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", bytes as unknown as ArrayBuffer);
  return Array.from(new Uint8Array(hash))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

/** Distinct values of one design column, sorted, so a screen can list them without recomputing. */
export function distinct(cells: CellDesign[], field: keyof CellDesign): string[] {
  return [...new Set(cells.map((one) => one[field]).filter(Boolean))].sort();
}

/** Donors per group - the count §7.2 and §7.5 judge rarity by, and the one a plan's min_n_per_group is set against. */
export function donorsPerGroup(cells: CellDesign[], onlySpecimens?: string[]): Record<string, number> {
  const scope = onlySpecimens ? new Set(onlySpecimens) : null;
  const byGroup: Record<string, Set<string>> = {};
  for (const cell of cells) {
    if (scope && !scope.has(cell.specimen_id)) continue;
    const group = cell.group || "unassigned";
    (byGroup[group] ||= new Set()).add(cell.donor_id || cell.specimen_id);
  }
  return Object.fromEntries(Object.entries(byGroup).map(([group, donors]) => [group, donors.size]).sort());
}
