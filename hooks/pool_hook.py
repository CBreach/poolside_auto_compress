#!/usr/bin/env python3
"""pool hook (SessionStart / Stop / PreCompact): publishes session state for the wrapper.

pool_autocompress.py can't see inside pool, so this hook (installed into
settings.yaml by install.sh) reads each event's JSON envelope from stdin and
records what the wrapper needs into a small state file it polls:

- SessionStart: which trajectory file belongs to this session.
- Stop:         the agent finished its turn, so pool is idle at the prompt
                (the only safe moment to inject /compact).
- PreCompact:   a compaction is happening (auto-triggered or typed by hand),
                plus the trajectory's size at that moment, so usage can be
                measured from there on instead of from the start of the file.

The state file path comes from $POOL_AUTOCOMPRESS_STATE, which the wrapper
sets uniquely per wrapper process - so two pool sessions in two terminals
never read each other's state. Without the wrapper (plain `pool`) it falls
back to a shared default path, which is only useful for debugging.

Always exits 0 with no stdout - this hook only observes, it never blocks or
rewrites anything, so a bug here can't break a real pool session.
"""
import json
import os
import sys
import time
from pathlib import Path

STATE_ENV = "POOL_AUTOCOMPRESS_STATE"
DEFAULT_STATE_PATH = Path.home() / ".cache" / "poolside_auto_compress" / "current_session.json"


def _handle(payload: dict):
    state_path = Path(os.environ.get(STATE_ENV) or DEFAULT_STATE_PATH)
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}

    now = time.time()
    event = payload.get("hook_event_name")
    trajectory_path = payload.get("trajectory_path")
    if trajectory_path:
        if trajectory_path != state.get("trajectory_path"):
            state["compact_offset"] = 0
        state["trajectory_path"] = trajectory_path
        state["session_id"] = payload.get("session_id")

    if event == "SessionStart":
        state["started_at"] = now
        state["source"] = payload.get("source")
    elif event == "Stop":
        state["last_stop_at"] = now
    elif event == "PreCompact":
        state["last_compact_at"] = now
        if trajectory_path:
            try:
                state["compact_offset"] = Path(trajectory_path).stat().st_size
            except OSError:
                pass

    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, state_path)


def main():
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            _handle(payload)
    except Exception:  # never let a hook bug surface inside pool
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
