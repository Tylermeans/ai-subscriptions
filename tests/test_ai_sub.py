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

    def profile(self, provider="codex", name="personal", email=None):
        return self.store.add(provider, name, email=email)

    def windows(self, short=20, week=40, reset=None):
        reset = reset or time.time() + 3600
        return [app.window("five_hour", short, reset, 300),
                app.window("seven_day", week, reset + 86400, 10080)]

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
        self.assertIn("25% used", result)
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
                 patch.dict(os.environ, {"AI_SUB_HOME": str(state)}), \
                 contextlib.redirect_stdout(io.StringIO()):
                with patch.object(sys, "argv", [str(checkout / "install.py")]):
                    installer.main()
                    installer.main()
                for target, destination in installer.links():
                    self.assertEqual(target.resolve(), destination.resolve())
                result = app.subprocess.run([str(fake_home / ".local/bin/ai-sub"), "doctor"],
                                            capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(str(state), result.stdout)
                with patch.object(sys, "argv", [str(checkout / "install.py"), "--uninstall"]):
                    installer.main()
                self.assertTrue(state.exists())
                self.assertTrue(all(not target.is_symlink() for target, _ in installer.links()))

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
