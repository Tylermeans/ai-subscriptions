#!/usr/bin/env python3
"""Bounded launchd sampler: no model calls, browser launch, or login prompts."""
import fcntl
import os
from ai_sub import Store, chosen_profiles, refresh

os.umask(0o077)
store = Store()
with open(store.home / "monitor.lock", "w") as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)
    log = store.home / "monitor.log"
    if log.exists() and log.stat().st_size > 1024 * 1024:
        log.write_text("")
    for profile in chosen_profiles(store, "codex"):
        refresh(store, profile)
