"""Serve the API over the reference run, for the workbench (plan V2.0 sections 9.5, 11.2).

This is a development entry point, not a deployment. It does three things in one process:

1. runs the real scRNA-seq chain through the real state machine, so the project, the run, the
   candidates and the card drafts are the products of a run rather than rows someone typed;
2. files the drafts as cards, which is the same call the API's own draft route makes;
3. mounts :func:`bioadapt_api.app.create_app` over that index and object plane and mints reference
   IdP tokens for the roles the workbench has to show apart.

Three honest limits of a single-process reference, declared by ``/v1/deployment``:

* The object plane is in memory and its signed URLs point at ``object.internal``, which nothing here
  resolves. A browser can therefore exercise a registration up to the declaration and the completion
  refusal (409 "nothing has been uploaded"), but a real PUT needs MinIO or S3.
* No compute worker executes runs submitted through the API by default; the in-memory queue only
  tracks slots. A machine declared through ``POST /v1/nodes`` does take the work, provided the agent
  on it was started with the secret this process holds for that node's id in
  ``BIOADAPT_NODE_TOKENS`` (a json object of node id to secret).
* The export worker is not part of this process, so a queued export stays QUEUED. The route contract
  - 202, then a download URL only at READY - is what the screens are written against.

Run it with ``.venv/bin/python scripts/serve_workbench.py`` and start ``npm run dev`` in ``apps/web``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "tests" / "integration", ROOT / "workflows" / "scrnaseq" / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import card_factories as CF  # noqa: E402 - the root tree that joins the layers (§9.5)
import driver_factories as F  # noqa: E402

from bioadapt_api.app import create_app  # noqa: E402
from bioadapt_api.deps import ApiState, DeploymentCapabilities, InMemoryQueue, SingleNodeCatalogs  # noqa: E402
from bioadapt_api.identity import Hs256Verifier, IdTokenClaims  # noqa: E402
from bioadapt_api.login_keys import (  # noqa: E402
    ALGORITHM,
    Ed25519Verifier,
    LoginKey,
    load_or_create_key_file,
)
from bioadapt_api.observability import MemoryLogSink  # noqa: E402
from bioadapt_api.quota import ProjectQuotas, StaticQuotas  # noqa: E402
from bioadapt_orchestrator.approvals import ApprovalPolicy  # noqa: E402
from bioadapt_policies.access import GLOBAL_SCOPE, Role  # noqa: E402
from bioadapt_schemas.enums import ApprovalKind  # noqa: E402
from bioadapt_storage.catalog import PLATFORM_SCOPE  # noqa: E402
from bioadapt_storage.objectstore import HmacSigner  # noqa: E402
from bioadapt_storage.traces import InMemoryTraceStore  # noqa: E402

#: The reference secret. A deployment reads this from the secret manager; §11.2 forbids it in code,
#: so this constant only ever exists in a process bound to 127.0.0.1 for local development.
SECRET = b"workbench-development-secret"
ISS = "https://idp.example/bioadapt"
AUD = "bioadapt-api"

#: The subject the bootstrap key file belongs to. A configured deployment provisions its first admin out
#: of band; here the seed writes the file and registers it under the same name the tests use.
ADMIN_SUBJECT = "user_platform_admin_01"

PORT = 8000


def carded_run() -> CF.Carded:
    """The reference run, with its cards filed and one gate left waiting for a person."""
    made = CF.carded_run()
    for draft in made.drafts:
        made.service.persist(draft)
    #: The inbox has to hold something a person has not answered, or the screen that separates
    #: "requested" from "decided" cannot be shown at all (§11.6).
    made.harness.journal.request_approval(
        made.harness.run_id,
        ApprovalKind.ESCALATION,
        actor=F.HUMAN_ANALYST,
        assigned_to=F.PI,
    )
    return made


def tokens(verifier: Hs256Verifier, now) -> dict[str, str]:
    """One token per role, so the workbench can show what a role may and may not do."""
    people = {
        "solo": ("user_solo_operator", [Role.PRINCIPAL_INVESTIGATOR, Role.ANALYST, Role.DOMAIN_EXPERT], ["pwd+mfa"]),
        "pi": (F.PI, [Role.PRINCIPAL_INVESTIGATOR], ["pwd+mfa"]),
        "analyst": (F.ANALYST, [Role.ANALYST], ["pwd+mfa"]),
        "expert": ("user_expert_01", [Role.DOMAIN_EXPERT], ["pwd+mfa"]),
        "expert2": ("user_expert_02", [Role.DOMAIN_EXPERT], []),
        "engine": (F.SERVICE, [Role.SERVICE_AGENT], []),
    }
    return {
        name: verifier.issue(
            IdTokenClaims(
                sub=subject,
                iss=ISS,
                aud=AUD,
                roles=roles,
                project_ids=[F.PROJECT_ID],
                is_service_account=name == "engine",
                solo_mode=name == "solo",
                amr=amr,
                exp=int((now + timedelta(hours=8)).timestamp()),
            )
        )
        for name, (subject, roles, amr) in people.items()
    }


def admin_credential(catalog, workbench: Path) -> LoginKey:
    """The bootstrap key: written 0600 on this machine, registered as the platform's first admin.

    §11.2 is why the seed hands over a file instead of printing a bearer token: the file is the thing a
    person keeps, and what it buys is a token that expires. Nothing about a login is decided by the key
    itself - the row below states the roles, and the same row is what a revocation stamps.
    """
    label = "written by scripts/serve_workbench.py"
    key = load_or_create_key_file(workbench / "admin.pem", subject=ADMIN_SUBJECT, label=label)
    catalog.set_scope(PLATFORM_SCOPE)
    if catalog.active_credential(key.kid) is None:
        catalog.register_credential(
            kid=key.kid,
            subject_id=ADMIN_SUBJECT,
            algorithm=ALGORITHM,
            public_key=key.public_pem,
            fingerprint=key.fingerprint,
            roles=[str(Role.PLATFORM_ADMIN)],
            project_ids=[GLOBAL_SCOPE],
            amr=["pwd+mfa"],
            solo_mode=False,
            is_service_account=False,
            label=label,
            created_by="deployment",
        )
    return key


def node_tokens() -> dict[str, str]:
    """The credential for each declared node, as a json object of node id to secret.

    One secret per machine, not one secret every machine knows: a shared secret would let any node
    open every other. Unset, a declared node cannot be reached and dispatch says so rather than
    running the work here.
    """
    raw = os.environ.get("BIOADAPT_NODE_TOKENS")
    if not raw:
        return {}
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise SystemExit(f"BIOADAPT_NODE_TOKENS is not json: {error}") from error
    if not isinstance(document, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in document.items()
    ):
        raise SystemExit("BIOADAPT_NODE_TOKENS must be a json object mapping node id to secret")
    return document


def build() -> tuple[object, dict[str, str], LoginKey, CF.Carded]:
    made = carded_run()
    harness = made.harness
    verifier = Hs256Verifier(SECRET, issuer=ISS, audience=AUD)
    admin = admin_credential(harness.catalog, ROOT / ".workbench")
    state = ApiState(
        catalogs=SingleNodeCatalogs(harness.catalog),
        objects=harness.objects,
        signer=HmacSigner(SECRET),
        verifier=verifier,
        quotas=StaticQuotas(
            default=ProjectQuotas(
                max_storage_bytes=10_000_000,
                max_active_runs=4,
                max_run_compute_minutes=480,
                max_run_token_cost_usd=10.0,
            )
        ),
        queue=InMemoryQueue(running_slots=1),
        traces=InMemoryTraceStore(),
        log_sink=MemoryLogSink(),
        clock=harness.clock,
        approval_policy=ApprovalPolicy(working_days=3),
        deployment=DeploymentCapabilities(
            mode="reference",
            object_upload="unavailable",
            run_execution="unavailable",
            export_generation="unavailable",
        ),
        login_keys=Ed25519Verifier(SingleNodeCatalogs(harness.catalog), issuer=ISS, audience=AUD),
        #: §9.7: the node credentials are the deployment's, per node, so they arrive from the
        #: environment and are never written to a row. Unset, a declared node cannot be reached and
        #: dispatch says so.
        node_tokens=node_tokens(),
    )
    return create_app(state), tokens(verifier, harness.clock()), admin, made


def main() -> None:
    import uvicorn

    app, minted, admin, made = build()
    out = ROOT / ".workbench"
    out.mkdir(exist_ok=True)
    (out / "tokens.json").write_text(json.dumps(minted, indent=2), encoding="utf-8")
    print(f"project    {F.PROJECT_ID}")
    print(f"run        {made.harness.run_id}")
    print(f"candidates {len(made.candidates)} · cards filed {len(made.drafts)}")
    login_key = (out / "admin.pem").relative_to(ROOT)
    print(f"login key  {login_key} (0600, subject {ADMIN_SUBJECT})")
    print(f"           kid {admin.kid} · fingerprint {admin.fingerprint}")
    print(f"           sign in with: .venv/bin/python scripts/bioadapt_login.py login --key {login_key}")
    print("           or paste a token into the workbench's 'Acting as' panel; the reference presets still answer.")
    print("  * Tip: In the web UI, click '⚡ Solo Mode' at the top bar to run in single-user mode (exempt from SoD blocks).")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
