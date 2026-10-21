"""Evidence-service failures (plan V2.0 sections 7.3, 9.7, 11.5-11.6).

Each error names the run state it belongs to, so the API's error body and the state machine read the
same vocabulary instead of translating between two of them.
"""

from __future__ import annotations

from typing import Optional

from bioadapt_schemas.enums import RunState
from bioadapt_schemas.errors import ErrorClass


class EvidenceError(Exception):
    error_class: ErrorClass = ErrorClass.VALIDATION
    code: str = "evidence_error"
    status_code: int = 400
    run_state: Optional[RunState] = None

    def __init__(
        self,
        message: str,
        *,
        field: Optional[str] = None,
        retryable: bool = False,
        run_state: Optional[RunState] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.retryable = retryable
        self.run_state = run_state if run_state is not None else type(self).run_state


class ContractDrift(EvidenceError):
    """The stored bytes and the index row - or two stored products - disagree about what they are."""

    error_class = ErrorClass.FAILED
    code = "contract_drift"
    status_code = 500
    run_state = RunState.FAILED


class MissingProduct(EvidenceError):
    """A measurement the card needs was never produced for this run."""

    error_class = ErrorClass.NEEDS_INPUT
    code = "missing_product"
    status_code = 422
    run_state = RunState.NEEDS_INPUT


class TriggerOnHeldOutCells(EvidenceError):
    """Section 7.4: a trigger signal discovered inside the confirmation partition is circular inference."""

    error_class = ErrorClass.NEEDS_INPUT
    code = "trigger_on_held_out_cells"
    status_code = 422
    run_state = RunState.NEEDS_INPUT


class EgressBlocked(EvidenceError):
    """Section 11.5: the field-level allowlist refused every field, so nothing may be sent."""

    error_class = ErrorClass.POLICY_BLOCKED
    code = "egress_blocked"
    status_code = 403
    run_state = RunState.POLICY_BLOCKED


class ModelPathBlocked(EvidenceError):
    """Section 4.5: routing refused this model for this task, so the call must not happen."""

    error_class = ErrorClass.POLICY_BLOCKED
    code = "model_path_blocked"
    status_code = 403
    run_state = RunState.POLICY_BLOCKED


class ContextCompressionViolation(EvidenceError):
    """Section 4.5: a rare signal was summarised away, so the context must not be built at all."""

    error_class = ErrorClass.POLICY_BLOCKED
    code = "context_compression_violation"
    status_code = 403
    run_state = RunState.POLICY_BLOCKED


class PromptUnsafe(EvidenceError):
    """Section 11.6: the assembled request carries secret material or an unresolved template variable."""

    error_class = ErrorClass.POLICY_BLOCKED
    code = "prompt_unsafe"
    status_code = 403
    run_state = RunState.POLICY_BLOCKED


class ModelSideParameter(EvidenceError):
    """Section 11.6 control 2: a tool parameter would have taken its value from retrieval or from a model.

    This is the shape the plan refuses outright - a model that can compose the arguments a production node
    runs with - so it is a recorded block rather than something to sanitise and continue.
    """

    error_class = ErrorClass.POLICY_BLOCKED
    code = "model_side_parameter"
    status_code = 403
    run_state = RunState.POLICY_BLOCKED


class ModelOutputRejected(EvidenceError):
    """Section 11.6 control 3: the output failed schema or enum validation, or touched a privileged field.

    The plan forbids retrying until it passes, so this is a recorded refusal, not a retryable failure.
    """

    error_class = ErrorClass.VALIDATION
    code = "model_output_rejected"
    status_code = 422


class CardUnderReview(EvidenceError):
    """Section 9.1: the drafting path holds no write permission over a card somebody has acted on.

    A rebuild that would replace a labelled, reviewed or non-unknown card is refused rather than merged,
    because the alternative is a machine edit that silently undoes a person's decision.
    """

    error_class = ErrorClass.POLICY_BLOCKED
    code = "card_under_review"
    status_code = 403
    run_state = RunState.POLICY_BLOCKED
