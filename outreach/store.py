"""On-disk state so a run is resumable and we never double-email anyone.

Two things live under ./runs/:

* ./runs/<run_id>/state.json   — the full RunState, rewritten after each stage.
  If the program crashes (or you Ctrl-C at the safety checkpoint), you can
  `--resume <run_id>` and pick up without re-spending API credits.

* ./runs/suppression.json      — a global set of addresses that have already
  been emailed (across all runs). Stage 4 always honours this, so re-running
  the same seed never hits the same person twice. Protecting prospects and the
  domain's sending reputation is a product decision, not an afterthought.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import RunState

RUNS_DIR = Path("runs")
SUPPRESSION_PATH = RUNS_DIR / "suppression.json"


def _run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def state_path(run_id: str) -> Path:
    return _run_dir(run_id) / "state.json"


def save_state(state: RunState) -> None:
    state.touch()
    d = _run_dir(state.run_id)
    d.mkdir(parents=True, exist_ok=True)
    state_path(state.run_id).write_text(state.model_dump_json(indent=2))


def load_state(run_id: str) -> RunState:
    raw = state_path(run_id).read_text()
    return RunState.model_validate_json(raw)


def run_exists(run_id: str) -> bool:
    return state_path(run_id).exists()


# --- suppression list -------------------------------------------------------

def load_suppression() -> set[str]:
    if not SUPPRESSION_PATH.exists():
        return set()
    try:
        return {e.lower() for e in json.loads(SUPPRESSION_PATH.read_text())}
    except (json.JSONDecodeError, OSError):
        return set()


def add_to_suppression(emails: list[str]) -> None:
    current = load_suppression()
    current.update(e.lower() for e in emails)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    SUPPRESSION_PATH.write_text(json.dumps(sorted(current), indent=2))
