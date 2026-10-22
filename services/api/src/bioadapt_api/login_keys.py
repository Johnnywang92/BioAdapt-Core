"""Login keys (plan V2.0 sections 9.7, 11.2, 11.6).

The deployment answer to a printed bearer token that has to be renewed some day: a person keeps an
Ed25519 key file the way they keep an SSH key, and each login signs a short-lived assertion that the
platform trades for an access token. Two properties come from that shape.

- The long-lived secret never crosses the wire. A captured access token is bounded by its own TTL, and
  a captured assertion is bounded by ``MAX_ASSERTION_TTL_SECONDS``.
- Nothing about a session is decided by the key that opened it. The registered row states the roles, the
  projects, whether the subject is a person or an engine, and the second factor the subject was attested
  with, so revocation is a write here and closes the door on the next login instead of waiting for a
  token someone already holds to expire.

``cryptography`` is what this module exists for: the standard library signs HMACs, not asymmetric keys,
and an HMAC secret held identically by the issuer and the verifier is one secret the whole deployment
has to rotate as a unit.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import Field, ValidationError

from bioadapt_policies.access import GLOBAL_SCOPE
from bioadapt_schemas.entities import ImmutableContract

from .errors import unauthorized
from .identity import IdTokenClaims, decode_segment, encode_segment

#: One algorithm, stated rather than negotiated: an assertion whose header says anything else is refused
#: before a byte of it is parsed, the same way §11.6 refuses an alg confusion on a bearer token.
ALGORITHM = "ed25519"
JWS_HEADER = "EdDSA"
#: A login assertion buys one exchange, not a session. It is signed by something that never leaves the
#: person's machine, so the replay window is still bounded by the platform instead of by their choice.
MAX_ASSERTION_TTL_SECONDS = 120


def _digest(public_key: Ed25519PublicKey) -> bytes:
    der = public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).digest()


def fingerprint_of(public_key: Ed25519PublicKey) -> str:
    """The OpenSSH-shaped short fingerprint, so two people can compare a key by reading it aloud."""
    return "SHA256:" + base64.b64encode(_digest(public_key)).decode().rstrip("=")


def kid_of(public_key: Ed25519PublicKey) -> str:
    """The directory key an assertion carries in its header - derived from the key, so it is never typed."""
    return "key_" + _digest(public_key).hex()[:16]


@dataclass(frozen=True)
class LoginKey:
    kid: str
    fingerprint: str
    public_pem: str
    private_pem: str


def key_from_private(private_pem: str) -> LoginKey:
    """Every half of a key file, read back from the private one - so an id is never typed or stored."""
    private = serialization.load_pem_private_key(private_pem.encode(), password=None)
    public = private.public_key()
    return LoginKey(
        kid=kid_of(public),
        fingerprint=fingerprint_of(public),
        public_pem=public.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode(),
        private_pem=private_pem,
    )


def generate_login_key() -> LoginKey:
    """One recipe for a new key file, shared by the login helper and the deployment seed."""
    return key_from_private(
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        .decode()
    )


#: The text file that travels beside a key file. It holds the half an admin registers plus who it belongs
#: to, so a person signs in by naming their key instead of typing a subject id they were told once.
SIDECAR_SUFFIX = ".pub"


def sidecar_path(key_path: Path) -> Path:
    return key_path.with_name(key_path.name + SIDECAR_SUFFIX)


def sidecar_text(key: LoginKey, *, subject: str, label: str) -> str:
    return (
        f"{key.public_pem}"
        f"# kid={key.kid} fingerprint={key.fingerprint} subject={subject} label={label}\n"
    )


def subject_of_key_file(key_path: Path) -> str:
    """The subject a key was written for, read back from its sidecar. Empty means the file says nothing."""
    side = sidecar_path(key_path)
    if not side.exists():
        return ""
    for line in side.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            fields = dict(pair.split("=", 1) for pair in line[2:].split() if "=" in pair)
            return fields.get("subject", "")
    return ""


def write_key_file(path: Path, key: LoginKey, *, subject: str, label: str) -> None:
    """A key file is a credential: owner-only before it is ever readable by anyone else on the machine."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.private_pem, encoding="utf-8")
    path.chmod(0o600)
    sidecar_path(path).write_text(sidecar_text(key, subject=subject, label=label), encoding="utf-8")


def load_or_create_key_file(path: Path, *, subject: str, label: str) -> LoginKey:
    """Keep one key file per subject, the way ``~/.ssh`` keeps one per machine.

    A restart of the platform re-registers the same public half, so a file a person already saved stays
    worth something; minting a fresh key each time would quietly retire their copy.
    """
    if path.exists():
        key = key_from_private(path.read_text(encoding="utf-8"))
        side = sidecar_path(path)
        if not side.exists():
            side.write_text(sidecar_text(key, subject=subject, label=label), encoding="utf-8")
        return key
    key = generate_login_key()
    write_key_file(path, key, subject=subject, label=label)
    return key


def load_public_key(pem: str) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(pem.encode())
    except Exception as exc:  # noqa: BLE001 - a key a person pasted is untrusted input
        raise unauthorized("this is not a usable Ed25519 public key in PEM form") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise unauthorized(f"only {ALGORITHM} login keys are registered here")
    return key


