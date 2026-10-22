#!/usr/bin/env python
"""Sign in with a key file, the way a person signs in to a server (§11.2).

    .venv/bin/python scripts/bioadapt_login.py keygen --out ~/.ssh/bioadapt_pi.pem --subject user_pi_02
    # an admin adds the printed .pub text on the members screen, giving that subject its roles
    .venv/bin/python scripts/bioadapt_login.py login --key ~/.ssh/bioadapt_pi.pem

``keygen`` never sends anything anywhere: the private half is written 0600 and stays on this machine,
and the ``.pub`` file is the text an admin registers. ``login`` signs a one-minute assertion with that
key, trades it for an access token of the deployment's choosing, and prints the token for the person
holding the terminal - short on purpose, so the thing worth keeping is the key file and not the printout.

Nothing here decides what a session may do. Roles, projects and the attested second factor are read
from the platform's identity directory (``POST /v1/iam/credentials``), so an admin revoking a key closes
the door on the next login rather than when a token happens to expire.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from bioadapt_api.login_keys import (
    MAX_ASSERTION_TTL_SECONDS,
    generate_login_key,
    key_from_private,
    sidecar_path,
    sign_assertion,
    subject_of_key_file,
    write_key_file,
)

DEFAULT_API = "http://127.0.0.1:8000"
ISSUER = "https://idp.example/bioadapt"
AUDIENCE = "bioadapt-api"


def keygen(args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser()
    if out.exists():
        print(f"refusing to overwrite {out}: it already holds a key file", file=sys.stderr)
        return 2
    subject = args.subject or ""
    key = generate_login_key()
    write_key_file(out, key, subject=subject, label=args.label)
    print(f"wrote {out} (0600, not to be pasted anywhere) and {sidecar_path(out)}")
    print(f"kid          {key.kid}")
    print(f"fingerprint  {key.fingerprint}")
    if subject:
        print(f"subject      {subject}")
    print("give the .pub text to an admin; the private half stays on this machine.")
    return 0


def login(args: argparse.Namespace) -> int:
    path = Path(args.key).expanduser()
    if not path.exists():
        print(f"no key file at {path}", file=sys.stderr)
        return 2
    subject = args.subject or subject_of_key_file(path)
    if not subject:
        print(
            f"{path} does not say which subject it belongs to; pass --subject user_...",
            file=sys.stderr,
        )
        return 2
    key = key_from_private(path.read_text(encoding="utf-8"))
    assertion = sign_assertion(
        key.private_pem,
        kid=key.kid,
        subject_id=subject,
        issuer=args.issuer,
        audience=args.audience,
        ttl_seconds=args.ttl,
    )
    request = urllib.request.Request(
        f"{args.api.rstrip('/')}/v1/token/exchange",
        data=json.dumps({"assertion": assertion}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # The platform's own words are the answer; a paraphrase would hide which rule refused.
        problem = _problem(exc.read())
        print(f"{exc.code}: {problem}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"the API at {args.api} did not answer: {exc.reason}", file=sys.stderr)
        return 1
    print(body["access_token"])
    print(
        f"# {body['subject_id']} as {', '.join(body['roles'])} in {', '.join(body['project_ids'])} - "
        f"valid for {body['expires_in']}s, paste into the workbench's 'Acting as' panel",
        file=sys.stderr,
    )
    return 0


def _problem(raw: bytes) -> str:
    try:
        return json.loads(raw)["message"]
    except Exception:  # noqa: BLE001 - a non-JSON refusal is still worth printing whole
        return raw.decode(errors="replace").strip() or "the platform refused without a message"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    make = commands.add_parser("keygen", help="write a new key file and its .pub half")
    make.add_argument("--out", required=True, help="where to write the private key file")
    make.add_argument("--subject", help="the subject id an admin should register this key under")
    make.add_argument("--label", default="workstation", help="a name for this key, shown in the directory")
    make.set_defaults(run=keygen)

    enter = commands.add_parser("login", help="trade a signed assertion for a short access token")
    enter.add_argument("--key", required=True, help="the key file keygen wrote")
    enter.add_argument("--subject", help="override the subject the .pub file names")
    enter.add_argument("--api", default=DEFAULT_API)
    enter.add_argument(
        "--ttl", type=int, default=60, help=f"assertion window, at most {MAX_ASSERTION_TTL_SECONDS}s"
    )
    enter.add_argument("--issuer", default=ISSUER)
    enter.add_argument("--audience", default=AUDIENCE)
    enter.add_argument("--timeout", type=float, default=10.0)
    enter.set_defaults(run=login)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
