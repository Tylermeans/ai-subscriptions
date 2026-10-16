"""Persistent, reversible setup for interactive zsh and bash terminals."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import shlex
import stat
import tempfile
import uuid

from ai_sub import UserError, read_json, write_json

START = b"# >>> ai-subscriptions native shell >>>"
END = b"# <<< ai-subscriptions native shell <<<"


def targets(shell=None):
    shell = shell or Path(os.environ.get("SHELL") or "/bin/zsh").name
    home = Path.home()
    if shell == "zsh":
        return [Path(os.environ.get("ZDOTDIR") or home).expanduser().absolute() / ".zshrc"]
    if shell == "bash":
        # Login bash reads the first existing file in this order; non-login
        # interactive bash reads .bashrc. Do not hide an existing .profile.
        candidates = [home / name for name in (".bash_profile", ".bash_login", ".profile")]
        login = next((p for p in candidates if p.exists() or p.is_symlink()), candidates[0])
        return [home / ".bashrc", login]
    raise UserError("Persistent shell setup supports zsh and bash. Use --shell zsh/--shell bash, "
                    "or --no-native to install only the explicit ai-sub launcher.")


def block(root):
    source = shlex.quote(str(root.resolve() / "native.zsh"))
    # Also safe when bash's .profile is read by a different POSIX shell. An
    # interactive-shell guard keeps noninteractive login workers untouched.
    return (b"\n" + START + b"\n" + (
        'case "$-" in\n'
        '  *i*)\n'
        '    if [ -n "${ZSH_VERSION:-}${BASH_VERSION:-}" ] && '
        f'[ -r {source} ]; then\n'
        f'      . {source}\n'
        '    fi\n'
        '    ;;\n'
        'esac\n').encode() + END + b"\n")


def read_file(path):
    if path.is_symlink() and not path.exists():
        raise UserError(f"Broken shell configuration symlink was left unchanged: {path}")
    target = path.resolve()
    if target.exists() and not target.is_file():
        raise UserError(f"Shell configuration is not a regular file: {path}")
    return target, target.read_bytes() if target.exists() else b""


def replace_file(target, before, after, backup=False):
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    if backup and target.exists():
        saved = target.with_name(target.name + ".ai-sub-backup-" + uuid.uuid4().hex[:12])
        fd = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(before)
        print(f"Shell configuration backup: {saved}")
    fd, temporary = tempfile.mkstemp(prefix=".ai-sub-shell-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        if (target.read_bytes() if target.exists() else b"") != before:
            raise UserError(f"Shell configuration changed during setup; retry the installer: {target}")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def configure(store, root, paths=None, uninstall=False):
    receipt_path = store.home / "native-shell.json"
    own_block = block(root)
    lock = os.open(store.home / "native-shell.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        receipt = read_json(receipt_path, {"root": str(root.resolve()), "paths": [], "created": []})
        if receipt["root"] != str(root.resolve()):
            raise UserError("This shell setup belongs to a different checkout; its configuration was left unchanged.")
        selected = [Path(p) for p in receipt["paths"]] if uninstall else paths
        plans = []
        for path in selected:
            target, before = read_file(path)
            count = before.count(own_block)
            if count > 1 or before.count(START) != count or before.count(END) != count:
                raise UserError(f"An edited or different ai-sub shell block was left unchanged: {path}")
            after = before.replace(own_block, b"", 1) if uninstall else before if count else before + own_block
            plans.append((path, target, before, after))
        if not uninstall:
            # Record ownership first, so a failed/interrupted setup is removable.
            receipt["paths"] = sorted(set(receipt["paths"]) | {str(p) for p in selected})
            receipt["created"] = sorted(set(receipt.get("created", [])) |
                                        {str(p) for p, target, _, _ in plans if not target.exists()})
            write_json(receipt_path, receipt)
        # Check every file before changing any, so a conflicting login file
        # cannot leave bash configured only for one type of terminal.
        for path, target, before, after in plans:
            if before != after:
                replace_file(target, before, after, backup=not uninstall)
                if uninstall and not after and str(path) in receipt.get("created", []) and not path.is_symlink():
                    target.unlink()
            print(f'{"Removed" if uninstall else "Enabled"} persistent CLI setup: {path}')
        if uninstall:
            receipt_path.unlink(missing_ok=True)
