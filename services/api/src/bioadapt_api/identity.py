"""Bearer token to actor (plan V2.0 section 9.7).

Production installs the customer IdP verifier (RS256 + JWKS) behind the same protocol;
:class:`Hs256Verifier` is the reference verifier for the single-node deployment, and it enforces
signature, issuer, audience and expiry before any claim is trusted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Protocol

from pydantic import Field, ValidationError

from bioadapt_policies.access import Actor, Role
from bioadapt_schemas.entities import ImmutableContract

from .errors import unauthorized

MFA_METHODS = frozenset({"mfa", "totp", "pwd+mfa", "otp"})


def encode_segment(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_segment(segment: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except Exception as exc:  # noqa: BLE001 - a malformed segment is simply an invalid token
        raise unauthorized("the bearer token is not a well-formed compact JWS") from exc


class IdTokenClaims(ImmutableContract):
    sub: str = Field(min_length=1)
    iss: str = Field(min_length=1)
    aud: str = Field(min_length=1)
    roles: list[Role] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    is_service_account: bool = False
    amr: list[str] = Field(default_factory=list)
    exp: int
    solo_mode: bool = False

    @property
    def expires_at(self) -> datetime:
        return datetime.fromtimestamp(self.exp, UTC)

    def actor(self) -> Actor:
        return Actor(
            subject_id=self.sub,
            roles=self.roles,
            project_ids=self.project_ids,
            is_service_account=self.is_service_account,
            mfa_verified=bool(MFA_METHODS & set(self.amr)),
            solo_mode=self.solo_mode,
        )


class TokenVerifier(Protocol):
    def verify(self, token: str, *, now: datetime) -> IdTokenClaims: ...


class Hs256Verifier:
    HEADER = encode_segment(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())

    def __init__(self, secret: bytes, *, issuer: str, audience: str) -> None:
        if len(secret) < 16:
            raise ValueError("verification secret is too short")
        self._secret = secret
        self.issuer = issuer
        self.audience = audience

    def _signature(self, signing_input: str) -> bytes:
        return hmac.new(self._secret, signing_input.encode(), hashlib.sha256).digest()

    def issue(self, claims: IdTokenClaims) -> str:
        """Reference IdP: mint a token for the claim set (used by the single-node login path and tests)."""
        payload = encode_segment(json.dumps(claims.model_dump(mode="json"), separators=(",", ":")).encode())
        signing_input = f"{self.HEADER}.{payload}"
        return f"{signing_input}.{encode_segment(self._signature(signing_input))}"

    def verify(self, token: str, *, now: datetime) -> IdTokenClaims:
        parts = token.split(".")
        if len(parts) != 3:
            raise unauthorized("the bearer token is not a compact JWS")
        header, payload, signature = parts
        signing_input = f"{header}.{payload}"
        if not hmac.compare_digest(decode_segment(signature), self._signature(signing_input)):
            raise unauthorized("the token signature does not verify")
        if json.loads(decode_segment(header)).get("alg") != "HS256":
            raise unauthorized("unexpected token algorithm")
        try:
            claims = IdTokenClaims.model_validate(json.loads(decode_segment(payload)))
        except (ValidationError, ValueError) as exc:
            raise unauthorized("the token claims are not usable") from exc
        if claims.iss != self.issuer:
            raise unauthorized(f"token issuer {claims.iss!r} is not trusted here")
        if claims.aud != self.audience:
            raise unauthorized(f"token audience {claims.aud!r} is not this platform")
        if claims.expires_at <= now:
            raise unauthorized("the bearer token has expired")
        return claims
