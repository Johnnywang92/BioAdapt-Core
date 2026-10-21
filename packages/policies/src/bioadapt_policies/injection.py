"""Prompt-injection hard controls (plan V2.0 section 11.6) and the secret-scan hooks (§11.2).

Each control is a function the orchestrator must call before a model request leaves the process:
checks return recorded violations so a blocked call still leaves an audit trail, and the two
guards that would silently corrupt state (parameter binding, unsafe prompt) raise instead.
"""

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from bioadapt_schemas.entities import Contract, ImmutableContract

#: Control 4: retrieved fragments are truncated rather than summarised on the model's say-so.
MAX_FRAGMENT_CHARS = 4000


class ContentOrigin(StrEnum):
    LITERATURE = "literature"
    WEB_PAGE = "web_page"
    UPLOADED_FILE = "uploaded_file"
    METADATA = "metadata"


#: Control 1: everything below is untrusted content and may never be merged into the instruction field.
UNTRUSTED_ORIGINS = frozenset(ContentOrigin)


class ParameterSource(StrEnum):
    PROJECT_PROFILE = "project_profile"
    REGISTRY_ENUM = "registry_enum"
    APPROVED_PLAN = "approved_plan"
    RETRIEVED_TEXT = "retrieved_text"
    MODEL_OUTPUT = "model_output"


#: Control 2: no tool parameter value may originate from model- or retrieval-side content.
MODEL_SIDE_SOURCES = frozenset({ParameterSource.RETRIEVED_TEXT, ParameterSource.MODEL_OUTPUT})

#: Control 6: write paths that only a human action may touch, matched as lower-case prefixes so
#: variants such as ``approver_id`` or ``signed_off`` cannot slip past an exact-name list.
PRIVILEGED_PATH_PREFIXES = (
    "allow",
    "approv",
    "grant",
    "permission",
    "polic",
    "promote",
    "reviewer",
    "role",
    "sign_off",
    "signed",
    "unregistered",
)

_URL = re.compile(r"""(?:https?://|ftp://|www\.)\S+""", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_INSTRUCTION = re.compile(
    r"""(?xi)
    (?:ignore|disregard|forget)\s+(?:all\s+|the\s+)?(?:previous|above|prior|earlier)\s+(?:instruction|prompt|rule|message)
    |you\s+are\s+now
    |new\s+instructions?\s*:
    |system\s*(?:prompt|instruction)
    |^\s*(?:system|assistant)\s*:
    |忽略(?:以上|之前|先前)
    |新的?指令\s*[:：]
    |请以?管理员
    """
)
_PEM_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})\b")
_AUTH_HEADER = re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+\S+")
_TEMPLATE = re.compile(r"{{\s*[A-Za-z0-9_.]+\s*}}|\$\{[A-Za-z0-9_.]+\}")


class UntrustedBlock(ImmutableContract):
    """Control 1: untrusted text travels as a structured field, never as a concatenated string."""

    origin: ContentOrigin
    object_id: str = Field(min_length=1)
    text: str

    def as_field(self) -> dict[str, str]:
        return {"origin": self.origin, "object_id": self.object_id, "text": self.text}


class SanitizedFragment(ImmutableContract):
    text: str
    removed: list[str] = Field(default_factory=list)


def sanitize_fragment(text: str, *, max_chars: int = MAX_FRAGMENT_CHARS) -> SanitizedFragment:
    """Control 4: strip links, instruction-like sentences and over-long segments."""
    removed: list[str] = []
    cleaned = _URL.sub("", text)
    if cleaned != text:
        removed.append("links stripped")
    cleaned = _EMAIL.sub("", cleaned)
    kept: list[str] = []
    for line in cleaned.splitlines():
        if _INSTRUCTION.search(line):
            removed.append("instruction-like line dropped")
            continue
        kept.append(line)
    body = "\n".join(kept)
    if len(body) > max_chars:
        body = body[:max_chars]
        removed.append(f"truncated to {max_chars} characters")
    return SanitizedFragment(text=body, removed=removed)


def compose_prompt(
    *,
    system_instructions: str,
    trusted_fields: dict[str, Any],
    untrusted: list[UntrustedBlock],
) -> dict[str, Any]:
    """Control 1: assemble the request so instructions and untrusted content occupy separate fields."""
    return {
        "system": system_instructions,
        "fields": trusted_fields,
        "untrusted": [block.as_field() for block in untrusted],
    }


class ToolParameter(ImmutableContract):
    name: str = Field(min_length=1)
    value: Any
    source: ParameterSource


def bind_tool_parameters(params: list[ToolParameter]) -> dict[str, Any]:
    """Control 2: raise if any parameter value was produced by retrieval or by the model."""
    bad = sorted(p.name for p in params if p.source in MODEL_SIDE_SOURCES)
    if bad:
        raise ValueError(f"tool parameters must not come from model-side content: {bad}")
    return {p.name: p.value for p in params}


def privileged_write_paths(payload: dict[str, Any]) -> list[str]:
    """Control 6: paths in a model-produced patch that only a human action may touch."""
    found: list[str] = []

    def walk(node: Any, prefix: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                path = prefix + (str(key),)
                if str(key).lower().startswith(PRIVILEGED_PATH_PREFIXES):
                    found.append(".".join(path))
                walk(value, path)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, prefix + (f"[{index}]",))

    walk(payload, ())
    return sorted(found)


class ModelOutputCheck(Contract):
    """Control 3: strict schema + enum validation, dropped fields recorded, no retry-to-pass."""

    usable: bool
    values: dict[str, Any] = Field(default_factory=dict)
    dropped: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)


def check_model_output(raw: dict[str, Any], schema: type[BaseModel]) -> ModelOutputCheck:
    allowed = set(schema.model_fields)
    dropped = sorted(str(key) for key in raw if str(key) not in allowed)
    kept = {key: value for key, value in raw.items() if str(key) in allowed}
    try:
        validated = schema.model_validate(kept)
    except ValidationError as exc:
        return ModelOutputCheck(
            usable=False,
            dropped=dropped,
            violations=[
                f"{'.'.join(str(part) for part in err['loc']) or '<root>'}: {err['msg']}"
                for err in exc.errors()
            ],
        )
    return ModelOutputCheck(usable=True, values=validated.model_dump(), dropped=dropped)


def secret_findings(text: str) -> list[str]:
    """Section 11.2: outbound content scan - keys never reach prompts, parameters or logs."""
    findings: list[str] = []
    if _PEM_KEY.search(text):
        findings.append("private key material")
    if _TOKEN.search(text):
        findings.append("api token")
    if _AUTH_HEADER.search(text):
        findings.append("authorization header")
    return findings


def assert_prompt_safe(rendered: dict[str, Any]) -> None:
    """Refuse to send a request that carries a secret or an unresolved template variable."""
    text = str(rendered)
    findings = secret_findings(text)
    if findings:
        raise ValueError(f"prompt contains secret material: {sorted(set(findings))}")
    unresolved = sorted({match.group(0) for match in _TEMPLATE.finditer(text)})
    if unresolved:
        raise ValueError(f"prompt contains unresolved template variables: {unresolved}")
