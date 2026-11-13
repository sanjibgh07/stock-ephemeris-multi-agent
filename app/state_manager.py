"""app/state_manager.py - RESUME system ka dil.

Simple English:
  * Computer band ho jaaye to kuch nahi bigadta. Jab dobara chalaoge,
    ye file `state/project_state.json` padhkar batayegi ki kahan tak kaam
    pura hua tha - aur wahin se shuru karegaa.
  * Har step pura hone par `state/todo.md` ka `[ ]` -> `[x]` ho jaata hai.
"""
from __future__ import annotations

import json
from pathlib import Path

from app import config

PHASE_ORDER = [
    "PHASE_0_SETUP",
    "PHASE_1_STOCK_DATA_BOOK",
    "PHASE_2_EPHEMERIS_BOOK",
    "PHASE_3_MULTI_AGENT_SYSTEM",
    "PHASE_4_FRONTEND_AUTOUPDATE",
    "PHASE_5_VERIFICATION_TESTING",
    "COMPLETE",
]

DEFAULT_STATE: dict = {
    "project_name": "Indian Stock + Ephemeris Multi-Agent Research System",
    "project_version": "1.0.0",
    "current_phase": "PHASE_0_SETUP",
    "disk_path": str(config.PROJECT_ROOT).replace("\\", "/"),
    "no_c_drive": config.NO_C_DRIVE,
    "created_ist": None,
    "last_run_date": None,
    "last_data_date_stock": None,
    "last_data_date_ephemeris": None,
    "last_result_mode": None,
    "completed_steps": [],
    "pending_steps": list(PHASE_ORDER),
    "missing_dates": [],
    "mismatches": [],
    "source_failures": [],
    "data_coverage": {},
    "row_counts": {},
    "notes": [],
}


def load_state(path: Path | None = None) -> dict:
    """State file padho. Na mile to default bana do."""
    p = Path(path or config.PROJECT_STATE_PATH)
    config.assert_not_c_drive(p)
    if not p.exists():
        state = dict(DEFAULT_STATE)
        state["created_ist"] = config.now_ist().isoformat(timespec="seconds")
        save_state(state, p)
        return state
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        state = dict(DEFAULT_STATE)
    # Naye fields purani file me na ho to default bhar do.
    for key, value in DEFAULT_STATE.items():
        state.setdefault(key, value)
    return state


def save_state(state: dict, path: Path | None = None) -> Path:
    p = Path(path or config.PROJECT_STATE_PATH)
    config.assert_not_c_drive(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def update_state(**fields) -> dict:
    """State me kuch fields update karo aur save karo."""
    state = load_state()
    for key, value in fields.items():
        state[key] = value
    save_state(state)
    return state


def _tick_todo(step_id: str, path: Path | None = None) -> bool:
    """todo.md me '- [ ] P1.2' ko '- [x] P1.2' bana do."""
    p = Path(path or config.TODO_PATH)
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8")
    changed = False
    out_lines = []
    for line in text.splitlines():
        if f"[ ] {step_id}" in line:
            out_lines.append(line.replace(f"[ ] {step_id}", f"[x] {step_id}"))
            changed = True
        else:
            out_lines.append(line)
    if changed:
        p.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return changed


def mark_step_done(step_id: str, phase: str | None = None, note: str | None = None) -> dict:
    """Ek step ko complete mark karo (state + todo.md dono update)."""
    state = load_state()
    if step_id not in state["completed_steps"]:
        state["completed_steps"].append(step_id)
    state["pending_steps"] = [s for s in state["pending_steps"] if s != step_id]
    if phase:
        state["current_phase"] = phase
    if note:
        state.setdefault("notes", []).append(
            f"{config.now_ist().isoformat(timespec='seconds')}: {note}")
    state["last_run_date"] = config.today_ist().isoformat()
    save_state(state)
    _tick_todo(step_id)
    return state


def mark_phase_done(phase: str, note: str | None = None) -> dict:
    """Poora phase complete mark karo."""
    state = load_state()
    if phase not in state["completed_steps"]:
        state["completed_steps"].append(phase)
    state["pending_steps"] = [s for s in state["pending_steps"] if s != phase]
    try:
        idx = PHASE_ORDER.index(phase)
        state["current_phase"] = PHASE_ORDER[min(idx + 1, len(PHASE_ORDER) - 1)]
    except ValueError:
        pass
    if note:
        state.setdefault("notes", []).append(
            f"{config.now_ist().isoformat(timespec='seconds')}: {note}")
    state["last_run_date"] = config.today_ist().isoformat()
    save_state(state)
    return state


def resume_message() -> str:
    """Frontend/logs ke liye 'Resuming from Phase X' message."""
    state = load_state()
    phase = state.get("current_phase", "PHASE_0_SETUP")
    done = len(state.get("completed_steps", []))
    pending = state.get("pending_steps", [])
    if phase == "COMPLETE" or not pending:
        return (f"All phases complete ({done} steps done). "
                f"Re-running daily update only.")
    return (f"Resuming from Phase {phase} - {done} steps already done, "
            f"{len(pending)} phases pending. No restart from zero.")


def record_source_failure(source: str, endpoint: str, status: str, message: str) -> dict:
    """Source failure ko state ke source_failures[] me likho (data invent nahi)."""
    state = load_state()
    entry = {
        "ist": config.now_ist().isoformat(timespec="seconds"),
        "source": source,
        "endpoint": endpoint,
        "status": status,
        "message": (message or "")[:300],
    }
    failures = state.get("source_failures", [])
    if not any(f.get("source") == source and f.get("endpoint") == endpoint for f in failures):
        failures.append(entry)
        state["source_failures"] = failures
        save_state(state)
    return state
