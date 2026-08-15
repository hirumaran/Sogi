from pathlib import Path

from sogi.core.phases import EngineeringPhase
from sogi.state.engineering_state import EngineeringState
from sogi.state.store import JsonStateStore


def test_json_state_store_round_trip(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path)
    state = EngineeringState(task_id="auth-fix", objective="Fix auth")
    state.constraints.append("Preserve OAuth")
    state.transition_to(EngineeringPhase.INVESTIGATE)

    path = store.save(state)
    restored = store.load("auth-fix")

    assert path.name == "auth-fix.json"
    assert restored.to_dict() == state.to_dict()
