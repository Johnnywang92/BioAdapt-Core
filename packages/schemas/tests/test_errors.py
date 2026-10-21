import pytest
from pydantic import ValidationError

from bioadapt_schemas.enums import RunState
from bioadapt_schemas.errors import ERROR_CLASS_TO_RUN_STATE, ApiError, ErrorClass, to_run_state


def make_error(**overrides):
    fields = {
        "code": "preflight_failed",
        "error_class": ErrorClass.NEEDS_INPUT,
        "message": "obs table has no donor_id column",
        "field": "specimen.donor_id",
    }
    fields.update(overrides)
    return ApiError(**fields)


class TestErrorModel:
    def test_errors_carry_a_field_and_trace(self):
        error = make_error(trace_id="0af0" * 8, run_state=RunState.NEEDS_INPUT)
        assert error.field == "specimen.donor_id"
        assert error.run_state is RunState.NEEDS_INPUT

    def test_unknown_codes_are_rejected(self):
        with pytest.raises(ValidationError):
            make_error(error_class="undefined_class")

    def test_retry_hints_are_non_negative(self):
        assert make_error(retryable=True, retry_after=30).retry_after == 30
        with pytest.raises(ValidationError):
            make_error(retry_after=-1)


class TestStateMapping:
    def test_every_failure_class_maps_to_a_declared_run_state(self):
        for state in ERROR_CLASS_TO_RUN_STATE.values():
            assert state in set(RunState)
        assert to_run_state(ErrorClass.GATE_BLOCKED) is RunState.BLOCKED
        assert to_run_state(ErrorClass.POLICY_BLOCKED) is RunState.POLICY_BLOCKED

    def test_client_errors_have_no_run_state_side_effect(self):
        assert to_run_state(ErrorClass.VALIDATION) is None
        assert to_run_state(ErrorClass.NOT_FOUND) is None
        assert to_run_state(ErrorClass.CONFLICT) is None

    def test_validation_and_gate_blocked_are_distinct(self):
        assert ErrorClass.VALIDATION != ErrorClass.GATE_BLOCKED
