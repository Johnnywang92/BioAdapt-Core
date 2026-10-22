"""The identity directory and the login-key exchange (plan V2.0 sections 9.7, 11.2, 11.6, 13.1).

A deployment that authenticates with key files has two halves. Admins register a subject's public key
with the roles and projects that subject is *given* - a human act, carrying a second factor, audited the
way every other permission change is. The subject then signs a short assertion with the private half
that never leaves their machine, and this module trades it for an access token of ``login_token_ttl``.

The routes here are the platform's own, not a project's: a person is not a property of one project, so
the rows and their audit trail live under the global scope every global-scope read uses (§5.8).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, ValidationError

from bioadapt_policies.access import GLOBAL_SCOPE, Action, Actor, Role
from bioadapt_schemas.entities import Contract

from .deps import ApiState, ClaimsDep, Session, StateDep, open_session, request_trace_id
from .errors import conflict, invalid, unauthorized
from .identity import IdTokenClaims
from .login_keys import ALGORITHM, assertion_kid, fingerprint_of, kid_of, load_public_key
from .paging import Page, page_params

router = APIRouter(tags=["iam"])


class CredentialRegistration(Contract):
    """What an admin writes about a key. The key's own id and fingerprint are derived, never typed."""

    subject_id: str = Field(min_length=1)
    public_key: str = Field(min_length=1)
    roles: list[Role] = Field(min_length=1)
    project_ids: list[str] = Field(min_length=1)
    amr: list[str] = Field(default_factory=list)
    solo_mode: bool = False
    #: §11.4: a key that signs in for a pipeline is not a person, and that is a fact of the registration,
    #: not something to infer from a role name later.
    is_service_account: bool = False
    label: Optional[str] = None


class TokenExchange(Contract):
    assertion: str = Field(min_length=1)


#: A screen never needs the PEM back: the fingerprint is what two people compare, and the directory text
#: is only ever read by the verifier that checks a signature against it.
def _visible(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "public_key"}


def _platform_session(request: Request, state: ApiState, claims: IdTokenClaims) -> Session:
    """A session over the directory: the global scope, so its rows and its audit trail are one place."""
    return open_session(state, claims, GLOBAL_SCOPE, request_trace_id(request))


@router.post("/iam/credentials", status_code=201)
def register_credential(body: CredentialRegistration, request: Request, state: StateDep, claims: ClaimsDep) -> dict:
    """Hand a subject a key: §11.6 makes this a human act with a second factor, and §13.1 keeps the
    roles it grants mutually consistent - so a registered key cannot hold a pair a live session is
    refused for, even in solo mode, where the platform's own actor check would look the other way."""
    session = _platform_session(request, state, claims)
    session.enforce(Action.ROLE_GRANT, body.subject_id, "iam_credential")
    try:
        public = load_public_key(body.public_key)
    except Exception as exc:  # noqa: BLE001 - the message is the answer, the PEM is untrusted input
        raise invalid(
            "public_key", "an admin registers an Ed25519 public key in PEM form, as the login helper prints it"
        ) from exc
    try:
        Actor(
            subject_id=body.subject_id,
            roles=body.roles,
            project_ids=body.project_ids,
            is_service_account=body.is_service_account,
        )
    except ValidationError as exc:
        raise invalid("roles", exc.errors()[0]["msg"]) from exc
    kid, fingerprint = kid_of(public), fingerprint_of(public)
    taken = session.catalog.find_credential(algorithm=ALGORITHM, fingerprint=fingerprint)
    if taken is not None:
        raise conflict(
            f"this key is already registered as {taken['kid']} for {taken['subject_id']}", field="public_key"
        )
    session.catalog.register_credential(
        kid=kid,
        subject_id=body.subject_id,
        algorithm=ALGORITHM,
        public_key=body.public_key,
        fingerprint=fingerprint,
        roles=[str(role) for role in body.roles],
        project_ids=list(body.project_ids),
        amr=list(body.amr),
        solo_mode=body.solo_mode,
        is_service_account=body.is_service_account,
        label=body.label,
        created_by=session.actor.subject_id,
    )
    session.audit(
        "credential:register",
        kid,
        details={
            "subject_id": body.subject_id,
            "fingerprint": fingerprint,
            "roles": [str(role) for role in body.roles],
            "project_ids": list(body.project_ids),
            "amr": list(body.amr),
            "solo_mode": body.solo_mode,
            "is_service_account": body.is_service_account,
        },
    )
    return _visible(session.catalog.get_credential(kid))


@router.get("/iam/credentials")
def list_credentials(
    request: Request,
    state: StateDep,
    claims: ClaimsDep,
    subject_id: Optional[str] = Query(None, description="only this subject's keys"),
    active_only: bool = Query(False, description="hide the keys someone has revoked"),
    page: Page = Depends(page_params),
) -> dict:
    """Read the directory as an accountable party would (§11.2): the same ``audit:read`` that opens the
    audit trail opens this, and a subject-scoped token still cannot reach it - the rows are platform
    level, so only a global-scope actor has any project the check could pass on."""
    session = _platform_session(request, state, claims)
    session.enforce(Action.AUDIT_READ, "iam_credential", "iam_credential")
    rows = session.catalog.list_credentials(
        subject_id=subject_id, active_only=active_only, limit=page.limit, offset=page.offset
    )
    return page.envelope([_visible(row) for row in rows])


@router.post("/iam/credentials/{kid}/revoke")
def revoke_credential(kid: str, request: Request, state: StateDep, claims: ClaimsDep) -> dict:
    """Close a key. The next exchange is refused, and a session already holding a token runs out on its
    own TTL - which is why that TTL is minutes, not the day a printed token used to be."""
    session = _platform_session(request, state, claims)
    session.enforce(Action.ROLE_GRANT, kid, "iam_credential")
    try:
        row = session.catalog.revoke_credential(kid, revoked_by=session.actor.subject_id)
    except LookupError as exc:
        raise conflict(f"key {kid} is not an active row in the identity directory", field="kid") from exc
    session.audit(
        "credential:revoke",
        kid,
        details={"subject_id": row["subject_id"], "fingerprint": row["fingerprint"]},
    )
    return _visible(row)


@router.post("/token/exchange")
def exchange(body: TokenExchange, request: Request, state: StateDep) -> dict:
    """Turn a signed assertion into a short-lived access token - the only route a key file is used for.

    Nothing here trusts the assertion's idea of the caller's authority: the signature, issuer, audience
    and window are checked, and the roles, projects and attested second factor are the directory's.
    """
    if state.login_keys is None:
        raise unauthorized("this deployment registers no login keys, so an assertion has nothing to verify against")
    if not hasattr(state.verifier, "issue"):
        raise unauthorized("this platform's IdP mints its own tokens, so the exchange has nothing to sign here")
    claims = state.login_keys.verify(body.assertion, now=state.clock())
    expires_at = state.clock() + state.login_token_ttl
    token = state.verifier.issue(claims.model_copy(update={"exp": int(expires_at.timestamp())}))
    session = _platform_session(request, state, claims)
    session.audit(
        "credential:login",
        assertion_kid(body.assertion),
        details={"subject_id": claims.sub, "expires_at": expires_at.isoformat()},
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": int(state.login_token_ttl.total_seconds()),
        "expires_at": expires_at.isoformat(),
        "subject_id": claims.sub,
        "roles": [str(role) for role in claims.roles],
        "project_ids": list(claims.project_ids),
        "is_service_account": claims.is_service_account,
    }
