"""The launcher that puts the cage around the work where the work happens (§9.6).

The cpu ceiling is the operating system's to keep, not the control plane's: a step that outruns its
budget is stopped by the kernel, with the signal it is supposed to get, whether or not anybody is
watching. The launcher sets that limit and then hands the process over to the command it was
given, so the tool runs with the limit already in force rather than with a promise that somebody
would have to keep.

The launcher is a separate program on purpose. A limit set in the parent would have to be set
between the fork and the exec, which is not a place a threaded control plane may put itself; a
program that sets its own limit and then replaces itself with the tool has no such gap.
"""

from __future__ import annotations

import argparse
import os
import resource
import sys
from typing import Optional, Sequence


def _cpu_limit(seconds: int) -> None:
    """Hold this process to ``seconds`` of cpu time, for this process and whatever it execs.

    The hard limit is lowered with the soft one where the platform allows it, so the ceiling cannot
    be raised again from inside. Where it does not, the soft limit alone still earns the kernel's
    attention at the ceiling.
    """
    limit = max(1, seconds)
    _soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
    target = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (target, target))
    except ValueError:
        resource.setrlimit(resource.RLIMIT_CPU, (target, hard))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="bioadapt_tool_registry.limits")
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("the command to run is required")
    _cpu_limit(args.cpu_seconds)
    os.execvp(command[0], command)
    return 0  # unreachable: execvp replaces this process


if __name__ == "__main__":
    sys.exit(main())
