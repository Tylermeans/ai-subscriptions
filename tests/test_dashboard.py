import contextlib
import json
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_sub as app
import build_vscode
import dashboard


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = app.Store(Path(self.temp.name) / "state")
        self.codex = self.store.add("codex", "work", email="work@example.com")
        self.claude = self.store.add("claude", "personal", email="personal@example.com")

    def test_weekly_only_and_missing_data_are_not_fabricated(self):
        self.store.save(self.codex, [app.window("codex/primary", 4, time.time() + 10000, 10080)], "fixture")
        with patch.object(app, "refresh") as refresh:
            data = dashboard.snapshot(self.store)
            refresh.assert_not_called()
        self.assertEqual(len(data["profiles"][0]["sample"]["windows"]), 1)
        self.assertEqual(data["profiles"][0]["sample"]["windows"][0]["used"], 4)
        self.assertIn("Both short-window and weekly", data["profiles"][0]["capacity"])
        self.assertIsNone(data["profiles"][1]["sample"])
        self.assertEqual(data["profiles"][1]["freshness"], "unknown")

    def test_chart_payload_excludes_home_inbox_credentials_and_task_context(self):
        with self.store.edit() as config:
            profile = config["profiles"]["claude/personal"]
            profile.update(inbox="https://mail.example.com", password="never-export-this", token="token-secret",
                           task_notes="private task text", chrome_profile="Profile 10")
        encoded = json.dumps(dashboard.snapshot(self.store))
        for secret in ("never-export-this", "token-secret", "private task text", "Profile 10", "mail.example.com", self.claude["home"]):
            self.assertNotIn(secret, encoded)

    def test_stale_and_backend_block_are_reported(self):
        self.store.save(self.codex, [app.window("week", 4, time.time() + 10000, 10080)], "fixture",
                        observed=time.time() - 301, blocked=True)
        profile = dashboard.snapshot(self.store)["profiles"][0]
        self.assertEqual(profile["freshness"], "stale")
        self.assertIn("block", profile["capacity"])

    def test_refreshing_display_does_not_make_old_usage_fresh(self):
        observed = time.time() - 301
        self.store.save(self.claude, [app.window("week", 95, time.time() + 10000, 10080)],
                        "claude-statusline", observed=observed)
        data = dashboard.snapshot(self.store)
        profile = data["profiles"][1]
        self.assertGreater(data["generated_at"], observed + 300)
        self.assertEqual(profile["freshness"], "stale")
        self.assertEqual(profile["sample"]["observed"], observed)
        self.assertEqual(profile["sample"]["windows"][0]["used"], 95)
        self.assertIn("Fresh usage needed", profile["capacity"])

    def test_snapshot_html_escapes_embedded_data_and_has_no_remote_resources(self):
        data = dashboard.snapshot(self.store)
        data["profiles"][0]["email"] = '</script><img src="https://evil.example" onerror="alert(1)">'
        html = dashboard.render(data)
        self.assertNotIn('</script><img', html)
        self.assertIn('\\u003c/script>', html)
        self.assertIn("default-src 'none'", html)
        self.assertNotIn("__AI_SUB_", html)
        self.assertNotIn('<script src=', html)
        self.assertNotIn('<link ', html)

    def test_chart_file_stays_private(self):
        directory = app.private_dir(self.store.home / "dashboard")
        path = directory / "index.html"
        dashboard.save_chart(path, dashboard.render(dashboard.snapshot(self.store)))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

    def test_extension_bundle_contains_only_allowlisted_source(self):
        # Build from a staging copy so no local account data can enter the package.
        import shutil
        root = Path(self.temp.name) / "package"
        shutil.copytree(app.ROOT / "vscode", root / "vscode")
        (root / "vscode" / "unexpected-secret.txt").write_text("do not bundle")
        result = build_vscode.build(root)
        with zipfile.ZipFile(result) as archive:
            self.assertEqual(set(archive.namelist()), {"extension.vsixmanifest", "[Content_Types].xml",
                             *["extension/" + name for name in build_vscode.FILES]})
            self.assertNotIn(b"do not bundle", b"".join(archive.read(name) for name in archive.namelist()))


if __name__ == "__main__":
    unittest.main()
