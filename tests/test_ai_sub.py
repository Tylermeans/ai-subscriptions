import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_sub as app


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = app.Store(Path(self.temporary.name) / "private")
        base_config = patch.object(app, "BASE_CLAUDE_JSON", Path(self.temporary.name) / "base-claude.json")
        base_config.start()
        self.addCleanup(base_config.stop)

    def profile(self, provider="codex", name="personal", email=None):
        return self.store.add(provider, name, email=email)

    def windows(self, short=20, week=40, reset=None):
        reset = reset or time.time() + 3600
        return [app.window("five_hour", short, reset, 300),
                app.window("seven_day", week, reset + 86400, 10080)]

    def probe_result(self, short=0.2, week=0.4, status="allowed", reset=None):
        reset = reset or time.time() + 3600
        event = {"type": "rate_limit_event", "rate_limit_info": {"status": status, "unifiedWindows": {
            "five_hour": {"utilization": short, "resetsAt": reset, "status": status},
            "seven_day": {"utilization": week, "resetsAt": reset + 86400, "status": status}}}}
        lines = ["not json", "[1]", json.dumps({"type": "assistant", "message": {"content": "private"}}),
                 json.dumps(event), json.dumps({"type": "result", "subtype": "success"})]
        return app.subprocess.CompletedProcess([], 0, "\n".join(lines), "")

    def test_claude_probe_keeps_only_the_official_rate_limit_event(self):
        windows, blocked = app.probe_windows(self.probe_result(0.07, 0.86).stdout)
        self.assertEqual([(w["label"], w["used"], w["minutes"]) for w in windows],
                         [("five_hour", 7.0, 300.0), ("seven_day", 86.0, 10080.0)])
        self.assertIs(blocked, False)
        windows, blocked = app.probe_windows(self.probe_result(1.2, 0.5, "rejected").stdout)
        self.assertEqual((windows[0]["used"], blocked), (100.0, True))
        self.assertEqual(app.probe_windows('{"type": "rate_limit_event", "rate_limit_info": {}}'), ([], None))

    def test_claude_standby_probe_is_minimal_shared_and_paced_across_dst(self):
        profile = self.profile("claude", "midwest", "m@example.com")
        zone = os.environ.get("TZ")
        self.addCleanup(lambda: (os.environ.pop("TZ", None) if zone is None
                                 else os.environ.__setitem__("TZ", zone), time.tzset()))
        # 2026-11-01 06:00Z (fall-back) and 2027-03-14 07:00Z (spring-forward), America/New_York.
        for instant in (1793512800.0, 1805007600.0):
            for tz in ("UTC", "America/New_York"):
                with self.subTest(instant=instant, tz=tz):
                    os.environ["TZ"] = tz
                    time.tzset()
                    with contextlib.closing(self.store.db()) as db, db:
                        db.execute("DELETE FROM samples")
                    clock = [instant - 1]
                    with patch.object(app.time, "time", side_effect=lambda: clock[0]), \
                            patch.object(app, "identity"), patch.object(app, "binary", return_value="claude"), \
                            patch.object(app.subprocess, "run", side_effect=lambda *a, **k:
                                         self.probe_result(reset=clock[0] + 3600)) as run, \
                            patch.dict(os.environ, {"CLAUDECODE": "1"}):
                        app.refresh(self.store, profile)
                        run.assert_not_called()
                        app.refresh(self.store, profile, standby=True)
                        self.assertEqual(run.call_count, 1)
                        self.assertEqual(run.call_args.args[0], ["claude", *app.PROBE_ARGS])
                        options = run.call_args.kwargs
                        self.assertEqual(options["cwd"], self.store.home / "probe")
                        self.assertEqual(options["env"]["CLAUDE_CONFIG_DIR"], profile["home"])
                        self.assertNotIn("CLAUDECODE", options["env"])
                        sample = self.store.latest(profile)
                        self.assertEqual((sample["source"], app.freshness(sample)), ("claude-probe", "fresh"))
                        clock[0] = instant + app.PROBE_REFRESH_SECONDS - 2
                        app.refresh(self.store, profile, standby=True)
                        self.assertEqual(run.call_count, 1)
                        clock[0] = instant + app.PROBE_REFRESH_SECONDS
                        app.refresh(self.store, profile, standby=True)
                        self.assertEqual(run.call_count, 2)
                        run.side_effect = app.subprocess.TimeoutExpired("claude", 90)
                        clock[0] += app.PROBE_REFRESH_SECONDS
                        app.refresh(self.store, profile, standby=True)
                        self.assertEqual(self.store.latest(profile)["error"], "Claude CLI quota check failed.")
                        clock[0] += 60
                        app.refresh(self.store, profile, standby=True)
                        self.assertEqual(run.call_count, 3)

    def test_profiles_keep_independent_homes_and_no_credentials_are_copied(self):
        one = self.profile(name="one")
        two = self.profile(name="two")
        self.assertNotEqual(one["home"], two["home"])
        self.assertEqual(stat.S_IMODE(Path(one["home"]).stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.store.config_path.stat().st_mode), 0o600)
        self.assertFalse((Path(one["home"]) / "auth.json").exists())
        self.assertIn('cli_auth_credentials_store = "keyring"', (Path(one["home"]) / "config.toml").read_text())

    def test_duplicate_and_traversal_profiles_rejected(self):
        self.profile()
        with self.assertRaises(app.UserError):
            self.profile()
        with self.assertRaises(app.UserError):
            self.profile(name="../outside")

    def test_current_registration_does_not_touch_native_home(self):
        native = Path(self.temporary.name) / "native"
        native.mkdir()
        (native / "sentinel").write_text("unchanged")
        with patch.dict(os.environ, {"CODEX_HOME": str(native)}):
            profile = self.store.add("codex", "current", current=True)
        self.assertEqual(profile["home"], str(native.resolve()))
        self.assertEqual(list(native.iterdir()), [native / "sentinel"])

    def test_environment_removes_overrides_without_modifying_parent(self):
        profile = self.profile("claude")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "do-not-store", "OPENAI_API_KEY": "also-secret"}):
            env = app.cli_env(self.store, profile)
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "do-not-store")
            self.assertEqual(env["CLAUDE_CONFIG_DIR"], profile["home"])

    def test_existing_default_claude_preserves_unset_config_dir(self):
        profile = {"provider": "claude", "name": "current", "home": str(Path.home() / ".claude"),
                   "current": True, "explicit_home": False}
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/tmp/wrong"}):
            self.assertNotIn("CLAUDE_CONFIG_DIR", app.cli_env(self.store, profile))
        profile["explicit_home"] = True
        self.assertEqual(app.cli_env(self.store, profile)["CLAUDE_CONFIG_DIR"], profile["home"])

    def test_codex_multiple_buckets_resets_and_backend_block(self):
        payload = {"ordinaryUsageAllowed": False, "rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 2000000000},
                      "secondary": {"usedPercent": 70, "windowDurationMins": 10080, "resetsAt": 2000100000}},
            "review": {"primary": {"usedPercent": 5, "windowDurationMins": 60}}}}
        windows, blocked = app.codex_windows(payload)
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0]["used"], 0)
        self.assertEqual(windows[1]["reset"], 2000100000)
        self.assertIsNone(windows[2]["reset"])
        self.assertTrue(blocked)

    def test_codex_legacy_bucket_and_spend_block(self):
        windows, blocked = app.codex_windows({"rateLimits": {"primary": {"usedPercent": 10},
                                             "spendControlReached": True}})
        self.assertEqual(windows[0]["label"], "codex/primary")
        self.assertTrue(blocked)

    def test_codex_does_not_clear_backend_block_from_low_percentages(self):
        profile = self.profile()
        self.store.save(profile, self.windows(80, 80), "test", blocked=True)
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def request(self, *args):
                return {"rateLimits": {"primary": {"usedPercent": 1}}}
        with patch.object(app, "CodexRPC", return_value=Connection()), patch.object(app, "identity"):
            app.refresh(self.store, profile)
        self.assertTrue(self.store.latest(profile)["blocked"])
        _, recovery = app.codex_windows({"ordinaryUsageAllowed": True, "rateLimits": {}})
        self.assertIs(recovery, False)

    def test_bad_percentages_are_unknown_not_available(self):
        for bad in (None, "2", -1, 101, float("nan"), float("inf"), True):
            self.assertIsNone(app.window("quota", bad, None, 300))

    def test_statusline_records_only_quota_fields(self):
        profile = self.profile("claude")
        payload = {"rate_limits": {"five_hour": {"used_percentage": 25, "resets_at": time.time() + 3600}},
                   "transcript_path": "/private/project", "secret": "never-save-this",
                   "context_window": {"used_percentage": 99}}
        result = app.statusline(self.store, profile, json.dumps(payload))
        self.assertIn("25%", result)
        sample = self.store.latest(profile)
        self.assertEqual(len(sample["windows"]), 1)
        self.assertNotIn("never-save-this", (self.store.home / "usage.sqlite3").read_bytes().decode(errors="ignore"))
        self.assertNotIn("/private/project", json.dumps(sample))
        self.assertEqual(stat.S_IMODE((self.store.home / "usage.sqlite3").stat().st_mode), 0o600)

    def test_disappearing_window_invalidates_previous_quota(self):
        profile = self.profile("claude")
        self.store.save(profile, self.windows(), "claude-statusline")
        app.statusline(self.store, profile, "{}")
        self.assertEqual(app.freshness(self.store.latest(profile)), "unknown")

    def test_expiry_and_staleness_never_imply_reset_to_zero(self):
        now = time.time()
        sample = {"observed": now, "windows": self.windows(), "error": None}
        self.assertEqual(app.freshness(sample, now), "fresh")
        sample["observed"] = now - 300
        self.assertEqual(app.freshness(sample, now), "fresh")
        sample["observed"] = now - 300.001
        self.assertEqual(app.freshness(sample, now), "stale")
        sample["observed"] = now
        sample["windows"][0]["reset"] = now - 1
        self.assertEqual(app.freshness(sample, now), "refresh needed")

    def test_recommendation_excludes_incomplete_stale_and_blocked_accounts(self):
        good = self.profile(name="good")
        blocked = self.profile(name="blocked")
        stale = self.profile(name="stale")
        incomplete = self.profile(name="incomplete")
        self.store.save(good, self.windows(40, 60), "test")
        self.store.save(blocked, self.windows(1, 1), "test", blocked=True)
        self.store.save(stale, self.windows(1, 1), "test", observed=time.time() - 301)
        self.store.save(incomplete, self.windows(1, 1)[:1], "test")
        before = self.store.config()
        remaining, key, _ = app.recommend(self.store, [blocked, stale, incomplete, good])
        self.assertEqual((remaining, key), (40, "codex/good"))
        self.assertEqual(self.store.config(), before)

    def test_no_recommendation_when_only_unknown_data(self):
        profile = self.profile()
        with self.assertRaises(app.UserError):
            app.recommend(self.store, [profile])

    def test_failed_refresh_invalidates_previously_fresh_data(self):
        profile = self.profile()
        self.store.save(profile, self.windows(), "test")
        with patch.object(app, "CodexRPC", side_effect=OSError("secret raw error")):
            app.refresh(self.store, profile)
        sample = self.store.latest(profile)
        self.assertEqual(app.freshness(sample), "unknown")
        self.assertNotIn("secret", sample["error"])

    def test_identity_mismatch_rejects_launch(self):
        profile = self.profile(email="right@example.com")
        class Connection:
            def request(self, *args):
                return {"account": {"type": "chatgpt", "email": "wrong@example.com", "planType": "plus"}}
        with self.assertRaises(app.UserError):
            app.identity(self.store, profile, Connection())
        self.assertIsNone(self.store.profile("codex", "personal")["verified_email"])

    def test_inbox_url_rejects_credentials_query_and_injection(self):
        for inbox in ("http://mail.example.com", "https://user:secret@mail.example.com", "javascript:alert(1)",
                      "https://mail.example.com/?token=secret", "https://mail.example.com/\ncommand"):
            with self.assertRaises(app.UserError):
                app.validate_metadata(inbox=inbox)

    def test_claude_overlay_preserves_previous_user_statusline(self):
        profile = self.profile("claude")
        app.write_json(Path(profile["home"]) / "settings.json", {"statusLine": {"command": "existing-status"}})
        with patch.object(app, "binary", return_value="/bin/claude"):
            command, env = app.session_command(self.store, profile, ["--chrome"])
        self.assertEqual(command[-1], "--chrome")
        self.assertIn("statusline", command[2])
        self.assertEqual(env["AI_SUB_PREVIOUS_STATUSLINE"], "existing-status")
        self.assertEqual(json.loads((Path(profile["home"]) / "settings.json").read_text())["statusLine"]["command"], "existing-status")

    def test_claude_launch_copies_mcp_servers_from_default_config(self):
        profile = self.profile("claude")
        target = Path(profile["home"]) / ".claude.json"
        server = {"type": "http", "url": "https://example.test/mcp", "headers": {"Authorization": "Bearer new"}}
        app.write_json(app.BASE_CLAUDE_JSON, {
            "mcpServers": {"everywhere": {"type": "stdio", "command": "tool"}},
            "projects": {"/repo": {"mcpServers": {"agents": server}, "allowedTools": ["private"]},
                         "/empty": {"history": []}}})
        app.write_json(target, {"hasCompletedOnboarding": True, "projects": {"/repo": {
            "mcpServers": {"agents": dict(server, headers={"Authorization": "Bearer old"}),
                           "mine": {"type": "stdio", "command": "own"}}, "history": ["kept"]}}})
        with patch.object(app, "binary", return_value="/bin/claude"):
            app.session_command(self.store, profile, [])
        synced = json.loads(target.read_text())
        self.assertTrue(synced["hasCompletedOnboarding"])
        self.assertEqual(synced["mcpServers"], {"everywhere": {"type": "stdio", "command": "tool"}})
        self.assertEqual(synced["projects"]["/repo"]["mcpServers"]["agents"], server)
        self.assertEqual(synced["projects"]["/repo"]["mcpServers"]["mine"], {"type": "stdio", "command": "own"})
        self.assertEqual(synced["projects"]["/repo"]["history"], ["kept"])
        self.assertNotIn("allowedTools", synced["projects"]["/repo"])
        self.assertNotIn("/empty", synced["projects"])
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertFalse(app.sync_mcp_servers(profile))

    def test_mcp_sync_skips_uninitialised_or_unreadable_profiles(self):
        profile = self.profile("claude")
        target = Path(profile["home"]) / ".claude.json"
        app.write_json(app.BASE_CLAUDE_JSON, {"mcpServers": {"x": {"command": "x"}}})
        self.assertFalse(app.sync_mcp_servers(profile))
        self.assertFalse(target.exists())
        target.write_text("{not json")
        self.assertFalse(app.sync_mcp_servers(profile))
        self.assertEqual(target.read_text(), "{not json")

    def test_default_launch_arguments_do_not_become_profile_name(self):
        self.profile("claude")
        with patch.dict(os.environ, {"AI_SUB_HOME": str(self.store.home)}), \
             patch.object(app, "identity"), patch.object(app, "binary", return_value="/bin/claude"), \
             patch.object(app.os, "execvpe") as execute, contextlib.redirect_stderr(io.StringIO()):
            app.main(["run", "claude", "--", "--chrome"])
        self.assertEqual(execute.call_args.args[1][-1], "--chrome")
        self.assertEqual(execute.call_args.args[2]["AI_SUB_PROFILE"], "claude/personal")

    def test_default_launch_prompt_after_separator_is_not_an_account_name(self):
        self.profile("claude")
        prompt = "Continue the current task; do not deploy."
        with patch.dict(os.environ, {"AI_SUB_HOME": str(self.store.home)}), \
             patch.object(app, "identity"), patch.object(app, "binary", return_value="/bin/claude"), \
             patch.object(app.os, "execvpe") as execute, contextlib.redirect_stderr(io.StringIO()):
            app.main(["run", "claude", "--", prompt])
        self.assertEqual(execute.call_args.args[1][-1], prompt)
        self.assertEqual(execute.call_args.args[2]["AI_SUB_PROFILE"], "claude/personal")

    def test_rpc_handshake_filters_notifications_and_never_sends_model_requests(self):
        profile = self.profile()
        fixture = Path(self.temporary.name) / "fake-codex"
        trace = Path(self.temporary.name) / "trace.jsonl"
        fixture.write_text(f'''#!{sys.executable}
import sys,json
for line in sys.stdin:
    message=json.loads(line)
    with open({str(trace)!r},'a') as out: out.write(line)
    method=message.get('method')
    if method=='initialized': continue
    print(json.dumps({{'method':'notice','params':{{'ignored':'yes'}}}}),flush=True)
    result={{}}
    if method=='account/read': result={{'account':{{'type':'chatgpt','email':'test@example.com','planType':'plus'}}}}
    if method=='account/rateLimits/read': result={{'rateLimits':{{'primary':{{'usedPercent':22,'windowDurationMins':300,'resetsAt':2000000000}}}}}}
    print(json.dumps({{'id':message['id'],'result':result}}),flush=True)
''')
        fixture.chmod(0o755)
        with patch.object(app, "binary", return_value=str(fixture)):
            app.refresh(self.store, profile)
        self.assertEqual(self.store.latest(profile)["windows"][0]["used"], 22)
        methods = [json.loads(line)["method"] for line in trace.read_text().splitlines()]
        self.assertEqual(methods, ["initialize", "initialized", "account/read", "account/rateLimits/read"])

    def test_email_change_invalidates_cached_usage(self):
        profile = self.profile(email="old@example.com")
        self.store.save(profile, self.windows(), "test")
        with patch.dict(os.environ, {"AI_SUB_HOME": str(self.store.home)}), contextlib.redirect_stdout(io.StringIO()):
            app.main(["configure", "codex", "personal", "--email", "new@example.com"])
        self.assertEqual(app.freshness(self.store.latest(profile)), "unknown")

    def test_use_flags_chrome_extension_until_switch_is_recorded(self):
        self.store.add("claude", "work", email="work@example.com")
        self.store.add("claude", "home", email="home@example.com")

        def run(*argv):
            output = io.StringIO()
            with patch.dict(os.environ, {"AI_SUB_HOME": str(self.store.home)}), contextlib.redirect_stdout(output):
                app.main(list(argv))
            return output.getvalue()

        run("extension", "done", "claude", "work")
        self.assertNotIn("Claude in Chrome", run("use", "claude", "work"))
        self.assertIn("ai-sub extension done claude", run("use", "claude", "home"))
        [row] = json.loads(run("extension", "status", "claude"))
        self.assertEqual((row["recorded_profile"], row["needs_switch"]), ("claude/work", True))
        run("extension", "done", "claude")
        [row] = json.loads(run("extension", "status", "claude"))
        self.assertEqual((row["recorded_profile"], row["needs_switch"]), ("claude/home", False))

    def test_extension_open_targets_the_profiles_chrome_profile(self):
        self.store.add("claude", "work", email="work@example.com", chrome="Profile 1")
        self.store.add("codex", "work", email="work@example.com")
        with patch.dict(os.environ, {"AI_SUB_HOME": str(self.store.home)}), \
                patch.object(app, "chrome_open") as opened, contextlib.redirect_stdout(io.StringIO()):
            app.main(["extension", "open", "claude"])
            with self.assertRaises(app.UserError):
                app.main(["extension", "open", "codex"])
        opened.assert_called_once_with(app.EXTENSION_PAGES["claude"], "Profile 1")

    def test_extension_sign_in_is_tracked_per_chrome_profile(self):
        self.store.add("claude", "work", email="work@example.com", chrome="Profile 1")
        self.store.add("claude", "home", email="home@example.com", chrome="Profile 2")
        with patch.dict(os.environ, {"AI_SUB_HOME": str(self.store.home)}), contextlib.redirect_stdout(io.StringIO()):
            app.main(["extension", "done", "claude", "home"])
            app.main(["extension", "done", "claude", "work"])
        self.assertEqual(set(self.store.config()["extensions"]), {"claude@Profile 1", "claude@Profile 2"})

    def test_old_session_cannot_write_quota_after_account_email_changes(self):
        profile = self.profile("claude", email="old@example.com")
        with patch.dict(os.environ, {"AI_SUB_HOME": str(self.store.home)}), contextlib.redirect_stdout(io.StringIO()):
            app.main(["configure", "claude", "personal", "--email", "new@example.com"])
        profile = self.store.profile("claude", "personal")
        with patch.dict(os.environ, {"AI_SUB_PROFILE_GENERATION": "1"}):
            result = app.statusline(self.store, profile, json.dumps({"rate_limits": {
                "five_hour": {"used_percentage": 1, "resets_at": time.time()+3600}}}))
        self.assertIn("restart", result)
        self.assertEqual(app.freshness(self.store.latest(profile)), "unknown")

    def test_console_login_is_not_mistaken_for_a_subscription(self):
        profile = self.profile("claude")
        result = app.subprocess.CompletedProcess([], 0, json.dumps({"loggedIn": True,
            "authMethod": "claude.ai", "subscriptionType": None, "apiKeySource": "/login managed key"}), "")
        with patch.object(app, "binary", return_value="claude"), patch.object(app.subprocess, "run", return_value=result):
            with self.assertRaises(app.UserError):
                app.identity(self.store, profile)

    def test_login_rejects_wrong_profile_email_and_console_before_starting_oauth(self):
        profile = self.profile("claude", email="work@example.com")
        for extra in (["--email", "other@example.com"], ["--console"]):
            with self.subTest(extra=extra), patch.object(app, "binary", return_value="claude"), \
                 patch.object(app.subprocess, "run") as run:
                with self.assertRaises(app.UserError):
                    app.login(self.store, profile, extra)
                run.assert_not_called()
                self.assertFalse((self.store.home / "pending").exists())

    def test_failed_login_cleans_pending_attempt_without_verifying_identity(self):
        profile = self.profile("claude", email="work@example.com")
        with patch.object(app, "binary", return_value="claude"), \
             patch.object(app.subprocess, "run", return_value=app.subprocess.CompletedProcess([], 1)), \
             patch.object(app, "identity") as identity, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(app.UserError, "Login did not complete"):
                app.login(self.store, profile)
        identity.assert_not_called()
        self.assertFalse((self.store.home / "pending/claude-personal.json").exists())

    def test_login_email_is_verified_even_when_profile_had_no_expected_email(self):
        profile = self.profile("claude")
        results = [app.subprocess.CompletedProcess([], 0),
                   app.subprocess.CompletedProcess([], 0, json.dumps({"loggedIn": True,
                       "authMethod": "claude.ai", "subscriptionType": "max", "email": "wrong@example.com"}))]
        with patch.object(app, "binary", return_value="claude"), \
             patch.object(app.subprocess, "run", side_effect=results), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(app.UserError, "does not match"):
                app.login(self.store, profile, ["--email", "requested@example.com"])
        self.assertIsNone(self.store.profile("claude")["verified_email"])

    def test_logged_out_profile_error_contains_runnable_login_command(self):
        profile = self.profile("claude")
        result = app.subprocess.CompletedProcess([], 1, '{"loggedIn": false}')
        with patch.object(app, "binary", return_value="claude"), patch.object(app.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(app.UserError, "ai-sub login claude personal"):
                app.identity(self.store, profile)

    def test_claude_email_can_be_verified_from_native_text_status(self):
        profile = self.profile("claude", email="me@example.com")
        json_status = app.subprocess.CompletedProcess([], 0, json.dumps({"loggedIn": True,
            "authMethod": "claude.ai", "subscriptionType": "max"}), "")
        text_status = app.subprocess.CompletedProcess([], 0, "Email: me@example.com\n", "")
        with patch.object(app, "binary", return_value="claude"), \
             patch.object(app.subprocess, "run", side_effect=[json_status, text_status]):
            self.assertEqual(app.identity(self.store, profile)["email"], "me@example.com")


class InstallerTests(unittest.TestCase):
    def test_fresh_checkout_installs_runs_and_uninstalls_without_personal_paths(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            checkout = temp / "coworker checkout"
            shutil.copytree(source, checkout, symlinks=True,
                            ignore=shutil.ignore_patterns(".git", ".state", "__pycache__"))
            self.assertEqual(os.readlink(checkout / "bin/ai-sub"), "../scripts/ai_sub.py")
            self.assertEqual((checkout / "bin/ai-sub").resolve(), (checkout / "scripts/ai_sub.py").resolve())
            spec = importlib.util.spec_from_file_location("ai_sub_fresh_installer", checkout / "install.py")
            installer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(installer)
            fake_home = temp / "coworker-home"
            state = temp / "private-state"
            with patch.object(Path, "home", return_value=fake_home), \
                 patch.dict(os.environ, {"AI_SUB_HOME": str(state), "SHELL": "/bin/zsh", "ZDOTDIR": str(fake_home)}), \
                 contextlib.redirect_stdout(io.StringIO()):
                with patch.object(sys, "argv", [str(checkout / "install.py")]):
                    installer.main()
                    installer.main()
                for target, destination in installer.links():
                    self.assertEqual(target.resolve(), destination.resolve())
                self.assertEqual((fake_home / ".zshrc").read_text().count("# >>> ai-subscriptions native shell >>>"), 1)
                result = app.subprocess.run([str(fake_home / ".local/bin/ai-sub"), "doctor"],
                                            capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(str(state), result.stdout)
                with patch.object(sys, "argv", [str(checkout / "install.py"), "--uninstall"]):
                    installer.main()
                self.assertTrue(state.exists())
                self.assertTrue(all(not target.is_symlink() for target, _ in installer.links()))
                self.assertFalse((fake_home / ".zshrc").exists())

    def test_installed_sampler_runs_before_usage_expires(self):
        import plistlib
        spec = importlib.util.spec_from_file_location("ai_sub_installer", app.ROOT / "install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(Path, "home", return_value=Path(temporary)), \
             patch.object(installer.subprocess, "run", return_value=app.subprocess.CompletedProcess([], 0)) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            installer.background(app.Store(Path(temporary) / "state"))
            path = Path.home() / "Library/LaunchAgents" / (installer.LABEL + ".plist")
            config = plistlib.loads(path.read_bytes())
            self.assertEqual(config["StartInterval"], 240)
            self.assertLess(config["StartInterval"], app.FRESH_SECONDS)
            self.assertTrue(config["RunAtLoad"])
            self.assertEqual(run.call_args.args[0][1], "bootstrap")

    def test_repeated_install_and_conflict_preserve_existing_files(self):
        location = Path(__file__).resolve().parents[1] / "install.py"
        spec = importlib.util.spec_from_file_location("ai_sub_installer", location)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            source = root / "source"
            source.write_text("original")
            target = root / "bin/alias"
            installer.install_links([(target, source)])
            installer.install_links([(target, source)])
            self.assertEqual(target.read_text(), "original")
            occupied = root / "occupied"
            occupied.write_text("preserve")
            with self.assertRaises(app.UserError):
                installer.install_links([(occupied, source)])
            self.assertEqual(occupied.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
