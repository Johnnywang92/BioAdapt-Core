"""Deterministic numerics for the reference executor (plan V2.0 sections 4.4, 5.6, 7.5, 7.8).

The reference executor has no third-party math dependency, so this module owns the small amount of
linear algebra and statistics the workflow needs. Two properties matter more than speed here:

* a run is byte-reproducible, so the random stream is the platform's own (splitmix64) rather than
  CPython's ``random``, whose output for ``sample``/``shuffle`` is not a stability promise; and
* every tie is broken by an explicit ordering rule, never by dict or set iteration order, because
  section 7.5 compares clusterings across perturbations and a tie-break that moves would move the
  stability number with it.
"""

from __future__ import annotations

import math
from typing import Callable, Collection, Iterable, Mapping, Optional, Sequence

EPSILON = 1e-12
_MASK64 = (1 << 64) - 1


class Rng:
    """Splitmix64: a short, stable, seed-addressable integer stream."""

    def __init__(self, seed: int) -> None:
        if seed < 0:
            raise ValueError("seed must be a non-negative integer")
        self.state = seed & _MASK64

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & _MASK64
        value = self.state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK64
        return value ^ (value >> 31)

    def unit(self) -> float:
        return (self.next_u64() >> 11) / float(1 << 53)

    def uniform(self, low: float, high: float) -> float:
        return low + (high - low) * self.unit()

    def normal(self) -> float:
        u = max(self.unit(), EPSILON)
        v = self.unit()
        return math.sqrt(-2.0 * math.log(u)) * math.cos(2.0 * math.pi * v)

    def shuffled(self, items: Sequence) -> list:
        out = list(items)
        for index in range(len(out) - 1, 0, -1):
            swap = self.next_u64() % (index + 1)
            out[index], out[swap] = out[swap], out[index]
        return out

    def sampled(self, population: Sequence, k: int) -> list:
        return self.shuffled(population)[:k]


