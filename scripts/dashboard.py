"""Read normalized usage and render a local chart; no credential or transcript access."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import webbrowser

import ai_sub as app
from handoff import policy


def snapshot(store):
    config = store.config()
    profiles = []
    for key, profile in config["profiles"].items():
        sample = store.latest(profile)
        fresh = app.freshness(sample)
        windows = sample["windows"] if sample else []
        short = any(w.get("minutes") and w["minutes"] < 10080 for w in windows)
        weekly = any(w.get("minutes") == 10080 for w in windows)
        if sample and sample.get("blocked"):
            capacity = "Provider reports a usage block"
        elif fresh != "fresh":
            capacity = "Fresh usage needed for automatic selection"
        elif not short or not weekly:
            capacity = "Both short-window and weekly data are required for automatic selection"
        elif not profile.get("verified_email"):
            capacity = "Verify this account before automatic selection"
        elif any(w["used"] >= policy(store, profile["provider"])["threshold"] for w in windows):
            capacity = "At the automatic handoff threshold"
        else:
            capacity = "Usage is below the handoff threshold"
        profiles.append({"profile": key, "provider": profile["provider"], "name": profile["name"],
            "email": profile.get("email") or profile.get("verified_email"),
            "default": config["defaults"].get(profile["provider"]) == profile["name"],
            "freshness": fresh, "capacity": capacity, "automatic": policy(store, profile["provider"]),
            "sample": None if sample is None else {
                "observed": sample["observed"], "windows": windows, "blocked": bool(sample.get("blocked")),
                "source": sample["source"], "error": app.clean_text(sample["error"]) if sample.get("error") else None}})
    return {"version": 1, "generated_at": time.time(), "profiles": profiles}


def render(data, live=False):
    template = (app.ROOT / "vscode" / "media" / "dashboard.html").read_text()
    encoded = json.dumps(data).replace("<", "\\u003c").replace("&", "\\u0026")
    return (template.replace("__AI_SUB_NONCE__", secrets.token_hex(16))
            .replace("__AI_SUB_MODE__", "live-file" if live else "snapshot")
            .replace("__AI_SUB_DATA__", encoded))


def save_chart(path, html):
    fd, temporary = tempfile.mkstemp(prefix=".chart-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(html)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def launch(store, once=False, open_browser=True, refresh=False):
    if refresh:
        for profile in app.chosen_profiles(store, "codex"):
            app.refresh(store, profile)
    path = app.private_dir(store.home.resolve() / "dashboard") / "index.html"
    save_chart(path, render(snapshot(store), live=not once))
    print(f"Usage chart: {path}", flush=True)
    if open_browser:
        chrome = Path("/Applications/Google Chrome.app")
        if chrome.exists():
            result = subprocess.run(["/usr/bin/open", "-a", "Google Chrome", str(path)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode:
                print("Browser could not open here; open the chart path above on your Mac.")
        else:
            webbrowser.open(path.as_uri())
    if once:
        return
    print("Updating saved observations every 15 seconds. Keep this terminal open; Ctrl-C stops updates.", flush=True)
    try:
        while True:
            time.sleep(15)
            save_chart(path, render(snapshot(store), live=True))
    finally:
        save_chart(path, render(snapshot(store), live=False))
