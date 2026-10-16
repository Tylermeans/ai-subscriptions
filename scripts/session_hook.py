#!/usr/bin/env python3
"""Stable command-hook entrypoint; run-specific state is passed through the environment."""
import json
import os
from pathlib import Path
import sys
import time

import ai_sub as app
from handoff import Run, hook_event


def main():
    directory = os.environ.get("AI_SUB_RUN_DIR")
    attempt = os.environ.get("AI_SUB_RUN_ATTEMPT")
    if not directory or not attempt:
        return
    root = Path(os.environ["AI_SUB_HOME"]).resolve() / "runs"
    path = Path(directory).resolve()
    if path.parent != root or not (path / "run.json").is_file():
        return
    run = Run(path)
    output, hold = hook_event(run, json.loads(sys.stdin.read(2_000_000)), attempt)
    if hold:
        # Keep the native CLI inside its synchronous Stop hook while the supervisor
        # consumes the lease. If it disappears, release without an automatic restart.
        while True:
            state = run.read()
            if (not state.get("active") or state.get("attempt") != attempt
                    or state.get("ready_until", 0) <= time.time()):
                break
            try:
                os.kill(state["supervisor_pid"], 0)
            except OSError:
                break
            time.sleep(0.1)
        # An expired lease must not permit a delayed supervisor to stop a new turn.
        with run.edit() as state:
            if state.get("attempt") == attempt:
                state["ready_until"] = 0
    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        # A broken hook provides no safe-boundary lease. Never leak raw hook inputs.
        print(json.dumps({"systemMessage": "ai-sub lifecycle check failed; no automatic handoff."}))
