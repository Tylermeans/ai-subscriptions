import contextlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_sub as app
import handoff as h


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name).resolve()
        self.store = app.Store(self.cwd / "private")
        self.one = self.profile("one")
        self.two = self.profile("two")
        with contextlib.redirect_stdout(io.StringIO()):
            h.configure(self.store, "on")
        self.run = h.Run.create(self.store, "codex", self.cwd, "Fix the current bug; do not deploy.")
        self.attempt = self.run.begin(self.one)
        self.event("SessionStart", source="startup")
        self.save(self.one, 91)
        self.save(self.two, 10)

    def profile(self, name, provider="codex"):
        profile = self.store.add(provider, name, email=f"{name}@example.com")
        with self.store.edit() as config:
            config["profiles"][app.profile_key(profile)]["verified_email"] = profile["email"]
        return self.store.profile(provider, name)

    def save(self, profile, used, observed=None, blocked=False):
        if observed is not None:
            with contextlib.closing(self.store.db()) as db, db:
                db.execute("DELETE FROM samples WHERE profile=?", (app.profile_key(profile),))
        self.store.save(profile, [app.window("short", used, time.time() + 7200, 300),
                                  app.window("week", used, time.time() + 86400, 10080)],
                        "fixture", blocked=blocked, observed=observed)

    def event(self, event, **extra):
        return h.hook_event(self.run, {"hook_event_name": event, "session_id": "main-session",
                           "cwd": str(self.cwd), "permission_mode": "default", **extra}, self.attempt)

    def request(self):
        with patch.object(app, "refresh"), patch.object(app, "identity"):
            h.poll_quota(self.store, self.run, self.one)

    def checkpoint(self, **extra):
        data = {"objective": "Fix the bug", "summary": "Implementation done; regression check remains.",
                "constraints": ["Do not deploy"], "completed": ["Edited app.py"],
                "next_steps": ["Run the targeted regression check"], "tests": ["Not run"],
                "blockers": [], "background_jobs": [], "task_complete": False, "waiting_for_user": False,
                **extra}
        return "Checkpoint:\n```ai-sub-handoff\n" + json.dumps(data) + "\n```"

    def ready(self):
        self.request()
        return self.event("Stop", last_assistant_message=self.checkpoint())

    def test_requires_threshold_and_distinct_same_provider_account(self):
        self.save(self.one, 89)
        self.request()
        self.assertIsNone(self.run.read()["pending"])
        self.save(self.one, 91)
        self.request()
        self.assertEqual(self.run.read()["pending"]["target"], "codex/two")
        self.assertFalse(h.eligible(self.store, self.one, self.one, 90))
        self.assertFalse(h.eligible(self.store, self.one, {**self.two, "provider": "claude"}, 90))
        self.assertFalse(h.eligible(self.store, self.one, {**self.two, "verified_email": self.one["email"].upper()}, 90))

    def test_stale_missing_weekly_expired_blocked_and_unverified_not_candidates(self):
        self.save(self.two, 10, observed=time.time() - 301)
        self.assertFalse(h.eligible(self.store, self.one, self.two, 90))
        self.save(self.two, 10, blocked=True)
        self.assertFalse(h.eligible(self.store, self.one, self.two, 90))
        self.store.save(self.two, [app.window("short", 10, time.time() + 300, 300)], "fixture")
        self.assertFalse(h.eligible(self.store, self.one, self.two, 90))
        self.store.save(self.two, [app.window("short", 10, time.time() - 1, 300),
                                  app.window("week", 10, time.time() + 300, 10080)], "fixture")
        self.assertFalse(h.eligible(self.store, self.one, self.two, 90))
        self.save(self.two, 10)
        self.assertFalse(h.eligible(self.store, self.one, {**self.two, "verified_email": None}, 90))

    def test_failed_identity_never_requests_switch(self):
        with patch.object(app, "refresh"), patch.object(app, "identity", side_effect=app.UserError("No auth")):
            h.poll_quota(self.store, self.run, self.one)
        self.assertIsNone(self.run.read()["pending"])
        self.assertIn("no eligible standby", self.run.read()["status"])

    def test_provider_block_triggers_even_at_low_percent_but_not_stale(self):
        self.save(self.one, 5, blocked=True)
        self.assertTrue(h.needs_switch(self.store.latest(self.one), 90))
        self.save(self.one, 5, blocked=True, observed=time.time() - 301)
        self.assertFalse(h.needs_switch(self.store.latest(self.one), 90))

    def test_missing_checkpoint_gets_one_request_without_looping(self):
        self.request()
        first, hold = self.event("Stop", last_assistant_message="All done")
        self.assertEqual(first["decision"], "block")
        self.assertFalse(hold)
        second, hold = self.event("Stop", last_assistant_message="All done")
        self.assertEqual(second, {})
        self.assertFalse(hold)
        self.assertIsNone(h.claim_handoff(self.store, self.run))

    def test_stop_hook_active_does_not_loop(self):
        self.request()
        output, hold = self.event("Stop", last_assistant_message="Done", stop_hook_active=True)
        self.assertEqual(output, {})
        self.assertFalse(hold)

    def test_active_tools_agents_background_jobs_prevent_handoff(self):
        self.request()
        self.event("PreToolUse", tool_use_id="tool1")
        self.assertFalse(self.event("Stop", last_assistant_message=self.checkpoint())[1])
        self.event("PostToolUse", tool_use_id="tool1")
        self.event("SubagentStart", agent_id="agent1")
        self.assertFalse(self.event("Stop", last_assistant_message=self.checkpoint())[1])
        self.event("SubagentStop", agent_id="agent1")
        self.assertFalse(self.event("Stop", last_assistant_message=self.checkpoint(background_jobs=["test process"]))[1])
        self.assertFalse(self.event("Stop", last_assistant_message=self.checkpoint(), background_tasks=[{"id": "t"}])[1])
        self.assertTrue(self.event("Stop", last_assistant_message=self.checkpoint())[1])

    def test_claude_requires_empty_native_background_registry(self):
        with self.run.edit() as state:
            state["provider"] = "claude"
        self.request()
        self.assertFalse(self.event("Stop", last_assistant_message=self.checkpoint())[1])
        self.assertTrue(self.event("Stop", last_assistant_message=self.checkpoint(),
                                   background_tasks=[], session_crons=[])[1])

    def test_new_prompt_or_tool_invalidates_stop_lease(self):
        self.ready()
        self.event("UserPromptSubmit", prompt="Actually do not make more edits")
        self.assertIsNone(h.claim_handoff(self.store, self.run))
        self.assertIn("Actually do not make more edits", self.run.read()["prompts"])
        self.ready()
        self.event("PreToolUse", tool_use_id="new-tool")
        self.assertIsNone(h.claim_handoff(self.store, self.run))

    def test_user_interrupt_never_triggers_switch(self):
        self.ready()
        self.event("Interrupt")
        self.assertIsNone(h.claim_handoff(self.store, self.run))

    def test_failure_does_not_use_old_checkpoint(self):
        self.ready()
        self.event("StopFailure", error="rate_limit")
        self.assertIsNone(h.claim_handoff(self.store, self.run))

    def test_expired_lease_and_disabled_policy_prevent_switch(self):
        self.ready()
        with self.run.edit() as state:
            state["ready_until"] = time.time() - 1
        self.assertIsNone(h.claim_handoff(self.store, self.run))
        self.ready()
        with contextlib.redirect_stdout(io.StringIO()):
            h.configure(self.store, "off", "codex")
        self.assertIsNone(h.claim_handoff(self.store, self.run))

    def test_changed_target_identity_invalidates_checkpoint(self):
        self.ready()
        with self.store.edit() as config:
            config["profiles"]["codex/two"]["generation"] = 2
        self.assertIsNone(h.claim_handoff(self.store, self.run))

    def test_changed_directory_and_permissions_suspend_switch(self):
        self.request()
        self.event("PreToolUse", tool_use_id="t", cwd="/some-other-worktree")
        self.assertIn("directory changed", self.run.read()["fault"])
        self.assertFalse(self.event("Stop", last_assistant_message=self.checkpoint())[1])
        with self.run.edit() as state:
            state["fault"] = None
        self.event("Stop", permission_mode="plan", last_assistant_message=self.checkpoint())
        self.assertIn("permissions changed", self.run.read()["fault"])

    def test_old_hooks_and_old_monitor_cannot_modify_replacement(self):
        old = self.attempt
        self.attempt = self.run.begin(self.two)
        before = self.run.read()
        h.hook_event(self.run, {"hook_event_name": "SessionEnd", "session_id": "main-session"}, old)
        with patch.object(app, "refresh") as refresh:
            h.poll_quota(self.store, self.run, self.one, old)
            refresh.assert_not_called()
        self.assertEqual(self.run.read(), before)

    def test_checkpoint_retains_context_in_private_file(self):
        self.ready()
        target, path = h.claim_handoff(self.store, self.run)
        self.assertEqual(target["name"], "two")
        data = app.read_json(path)
        self.assertEqual(data["cwd"], str(self.cwd))
        self.assertEqual(data["checkpoint"]["constraints"], ["Do not deploy"])
        self.assertEqual(data["user_prompts"], ["Fix the current bug; do not deploy."])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        denied, _ = self.event("PreToolUse", tool_use_id="late")
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_distinct_runs_do_not_share_task_notes(self):
        other = h.Run.create(self.store, "codex", self.cwd, "Different task")
        other.begin(self.two)
        self.ready()
        h.claim_handoff(self.store, self.run)
        self.assertEqual(other.read()["prompts"], ["Different task"])
        self.assertIsNone(other.read()["pending"])

    def test_synthetic_checkpoint_request_not_saved_as_user_instruction(self):
        self.request()
        self.event("UserPromptSubmit", prompt=h.CHECKPOINT_REQUEST)
        self.assertEqual(len(self.run.read()["prompts"]), 1)

    def test_malformed_checkpoint_is_not_trusted(self):
        for text in ("not json", "```ai-sub-handoff\n{}\n```", self.checkpoint(task_complete="false"),
                     self.checkpoint(next_steps=[], task_complete=False, waiting_for_user=False)):
            self.assertIsNone(h.checkpoint_from(text))

    def test_managed_arguments_keep_options_but_do_not_replay_prompt(self):
        options, prompt = h.managed_args("codex", ["--model=m1", "--sandbox", "read-only", "Fix bug"])
        self.assertEqual(prompt, "Fix bug")
        self.assertNotIn(prompt, options)
        self.assertIn("--no-daemon", options)
        self.assertIn("read-only", options)
        for args in (["resume", "--last"], ["--worktree"], ["-C", "/tmp"], ["--remote", "ws://host"],
                     ["--dangerously-bypass-hook-trust"], ["--config", "hooks=false"]):
            with self.assertRaises(app.UserError):
                h.managed_args("codex", args)
        for args in (["--print"], ["--resume", "id"], ["--bare"], ["--worktree"], ["--settings", "{}"]):
            with self.assertRaises(app.UserError):
                h.managed_args("claude", args)
        self.assertIn("--chrome", h.managed_args("claude", ["--chrome"])[0])

    def test_hook_overlay_does_not_bypass_native_trust_and_keeps_statusline(self):
        profile = self.profile("claude1", "claude")
        with patch.object(app, "binary", return_value="claude"):
            command, env = app.session_command(self.store, profile, [], h.hooks_for("claude"))
        settings = json.loads(command[2])
        self.assertIn("statusLine", settings)
        self.assertIn("Stop", settings["hooks"])
        self.assertNotIn("disableAllHooks", settings)
        with patch.object(app, "binary", return_value="codex"):
            command, env = app.session_command(self.store, self.one, [], h.hooks_for("codex"))
        self.assertNotIn("--dangerously-bypass-hook-trust", command)
        try:
            import tomllib
        except ImportError:
            return  # Native TOML parsing is also checked separately; Python 3.10 has no tomllib.
        for setting in command[2::2]:
            parsed = tomllib.loads(setting)
            self.assertTrue(parsed)

    def test_stop_timeout_never_uses_sigkill(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("agent", 10)
        self.assertFalse(h.stop_child(process))
        process.terminate.assert_called_once()
        process.kill.assert_not_called()

    def test_partial_codex_hook_trust_fails_closed(self):
        hooks = h.hooks_for("codex")
        command = hooks["Stop"][0]["hooks"][0]["command"]
        metadata = [{"eventName": name[0].lower() + name[1:], "command": command,
                     "enabled": True, "source": "sessionFlags", "trustStatus": "trusted"} for name in hooks]
        rpc = Mock()
        rpc.__enter__ = Mock(return_value=rpc)
        rpc.__exit__ = Mock(return_value=False)
        rpc.request.return_value = {"data": [{"cwd": str(self.cwd), "errors": [], "hooks": metadata}]}
        with patch.object(app, "CodexRPC", return_value=rpc), patch.object(app, "binary", return_value="codex"):
            self.assertTrue(h.preflight(self.store, self.one, self.cwd))
            metadata[0]["trustStatus"] = "untrusted"
            self.assertFalse(h.preflight(self.store, self.one, self.cwd))
            metadata[0]["trustStatus"] = "trusted"
            metadata[0]["enabled"] = False
            self.assertFalse(h.preflight(self.store, self.one, self.cwd))

    def test_untrusted_hooks_and_switch_limit_cannot_request_handoff(self):
        self.attempt = self.run.begin(self.one, hooks_ok=False)
        self.event("SessionStart", source="startup")
        self.request()
        self.assertIsNone(self.run.read()["pending"])
        self.attempt = self.run.begin(self.one)
        self.event("SessionStart", source="startup")
        with self.run.edit() as state:
            state["switches"] = h.MAX_SWITCHES
        self.request()
        self.assertIsNone(self.run.read()["pending"])

    def test_completed_and_waiting_tasks_are_not_replayed(self):
        self.exercise_supervisor(task_complete=True)

    def test_waiting_for_user_is_not_automatically_continued(self):
        self.exercise_supervisor(waiting_for_user=True)

    def test_stop_hook_entrypoint_and_lease_with_real_subprocess(self):
        self.request()
        env = {**os.environ, "AI_SUB_HOME": str(self.store.home), "AI_SUB_RUN_DIR": str(self.run.directory),
               "AI_SUB_RUN_ATTEMPT": self.attempt}
        script = app.ROOT / "scripts" / "session_hook.py"
        child = subprocess.Popen([sys.executable, str(script)], env=env, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        child.stdin.write(json.dumps({"hook_event_name": "Stop", "session_id": "main-session",
                                     "cwd": str(self.cwd), "last_assistant_message": self.checkpoint()}))
        child.stdin.close()
        deadline = time.monotonic() + 5
        while self.run.read().get("status") != "checkpoint ready" and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIsNone(child.poll(), "Synchronous hook must hold the native CLI until the handoff is claimed")
        self.assertIsNotNone(h.claim_handoff(self.store, self.run))
        with self.run.edit() as state:
            state["active"] = False
        self.assertEqual(child.wait(timeout=5), 0)
        self.assertFalse(json.loads(child.stdout.read())["continue"])
        child.stdout.close()
        child.stderr.close()

    def test_supervisor_continues_codex_in_same_worktree_only_after_outgoing_exit(self):
        self.exercise_supervisor()

    def test_supervisor_continues_claude_in_same_worktree_only_after_outgoing_exit(self):
        self.one, self.two = self.profile("one", "claude"), self.profile("two", "claude")
        self.save(self.one, 91)
        self.save(self.two, 10)
        self.exercise_supervisor()

    def exercise_supervisor(self, **checkpoint_options):
        fake = self.cwd / "fake-cli"
        fake.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, signal, subprocess, sys, time
root = pathlib.Path.cwd()
state_path = pathlib.Path(os.environ["AI_SUB_RUN_DIR"]) / "run.json"
def state(): return json.loads(state_path.read_text())
def record(text):
    with (root / "order.txt").open("a") as f: f.write(text + "\\n")
def hook(event, **extra):
    data = {"hook_event_name": event, "session_id": os.environ["AI_SUB_PROFILE"],
            "cwd": str(root), "source": "startup", "permission_mode": "default", **extra}
    return subprocess.run([sys.executable, HOOK], input=json.dumps(data), text=True,
                          capture_output=True, timeout=30)
def shutdown(*args):
    record("outgoing exited")
    sys.exit(0)
if os.environ["AI_SUB_PROFILE"].endswith("/one"):
    signal.signal(signal.SIGTERM, shutdown)
    record("outgoing started")
    hook("SessionStart")
    deadline = time.time() + 8
    while not state().get("pending") and time.time() < deadline: time.sleep(.02)
    assert state().get("pending"), "No handoff request"
    hook("PreToolUse", tool_use_id="edit")
    (root / "unfinished.txt").write_text("Keep this edit")
    hook("PostToolUse", tool_use_id="edit")
    hook("Stop", last_assistant_message=CHECKPOINT, background_tasks=[], session_crons=[])
    # The supervisor must stop this process before creating its replacement.
    time.sleep(10)
    sys.exit(3)
else:
    record("replacement started")
    assert "outgoing exited" in (root / "order.txt").read_text()
    assert (root / "unfinished.txt").read_text() == "Keep this edit"
    assert str(root) == state()["cwd"]
    saved = json.loads(pathlib.Path(state()["handoff_path"]).read_text())
    assert saved["checkpoint"]["constraints"] == ["Do not deploy"]
    assert saved["user_prompts"] == ["Fix bug without deploying"]
    assert "Fix bug without deploying" not in sys.argv
    assert any("Continue the existing task" in arg for arg in sys.argv) == SHOULD_CONTINUE
    hook("SessionStart")
    (root / "continued.txt").write_text(saved["checkpoint"]["next_steps"][0])
    sys.exit(0)
'''.replace("HOOK", repr(str(app.ROOT / "scripts" / "session_hook.py")))
             .replace("CHECKPOINT", repr(self.checkpoint(**checkpoint_options)))
             .replace("SHOULD_CONTINUE", repr(not any(checkpoint_options.values()))))
        fake.chmod(0o700)

        def fast_monitor(store, run, profile, stopped, attempt):
            while not stopped.is_set():
                h.poll_quota(store, run, profile, attempt)
                stopped.wait(.02)

        with patch.object(app, "binary", return_value=str(fake)), patch.object(app, "identity"), \
                patch.object(app, "refresh"), patch.object(h, "monitor", side_effect=fast_monitor), \
                patch.object(h, "preflight", return_value=True), \
                patch.object(h.Path, "cwd", return_value=self.cwd), \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch.object(sys.stdout, "isatty", return_value=True), \
                patch.object(h.termios, "tcgetattr", return_value=[]), patch.object(h.termios, "tcsetattr"), \
                contextlib.redirect_stderr(io.StringIO()):
            result = h.supervise(self.store, self.one, ["Fix bug without deploying"])
        self.assertEqual(result, 0)
        self.assertEqual((self.cwd / "order.txt").read_text().splitlines(),
                         ["outgoing started", "outgoing exited", "replacement started"])
        self.assertEqual((self.cwd / "continued.txt").read_text(), "Run the targeted regression check")


if __name__ == "__main__":
    unittest.main()
