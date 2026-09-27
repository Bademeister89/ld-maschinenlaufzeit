import pyLSV2
import pytest

from app.state import MachineState, classify, run_result


@pytest.mark.parametrize(
    ("pgm_state", "expected"),
    [
        ("STARTED", MachineState.RUNNING),
        ("STOPPED", MachineState.STOPPED),
        ("INTERRUPTED", MachineState.STOPPED),
        ("ERROR", MachineState.ERROR),
        ("FINISHED", MachineState.READY),
        ("CANCELLED", MachineState.READY),
        ("ERROR_CLEARED", MachineState.READY),
        ("IDLE", MachineState.READY),
        ("UNDEFINED", MachineState.UNKNOWN),
        (None, MachineState.UNKNOWN),
    ],
)
def test_classify(pgm_state, expected):
    assert classify(pgm_state) is expected


def test_every_pylsv2_program_state_is_mapped():
    # Schutz gegen neue Zustände in künftigen pyLSV2-Versionen
    for state in pyLSV2.PgmState:
        assert classify(state.name) is not None
        if state is not pyLSV2.PgmState.UNDEFINED:
            assert classify(state.name) is not MachineState.UNKNOWN, state


@pytest.mark.parametrize(
    ("pgm_state", "had_error", "expected"),
    [
        ("FINISHED", False, "finished"),
        ("FINISHED", True, "finished"),
        ("CANCELLED", False, "cancelled"),
        ("CANCELLED", True, "error"),
        ("ERROR_CLEARED", False, "error"),
        ("IDLE", False, "aborted"),
        ("STARTED", False, "aborted"),
    ],
)
def test_run_result(pgm_state, had_error, expected):
    assert run_result(pgm_state, had_error) == expected
