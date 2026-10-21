"""A synthetic, fully deterministic scRNA-seq dataset for the workflow tests.

The point of building it here rather than in each test is that the truth is known by construction:
which cells belong to which population, which donor and group they came from, which markers define
them, which genes really differ between the groups, and which cells were deliberately damaged. A
test that cannot say what the answer should be cannot tell a correct executor from a fast wrong one.

Two properties matter more than realism. Every gene is either expressed by one population, by one
group, by every cell, or at a level low enough to drop out - so a marker or differential-expression
step has a single defensible answer. And nothing uses ``random``: the same seed always yields the
same counts, which is what makes byte-level reproducibility testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from bioadapt_schemas.entities import Genome
from bioadapt_schemas.enums import GenomeIdSystem
from bioadapt_schemas.identifiers import canonical_cell_key

from bioadapt_scrnaseq.matrix import CellMeta, CountMatrix
from bioadapt_scrnaseq.numeric import Rng

GENOME_BUILD = "gencode-v44"
SPECIES = "human"

MITO_GENES = ("MT-CO1", "MT-ND1")
RIBOSOMAL_GENES = ("RPL13A", "RPS6")
SHARED_GENES = {"GAPDH": 1200.0, "ACTB": 1600.0, "B2M": 600.0}

#: Expected counts per cell in read space, so a test can reason about library size and about which
#: genes fall below the detection floor.
MITO_LEVEL = 300.0
RIBOSOMAL_LEVEL = 260.0

POPULATION_PROGRAMS: dict[str, dict[str, float]] = {
    "t_cell": {"CD3D": 800.0, "CD3E": 600.0, "IL7R": 240.0},
    "b_cell": {"MS4A1": 700.0, "CD79A": 560.0, "CD79B": 400.0},
    "monocyte": {"CD14": 900.0, "LYZ": 1200.0, "FCGR3A": 160.0},
    "rare_state": {"GATA1": 500.0, "HBB": 800.0, "PTPRC": 120.0},
}

#: The only real between-group effect, applied to every population in the ``case`` donors. Because it
#: crosses all four populations, a group contrast is not secretly a population contrast - the confound
#: section 7.4 names.
GROUP_PROGRAMS: dict[str, dict[str, float]] = {
    "case": {"S100A8": 350.0, "S100A9": 300.0},
    "control": {},
}

#: Expressed everywhere at a low level: the pool of uninformative genes a feature-ranking step has to
#: reject, which is the only way to test that it ranks rather than lists.
N_BACKGROUND_GENES = 72
BACKGROUND_LEVEL = 40.0
BACKGROUND_GENES = tuple(f"BGL{index:03d}" for index in range(1, N_BACKGROUND_GENES + 1))

#: Near the detection floor, so detection frequency stays well below one and the section 7.8
#: expressed-in / not-expressed-in filters have something to exclude.
N_SPARSE_GENES = 12
SPARSE_LEVEL = 3.0
SPARSE_GENES = tuple(f"SPG{index:03d}" for index in range(1, N_SPARSE_GENES + 1))

ALL_GENES = tuple(
    sorted(
        set(MITO_GENES)
        | set(RIBOSOMAL_GENES)
        | set(SHARED_GENES)
        | {gene for program in POPULATION_PROGRAMS.values() for gene in program}
        | {gene for program in GROUP_PROGRAMS.values() for gene in program}
        | set(BACKGROUND_GENES)
        | set(SPARSE_GENES)
    )
)

POPULATION_CELLS = {"t_cell": 6, "b_cell": 4, "monocyte": 3, "rare_state": 3}

#: Four donors per group. Donors are the unit of replication, so this is also the number that decides
#: whether a between-group comparison can claim anything at all (section 7.4).
GROUP_DONORS = {
    "case": ("donor_a", "donor_b", "donor_e", "donor_f"),
    "control": ("donor_c", "donor_d", "donor_g", "donor_h"),
}

#: Donor-level depth. Both groups hold fast and slow donors, so no group difference can be one
#: donor's sequencing depth.
DONOR_FACTORS = {
    "donor_a": 1.0,
    "donor_b": 0.85,
    "donor_c": 1.15,
    "donor_d": 0.95,
    "donor_e": 1.05,
    "donor_f": 0.9,
    "donor_g": 1.1,
    "donor_h": 0.8,
}

DESIGN: tuple[tuple[str, str, str, int], ...] = tuple(
    (population, group, donor, POPULATION_CELLS[population])
    for group in ("case", "control")
    for donor in GROUP_DONORS[group]
    for population in POPULATION_PROGRAMS
)

#: Deliberate defects, one class per donor pair so that each artefact straddles the two groups.
#: ``index`` counts cells inside one (population, donor) block.
SHALLOW_DONORS = ("donor_a", "donor_g")
DEEP_DONORS = ("donor_b", "donor_h")
AMBIENT_DONORS = ("donor_c", "donor_f")
MITO_DONORS = ("donor_d", "donor_e")
SHALLOW_INDEX = 1
DEEP_INDEX = 2
AMBIENT_INDEX = 3
MITO_INDEX = 3
SHALLOW_DEPTH = 0.12
DEEP_DEPTH = 3.5
DAMAGED_MITO_SHARE = 0.45
DOUBLET_SCORE = 0.85
AMBIENT_SCORE = 0.35
#: Every other cell is drawn below these ceilings, so a rule set at the ceiling marks exactly the
#: planted cells and nothing else.
ORDINARY_DOUBLET_CEILING = 0.28
ORDINARY_AMBIENT_CEILING = 0.09


def _barcode(population: str, index: int) -> str:
    return f"{population[:3].upper()}{index:03d}"


def _cell_key(donor: str, barcode: str) -> str:
    return canonical_cell_key(f"spc_{donor}", barcode)


def _baselines(population: str, group: str) -> dict[str, float]:
    levels: dict[str, float] = {gene: BACKGROUND_LEVEL for gene in BACKGROUND_GENES}
    levels.update({gene: SPARSE_LEVEL for gene in SPARSE_GENES})
    levels.update(SHARED_GENES)
    levels.update(POPULATION_PROGRAMS[population])
    levels.update(GROUP_PROGRAMS.get(group, {}))
    for gene in MITO_GENES:
        levels[gene] = MITO_LEVEL
    for gene in RIBOSOMAL_GENES:
        levels[gene] = RIBOSOMAL_LEVEL
    return levels


def _library(rng: Rng, population: str, group: str, donor: str, *, depth: float) -> dict[str, float]:
    factor = depth * DONOR_FACTORS[donor]
    counts: dict[str, float] = {}
    for gene, baseline in _baselines(population, group).items():
        rounded = int(round(baseline * factor * (0.5 + rng.unit())))
        if rounded > 0:
            counts[gene] = float(rounded)
    return counts


def _raise_mito_share(counts: dict[str, float], target: float) -> None:
    """Push the mitochondrial share to exactly ``target`` by adding to the first mito gene."""
    total = sum(counts.values())
    mito = sum(counts.get(gene, 0.0) for gene in MITO_GENES)
    if total <= 0 or mito / total >= target:
        return
    counts[MITO_GENES[0]] = mito + (target * total - mito) / (1.0 - target)


@dataclass(frozen=True)
class SyntheticData:
    """Counts plus the artefact scores plus the construction truth, so QC has a checkable answer."""

    matrix: CountMatrix
    doublet_scores: Mapping[str, float] = field(default_factory=dict)
    ambient_scores: Mapping[str, float] = field(default_factory=dict)
    population_of: Mapping[str, str] = field(default_factory=dict)
    donor_of: Mapping[str, str] = field(default_factory=dict)
    group_of: Mapping[str, str] = field(default_factory=dict)
    shallow_cells: tuple[str, ...] = ()
    doublet_cells: tuple[str, ...] = ()
    ambient_cells: tuple[str, ...] = ()
    high_mito_cells: tuple[str, ...] = ()

    @property
    def planted_defects(self) -> set[str]:
        return set(self.shallow_cells) | set(self.doublet_cells) | set(self.ambient_cells) | set(self.high_mito_cells)

    def expected_populations(self) -> dict[str, set[str]]:
        truth: dict[str, set[str]] = {}
        for cell_key, population in sorted(self.population_of.items()):
            truth.setdefault(population, set()).add(cell_key)
        return truth

    def cells_of(self, population: str, donor: str, index: int) -> str:
        return _cell_key(donor, _barcode(population, index))


def synthetic_dataset(*, seed: int = 20260501) -> SyntheticData:
    """The dataset, generated in a fixed order so the seed alone decides every count.

    The doublet and ambient scores are *not* derived from the counts. They arrive the way a real
    screen's output arrives: a step that claims to screen for doublets has to be handed a score, and
    cannot invent one from a library size.
    """
    rng = Rng(seed)
    cells: list[CellMeta] = []
    counts: dict[str, dict[str, float]] = {}
    scores: dict[str, dict[str, float]] = {"doublet": {}, "ambient": {}}
    truth: dict[str, dict[str, str]] = {"population": {}, "donor": {}, "group": {}}
    defects: dict[str, list[str]] = {"shallow": [], "doublet": [], "ambient": [], "high_mito": []}
    for population, group, donor, n_cells in DESIGN:
        for index in range(1, n_cells + 1):
            key = _cell_key(donor, _barcode(population, index))
            cells.append(CellMeta(cell_key=key, specimen_id=f"spc_{donor}", donor_id=donor, group=group))
            truth["population"][key] = population
            truth["donor"][key] = donor
            truth["group"][key] = group
            depth = 1.0
            if index == SHALLOW_INDEX and donor in SHALLOW_DONORS:
                depth = SHALLOW_DEPTH
                defects["shallow"].append(key)
            elif index == DEEP_INDEX and donor in DEEP_DONORS:
                depth = DEEP_DEPTH
                defects["doublet"].append(key)
            scores["doublet"][key] = DOUBLET_SCORE if key in defects["doublet"] else round(
                0.02 + ORDINARY_DOUBLET_CEILING * rng.unit(), 3
            )
            if index == AMBIENT_INDEX and donor in AMBIENT_DONORS:
                defects["ambient"].append(key)
            scores["ambient"][key] = AMBIENT_SCORE if key in defects["ambient"] else round(
                0.01 + ORDINARY_AMBIENT_CEILING * rng.unit(), 3
            )
            row = _library(rng, population, group, donor, depth=depth)
            if population == "rare_state" and index == MITO_INDEX and donor in MITO_DONORS:
                _raise_mito_share(row, DAMAGED_MITO_SHARE)
                defects["high_mito"].append(key)
            counts[key] = row
    matrix = CountMatrix.build(
        cells=cells,
        counts=counts,
        genome=Genome(
            build=GENOME_BUILD, annotation_source="GENCODE", version="44", id_system=GenomeIdSystem.HGNC_SYMBOL
        ),
    )
    return SyntheticData(
        matrix=matrix,
        doublet_scores=scores["doublet"],
        ambient_scores=scores["ambient"],
        population_of=truth["population"],
        donor_of=truth["donor"],
        group_of=truth["group"],
        shallow_cells=tuple(defects["shallow"]),
        doublet_cells=tuple(defects["doublet"]),
        ambient_cells=tuple(defects["ambient"]),
        high_mito_cells=tuple(defects["high_mito"]),
    )
