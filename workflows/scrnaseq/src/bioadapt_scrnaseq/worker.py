"""The child side of the execution seam: one node's work, run in this process (§3.3, §4.2).

The control plane hands a node its work as a request and takes back an answer; this module is the
node's end of that. It reads the request the parent staged, runs the step, and writes the answer
where the parent will find it. The bytes are the same either way - whether the work ran here or on
a server across the room - because the answer is the product's own canonical form and not a row in
an index this process cannot see (§4.2).

A node that runs work for other people does not trust what it was handed: the parameters are
checked against the digest the request names, and the inputs are checked against theirs, so a
request that was changed on the way here is refused rather than run (§4.1).
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Sequence

from bioadapt_tool_registry.process import (
    WORK_DIR_ENV,
    read_process_request,
    write_process_outcome,
)

from .backends import InProcessBackend
from .implementation import parameters_sha256


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the attempt this work dir holds, and answer with what came out."""
    work_dir = os.environ.get(WORK_DIR_ENV)
    if not work_dir:
        print(
            "no work dir was named, so no attempt was handed to this process: a node does not pick "
            "up work of its own accord",
            file=sys.stderr,
        )
        return 2
    request = read_process_request(work_dir)
    if parameters_sha256(request.parameters) != request.parameters_sha256:
        print(
            "the parameters this process was handed are not the ones the request names, so the "
            "attempt is not run",
            file=sys.stderr,
        )
        return 3
    outcome = InProcessBackend().execute(request)
    write_process_outcome(work_dir, outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
