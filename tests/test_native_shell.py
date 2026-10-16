import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_sub as app
import handoff
import native_shell as native

ROOT = Path(__file__).resolve().parents[1]


class NativeShellTests(unittest.TestCase):
    def test_interactive_command_uses_selected_profile_and_keeps_prompt_as_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = app.Store(Path(directory) / "private")
            profile = store.add("claude", "work", email="work@example.com")
            with store.edit() as config:
                config["automatic"] = {"claude": {"enabled": True, "threshold": 90}}
            with patch.dict(os.environ, {"AI_SUB_HOME": str(store.home)}), \
                 patch.object(sys.stdin, "isatty", return_value=True), \
                 patch.object(sys.stdout, "isatty", return_value=True), \
                 patch.object(handoff, "supervise", return_value=23) as supervise:
                result = app.main(["native", "claude", "--", "Fix the current bug", "--permission-mode", "auto"])
            self.assertEqual(result, 23)
            self.assertEqual(supervise.call_args.args[1], profile)
            self.assertEqual(supervise.call_args.args[2], ["Fix the current bug", "--permission-mode", "auto"])

    def test_headless_and_admin_commands_keep_native_environment_and_never_open_store(self):
        cases = [("claude", ["-p", "Foreman worker prompt"]),
                 ("claude", ["--print", "--output-format", "stream-json", "task"]),
                 ("claude", ["auth", "status", "--json"]),
                 ("claude", ["--version"]),
                 ("codex", ["exec", "--json", "task"]),
                 ("codex", ["mcp-server"]), ("codex", ["--help"])]
        with patch.object(sys.stdin, "isatty", return_value=True), \
             patch.object(sys.stdout, "isatty", return_value=True), \
             patch.object(app, "Store", side_effect=AssertionError("must not open registry")), \
             patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/worker/config", "ANTHROPIC_API_KEY": "synthetic-fixture"}):
            for provider, extra in cases:
                with self.subTest(provider=provider, extra=extra), \
                     patch.object(app, "binary", return_value="/native/provider"), \
                     patch.object(os, "execvpe") as execute:
                    native.launch(provider, extra)
                    self.assertEqual(execute.call_args.args[1], ["/native/provider", *extra])
                    self.assertEqual(execute.call_args.args[2], dict(os.environ))

    def test_redirected_streams_do_not_wrap_a_worker(self):
        for stdin, stdout in ((False, True), (True, False), (False, False)):
            with self.subTest(stdin=stdin, stdout=stdout), \
                 patch.object(sys.stdin, "isatty", return_value=stdin), \
                 patch.object(sys.stdout, "isatty", return_value=stdout):
                self.assertTrue(native.passthrough("claude", ["worker task"]))
                self.assertTrue(native.passthrough("codex", []))

    def test_prompt_separator_and_option_values_are_not_passthrough_flags(self):
        with patch.object(sys.stdin, "isatty", return_value=True), \
             patch.object(sys.stdout, "isatty", return_value=True):
            for extra in (["--", "--print"], ["--model", "--help"],
                          ["Fix auth and --print handling"], ["--permission-mode", "auto"]):
                self.assertFalse(native.passthrough("claude", extra))
            self.assertTrue(native.passthrough("claude", ["--model", "sonnet", "-p", "task"]))

    def test_unsupported_interactive_modes_fail_visibly_instead_of_silently_bypassing(self):
        with patch.object(sys.stdin, "isatty", return_value=True), \
             patch.object(sys.stdout, "isatty", return_value=True), \
             patch.object(app, "main", side_effect=app.UserError("Unsupported resume")), \
             patch.object(os, "execvpe") as execute:
            with self.assertRaisesRegex(app.UserError, "command claude"):
                native.launch("claude", ["--resume", "session-id"])
            execute.assert_not_called()

    def test_shell_functions_preserve_arguments_status_and_existing_customizations(self):
        for shell in ("zsh", "bash"):
            binary = shutil.which(shell)
            if not binary:
                continue
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                fixture = directory / "ai-sub"
                fixture.write_text(f"#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(23)\n")
                fixture.chmod(0o755)
                env = {**os.environ, "PATH": os.pathsep.join([str(directory), str(ROOT / "bin"), os.environ["PATH"]])}
                env.pop("_AI_SUB_NATIVE_ENABLED", None)
                # Source twice, verify quoted arguments survive, and propagate
                # the launcher's exit code without evaluating shell metacharacters.
                source = f"source {shlex.quote(str(ROOT / 'native.zsh'))}"
                prompt = "Fix this; $(echo must-not-run) 'quotes'\nnext line"
                result = subprocess.run([binary, "-c", source + "\n" + source + "\nclaude " + shlex.quote(prompt)],
                                        env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 23, result.stderr)
                self.assertEqual(json.loads(result.stdout), ["native", "claude", "--", prompt])
                result = subprocess.run([binary, "-c", "alias claude='echo existing'\n" + source + "\nalias claude"],
                                        env=env, text=True, capture_output=True)
                self.assertIn("existing", result.stdout)
                self.assertIn("left unchanged", result.stderr)

    def test_shell_functions_are_not_inherited_by_worker_shells(self):
        for shell in ("zsh", "bash"):
            binary = shutil.which(shell)
            if not binary:
                continue
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                fixture = directory / "claude"
                fixture.write_text("#!/bin/sh\nprintf '%s\\n' native-worker\n")
                fixture.chmod(0o755)
                env = {**os.environ, "PATH": str(directory) + os.pathsep + os.environ["PATH"]}
                env.pop("_AI_SUB_NATIVE_ENABLED", None)
                script = f"source {shlex.quote(str(ROOT / 'native.zsh'))}\n{shlex.quote(binary)} -c claude"
                result = subprocess.run([binary, "-c", script], env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "native-worker")

    def test_real_entrypoint_passes_headless_call_without_initializing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            fixture = directory / "claude"
            fixture.write_text(f"#!{sys.executable}\nimport json,os,sys\n"
                               "print(json.dumps([sys.argv[1:], os.environ.get('CLAUDE_CONFIG_DIR')]))\n")
            fixture.chmod(0o755)
            state = directory / "unused-state"
            env = {**os.environ, "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                   "AI_SUB_HOME": str(state), "CLAUDE_CONFIG_DIR": "/worker/config"}
            result = subprocess.run([sys.executable, str(ROOT / "scripts/ai_sub.py"),
                                     "native", "claude", "--", "-p", "worker task"],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [["-p", "worker task"], "/worker/config"])
            self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
