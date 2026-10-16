import contextlib
import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_sub as app
import shell_setup as setup


class ShellSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.store = app.Store(self.home / "state")
        self.root = app.ROOT
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def test_repeated_install_backs_up_once_and_uninstall_preserves_later_edits(self):
        rc = self.home / ".zshrc"
        original = b"# Existing customizations\nexport EXAMPLE=unchanged"  # No trailing newline.
        rc.write_bytes(original)
        rc.chmod(0o640)
        setup.configure(self.store, self.root, [rc])
        setup.configure(self.store, self.root, [rc])
        self.assertEqual(rc.read_bytes(), original + setup.block(self.root))
        self.assertEqual(stat.S_IMODE(rc.stat().st_mode), 0o640)
        backups = list(self.home.glob(".zshrc.ai-sub-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        later = b"\n# Added after installation\n"
        rc.write_bytes(rc.read_bytes() + later)
        setup.configure(self.store, self.root, uninstall=True)
        self.assertEqual(rc.read_bytes(), original + later)
        self.assertFalse((self.store.home / "native-shell.json").exists())

    def test_new_empty_rc_is_removed_and_existing_symlink_is_preserved(self):
        rc = self.home / ".zshrc"
        setup.configure(self.store, self.root, [rc])
        setup.configure(self.store, self.root, uninstall=True)
        self.assertFalse(rc.exists())
        target = self.home / "dotfiles/zshrc"
        target.parent.mkdir()
        target.write_bytes(b"# managed dotfiles\n")
        rc.symlink_to(target)
        setup.configure(self.store, self.root, [rc])
        self.assertTrue(rc.is_symlink())
        setup.configure(self.store, self.root, uninstall=True)
        self.assertTrue(rc.is_symlink())
        self.assertEqual(target.read_bytes(), b"# managed dotfiles\n")

    def test_conflicting_or_edited_blocks_leave_all_profiles_untouched(self):
        first, second = self.home / ".bashrc", self.home / ".bash_profile"
        first.write_bytes(b"# preserve\n")
        for content in (setup.block(self.home / "another checkout"), setup.START + b"\n# incomplete"):
            second.write_bytes(content)
            with self.assertRaises(app.UserError):
                setup.configure(self.store, self.root, [first, second])
            self.assertEqual(first.read_bytes(), b"# preserve\n")
            self.assertEqual(second.read_bytes(), content)
        self.assertFalse((self.store.home / "native-shell.json").exists())

    def test_zdotdir_and_existing_bash_login_precedence(self):
        with patch.object(Path, "home", return_value=self.home), \
             patch.dict(os.environ, {"ZDOTDIR": str(self.home / "zsh-dir")}):
            self.assertEqual(setup.targets("zsh"), [self.home / "zsh-dir/.zshrc"])
            profile = self.home / ".profile"
            profile.touch()
            self.assertEqual(setup.targets("bash"), [self.home / ".bashrc", profile])
            login = self.home / ".bash_login"
            login.touch()
            self.assertEqual(setup.targets("bash"), [self.home / ".bashrc", login])
            with self.assertRaises(app.UserError):
                setup.targets("fish")

    def test_new_interactive_terminals_load_functions_without_manual_source(self):
        for shell in ("zsh", "bash"):
            binary = shutil.which(shell)
            if not binary:
                continue
            with self.subTest(shell=shell), patch.object(Path, "home", return_value=self.home), \
                 patch.dict(os.environ, {"ZDOTDIR": str(self.home)}):
                setup.configure(self.store, self.root, setup.targets(shell))
                args = (["-d", "-i", "-c"] if shell == "zsh" else ["--noprofile", "-i", "-c"])
                env = {**os.environ, "HOME": str(self.home), "ZDOTDIR": str(self.home),
                       "AI_SUB_HOME": str(self.store.home)}
                env.pop("_AI_SUB_NATIVE_ENABLED", None)
                result = subprocess.run([binary, *args, "type claude; type codex"], env=env,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("claude is a", result.stdout)
                self.assertIn("codex is a", result.stdout)
                self.assertEqual(result.stdout.count("function"), 2)

    def test_noninteractive_profile_read_does_not_load_functions(self):
        rc = self.home / ".profile"
        setup.configure(self.store, self.root, [rc])
        for shell in ("bash", "zsh", "sh"):
            binary = shutil.which(shell)
            if not binary:
                continue
            with self.subTest(shell=shell):
                result = subprocess.run([binary, "-c", '. "$1"; test "${_AI_SUB_NATIVE_ENABLED:-}" != 1',
                                         "test-shell", str(rc)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_shell_block_quotes_checkout_paths_and_missing_checkout_is_quiet(self):
        # A path that could execute a command if it were not shell-quoted.
        root = self.home / "missing $(touch INJECTED) 'checkout'"
        rc = self.home / ".bashrc"
        setup.configure(self.store, root, [rc])
        result = subprocess.run(["bash", "--noprofile", "--rcfile", str(rc), "-i", "-c", "true"],
                                cwd=self.home, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / "INJECTED").exists())


if __name__ == "__main__":
    unittest.main()
