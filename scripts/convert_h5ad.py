"""Convert an h5ad into an asset the platform can read (plan V2.0 sections 4.1, 4.4, 5.2).

The repository has both halves of this and had neither joined: :func:`read_h5ad` turns a file into a
contract product, and ``write_asset`` indexes a product. Between them sits the reference deployment's
own limit - its index and object plane live in one process's memory (``serve_workbench.py``), so a
second process has no rows to add. This script therefore writes a drop zone: the payload bytes named by
their own digest, and a manifest whose ``asset`` object is exactly the body
``POST /v1/projects/{project}/assets`` accepts. Registration stays a person's act through the upload
routes rather than a side door into a running server.

    .venv/bin/python scripts/convert_h5ad.py pbmc.h5ad --project-id prj_scrnaseq \\
        --genome-build gencode-v44 --annotation-source GENCODE --genome-version 44 --id-system hgnc_symbol

The annotation is asked for rather than guessed: §10.1 rejects a run whose gene identifier space is
unknown, and a symbol/Ensembl mix "matches no gene and reads as dropout". ``--transform`` is likewise a
declaration - the reference DE step sums raw counts and refuses a log-normalised matrix.

Where the optional scientific stack is absent this prints the adapter's classified ``NEEDS_INPUT`` stop
and exits, because that is a deployment fact a person resolves, not a traceback to read.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from bioadapt_schemas.entities import Genome
from bioadapt_schemas.enums import GenomeIdSystem
from bioadapt_schemas.errors import ErrorClass
from bioadapt_scrnaseq.admission import admit, note_of, write_admission
from bioadapt_scrnaseq.errors import StepFailure
from bioadapt_scrnaseq.scanpy_adapter import DEFAULT_OBS_KEYS, availability, read_h5ad

ROOT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="convert_h5ad.py", description=__doc__.splitlines()[0])
    p.add_argument("input", help="the h5ad file to convert")
    p.add_argument("--project-id", required=True, help="the project the converted asset belongs to")
    p.add_argument("--out", default=".workbench/admissions", help="drop zone directory (default: %(default)s)")
    p.add_argument("--genome-build", required=True, help="e.g. gencode-v44")
    p.add_argument("--annotation-source", required=True, help="e.g. GENCODE")
    p.add_argument("--genome-version", required=True, help="e.g. 44")
    p.add_argument(
        "--id-system",
        required=True,
        choices=[system.value for system in GenomeIdSystem],
        help="the identifier space var names live in; a node refuses a matrix it was not reviewed for",
    )
    p.add_argument("--slot", default="counts", help="the plan's input slot this asset feeds (default: %(default)s)")
    p.add_argument(
        "--transform",
        default="counts",
        help="the state the matrix is really in; the reference steps know 'counts' and 'log2_cpm'",
    )
    p.add_argument("--counts-layer", help="read counts from this layer instead of X")
    for field in DEFAULT_OBS_KEYS:
        p.add_argument(f"--obs-{field.replace('_', '-')}", default=field, help=f"obs column holding {field}")
    p.add_argument(
        "--original-not-kept",
        action="store_false",
        dest="both_sides_kept",
        help="declare that the h5ad is not admitted as an asset too, which §4.1 reports as a breach",
    )
    p.add_argument("--created-at", help="ISO-8601 index timestamp; defaults to now in UTC")
    p.add_argument("--check-backend", action="store_true", help="only report whether the optional backend is here")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    found = availability()
    if args.check_backend:
        for name, version in found.items():
            print(f"backend    {name}={version or 'absent'}")
        return 0 if all(found.values()) else 2

    obs_keys = {field: getattr(args, f"obs_{field}") for field in DEFAULT_OBS_KEYS}
    try:
        read = read_h5ad(
            args.input,
            genome=Genome(
                build=args.genome_build,
                annotation_source=args.annotation_source,
                version=args.genome_version,
                id_system=GenomeIdSystem(args.id_system),
            ),
            slot=args.slot,
            transform=args.transform,
            counts_layer=args.counts_layer,
            obs_keys=obs_keys,
            both_sides_kept=args.both_sides_kept,
        )
        admission = admit(
            read,
            project_id=args.project_id,
            created_at=datetime.fromisoformat(args.created_at) if args.created_at else None,
            obs_keys=obs_keys,
            original=note_of(args.input),
        )
    except StepFailure as failure:
        print(f"{failure.error_class.value} [{failure.code}] {failure.message}", file=sys.stderr)
        return 2 if failure.error_class is ErrorClass.NEEDS_INPUT else 1

    written = write_admission(admission, ROOT / args.out)
    for line in report(read, admission, found, written):
        print(line)
    return 0


def shown(path: Path) -> str:
    """Repo-relative when it is under the checkout, absolute when the person pointed --out elsewhere."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def report(read, admission, found, written) -> list[str]:
    """What the read carried, what it lost, and how to hand the result to the platform."""
    matrix = read.product.matrix
    original = admission.original
    lines = [
        "backend    " + " ".join(f"{name}={version or 'absent'}" for name, version in found.items()),
        f"input      {original.path} sha256={original.file_sha256} {original.size_bytes} bytes",
        (
            f"contract   {matrix.n_cells} cells x {matrix.n_genes} genes slot={admission.slot!r} "
            f"transform={read.product.transform!r} from={read.product.parameters['source']}"
        ),
        "obs        " + " ".join(f"{field}<-{column}" for field, column in sorted(admission.obs_keys.items())),
        f"dropped    {list(admission.dropped_slots) or 'nothing'} "
        f"(h5ad kept alongside: {read.report.both_sides_kept})",
        f"           cells with non-zero counts: {admission.cells_with_counts} of {matrix.n_cells}",
    ]
    lines += [f"warning    {warning}" for warning in admission.warnings]
    lines.append(
        f"asset      {admission.asset_id} format={admission.asset.format} "
        f"schema={admission.asset.schema_version} {len(admission.payload)} bytes"
    )
    lines += [f"wrote      {shown(path)}" for path in written]
    payload = f"{admission.asset_id}.json"
    project = admission.asset.project_id
    lines += [
        "next       1. take the \"asset\" object of " + f"{admission.asset_id}.manifest.json",
        f"           2. POST /v1/projects/{project}/assets with it, then PUT {payload} to the upload_url",
        f"           3. POST /v1/projects/{project}/assets/{admission.asset_id}/complete with the same body",
        f"           4. pass {admission.asset_id} as the asset for slot {admission.slot!r} when launching a run",
        (
            f"note       the row above is only indexed once step 3 runs, and a run still needs a registered "
            "plan and an executor behind it (README §6.4 breakpoints 1-2)"
        ),
    ]
    return lines


if __name__ == "__main__":
    sys.exit(main())
