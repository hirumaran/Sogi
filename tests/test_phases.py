import pytest

from sogi.core.phases import EngineeringPhase
from sogi.state.engineering_state import EngineeringState


def test_state_machine_allows_only_next_phase() -> None:
    state = EngineeringState(task_id="task-1", objective="Ship the fix")
    state.transition_to(EngineeringPhase.INVESTIGATE)
    assert state.phase is EngineeringPhase.INVESTIGATE

    with pytest.raises(ValueError, match="Invalid engineering phase transition"):
        state.transition_to(EngineeringPhase.DONE)
