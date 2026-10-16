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

    def cross_fallback(self):
        claude = self.profile("two", "claude")
        self.save(claude, 10)
        self.save(self.two, 91)
        with contextlib.redirect_stdout(io.StringIO()):
            h.configure(self.store, "on", "codex", fallback="claude/two")
        return claude

    def test_fallback_requires_explicit_codex_to_registered_claude_policy(self):
        self.profile("two", "claude")
        original = self.store.config()
        for mode, provider, fallback in (("on", None, "claude/two"), ("off", "codex", "claude/two"),
                ("on", "claude", "codex/two"), ("on", "codex", "codex/two"),
                ("on", "codex", "claude/missing"), ("on", "codex", "claude/")):
            with self.subTest(mode=mode, provider=provider, fallback=fallback), self.assertRaises(app.UserError):
                h.configure(self.store, mode, provider, fallback=fallback)
            self.assertEqual(self.store.config(), original)
        args = app.parser().parse_args(["auto", "on", "codex", "--fallback", "claude/two"])
        self.assertEqual(args.fallback, "claude/two")

    def test_fallback_prefers_codex_then_only_the_named_claude_profile(self):
        target = self.cross_fallback()
        other = self.profile("unused", "claude")
        self.save(other, 1)
        self.save(self.two, 20)
        self.request()
        self.assertEqual(self.run.read()["pending"]["target"], "codex/two")
        self.save(self.two, 91)
        self.request()
        self.assertEqual(self.run.read()["pending"]["target"], "claude/two")
        self.assertFalse(h.eligible(self.store, self.one, other, 90))
        self.assertTrue(h.eligible(self.store, self.one, {**target, "verified_email": self.one["email"]}, 90))
        self.assertFalse(h.eligible(self.store, target, self.one, 90))

    def test_fallback_requires_fresh_complete_unblocked_quota_below_threshold(self):
        target = self.cross_fallback()
        samples = [
            (10, time.time() - 301, False), (90, time.time(), False), (1, time.time(), True)]
        for used, observed, blocked in samples:
            with self.subTest(used=used, observed=observed, blocked=blocked):
                self.save(target, used, observed=observed, blocked=blocked)
                self.request()
                self.assertIsNone(self.run.read()["pending"])
        self.store.save(target, [app.window("week", 1, time.time() + 3600, 10080)], "fixture")
        self.request()
        self.assertIsNone(self.run.read()["pending"])

    def test_weekly_only_source_can_request_claude_fallback(self):
        self.cross_fallback()
        self.store.save(self.one, [app.window("week", 91, time.time() + 3600, 10080)], "fixture")
        self.request()
        self.assertEqual(self.run.read()["pending"]["target"], "claude/two")

    def test_initial_routing_avoids_exhausted_claude_before_starting_a_session(self):
        current = self.profile("current", "claude")
        standby = self.profile("midwest", "claude")
        self.save(current, 100)
        self.save(standby, 1)
        options, _ = h.managed_args("claude", ["--permission-mode", "auto"])
        with patch.object(app, "identity"), patch.object(app, "refresh"):
            target, launch, source = h.initial_profile(self.store, current, options)
        self.assertEqual(target, standby)
        self.assertEqual(source, current)
        self.assertEqual(launch["options"], ["--permission-mode", "auto"])

    def test_initial_routing_requires_verified_fresh_standby(self):
        self.save(self.one, 100)
        options, _ = h.managed_args("codex", [])
        with patch.object(app, "identity"), patch.object(app, "refresh"):
            self.save(self.two, 10, observed=time.time() - 301)
            with self.assertRaisesRegex(app.UserError, "No CLI started"):
                h.initial_profile(self.store, self.one, options)
            self.save(self.two, 10)
            with patch.object(app, "identity", side_effect=app.UserError("Cannot verify identity")):
                with self.assertRaises(app.UserError):
                    h.initial_profile(self.store, self.one, options)

    def test_initial_routing_keeps_healthy_account_and_requires_cross_provider_opt_in(self):
        target = self.cross_fallback()
        options, _ = h.managed_args("codex", ["--model", "codex-model"])
        with patch.object(app, "identity"), patch.object(app, "refresh"):
            self.save(self.one, 20)
            self.assertEqual(h.initial_profile(self.store, self.one, options)[0], self.one)
            self.save(self.one, 100)
            selected, launch, _ = h.initial_profile(self.store, self.one, options)
            self.assertEqual(selected, target)
            self.assertEqual(launch["options"], ["--permission-mode", "manual"])
            with contextlib.redirect_stdout(io.StringIO()):
                h.configure(self.store, "on", "codex", clear_fallback=True)
            with self.assertRaisesRegex(app.UserError, "No CLI started"):
                h.initial_profile(self.store, self.one, options)

    def test_stale_exhausted_source_routes_new_session_only_to_fresh_verified_standby(self):
        options, _ = h.managed_args("codex", [])
        self.save(self.one, 100, observed=time.time() - 301)
        with patch.object(app, "identity"), patch.object(app, "refresh"):
            self.assertEqual(h.initial_profile(self.store, self.one, options)[0], self.two)
            self.save(self.two, 10, observed=time.time() - 301)
            with self.assertRaisesRegex(app.UserError, "last seen exhausted"):
                h.initial_profile(self.store, self.one, options)
        self.request()
        self.assertIsNone(self.run.read()["pending"])

    def test_missing_usage_does_not_erase_exhaustion_but_new_identity_does(self):
        options, _ = h.managed_args("codex", [])
        self.save(self.one, 100)
        self.store.save(self.one, [], "claude-statusline", error="No subscription quota")
        with patch.object(app, "identity"), patch.object(app, "refresh"):
            self.assertEqual(h.initial_profile(self.store, self.one, options)[0], self.two)
            self.save(self.two, 10, observed=time.time() - 301)
            with self.assertRaisesRegex(app.UserError, "last seen exhausted"):
                h.initial_profile(self.store, self.one, options)
            self.store.save(self.one, [], "configuration", error="Account email changed")
            self.assertFalse(h.last_known_exhausted(self.store, self.one))
            self.assertEqual(h.initial_profile(self.store, self.one, options)[0], self.one)

    def test_new_healthy_usage_or_elapsed_reset_clears_known_exhaustion(self):
        self.save(self.one, 100)
        self.save(self.one, 10)
        self.assertFalse(h.last_known_exhausted(self.store, self.one))
        self.store.save(self.one, [app.window("week", 100, time.time() - 1, 10080)], "fixture")
        self.assertFalse(h.last_known_exhausted(self.store, self.one))

    def test_cross_provider_auth_failure_keeps_outgoing_agent(self):
        self.cross_fallback()
        def identity(store, profile):
            if profile["provider"] == "claude":
                raise app.UserError("Sign in required")
        with patch.object(app, "refresh"), patch.object(app, "identity", side_effect=identity):
            h.poll_quota(self.store, self.run, self.one)
        self.assertIsNone(self.run.read()["pending"])
        self.assertIsNone(h.claim_handoff(self.store, self.run))

    def test_removing_fallback_after_checkpoint_cancels_cross_provider_switch(self):
        self.cross_fallback()
        self.ready()
        with contextlib.redirect_stdout(io.StringIO()):
            h.configure(self.store, "on", "codex", clear_fallback=True)
        self.assertIsNone(h.claim_handoff(self.store, self.run))
        self.assertIsNone(h.policy(self.store, "codex")["fallback"])

    def test_cross_provider_source_permissions_must_be_portable(self):
        target = self.cross_fallback()
        for extra in (["-s", "read-only"], ["-a", "never"], ["-a", "untrusted"],
                      ["--sandbox", "workspace-write", "-s", "read-only"]):
            with self.subTest(extra=extra):
                options, _ = h.managed_args("codex", extra)
                self.attempt = self.run.begin(self.one, options=options)
                self.event("SessionStart", source="startup")
                self.request()
                self.assertIsNone(self.run.read()["pending"])
                self.assertIn("source permissions are not portable", self.run.read()["fallback_error"])
        with self.run.edit() as state:
            state.pop("launch_options")  # A run started by an older installed supervisor.
        with self.assertRaises(app.UserError):
            h.replacement_launch(self.run.read(), target)

    def test_cross_checkpoint_records_both_native_launches(self):
        self.cross_fallback()
        options, _ = h.managed_args("codex", ["--model", "codex-model", "--search"])
        self.attempt = self.run.begin(self.one, options=options)
        self.event("SessionStart", source="startup")
        self.ready()
        target, path = h.claim_handoff(self.store, self.run)
        saved = app.read_json(path)
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["from_profile"], "codex/one")
        self.assertEqual(saved["to_profile"], "claude/two")
        self.assertEqual(saved["source_launch"]["options"], options)
        self.assertEqual(saved["target_launch"]["options"], ["--permission-mode", "manual"])
        self.assertEqual(saved["target_launch"]["provider"], "claude")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_cross_provider_run_rebinds_hooks_and_background_checks(self):
        target = self.cross_fallback()
        old_attempt = self.attempt
        self.attempt = self.run.begin(target)
        self.event("SessionStart", source="startup")
        before = self.run.read()
        self.assertEqual(before["provider"], "claude")
        h.hook_event(self.run, {"hook_event_name": "Interrupt", "session_id": "main-session"}, old_attempt)
        self.assertEqual(self.run.read(), before)
        with self.run.edit() as state:
            state["pending"] = {"target": "claude/another"}
        _, hold = self.event("Stop", last_assistant_message=self.checkpoint())
        self.assertFalse(hold, "Claude must report empty background_tasks and session_crons")
        _, hold = self.event("Stop", last_assistant_message=self.checkpoint(), background_tasks=[], session_crons=[])
        self.assertTrue(hold)

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
        self.assertEqual(self.run.read()["status"], "rate limited; no safe checkpoint")
        self.assertIn("No task checkpoint was saved", self.run.read()["recovery_note"])

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

    def test_claude_supported_mode_changes_are_preserved_without_bypassing_permissions(self):
        profile = self.profile("claude1", "claude")
        for mode in ("auto", "plan", "acceptEdits", "dontAsk", "manual", "default"):
            with self.subTest(mode=mode):
                self.attempt = self.run.begin(profile)
                self.event("SessionStart", permission_mode="default")
                with self.run.edit() as state:
                    state["ready_until"] = time.time() + 20
                    state["checkpoint"] = {"summary": "Previous mode"}
                self.event("UserPromptSubmit", permission_mode=mode, prompt="Continue the task")
                state = self.run.read()
                self.assertIsNone(state["fault"])
                self.assertEqual(state["ready_until"], 0)
                self.assertIsNone(state["checkpoint"])
                expected = "manual" if mode == "default" else mode
                self.assertEqual(h.replacement_launch(state, profile)["options"], ["--permission-mode", expected])
        self.event("UserPromptSubmit", permission_mode="bypassPermissions", prompt="Continue")
        self.assertIn("Unsupported Claude permission mode", self.run.read()["fault"])
        self.assertNotIn("bypassPermissions", self.run.read()["launch_options"])

    def test_claude_mode_change_revokes_stop_lease_before_claim(self):
        profile = self.profile("claude1", "claude")
        self.attempt = self.run.begin(profile)
        self.event("SessionStart", permission_mode="default")
        with self.run.edit() as state:
            state["ready_until"] = time.time() + 20
            state["checkpoint"] = {"summary": "Previous mode"}
        self.event("SubagentStop", agent_id="completed", permission_mode="auto")
        self.assertEqual(self.run.read()["ready_until"], 0)
        self.assertIsNone(self.run.read()["checkpoint"])

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

    def test_session_status_does_not_report_restricted_process_as_stopped(self):
        for error, expected in ((PermissionError(), "process check restricted"),
                                (ProcessLookupError(), "supervisor stopped"),
                                (OSError(), "supervisor status unknown")):
            with self.subTest(error=type(error).__name__):
                output = io.StringIO()
                with patch.object(h.os, "kill", side_effect=error), contextlib.redirect_stdout(output):
                    h.sessions(self.store)
                self.assertIn(expected, output.getvalue())
                if not isinstance(error, ProcessLookupError):
                    self.assertNotIn("supervisor stopped", output.getvalue())

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

    def test_supervisor_moves_codex_to_claude_with_native_options_and_preserved_edits(self):
        self.cross_fallback()
        self.exercise_supervisor(cross=True)

    def test_cross_provider_completed_task_starts_idle(self):
        self.cross_fallback()
        self.exercise_supervisor(cross=True, task_complete=True)

    def test_cross_provider_waiting_for_user_starts_idle(self):
        self.cross_fallback()
        self.exercise_supervisor(cross=True, waiting_for_user=True)

    def test_target_becomes_stale_during_exit_does_not_launch_claude(self):
        self.cross_fallback()
        self.exercise_supervisor(cross=True, stale_on_exit=True)

    def test_supervisor_skips_exhausted_claude_after_an_empty_usage_update(self):
        current = self.profile("current", "claude")
        standby = self.profile("midwest", "claude")
        self.save(current, 100)
        self.store.save(current, [], "claude-statusline", error="No subscription quota")
        self.save(standby, 1)
        fake = self.cwd / "claude"
        fake.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys
