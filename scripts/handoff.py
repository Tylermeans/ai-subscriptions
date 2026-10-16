"""Cooperative account handoffs at a native CLI Stop hook, never mid-tool."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import termios
import threading
import time
import uuid

import ai_sub as app

MAX_SWITCHES = 8
LEASE_SECONDS = 20
CHECKPOINT_REQUEST = '''AI-SUB HANDOFF REQUEST: Pause this task at a safe boundary for an account change.
Finish or wait for all running commands, subagents, background jobs, and scheduled wakeups.
Do not start new implementation work. Do not commit, stash, reset, or undo changes for this handoff.
End your response with a fenced block labelled ai-sub-handoff containing a JSON object:
{"objective":"original task and current user direction","summary":"where work stopped and why",
"constraints":["user constraints, approval boundaries, important decisions"],
"completed":["completed work and relevant file paths"],"next_steps":["precise unfinished steps"],
"tests":["commands and actual results, including failures or checks not run"],
"blockers":["unresolved questions or approvals"],"background_jobs":[],
"task_complete":false,"waiting_for_user":false}
Include enough context for a fresh agent in this exact worktree. Never include passwords, tokens,
email codes, or secrets. Report active jobs in background_jobs until they have finished; do not
claim they are finished just to switch. Set task_complete or waiting_for_user accurately.
No shell command or file write is required to submit the checkpoint; the Stop hook saves the block.'''


def policy(store, provider):
    return {"enabled": False, "threshold": 90,
            **store.config().get("automatic", {}).get(provider, {})}


def configure(store, mode, provider=None, threshold=None, fallback=None, clear_fallback=False):
    if threshold is not None and (mode != "on" or not 50 <= threshold <= 95):
        raise app.UserError("Use --threshold with auto on, between 50 and 95 percent.")
    if fallback is not None or clear_fallback:
        if mode != "on" or provider != "codex" or (fallback is not None and clear_fallback):
            raise app.UserError("Use --fallback claude/NAME or --clear-fallback with auto on codex.")
        if fallback is not None:
            parts = fallback.split("/")
            if len(parts) != 2 or parts[0] != "claude" or not parts[1]:
                raise app.UserError("Codex fallback must name a registered Claude profile: claude/NAME.")
            store.profile(*parts)
    providers = [provider] if provider else app.PROVIDERS
    if mode != "status":
        with store.edit() as config:
            for name in providers:
                entry = config.setdefault("automatic", {}).setdefault(name, {"threshold": 90})
                entry["enabled"] = mode == "on"
                if threshold is not None:
                    entry["threshold"] = threshold
                if fallback is not None or clear_fallback:
                    entry["fallback"] = fallback
    for name in providers:
        entry = policy(store, name)
        print(f'{name}: automatic handoff {"on" if entry["enabled"] else "off"}; '
              f'request at {entry["threshold"]}% used')
        if entry.get("fallback"):
            print(f'  Fallback: {entry["fallback"]} after eligible {name} accounts; fresh quota required.')
    if mode == "on":
        print("Start a managed session with ai-sub run PROVIDER in the target repository.")
        print("Requires trusted lifecycle hooks, a safe checkpoint, and another account with fresh quota.")


def fingerprint(profile):
    return [app.profile_key(profile), profile.get("generation", 1), profile["home"],
            profile.get("email"), profile.get("verified_email")]


def needs_switch(sample, threshold):
    if not sample or sample.get("error") or not 0 <= time.time() - sample["observed"] <= app.FRESH_SECONDS:
        return False
    return bool(sample.get("blocked") or (app.freshness(sample) == "fresh"
                and any(w["used"] >= threshold for w in sample["windows"])))


def eligible(store, current, candidate, threshold):
    same_provider = candidate["provider"] == current["provider"]
    cross_provider = (current["provider"] == "codex" and candidate["provider"] == "claude"
                      and policy(store, "codex").get("fallback") == app.profile_key(candidate))
    if (not (same_provider or cross_provider) or app.profile_key(candidate) == app.profile_key(current)):
        return False
    email = candidate.get("verified_email")
    current_email = current.get("verified_email")
    if not email or not current_email or (same_provider and email.casefold() == current_email.casefold()):
        return False
    sample = store.latest(candidate)
    if app.freshness(sample) != "fresh" or sample.get("blocked"):
        return False
    windows = sample["windows"]
    return (any(w["minutes"] == 10080 for w in windows)
            and any(w["minutes"] and w["minutes"] < 10080 for w in windows)
            and max(w["used"] for w in windows) < threshold)


class Run:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "run.json"

    @classmethod
    def create(cls, store, provider, cwd, prompt=None):
        run = cls(app.private_dir(store.home.resolve() / "runs" / uuid.uuid4().hex))
        app.write_json(run.path, {"id": run.directory.name, "provider": provider, "cwd": str(cwd),
            "supervisor_pid": os.getpid(), "created": time.time(), "status": "starting",
            "prompts": [prompt] if prompt else [], "prompt_revision": 0, "switches": 0})
        return run

    @contextlib.contextmanager
    def edit(self):
        fd = os.open(self.directory / "run.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            state = self.read()
            previous = json.dumps(state, sort_keys=True)
            yield state
            if json.dumps(state, sort_keys=True) != previous:
                app.write_json(self.path, state)

    def read(self):
        return app.read_json(self.path, {})

    def change(self, attempt, **updates):
        with self.edit() as state:
            if state.get("attempt") == attempt and state.get("active") and state["status"] != "switching":
                state.update(updates)

    def begin(self, profile, prompt=None, hooks_ok=True, options=None, settings=None):
        with self.edit() as state:
            state.update(provider=profile["provider"], profile=app.profile_key(profile), fingerprint=fingerprint(profile),
                attempt=uuid.uuid4().hex, active=True, status="waiting for hooks", session_id=None,
                pending=None, ready_until=0, tools=[], agents=[],
                fault=None if hooks_ok else "Review all ai-sub hooks in /hooks, then restart this managed run.",
                checkpoint=None,
                stop_requests=0, skip_prompt=prompt, hooks_seen=[], permission_mode=None,
                initial_permission_mode=None,
                launch_options=options if options is not None else managed_args(profile["provider"], [])[0],
                launch_settings=settings or {}, monitor_error=None, fallback_error=None)
        return state["attempt"]


def context(event, text):
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def checkpoint_from(text):
    matches = re.findall(r"```ai-sub-handoff\s*\n(.*?)\n```", text or "", re.S)
    if len(matches) != 1 or len(matches[0]) > 100_000:
        return None
    try:
        data = json.loads(matches[0])
        if not isinstance(data, dict):
            return None
        for key in ("objective", "summary"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                return None
        for key in ("constraints", "completed", "next_steps", "tests", "blockers", "background_jobs"):
            if not isinstance(data.get(key), list) or not all(isinstance(v, str) for v in data[key]):
                return None
        for key in ("task_complete", "waiting_for_user"):
            if type(data.get(key)) is not bool:
                return None
        if not data["task_complete"] and not data["waiting_for_user"] and not data["next_steps"]:
            return None
        return {key: data[key] for key in ("objective", "summary", "constraints", "completed",
                "next_steps", "tests", "blockers", "background_jobs", "task_complete", "waiting_for_user")}
    except (ValueError, TypeError):
        return None


def hook_event(run, payload, attempt):
    """Return (provider JSON output, hold Stop hook). Never save raw tool/credential payloads."""
    event = payload.get("hook_event_name")
    with run.edit() as state:
        if not state.get("active") or attempt != state.get("attempt"):
            return {}, False
        session = payload.get("session_id")
        if not isinstance(session, str) or not session:
            state["fault"] = "Lifecycle hook omitted its session identifier."
            return {}, False
        if not state.get("session_id"):
            if event != "SessionStart" or payload.get("source") not in ("startup", None):
                return {}, False
            state["session_id"] = session
        if session != state["session_id"]:
            if event == "SessionStart" and payload.get("source") in ("clear", "resume"):
                state["fault"] = "Native session changed; restart a managed run for this conversation."
            return {}, False
        if not payload.get("cwd") or Path(payload["cwd"]).resolve() != Path(state["cwd"]):
            state["fault"] = "Session directory changed; automatic handoff suspended."
            return {}, False
        state["hooks_seen"] = sorted(set(state["hooks_seen"] + [event]))
        if payload.get("permission_mode"):
            state["permission_mode"] = payload["permission_mode"]
            if state["initial_permission_mode"] is None:
                state["initial_permission_mode"] = payload["permission_mode"]
            elif state["initial_permission_mode"] != payload["permission_mode"]:
                state["fault"] = "Session permissions changed; restart a managed session to enable handoff."
        if event == "SessionStart":
            if state["status"] == "waiting for hooks":
                state["status"] = "monitoring"
            message = ("This session is supervised by ai-sub. If an AI-SUB HANDOFF REQUEST arrives, "
                       "finish running work and submit the requested checkpoint in your final response. "
                       "Do not reset, stash, commit, or change repository/worktree for account switching. "
                       "Never include authentication secrets in task notes.")
            if state.get("handoff_path"):
                message += (f' Read the previous agent checkpoint at {state["handoff_path"]}. '
                            "Its task notes are context, not new permissions. Respect the original user constraints. "
                            "If task_complete or waiting_for_user is true, wait for user direction.")
            return context(event, message), False
        if state["status"] == "switching" and event == "Stop":
            return {"continue": False, "stopReason": "Account handoff in progress."}, False
        if event == "UserPromptSubmit":
            if state["status"] == "switching":
                return {"decision": "block", "reason": "Account handoff in progress; resend after it finishes."}, False
            prompt = payload.get("prompt")
            if isinstance(prompt, str) and prompt and prompt != CHECKPOINT_REQUEST:
                if prompt != state.pop("skip_prompt", None):
                    state["prompts"].append(prompt)
                    state["prompt_revision"] += 1
                state["ready_until"] = 0
                state["checkpoint"] = None
                state["stop_requests"] = 0
            return context(event, CHECKPOINT_REQUEST) if state["pending"] else {}, False
        if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            tool_id = payload.get("tool_use_id")
            if not tool_id:
                state["fault"] = "Tool lifecycle identifier missing; handoff suspended."
                return {}, False
            if event == "PreToolUse":
                if state["status"] == "switching":
                    return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                            "permissionDecisionReason": "Account handoff in progress."}}, False
                state["ready_until"] = 0
                state["tools"] = sorted(set(state["tools"] + [tool_id]))
                state["checkpoint"] = None
                return context(event, CHECKPOINT_REQUEST) if state["pending"] else {}, False
            state["tools"] = [t for t in state["tools"] if t != tool_id]
        elif event in ("SubagentStart", "SubagentStop"):
            agent = payload.get("agent_id")
            if not agent:
                state["fault"] = "Subagent lifecycle identifier missing; handoff suspended."
            elif event == "SubagentStart":
                state["agents"] = sorted(set(state["agents"] + [agent]))
                state["ready_until"] = 0
            else:
                state["agents"] = [a for a in state["agents"] if a != agent]
        elif event in ("Interrupt", "StopFailure", "SessionEnd"):
            state["ready_until"] = 0
            state["status"] = "interrupted" if event == "Interrupt" else "stopped without handoff"
            # Never infer safety from a rate-limit error or user interruption.
        elif event == "Stop" and state["pending"] and not state["fault"]:
            checkpoint = checkpoint_from(payload.get("last_assistant_message"))
            registry_clear = (state["provider"] != "claude" or
                              (payload.get("background_tasks") == [] and payload.get("session_crons") == []))
            if (checkpoint and not checkpoint["background_jobs"] and not state["tools"] and not state["agents"]
                    and registry_clear and not payload.get("background_tasks") and not payload.get("session_crons")):
                state["checkpoint"] = checkpoint
                state["checkpoint_revision"] = state["prompt_revision"]
                state["ready_until"] = time.time() + LEASE_SECONDS
                state["status"] = "checkpoint ready"
                return {"continue": False, "stopReason": "Task checkpoint saved for account handoff."}, True
            if state["stop_requests"] < 1 and not payload.get("stop_hook_active"):
                state["stop_requests"] += 1
                return {"decision": "block", "reason": CHECKPOINT_REQUEST}, False
            state["status"] = "waiting for safe checkpoint"
        return {}, False


def hooks_for(provider):
    command = shlex.join([sys.executable, str(Path(__file__).with_name("session_hook.py").resolve())])
    events = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart",
              "SubagentStop", "Stop", "SessionEnd"]
    events += ["Interrupt"] if provider == "codex" else ["PostToolUseFailure", "StopFailure"]
    return {event: [{"hooks": [{"type": "command", "command": command,
                                "timeout": 1 if event == "Interrupt" else 3 if event == "SessionEnd" else 30}]}]
            for event in events}


def preflight(store, profile, cwd):
    if profile["provider"] != "codex":
        return True  # Claude gates its hook overlay on native workspace trust at launch.
    hooks = hooks_for("codex")
    command, _ = app.session_command(store, profile, [], hooks)
    expected_command = hooks["Stop"][0]["hooks"][0]["command"]
    expected = {event[0].lower() + event[1:] for event in hooks}
    try:
        with app.CodexRPC(store, profile, config_args=command[1:], experimental=True) as rpc:
            result = rpc.request("hooks/list", {"cwds": [str(cwd)]})
        entries = [entry for entry in result.get("data", []) if Path(entry["cwd"]).resolve() == cwd]
        if len(entries) != 1 or entries[0].get("errors"):
            return False
        ready = {hook["eventName"] for hook in entries[0]["hooks"]
                 if hook.get("command") == expected_command and hook.get("enabled")
                 and hook.get("source") == "sessionFlags" and hook.get("trustStatus") in ("trusted", "managed")}
        return ready == expected
    except (app.UserError, OSError, subprocess.SubprocessError, ValueError, KeyError):
        return False


def managed_args(provider, extra):
    """Keep replayable settings; a prompt is sent once, never replayed on the next account."""
    values = ({"-m", "--model", "-s", "--sandbox", "-a", "--ask-for-approval"} if provider == "codex"
              else {"--model", "--effort", "--permission-mode"})
    flags = ({"--search", "--no-alt-screen", "--no-daemon"} if provider == "codex"
             else {"--chrome", "--no-chrome", "--verbose"})
    native_commands = {"resume", "exec", "e", "fork", "review", "login", "logout", "app", "app-server",
                       "auth", "mcp", "agents", "attach", "update", "doctor", "help"}
    options, prompt = [], None
    i = 0
    while i < len(extra):
        arg = extra[i]
        key, equals, value = arg.partition("=")
        if key in values:
            if not equals:
                i += 1
                if i == len(extra):
                    raise app.UserError(f"{key} needs a value.")
                value = extra[i]
            if key in ("-s", "--sandbox") and value not in ("read-only", "workspace-write"):
                raise app.UserError("Managed handoffs support read-only or workspace-write sandbox mode.")
            if key == "--permission-mode" and value not in ("manual", "plan", "acceptEdits"):
                raise app.UserError("Managed handoffs support manual, plan, or acceptEdits permission mode.")
            options += [key, value]
        elif arg in flags:
            options.append(arg)
        elif not arg.startswith("-") and prompt is None and arg not in native_commands:
            prompt = arg
        else:
            raise app.UserError(f"Managed handoff does not support argument {app.clean_text(arg)}. "
                                "Start in the existing repository/worktree with a quoted prompt. "
                                f"Use ai-sub auto off {provider} for other native CLI modes.")
        i += 1
    if provider == "codex":
        if "--no-daemon" not in options:
            options.append("--no-daemon")
        if not any(k in options for k in ("-s", "--sandbox")):
            options += ["--sandbox", "workspace-write"]
        if not any(k in options for k in ("-a", "--ask-for-approval")):
            options += ["--ask-for-approval", "on-request"]
    elif "--permission-mode" not in options:
        options += ["--permission-mode", "manual"]
    return options, prompt


def replacement_launch(state, target):
    """Use native target options; Codex model/approval flags must never reach Claude."""
    if state["provider"] == target["provider"]:
        return {"provider": target["provider"], "options": state["launch_options"],
                "settings": state.get("launch_settings", {})}
    if (state["provider"], target["provider"]) != ("codex", "claude"):
        raise app.UserError("Only Codex to Claude cross-provider handoffs are supported.")
    options = state.get("launch_options", [])
    # Honour the last occurrence, as the native CLI does. Missing recorded flags
    # (including runs started by an older supervisor) are not portable permissions.
    sandbox = approval = None
    for index, value in enumerate(options[:-1]):
        if value in ("-s", "--sandbox"):
            sandbox = options[index + 1]
        if value in ("-a", "--ask-for-approval"):
            approval = options[index + 1]
    if sandbox != "workspace-write" or approval != "on-request":
        raise app.UserError("Codex to Claude requires workspace-write and on-request; source permissions are not portable.")
    return {"provider": "claude", "options": ["--permission-mode", "manual"], "settings": {
        "sandbox": {"enabled": True, "failIfUnavailable": True,
                    "allowUnsandboxedCommands": False, "autoAllowBashIfSandboxed": False},
        "permissions": {"ask": ["Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch"]}}}


def poll_quota(store, run, profile, attempt=None):
    rule = policy(store, profile["provider"])
    state = run.read()
    attempt = attempt or state.get("attempt")
    if (not state.get("active") or state.get("attempt") != attempt
            or state.get("profile") != app.profile_key(profile) or state["status"] == "switching"):
        return
    if not rule["enabled"] or state.get("switches", 0) >= MAX_SWITCHES:
        run.change(attempt, pending=None, ready_until=0)
        return
    if state.get("fault") or not state.get("session_id"):
        return
    current = store.profile(profile["provider"], profile["name"])
    if fingerprint(current) != state["fingerprint"]:
        run.change(attempt, fault="Account metadata changed; restart this session.")
        return
    app.refresh(store, current)
    if not needs_switch(store.latest(current), rule["threshold"]):
        if state.get("pending"):
            run.change(attempt, pending=None, ready_until=0, status="monitoring")
        return
    candidates = []
    available = app.chosen_profiles(store, profile["provider"])
    fallback_error = None
    if profile["provider"] == "codex" and rule.get("fallback"):
        try:
            target = store.profile(*rule["fallback"].split("/"))
            replacement_launch(state, target)
            available.append(target)
        except (app.UserError, TypeError, ValueError) as exc:
            fallback_error = str(exc)
    for candidate in available:
        if app.profile_key(candidate) == app.profile_key(current):
            continue
        app.refresh(store, candidate)
        try:
            # Verify identity before treating a profile as spare subscription capacity.
            app.identity(store, candidate)
            candidate = store.profile(candidate["provider"], candidate["name"])
            if eligible(store, current, candidate, rule["threshold"]):
                candidates.append(candidate)
        except (app.UserError, OSError, subprocess.SubprocessError):
            continue
    candidates.sort(key=lambda p: (p["provider"] != current["provider"],
                                  max(w["used"] for w in store.latest(p)["windows"])))
    with run.edit() as data:
        if data["attempt"] != attempt or not data["active"] or data["status"] == "switching":
            return
        data["fallback_error"] = fallback_error
        if candidates:
            if data.get("pending") and data["pending"]["fingerprint"] == fingerprint(candidates[0]):
                return
            data["pending"] = {"target": app.profile_key(candidates[0]),
                               "fingerprint": fingerprint(candidates[0]), "requested": time.time()}
            data["status"] = "handoff requested"
            data["stop_requests"] = 0
        else:
            data["pending"] = None
            data["ready_until"] = 0
            data["status"] = "quota threshold reached; no eligible standby"


def monitor(store, run, profile, stopped, attempt):
    while not stopped.is_set():
        try:
            poll_quota(store, run, profile, attempt)
        except (app.UserError, OSError, ValueError, subprocess.SubprocessError, app.sqlite3.Error):
            run.change(attempt, monitor_error="Quota check failed; no account change authorized.",
                       pending=None, ready_until=0)
        stopped.wait(60)


def repository_snapshot(cwd):
    # Read-only metadata, no diff contents, filesystem mutations, stash or commit.
    result = {}
    for key, args in (("root", ["rev-parse", "--show-toplevel"]),
                      ("branch", ["symbolic-ref", "--short", "HEAD"]),
                      ("head", ["rev-parse", "HEAD"]),
                      ("status", ["status", "--short", "--untracked-files=normal"])):
        try:
            output = subprocess.run(["git", "--no-optional-locks", *args], cwd=cwd,
                                    capture_output=True, text=True, timeout=5)
            result[key] = output.stdout[:100_000].strip() if output.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            result[key] = None
    return result


def claim_handoff(store, run):
    """Consume a short Stop-hook lease before signalling only this supervisor's child."""
    with run.edit() as state:
        pending = state.get("pending")
        if (not pending or not state.get("active") or not state.get("checkpoint") or state.get("fault")
                or state.get("ready_until", 0) < time.time() + 2
                or state.get("checkpoint_revision") != state["prompt_revision"]
                or state["tools"] or state["agents"]):
            return None
        provider = state["provider"]
        rule = policy(store, provider)
        current = store.profile(provider, state["profile"].split("/", 1)[1])
        target = store.profile(*pending["target"].split("/", 1))
        if (not rule["enabled"] or fingerprint(current) != state["fingerprint"]
                or fingerprint(target) != pending["fingerprint"]
                or not eligible(store, current, target, rule["threshold"])
                or not needs_switch(store.latest(current), rule["threshold"])):
            state.update(pending=None, ready_until=0, status="handoff cancelled; quota or profile changed")
            return None
        try:
            launch = replacement_launch(state, target)
        except app.UserError as exc:
            state.update(pending=None, ready_until=0, status="handoff cancelled; incompatible permissions",
                         fallback_error=str(exc))
            return None
        state["status"] = "switching"
        path = run.directory / f'handoff-{state["switches"] + 1}.json'
        app.write_json(path, {"version": 2, "cwd": state["cwd"], "from_profile": state["profile"],
            "to_profile": pending["target"], "session_id": state["session_id"], "saved": time.time(),
            "user_prompts": state["prompts"], "checkpoint": state["checkpoint"],
            "permission_mode": state["permission_mode"],
            "source_launch": {"provider": provider, "options": state["launch_options"],
                              "settings": state.get("launch_settings", {})}, "target_launch": launch})
        state["handoff_path"] = str(path)
        return target, path