def sign_assertion(
    private_pem: str, *, kid: str, subject_id: str, issuer: str, audience: str, ttl_seconds: int
) -> str:
    """Sign the one thing a login key is allowed to state: who, where, and for how long.

    Roles are absent by construction. A key file that could assert its own authority would make the
    directory a suggestion, and §11.6 is about the permission state being reachable only by a human act.
    """
    if ttl_seconds <= 0 or ttl_seconds > MAX_ASSERTION_TTL_SECONDS:
        raise unauthorized(f"a login assertion is signed for at most {MAX_ASSERTION_TTL_SECONDS} seconds")
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    started = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": subject_id,
        "iss": issuer,
        "aud": audience,
        "iat": started,
        "exp": started + ttl_seconds,
    }
    signing_input = (
        f"{encode_segment(json.dumps({'alg': JWS_HEADER, 'typ': 'JWT', 'kid': kid}, separators=(',', ':')).encode())}"
        f".{encode_segment(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    return f"{signing_input}.{encode_segment(key.sign(signing_input.encode()))}"


class CredentialRows(Protocol):
    """The identity directory, read the way the rest of the platform reads a project's rows."""

    def acquire(self, project_id: str) -> Any: ...


def assertion_kid(token: str) -> str:
    """The key a presentation names, read for the audit row after that presentation has verified.

    Nothing is decided from it - the verifier looked the directory row up under the same value - but a
    login record has to say which of a person's keys opened the session.
    """
    header = token.split(".")[0]
    try:
        kid = json.loads(decode_segment(header)).get("kid")
    except Exception as exc:  # noqa: BLE001 - already verified, so this is a broken record, not an attack
        raise unauthorized("the verified presentation names no key to record") from exc
    if not kid:
        raise unauthorized("the verified presentation names no key to record")
    return str(kid)


class KeyAssertion(ImmutableContract):
    """The whole of what a login key may state about itself.

    ``iat`` is here so the validity window is a property of the signed bytes: a platform whose clock
    runs behind the signer's still bounds an assertion to the window it was signed for, instead of
    reading a five-month-old frozen clock as "still valid for another five months".
    """

    sub: str = Field(min_length=1)
    iss: str = Field(min_length=1)
    aud: str = Field(min_length=1)
    iat: int
    exp: int

    @property
    def window_seconds(self) -> int:
        return self.exp - self.iat

    @property
    def expires_at(self) -> datetime:
        return datetime.fromtimestamp(self.exp, UTC)


class Ed25519Verifier:
    """Verifies a login assertion against the directory row, then answers with that row's authority.

    The signature proves the caller holds the private half of a registered key; everything the resulting
    session may do comes off the registered row. A presentation for a subject other than the one the key
    was issued to is refused even though its signature is valid - that is a stolen key, not a login.
    """

    def __init__(self, rows: CredentialRows, *, issuer: str, audience: str) -> None:
        self._rows = rows
        self.issuer = issuer
        self.audience = audience

    def verify(self, token: str, *, now: datetime) -> IdTokenClaims:
        parts = token.split(".")
        if len(parts) != 3:
            raise unauthorized("a login assertion is a compact JWS of three segments")
        header, payload, signature = parts
        try:
            stated = json.loads(decode_segment(header))
        except Exception as exc:  # noqa: BLE001 - the header is untrusted input either way
            raise unauthorized("the login assertion header is not readable") from exc
        if stated.get("alg") != JWS_HEADER:
            raise unauthorized(f"only a {JWS_HEADER} login assertion is accepted here")
        kid = stated.get("kid")
        if not kid:
            raise unauthorized("the login assertion names no key")
        row = self._rows.acquire(GLOBAL_SCOPE).active_credential(kid)
        if row is None:
            raise unauthorized(f"key {kid} is not registered here, or it has been revoked")
        public = load_public_key(row["public_key"])
        try:
            public.verify(decode_segment(signature), f"{header}.{payload}".encode())
        except InvalidSignature as exc:
            raise unauthorized(f"the presentation is not signed by key {kid}") from exc
        try:
            assertion = KeyAssertion.model_validate(json.loads(decode_segment(payload)))
        except (ValidationError, ValueError) as exc:
            raise unauthorized(
                "a login assertion states sub, iss, aud, iat and exp only; authority is read from the "
                "directory row the key is registered in"
            ) from exc
        if assertion.iss != self.issuer:
            raise unauthorized(f"assertion issuer {assertion.iss!r} is not this platform's IdP")
        if assertion.aud != self.audience:
            raise unauthorized(f"assertion audience {assertion.aud!r} is not this platform")
        if assertion.expires_at <= now:
            raise unauthorized("the login assertion has expired; sign a new one")
        if assertion.window_seconds <= 0:
            raise unauthorized("the login assertion expires before it starts")
        if assertion.window_seconds > MAX_ASSERTION_TTL_SECONDS:
            raise unauthorized(f"a login assertion is signed for at most {MAX_ASSERTION_TTL_SECONDS} seconds")
        if assertion.sub != row["subject_id"]:
            raise unauthorized(
                f"key {kid} is registered to {row['subject_id']}, not to the {assertion.sub!r} it presents"
            )
        return IdTokenClaims(
            sub=row["subject_id"],
            iss=assertion.iss,
            aud=assertion.aud,
            roles=list(row["roles"]),
            project_ids=list(row["project_ids"]),
            amr=list(row["amr"]),
            solo_mode=bool(row["solo_mode"]),
            #: §11.4 is decided by this flag rather than by the role name, so it has to come off the row
            #: too - a pipeline engine that signs in with a key file is still not a person.
            is_service_account=bool(row["is_service_account"]),
            exp=assertion.exp,
        )