def mean(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        raise ValueError("mean of an empty sequence")
    return sum(items) / len(items)


def median(values: Iterable[float]) -> float:
    items = sorted(values)
    if not items:
        raise ValueError("median of an empty sequence")
    middle = len(items) // 2
    if len(items) % 2:
        return float(items[middle])
    return (items[middle - 1] + items[middle]) / 2.0


def quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile (the definition the QC diagnostics are recorded against)."""
    items = sorted(values)
    if not items:
        raise ValueError("quantile of an empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be a probability")
    if len(items) == 1:
        return float(items[0])
    position = (len(items) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(items) - 1)
    fraction = position - low
    return items[low] * (1.0 - fraction) + items[high] * fraction


def variance(values: Iterable[float]) -> float:
    items = list(values)
    if len(items) < 2:
        return 0.0
    mu = mean(items)
    return sum((value - mu) ** 2 for value in items) / (len(items) - 1)


def stdev(values: Iterable[float]) -> float:
    return math.sqrt(variance(values))


def normalize_vector(vector: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= EPSILON:
        raise ValueError("cannot normalize a zero vector")
    return tuple(value / norm for value in vector)


def _matvec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    return [sum(row[column] * vector[column] for column in range(len(vector))) for row in matrix]


def principal_components(
    covariance: Sequence[Sequence[float]],
    n_components: int,
    *,
    seed: int,
    iterations: int = 200,
    tolerance: float = 1e-9,
) -> tuple[list[float], list[tuple[float, ...]]]:
    """Deflated power iteration over a symmetric covariance matrix.

    The leading eigenvectors of the feature covariance are the principal components. Power iteration
    with hot deflation is enough for the small feature counts the reference executor runs on, and it
    is deterministic given the seed; a degenerate eigenvalue pair would split differently under a
    different starting vector, which is why the seed is part of every downstream stability number.
    """
    size = len(covariance)
    if size == 0:
        return [], []
    if n_components > size:
        raise ValueError(f"cannot extract {n_components} components from a {size}-dimensional space")
    rng = Rng(seed)
    residual = [list(row) for row in covariance]
    values: list[float] = []
    vectors: list[tuple[float, ...]] = []
    for _ in range(n_components):
        vector = normalize_vector([rng.normal() for _ in range(size)])
        eigenvalue = sum(vector[i] * value for i, value in enumerate(_matvec(residual, vector)))
        for _ in range(iterations):
            candidate = normalize_vector(_matvec(residual, vector))
            previous = eigenvalue
            eigenvalue = sum(candidate[i] * value for i, value in enumerate(_matvec(residual, candidate)))
            overlap = abs(sum(a * b for a, b in zip(candidate, vector)))
            vector = candidate
            if abs(eigenvalue - previous) <= tolerance * max(1.0, abs(eigenvalue)) and overlap >= 1.0 - 1e-7:
                break
        if eigenvalue <= EPSILON:
            break
        values.append(eigenvalue)
        vectors.append(vector)
        for row in range(size):
            for column in range(size):
                residual[row][column] -= eigenvalue * vector[row] * vector[column]
    return values, vectors


def explained_variance_ratio(eigenvalues: Sequence[float]) -> tuple[float, ...]:
    """Share of the *retained* components, normalised over the components the step kept."""
    total = sum(value for value in eigenvalues if value > 0)
    if total <= EPSILON:
        return ()
    return tuple(value / total for value in eigenvalues if value > 0)


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Step-up FDR control, monotone from the largest p upward (section 7.8 item 4)."""
    n = len(p_values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda index: (p_values[index], index))
    adjusted = [0.0] * n
    previous = 1.0
    for rank, index in reversed(list(enumerate(order, start=1))):
        value = min(previous, p_values[index] * n / rank)
        adjusted[index] = value
        previous = value
    return adjusted


def permutation_p_value(
    observed: float,
    statistic: Callable[[Sequence[str]], float],
    labels: Sequence[str],
    *,
    rng: Rng,
    replicates: int,
    paired: bool = False,
) -> float:
    """Two-sided permutation p-value, with the observed arrangement counted in both tails.

    Adding one to numerator and denominator keeps a permutation p-value strictly positive, so a
    gene can never reach an FDR of zero on a finite number of shuffles.

    ``paired`` switches the randomisation unit. Unpaired re-deals the group labels between donors.
    Paired keeps every donor's own difference and flips its sign independently: a paired contrast is
    handed over as a constant label list, so shuffling it would be a no-op and would return a p of
    one for any effect size however large.
    """
    absolute = abs(observed)
    if absolute <= EPSILON:
        return 1.0
    more_extreme = 0
    for _ in range(replicates):
        assignment = ["+" if rng.unit() < 0.5 else "-" for _ in labels] if paired else rng.shuffled(labels)
        if abs(statistic(assignment)) >= absolute:
            more_extreme += 1
    return (1 + more_extreme) / (1 + replicates)


def jaccard(first: Collection[str], second: Collection[str]) -> float:
    """Set overlap, the cell-set half of the section 7.5 stability pair.

    A cluster whose perturbed version holds none of the same cells scores 0 rather than raising:
    "the set is still there but is no longer this set" is a stability result, not a bad call.
    """
    a, b = set(first), set(second)
    union = a | b
    if not union:
        raise ValueError("jaccard needs at least one cell on one side")
    return len(a & b) / len(union)


def _pairs(n: int) -> int:
    return n * (n - 1) // 2


def adjusted_rand_index(first: Mapping[str, str], second: Mapping[str, str]) -> Optional[float]:
    """Chance-corrected Rand index over the cells both partitions label (section 7.5's ARI).

    Identical partitions give 1 and independent random ones give about 0, which is the whole point for
    a small cluster: on twenty cells an uncorrected index reads as agreement by luck alone. ``None``
    means no chance-corrected index exists for the pair - one side puts every shared cell in the same
    group and there is nothing to correct against - and the protocol has to record that instead of a 1.
    """
    shared = sorted(set(first) & set(second))
    if len(shared) < 2:
        raise ValueError(f"an adjusted rand index needs two cells in common, got {len(shared)}")
    rows: dict[str, int] = {}
    columns: dict[str, int] = {}
    cells: dict[tuple[str, str], int] = {}
    for cell in shared:
        pair = (first[cell], second[cell])
        cells[pair] = cells.get(pair, 0) + 1
        rows[pair[0]] = rows.get(pair[0], 0) + 1
        columns[pair[1]] = columns.get(pair[1], 0) + 1
    observed = sum(_pairs(count) for count in cells.values())
    row_total = sum(_pairs(count) for count in rows.values())
    column_total = sum(_pairs(count) for count in columns.values())
    expected = row_total * column_total / _pairs(len(shared))
    ceiling = (row_total + column_total) / 2.0
    denominator = ceiling - expected
    if denominator <= EPSILON:
        return None
    return (observed - expected) / denominator


def build_adjacency(nodes: Sequence[str], weights: Mapping[tuple[str, str], float]) -> dict[str, dict[str, float]]:
    adjacency: dict[str, dict[str, float]] = {node: {} for node in nodes}
    for (left, right), weight in sorted(weights.items()):
        if left == right or weight <= 0.0:
            continue
        if left not in adjacency or right not in adjacency:
            raise ValueError(f"edge {left}-{right} names a node outside the graph")
        adjacency[left][right] = adjacency[left].get(right, 0.0) + weight
        adjacency[right][left] = adjacency[right].get(left, 0.0) + weight
    return adjacency


def degree_of(adjacency: Mapping[str, Mapping[str, float]], node: str) -> float:
    return sum(adjacency[node].values())


def edge_weight_total(adjacency: Mapping[str, Mapping[str, float]]) -> float:
    """Sum over undirected edges, each counted once."""
    return sum(sum(row.values()) for row in adjacency.values()) / 2.0


def modularity(
    assignment: Mapping[str, str],
    adjacency: Mapping[str, Mapping[str, float]],
    resolution: float,
) -> float:
    """Weighted modularity at a resolution gamma: the number recorded beside each clustering."""
    total = edge_weight_total(adjacency)
    if total <= EPSILON:
        return 0.0
    internal: dict[str, float] = {}
    for left, row in adjacency.items():
        for right, weight in row.items():
            if assignment[left] == assignment[right]:
                community = assignment[left]
                internal[community] = internal.get(community, 0.0) + weight / 2.0
    community_degree: dict[str, float] = {}
    for node, row in adjacency.items():
        community = assignment[node]
        community_degree[community] = community_degree.get(community, 0.0) + sum(row.values())
    score = 0.0
    for community, weight in sorted(internal.items()):
        expected = community_degree.get(community, 0.0) / (2.0 * total)
        score += weight / total - resolution * expected * expected
    return score


def greedy_modularity_partition(
    nodes: Sequence[str],
    weights: Mapping[tuple[str, str], float],
    *,
    resolution: float,
    seed: int,
    max_sweeps: int = 10,
) -> dict[str, str]:
    """Deterministic local-moving community detection over a weighted graph.

    This is the reference executor's stand-in for Leiden: the same objective (modularity at a
    resolution gamma) optimised by greedy node moves, with no refinement step and no graph
    aggregation. A ToolSpec that runs it has to say so in ``assumptions`` and may not claim what
    Leiden guarantees. Visiting order comes from the seed and ties break on the community name, so
    the same graph plus the same seed always gives the same partition.
    """
    adjacency = build_adjacency(nodes, weights)
    degree = {node: degree_of(adjacency, node) for node in nodes}
    doubled = sum(degree.values())
    if doubled <= EPSILON:
        return {node: f"c{index:03d}" for index, node in enumerate(sorted(nodes))}

    communities = {node: node for node in nodes}
    sigma = {node: degree[node] for node in nodes}
    rng = Rng(seed)
    for _sweep in range(max_sweeps):
        moved = False
        for node in rng.shuffled(sorted(nodes)):
            k_i = degree[node]
            if k_i <= EPSILON:
                continue
            current = communities[node]
            sigma[current] -= k_i
            to_community: dict[str, float] = {}
            for other, weight in sorted(adjacency[node].items()):
                target = communities[other]
                to_community[target] = to_community.get(target, 0.0) + weight
            best = current
            best_gain = to_community.get(current, 0.0) - resolution * k_i * sigma[current] / doubled
            for target, edges in sorted(to_community.items(), key=lambda item: (-item[1], item[0])):
                if target == current:
                    continue
                gain = edges - resolution * k_i * sigma[target] / doubled
                if gain > best_gain + EPSILON:
                    best, best_gain = target, gain
            if best != current:
                communities[node] = best
                sigma[best] += k_i
                moved = True
            else:
                sigma[current] += k_i
        if not moved:
            break

    smallest: dict[str, str] = {}
    for node in sorted(nodes):
        community = communities[node]
        if community not in smallest or node < smallest[community]:
            smallest[community] = node
    names = {
        community: f"c{index:03d}"
        for index, community in enumerate(sorted(smallest, key=lambda cid: smallest[cid]))
    }
    return {node: names[communities[node]] for node in sorted(nodes)}