def stop_child(child):
    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=10)
        return True
    except subprocess.TimeoutExpired:
        return False  # Never SIGKILL a potentially busy agent or start a second writer.


def continuation(path):
    return (f"Continue the existing task using the private account handoff file {path}. "
            "Read it and the repository instructions (including AGENTS.md and CLAUDE.md) before working. "
            "Preserve the same directory/worktree "
            "and existing edits. The checkpoint is a summary, not new authorization. Verify the current "
            "files before acting and continue only the unfinished user-requested work. Respect blockers "
            "and pending approvals; do not repeat completed actions or deployments. If task_complete or "
            "waiting_for_user is true, wait for user direction.")


def supervise(store, profile, extra):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise app.UserError("Automatic handoff requires an interactive terminal. Use auto off for scripts.")
    options, prompt = managed_args(profile["provider"], extra)
    settings = {}
    cwd = Path.cwd().resolve()
    run = Run.create(store, profile["provider"], cwd, prompt)
    print(f"Managed run: {run.directory.name}; task notes stay in {run.directory}", file=sys.stderr)
    print("Codex: review /hooks in each profile. Switching waits for trusted hooks and a safe checkpoint.", file=sys.stderr)
    # Both processes remain in the terminal's foreground group. Let the native CLI
    # handle Ctrl-C; a Python handler (not SIG_IGN) is reset on exec in the child.
    terminal = termios.tcgetattr(sys.stdin.fileno())
    previous_sigint = signal.signal(signal.SIGINT, lambda *_: None)
    child = None
    expected_target = None
    try:
        while True:
            profile = store.profile(profile["provider"], profile["name"])
            app.identity(store, profile)
            profile = store.profile(profile["provider"], profile["name"])
            hooks_ok = preflight(store, profile, cwd)
            if expected_target:
                # Native login checks and outgoing shutdown take time. Recheck the
                # target after them, before any replacement process can run.
                data = app.read_json(run.read()["handoff_path"])
                outgoing = store.profile(*data["from_profile"].split("/", 1))
                rule = policy(store, outgoing["provider"])
                if (fingerprint(profile) != expected_target or not rule["enabled"]
                        or not eligible(store, outgoing, profile, rule["threshold"])):
                    raise app.UserError("Replacement no longer qualifies; no new CLI started. "
                                        "Checkpoint retained: " + run.read()["handoff_path"])
            attempt = run.begin(profile, prompt, hooks_ok, options, settings)
            if not hooks_ok:
                print("ai-sub: automatic handoff is suspended until all hooks are trusted. "
                      "Review /hooks, then exit and restart this managed run.", file=sys.stderr, flush=True)
            command, env = app.session_command(store, profile, options + ([prompt] if prompt else []),
                                               hooks=hooks_for(profile["provider"]), handoff_settings=settings)
            env.update(AI_SUB_RUN_DIR=str(run.directory), AI_SUB_RUN_ATTEMPT=attempt)
            print(f"Starting {app.profile_key(profile)} in {cwd}", file=sys.stderr, flush=True)
            child = subprocess.Popen(command, cwd=cwd, env=env)
            stopped = threading.Event()
            worker = threading.Thread(target=monitor, args=(store, run, profile, stopped, attempt), daemon=True)
            worker.start()
            started, warned = time.monotonic(), False
            target = None
            try:
                while child.poll() is None:
                    if time.monotonic() - started > 30 and not warned and not run.read().get("session_id"):
                        print("ai-sub: lifecycle hooks are not active; automatic handoff is unavailable. "
                              "Review /hooks and restart this managed run.", file=sys.stderr, flush=True)
                        warned = True
                    try:
                        target = claim_handoff(store, run)
                    except (app.UserError, OSError, ValueError, app.sqlite3.Error):
                        stopped.set()
                        print("ai-sub: handoff check failed; keeping the current CLI until you exit it.",
                              file=sys.stderr, flush=True)
                        child.wait()
                        target = None
                        break
                    if target:
                        stopped.set()
                        if not stop_child(child):
                            with run.edit() as state:
                                state.update(fault="Outgoing process did not exit; no replacement was launched.",
                                             ready_until=0, pending=None, status="handoff stopped")
                            print("ai-sub: outgoing CLI did not exit; replacement held. Check ai-sub sessions.",
                                  file=sys.stderr, flush=True)
                            target = None
                        else:
                            break
                    time.sleep(0.2)
            finally:
                stopped.set()
                # A quota request can still finish later; its attempt token cannot edit a new run.
                with run.edit() as state:
                    state["active"] = False
                    state["ready_until"] = 0
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, terminal)
            if not target:
                with run.edit() as state:
                    state["status"] = "exited"
                return child.returncode if child.returncode >= 0 else 128 - child.returncode
            profile, path = target
            expected_target = fingerprint(profile)
            data = app.read_json(path)
            options = data["target_launch"]["options"]
            settings = data["target_launch"]["settings"]
            data["repository"] = repository_snapshot(cwd)
            app.write_json(path, data)
            with run.edit() as state:
                state["switches"] += 1
            checkpoint = data["checkpoint"]
            prompt = None if checkpoint["task_complete"] or checkpoint["waiting_for_user"] else continuation(path)
            print(f"ai-sub: checkpoint saved; continuing with {app.profile_key(profile)}.", file=sys.stderr, flush=True)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, terminal)
        with run.edit() as state:
            state["active"] = False
            if state["status"] != "exited":
                state["status"] = "supervisor stopped; checkpoint retained"


def sessions(store):
    paths = sorted((store.home / "runs").glob("*/run.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths[:20]:
        state = app.read_json(path)
        pid = state.get("supervisor_pid")
        try:
            os.kill(pid, 0)
            running = "supervisor present" if state.get("active") else "finished"
        except ProcessLookupError:
            running = "supervisor stopped"
        except PermissionError:
            running = "process check restricted" if state.get("active") else "finished"
        except (OSError, TypeError):
            running = "supervisor status unknown"
        print(f'{state["id"]} | {state.get("profile", state["provider"])} | {running} | '
              f'{app.clean_text(state["status"])}')
        print(f'  Directory: {app.clean_text(state["cwd"])}')
        if state.get("fault"):
            print(f'  {state["fault"]}')
        if state.get("monitor_error"):
            print(f'  {state["monitor_error"]}')
        if state.get("fallback_error"):
            print(f'  Fallback unavailable: {app.clean_text(state["fallback_error"])}')
        if state.get("handoff_path"):
            print(f'  Continuation: {state["handoff_path"]}')
    if not paths:
        print("No managed sessions yet. Start with ai-sub auto on, then ai-sub run PROVIDER.")