root = pathlib.Path.cwd()
assert os.environ['AI_SUB_PROFILE'] == 'claude/midwest'
assert sys.argv[-1] == 'Finish the authorized Foreman work'
assert sys.argv[sys.argv.index('--permission-mode') + 1] == 'auto'
state = json.loads((pathlib.Path(os.environ['AI_SUB_RUN_DIR']) / 'run.json').read_text())
assert state['startup_route'] == {'from_profile': 'claude/current', 'to_profile': 'claude/midwest'}
assert state['prompts'] == ['Finish the authorized Foreman work']
assert state.get('handoff_path') is None
(root / 'started-profile.txt').write_text(os.environ['AI_SUB_PROFILE'])
''')
        fake.chmod(0o700)
        with patch.object(app, "binary", return_value=str(fake)), patch.object(app, "identity"), \
                patch.object(app, "refresh"), patch.object(h, "monitor"), \
                patch.object(h.Path, "cwd", return_value=self.cwd), \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch.object(sys.stdout, "isatty", return_value=True), \
                patch.object(h.termios, "tcgetattr", return_value=[]), patch.object(h.termios, "tcsetattr"), \
                contextlib.redirect_stderr(io.StringIO()):
            result = h.supervise(self.store, current, ["--permission-mode", "auto", "Finish the authorized Foreman work"])
        self.assertEqual(result, 0)
        self.assertEqual((self.cwd / "started-profile.txt").read_text(), "claude/midwest")

    def exercise_supervisor(self, cross=False, stale_on_exit=False, **checkpoint_options):
        # This fixture exercises a limit reached during work, after startup routing.
        self.save(self.one, 10)
        fake = self.cwd / "fake-cli"
        fake.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, signal, subprocess, sys, time
root = pathlib.Path.cwd()
state_path = pathlib.Path(os.environ["AI_SUB_RUN_DIR"]) / "run.json"
def state(): return json.loads(state_path.read_text())
assert pathlib.Path(sys.argv[0]).name == state()["provider"]
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
    assert saved["to_profile"] == os.environ["AI_SUB_PROFILE"]
    if saved["from_profile"].startswith("codex/") and state()["provider"] == "claude":
        assert not any(a in sys.argv for a in ("--sandbox", "--ask-for-approval", "--no-daemon", "--model", "--search"))
        assert sys.argv[sys.argv.index("--permission-mode") + 1] == "manual"
        settings = json.loads(sys.argv[sys.argv.index("--settings") + 1])
        assert settings["sandbox"]["enabled"] is True
        assert settings["sandbox"]["failIfUnavailable"] is True
        assert settings["sandbox"]["allowUnsandboxedCommands"] is False
        assert settings["sandbox"]["autoAllowBashIfSandboxed"] is False
        assert "Bash" in settings["permissions"]["ask"]
        assert "statusLine" in settings and "StopFailure" in settings["hooks"]
        assert "Interrupt" not in settings["hooks"]
        assert os.environ["CLAUDE_CONFIG_DIR"] == CLAUDE_HOME
    hook("SessionStart")
    (root / "continued.txt").write_text(saved["checkpoint"]["next_steps"][0])
    sys.exit(0)
'''.replace("HOOK", repr(str(app.ROOT / "scripts" / "session_hook.py")))
             .replace("CHECKPOINT", repr(self.checkpoint(**checkpoint_options)))
             .replace("CLAUDE_HOME", repr(str(self.store.profile("claude", "two")["home"]) if cross else ""))
             .replace("SHOULD_CONTINUE", repr(not any(checkpoint_options.values()))))
        fake.chmod(0o700)
        for provider in app.PROVIDERS:
            (self.cwd / provider).symlink_to(fake)

        def fast_monitor(store, run, profile, stopped, attempt):
            if app.profile_key(profile) == app.profile_key(self.one):
                self.save(self.one, 91)
            while not stopped.is_set():
                h.poll_quota(store, run, profile, attempt)
                stopped.wait(.02)

        def identity(store, profile):
            if stale_on_exit and (self.cwd / "order.txt").exists() and "outgoing exited" in (self.cwd / "order.txt").read_text():
                self.save(profile, 10, observed=time.time() - 301)

        with patch.object(app, "binary", side_effect=lambda provider: str(self.cwd / provider)), \
                patch.object(app, "identity", side_effect=identity), \
                patch.object(app, "refresh"), patch.object(h, "monitor", side_effect=fast_monitor), \
                patch.object(h, "preflight", return_value=True), \
                patch.object(h.Path, "cwd", return_value=self.cwd), \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch.object(sys.stdout, "isatty", return_value=True), \
                patch.object(h.termios, "tcgetattr", return_value=[]), patch.object(h.termios, "tcsetattr"), \
                contextlib.redirect_stderr(io.StringIO()):
            extra = ["--model", "codex-model", "--search"] if cross else []
            if stale_on_exit:
                with self.assertRaisesRegex(app.UserError, "Replacement no longer qualifies"):
                    h.supervise(self.store, self.one, extra + ["Fix bug without deploying"])
                self.assertEqual((self.cwd / "order.txt").read_text().splitlines(), ["outgoing started", "outgoing exited"])
                self.assertFalse((self.cwd / "continued.txt").exists())
                self.assertEqual(len(list((self.store.home / "runs").glob("*/handoff-1.json"))), 1)
                return
            result = h.supervise(self.store, self.one, extra + ["Fix bug without deploying"])
        self.assertEqual(result, 0)
        self.assertEqual((self.cwd / "order.txt").read_text().splitlines(),
                         ["outgoing started", "outgoing exited", "replacement started"])
        self.assertEqual((self.cwd / "continued.txt").read_text(), "Run the targeted regression check")


if __name__ == "__main__":
    unittest.main()
