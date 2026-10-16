"""Route ordinary interactive CLI starts through ai-sub without wrapping workers."""
from __future__ import annotations

import os
import sys

import ai_sub as app


COMMANDS = {
    "claude": {"agents", "attach", "auth", "auto-mode", "daemon", "doctor", "gateway",
               "help", "import", "install", "kill", "logs", "mcp", "plugin", "plugins",
               "purge", "remote-control", "respawn", "rm", "self-hosted-runner",
               "setup-token", "stop", "ultrareview", "update"},
    "codex": {"app", "app-server", "apply", "a", "cloud", "completion", "debug", "exec", "e",
              "features", "help", "login", "logout", "mcp", "mcp-server", "review", "sandbox"},
}


def passthrough(provider, extra):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return True
    if extra and extra[0] in COMMANDS[provider]:
        return True
    # Leave -- and its following prompt intact. Supported option values are not
    # flags, even if the user typed a value that resembles --help or --print.
    values = {"-m", "--model", "-s", "--sandbox", "-a", "--ask-for-approval",
              "--effort", "--permission-mode"}
    flags = {"--help", "-h", "--version", "-V", "-v"}
    if provider == "claude":
        flags |= {"--print", "-p"}
    skip = False
    for value in extra:
        if skip:
            skip = False
            continue
        if value == "--":
            break
        key = value.split("=", 1)[0]
        if key in flags:
            return True
        skip = value in values
    return False


def launch(provider, extra):
    if passthrough(provider, extra):
        # No Store, login checks, environment cleanup, or hooks for native admin
        # commands and headless workers. Shell functions are invisible to which().
        command = [app.binary(provider), *extra]
        os.execvpe(command[0], command, dict(os.environ))
        return 0
    try:
        return app.main(["run", provider, "--", *extra])
    except app.UserError as exc:
        raise app.UserError(f"{exc}\nShell integration is active. For an unmanaged native invocation, "
                            f"use command {provider} ... (no ai-sub account routing or handoffs).") from None
