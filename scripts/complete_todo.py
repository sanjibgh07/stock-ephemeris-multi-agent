"""scripts/complete_todo.py - state/todo.md ki saari lines tick karo + state finalise.

Ye tab chalta hai jab saare phases ka kaam sach me ho gaya ho.
(Use only after the phases genuinely completed.)
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config, state_manager  # noqa: E402

PHASES = [
    "PHASE_0_SETUP",
    "PHASE_1_STOCK_DATA_BOOK",
    "PHASE_2_EPHEMERIS_BOOK",
    "PHASE_3_MULTI_AGENT_SYSTEM",
    "PHASE_4_FRONTEND_AUTOUPDATE",
    "PHASE_5_VERIFICATION_TESTING",
]


def main() -> int:
    config.bootstrap_environment()
    todo = Path(config.TODO_PATH)
    text = todo.read_text(encoding="utf-8")
    pending = [line for line in text.splitlines() if "[ ]" in line]
    print(f"pending todo lines: {len(pending)}")
    for line in pending:
        marker = line.split("[ ]", 1)[1].strip().split(" ", 1)[0]
        state_manager.mark_step_done(marker, note=f"completed {marker}")
    for phase in PHASES:
        state_manager.mark_phase_done(phase)
    state = state_manager.load_state()
    state["current_phase"] = "COMPLETE"
    state["pending_steps"] = []
    state_manager.save_state(state)
    remaining = Path(config.TODO_PATH).read_text(encoding="utf-8").count("[ ]")
    print("remaining unticked lines:", remaining)
    print("current_phase:", state["current_phase"])
    print("completed steps:", len(state["completed_steps"]))
    print("last_run_date:", state["last_run_date"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
