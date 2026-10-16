#!/usr/bin/env python3
"""Install persistent terminal integration, local skills and optional usage tools."""
import argparse
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
from ai_sub import Store, UserError, SAMPLE_SECONDS

LABEL = "local.ai-subscriptions.monitor"


def links(project=False):
    if project:
        return [(ROOT.parent / ".agents/skills/ai-subscriptions", ROOT),
                (ROOT.parent / ".claude/skills/ai-subscriptions", ROOT)]
    return [(Path.home() / ".local/bin/ai-sub", ROOT / "scripts/ai_sub.py"),
            (Path.home() / ".agents/skills/ai-subscriptions", ROOT),
            (Path.home() / ".codex/skills/ai-subscriptions", ROOT),
            (Path.home() / ".claude/skills/ai-subscriptions", ROOT)]


def install_links(entries):
    for target, source in entries:
        if target.is_symlink() and target.resolve() == source.resolve():
            continue
        if target.exists() or target.is_symlink():
            raise UserError(f"Existing path was not replaced: {target}")
    for target, source in entries:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_symlink():
            target.symlink_to(source, target_is_directory=source.is_dir())
        print(f"Linked {target}")


def background(store, uninstall=False):
    target = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    domain = f"gui/{os.getuid()}"
    if target.exists():
        data = plistlib.loads(target.read_bytes())
        expected_script = str(ROOT / "scripts/sample.py")
        if expected_script not in data.get("ProgramArguments", []):
            raise UserError("A different monitor occupies the LaunchAgent path; left unchanged.")
        subprocess.run(["launchctl", "bootout", domain, str(target)], capture_output=True)
    if uninstall:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {"Label": LABEL, "ProgramArguments": [sys.executable, str(ROOT / "scripts/sample.py")],
            "EnvironmentVariables": {"AI_SUB_HOME": str(store.home.resolve()), "PATH": os.environ["PATH"]},
            "StartInterval": SAMPLE_SECONDS, "RunAtLoad": True, "ProcessType": "Background",
            "StandardOutPath": str(store.home / "monitor.log"),
            "StandardErrorPath": str(store.home / "monitor.log")}
    target.write_bytes(plistlib.dumps(data))
    target.chmod(0o600)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(target)], capture_output=True)
    if result.returncode:
        raise UserError("LaunchAgent file is ready but bootstrap failed. Run the installer in your logged-in Mac terminal.")
    print("Background sampling enabled every 4 minutes; observations expire after 5 minutes.")


def vscode_extension(uninstall=False):
    code = shutil.which("code")
    if not code:
        bundled = Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")
        code = str(bundled) if bundled.exists() else None
    if not code:
        raise UserError("VS Code was not found. Install it, then rerun install.py --vscode.")
    if uninstall:
        command = [code, "--uninstall-extension", "tylermeans.ai-subscriptions"]
    else:
        from build_vscode import build
        command = [code, "--install-extension", str(build(ROOT)), "--force"]
    result = subprocess.run(command)
    if result.returncode:
        raise UserError("VS Code extension setup failed. Rerun install.py --vscode in your regular terminal.")
    if not uninstall:
        print("VS Code usage indicator installed. Reload VS Code to show it at the bottom right.")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", action="store_true")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--vscode", action="store_true", help="Also install/remove the VS Code usage indicator")
    parser.add_argument("--no-native", action="store_true", help="Skip persistent claude/codex shell setup")
    parser.add_argument("--shell", choices=("zsh", "bash"), help="Shell to configure (default: your SHELL)")
    args = parser.parse_args()
    if args.project and args.background:
        raise UserError("Background installation needs user-level paths; omit --project in a normal terminal.")
    if args.shell and (args.project or args.no_native):
        raise UserError("Use --shell for user-level native setup, without --project or --no-native.")
    from shell_setup import configure as configure_shell, targets as shell_targets
    native_paths = shell_targets(args.shell) if not (args.project or args.no_native or args.uninstall) else None
    entries = links(args.project)
    if args.uninstall:
        if args.vscode:
            vscode_extension(uninstall=True)
        if not args.project:
            configure_shell(Store(), ROOT, uninstall=True)
            background(Store(), uninstall=True)
        for target, source in entries:
            if target.is_symlink() and target.resolve() == source.resolve():
                target.unlink()
                print(f"Removed {target}")
        return
    for name in ("ai_sub.py", "chrome_browser.py"):
        (ROOT / "scripts" / name).chmod(0o755)
    local = ROOT / "bin/ai-sub"
    install_links([(local, ROOT / "scripts/ai_sub.py")] + entries)
    store = Store()
    config = store.config()
    for provider in ("codex", "claude"):
        if f"{provider}/current" not in config["profiles"] and (Path.home() / f".{provider}").is_dir():
            store.add(provider, "current", current=True)
            print(f"Registered existing {provider} configuration as current; no credentials copied.")
    if native_paths is not None:
        configure_shell(store, ROOT, native_paths)
        print("New terminals will use ai-sub automatically when you type claude or codex.")
    if args.background:
        background(store)
    if args.vscode:
        vscode_extension()
    activation = "native.zsh" if native_paths is not None else "activate.zsh"
    print("For this already-open terminal only: source " + shlex.quote(str(ROOT / activation)))


if __name__ == "__main__":
    try:
        main()
    except (UserError, OSError) as exc:
        print(f"Install incomplete: {exc}", file=sys.stderr)
        sys.exit(1)
