#!/usr/bin/env python3
"""Subscription profiles and quota observations. Python standard library only."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = ("codex", "claude")
FRESH_SECONDS = 300
SAMPLE_SECONDS = 240
AUTH_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN",
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR")
ROUTING_ENV = ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
               "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


class UserError(Exception):
    pass


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def write_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def safe_label(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", value):
        raise UserError("Names must be 1–40 lowercase letters, digits, underscores or hyphens.")
    return value


def safe_url(value):
    url = urlparse(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or any(ord(c) < 32 for c in value)):
        raise UserError("Use an HTTPS URL without embedded credentials.")
    return value


def clean_text(value):
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", str(value))[:250]


def validate_metadata(email=None, inbox=None, chrome=None):
    if email and (not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
                  or any(ord(c) < 32 for c in email)):
        raise UserError("Enter an email address, not a password or token.")
    if inbox:
        safe_url(inbox)
        if urlparse(inbox).query or urlparse(inbox).fragment:
            raise UserError("Store the inbox landing page, without query parameters or fragments.")
    if chrome and not re.fullmatch(r"[A-Za-z0-9 _-]{1,80}", chrome):
        raise UserError("Use a Chrome profile directory such as Default or Profile 1.")


class Store:
    def __init__(self, home=None):
        self.home = private_dir(home or os.environ.get("AI_SUB_HOME", ROOT / ".state"))
        self.config_path = self.home / "accounts.json"

    @contextlib.contextmanager
    def edit(self):
        lock = os.open(self.home / "accounts.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            config = self.config()
            yield config
            write_json(self.config_path, config)

    def config(self):
        return read_json(self.config_path, {"version": 1, "profiles": {}, "defaults": {}})

    def profile(self, provider, name=None):
        config = self.config()
        name = name or config["defaults"].get(provider)
        if not name or f"{provider}/{name}" not in config["profiles"]:
            raise UserError(f"No such profile. Run: ai-sub add {provider} NAME --email ADDRESS")
        return config["profiles"][f"{provider}/{name}"]

    def add(self, provider, name, email=None, inbox=None, chrome=None, current=False):
        safe_label(name)
        validate_metadata(email, inbox, chrome)
        key = f"{provider}/{name}"
        with self.edit() as config:
            if key in config["profiles"]:
                raise UserError("Profile already exists; use configure to change its metadata.")
            if current:
                env_key = "CODEX_HOME" if provider == "codex" else "CLAUDE_CONFIG_DIR"
                home = Path(os.environ.get(env_key, Path.home() / f".{provider}")).expanduser().resolve()
                if not home.is_dir():
                    raise UserError("The current CLI configuration directory does not exist.")
            else:
                home = (self.home / "profiles" / provider / name).resolve()
            if any(Path(p["home"]).resolve() == home for p in config["profiles"].values()):
                raise UserError("This CLI home is already registered; use that profile.")
            if not current:
                private_dir(home)
                skills = private_dir(home / "skills")
                (skills / "ai-subscriptions").symlink_to(ROOT, target_is_directory=True)
                if provider == "codex":
                    (home / "config.toml").write_text(
                        'cli_auth_credentials_store = "keyring"\nforced_login_method = "chatgpt"\n')
                    (home / "config.toml").chmod(0o600)
            profile = {"provider": provider, "name": name, "home": str(home),
                       "current": current, "email": email, "inbox": inbox,
                       "chrome_profile": chrome, "verified_email": None, "generation": 1,
                       "explicit_home": (env_key in os.environ) if current else True}
            config["profiles"][key] = profile
            config["defaults"].setdefault(provider, name)
        return profile

    def db(self):
        path = self.home / "usage.sqlite3"
        db = sqlite3.connect(path, timeout=10)
        path.chmod(0o600)
        db.execute("CREATE TABLE IF NOT EXISTS samples (id INTEGER PRIMARY KEY, "
                   "profile TEXT NOT NULL, observed REAL NOT NULL, source TEXT NOT NULL, "
                   "windows TEXT NOT NULL, blocked INTEGER, error TEXT)")
        db.execute("CREATE INDEX IF NOT EXISTS by_profile_time ON samples(profile, observed)")
        return db

    def save(self, profile, windows, source, blocked=None, error=None, observed=None):
        # Only normalized quota measurements go to disk. Never save raw CLI/statusline payloads.
        with contextlib.closing(self.db()) as db, db:
            db.execute("INSERT INTO samples(profile,observed,source,windows,blocked,error) "
                       "VALUES (?,?,?,?,?,?)", (profile_key(profile), observed or time.time(),
                       source, json.dumps(windows), blocked, error))
            db.execute("DELETE FROM samples WHERE observed < ?", (time.time() - 90 * 86400,))

    def latest(self, profile):
        with contextlib.closing(self.db()) as db:
            row = db.execute("SELECT observed,source,windows,blocked,error FROM samples "
                             "WHERE profile=? ORDER BY observed DESC,id DESC LIMIT 1",
                             (profile_key(profile),)).fetchone()
        if row is None:
            return None
        return dict(zip(("observed", "source", "windows", "blocked", "error"),
                        (row[0], row[1], json.loads(row[2]), row[3], row[4])))


def profile_key(profile):
    return f'{profile["provider"]}/{profile["name"]}'


def cli_env(store, profile):
    env = dict(os.environ)
    for key in AUTH_ENV + ROUTING_ENV:
        env.pop(key, None)
    for key in ("AI_SUB_RUN_DIR", "AI_SUB_RUN_ATTEMPT"):
        env.pop(key, None)
    env["AI_SUB_HOME"] = str(store.home.resolve())
    env["AI_SUB_PROFILE"] = profile_key(profile)
    env["AI_SUB_PROFILE_GENERATION"] = str(profile.get("generation", 1))
    if profile["provider"] == "codex":
        env["CODEX_HOME"] = profile["home"]
    elif (profile["current"] and not profile.get("explicit_home")
          and Path(profile["home"]) == Path.home() / ".claude"):
        # Unset uses Claude's existing default Keychain entry; explicitly setting even
        # ~/.claude may select a different, directory-keyed credential entry.
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = profile["home"]
    env["BROWSER"] = str(ROOT / "scripts" / "chrome_browser.py")
    if profile.get("chrome_profile"):
        env["AI_SUB_CHROME_PROFILE"] = profile["chrome_profile"]
    else:
        env.pop("AI_SUB_CHROME_PROFILE", None)
    return env


def binary(provider):
    path = shutil.which(provider)
    if not path:
        raise UserError(f"{provider} is not installed or is missing from PATH.")
    return path


class CodexRPC:
    """A short-lived app-server connection; never starts a model turn."""
    def __init__(self, store, profile, config_args=(), experimental=False):
        self.experimental = experimental
        self.messages = queue.Queue()
        self.counter = 0
        self.process = subprocess.Popen([binary("codex"), *config_args, "app-server", "--stdio"],
            env=cli_env(store, profile), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                continue
        self.messages.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params=None, timeout=25):
        self.counter += 1
        identifier = self.counter
        self.send({"id": identifier, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if message is None:
                raise UserError("Codex app-server stopped. Check CLI login and filesystem access.")
            if message.get("method") and "id" in message:
                self.send({"id": message["id"], "error": {"code": -32601,
                           "message": "This usage reader does not support interactive requests."}})
                continue
            if message.get("id") == identifier:
                if "error" in message:
                    # Provider error strings can contain account/session material.
                    code = message["error"].get("code", "unknown")
                    raise UserError(f"Codex {method} failed (code {clean_text(code)}). Check login/network.")
                return message.get("result", {})
        raise UserError("Codex usage request timed out; no quota estimate was recorded.")

    def __enter__(self):
        try:
            params = {"clientInfo": {"name": "ai_subscriptions", "version": "1.0.0"}}
            if self.experimental:
                params["capabilities"] = {"experimentalApi": True}
            self.request("initialize", params)
            self.send({"method": "initialized", "params": {}})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


def identity(store, profile, rpc=None):
    if profile["provider"] == "codex":
        if rpc is None:
            with CodexRPC(store, profile) as connection:
                return identity(store, profile, connection)
        account = rpc.request("account/read", {"refreshToken": False}).get("account") or {}
        if account.get("type") != "chatgpt":
            raise UserError("This profile needs a ChatGPT subscription login. Run ai-sub login.")
        result = {"email": account.get("email"), "plan": account.get("planType")}
    else:
        proc = subprocess.run([binary("claude"), "auth", "status", "--json"],
            env=cli_env(store, profile), capture_output=True, text=True, timeout=25)
        try:
            account = json.loads(proc.stdout)
        except ValueError:
            raise UserError("Claude auth status is unavailable; run ai-sub login.") from None
        if (proc.returncode or not account.get("loggedIn") or account.get("authMethod") != "claude.ai"
                or not account.get("subscriptionType")
                or account.get("apiProvider", "firstParty") != "firstParty"):
            raise UserError("This profile needs a Claude subscription login. Run ai-sub login.")
        result = {"email": account.get("email"), "plan": account.get("subscriptionType")}
        if not result["email"]:
            # Some releases expose email only in the human-readable status.
            status = subprocess.run([binary("claude"), "auth", "status", "--text"],
                env=cli_env(store, profile), capture_output=True, text=True, timeout=25)
            found = re.search(r"(?im)^\s*(?:Email|Account):\s*([^\s@]+@[^\s@]+\.[^\s@]+)\s*$", status.stdout)
            if status.returncode == 0 and found:
                result["email"] = found.group(1)
    expected = profile.get("email") or profile.get("verified_email")
    if expected and not result["email"]:
        raise UserError("The CLI did not report its account email, so the selected identity could not be verified.")
    if expected and (result["email"] or "").casefold() != expected.casefold():
        raise UserError("The signed-in email does not match this profile. Reauthenticate the correct account.")
    with store.edit() as config:
        p = config["profiles"][profile_key(profile)]
        p["verified_email"] = result["email"]
        p["verified_at"] = time.time()
    return result


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def window(label, used, reset, minutes):
    if not number(used) or not 0 <= used <= 100:
        return None
    return {"label": clean_text(label), "used": float(used),
            "reset": float(reset) if number(reset) and reset > 0 else None,
            "minutes": float(minutes) if number(minutes) and minutes > 0 else None}


def codex_windows(payload):
    buckets = payload.get("rateLimitsByLimitId")
    if not buckets:
        fallback = payload.get("rateLimits") or {}
        buckets = {fallback.get("limitId") or "codex": fallback}
    windows = []
    blocked = payload.get("ordinaryUsageAllowed") is False
    for bucket, limits in buckets.items():
        if not isinstance(limits, dict):
            continue
        blocked |= bool(limits.get("rateLimitReachedType") or limits.get("spendControlReached"))
        for label in ("primary", "secondary"):
            value = limits.get(label) or {}
            normalized = window(f"{bucket}/{label}", value.get("usedPercent"),
                                value.get("resetsAt"), value.get("windowDurationMins"))
            if normalized:
                windows.append(normalized)
        spend = limits.get("individualLimit") or {}
        if number(spend.get("remainingPercent")):
            normalized = window(f"{bucket}/spend", 100 - spend["remainingPercent"],
                                spend.get("resetsAt"), None)
            if normalized:
                windows.append(normalized)
    # Missing backend permission is unknown, not affirmative recovery from a block.
    return windows, True if blocked else (False if payload.get("ordinaryUsageAllowed") is True else None)


def claude_windows(payload):
    windows = []
    for label, minutes in (("five_hour", 300), ("seven_day", 10080), ("spend_limit", None)):
        value = (payload.get("rate_limits") or {}).get(label) or {}
        normalized = window(label, value.get("used_percentage"), value.get("resets_at"), minutes)
        if normalized:
            windows.append(normalized)
    return windows


def refresh(store, profile):
    if profile["provider"] == "claude":
        return  # Official status-line events supply observations during active Claude sessions.
    try:
        with CodexRPC(store, profile) as rpc:
            identity(store, profile, rpc)
            windows, blocked = codex_windows(rpc.request("account/rateLimits/read"))
        previous = store.latest(profile)
        if blocked is None and previous and previous.get("blocked"):
            blocked = True
        store.save(profile, windows, "codex-app-server", blocked,
                   None if windows else "Provider returned no quota windows.")
    except (UserError, OSError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, UserError) else "CLI/network/filesystem check failed."
        previous = store.latest(profile)
        store.save(profile, [], "codex-app-server", blocked=previous.get("blocked") if previous else None,
                   error=message)


def statusline(store, profile, raw):
    generation = os.environ.get("AI_SUB_PROFILE_GENERATION")
    if generation is not None and generation != str(profile.get("generation", 1)):
        return profile_key(profile) + " | profile changed; restart this session to collect quota"
    try:
        payload = json.loads(raw)
        windows = claude_windows(payload)
        previous = store.latest(profile)
        # Limit log frequency while retaining changes, including windows disappearing.
        if not previous or windows != previous["windows"] or time.time() - previous["observed"] >= 60:
            store.save(profile, windows, "claude-statusline",
                       error=None if windows else "No subscription quota in latest Claude status-line event.")
        parts = [f'{w["label"]}: {w["used"]:g}% used' for w in windows]
        return " | ".join([profile_key(profile)] + parts) if parts else profile_key(profile) + " | quota unavailable"
    except (ValueError, TypeError, AttributeError):
        return profile_key(profile) + " | invalid status-line input"


def freshness(sample, now=None):
    now = now or time.time()
    if not sample or sample.get("error") or not sample.get("windows"):
        return "unknown"
    if now - sample["observed"] > FRESH_SECONDS or sample["observed"] > now + 60:
        return "stale"
    if any(w["reset"] is None or w["reset"] <= now for w in sample["windows"]):
        return "refresh needed"
    return "fresh"


def reset_text(timestamp):
    if timestamp is None:
        return "unknown"
    return dt.datetime.fromtimestamp(timestamp).astimezone().strftime("%a %b %d %H:%M %Z")


def duration_text(minutes):
    if minutes is None:
        return "window"
    if minutes % 1440 == 0:
        return f"{minutes / 1440:g}d"
    if minutes % 60 == 0:
        return f"{minutes / 60:g}h"
    return f"{minutes:g}m"


def report(store, profiles, as_json=False):
    rows = []
    for profile in profiles:
        sample = store.latest(profile)
        rows.append({"profile": profile_key(profile), "email": profile.get("email") or profile.get("verified_email"),
                     "freshness": freshness(sample), "sample": sample})
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    for row in rows:
        sample = row["sample"]
        print(f'{row["profile"]}: {row["freshness"]}')
        if sample:
            print(f'  observed {reset_text(sample["observed"])} via {sample["source"]}')
            if sample.get("error"):
                print("  " + sample["error"])
            if sample.get("blocked"):
                print("  Provider reports a quota or spending block.")
            for w in sample["windows"]:
                print(f'  {w["label"]} ({duration_text(w["minutes"])}): {w["used"]:g}% used; resets {reset_text(w["reset"])}')
        elif row["profile"].startswith("claude/"):
            print("  Launch through ai-sub run; Claude reports quotas after the first response.")
        else:
            print("  Run ai-sub status --refresh after login.")
    if not rows:
        print("No profiles. Run ai-sub add codex personal --email you@example.com")


def recommend(store, profiles):
    candidates = []
    for profile in profiles:
        sample = store.latest(profile)
        if freshness(sample) != "fresh" or sample.get("blocked"):
            continue
        windows = sample["windows"]
        # Require both the short and weekly windows; missing data is not spare quota.
        if not any(w["minutes"] == 10080 for w in windows):
            continue
        if not any(w["minutes"] and w["minutes"] < 10080 for w in windows):
            continue
        available = min(100 - w["used"] for w in windows)
        if available > 0:
            candidates.append((available, profile_key(profile), profile))
    if not candidates:
        raise UserError("No account has fresh, complete short-window and weekly quota data.")
    return max(candidates, key=lambda entry: (entry[0], entry[1]))


def chrome_open(url, chrome_profile=None):
    safe_url(url)
    validate_metadata(chrome=chrome_profile)
    command = ["/usr/bin/open", "-a", "Google Chrome", url]
    if chrome_profile:
        # Launch the Chrome binary so --profile-directory also works with a running browser.
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if not chrome.exists():
            raise UserError("Google Chrome is not installed in /Applications.")
        command = [str(chrome), f"--profile-directory={chrome_profile}", url]
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    else:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def login(store, profile):
    pending = private_dir(store.home / "pending") / (profile_key(profile).replace("/", "-") + ".json")
    write_json(pending, {"profile": profile_key(profile), "started": time.time(),
                         "email": profile.get("email"), "inbox": profile.get("inbox")})
    provider = profile["provider"]
    command = [binary(provider)]
    if provider == "codex":
        command += ["login"]
    else:
        command += ["auth", "login", "--claudeai"]
        if profile.get("email"):
            command += ["--email", profile["email"]]
    print(f"Signing in {profile_key(profile)}. Use the account associated with this profile.", flush=True)
    print("Chrome opens through BROWSER when the CLI honors it; otherwise open the CLI link in Chrome.", flush=True)
    try:
        result = subprocess.run(command, env=cli_env(store, profile))
        if result.returncode:
            raise UserError("Login did not complete. Existing sessions were not logged out.")
        account = identity(store, profile)
        print(f'Verified {clean_text(account.get("email") or "subscription account")}.')
    finally:
        pending.unlink(missing_ok=True)


def session_command(store, profile, extra, hooks=None, handoff_settings=None):
    command = [binary(profile["provider"])]
    env = cli_env(store, profile)
    if profile["provider"] == "claude":
        if any(a == "--settings" or a.startswith("--settings=") for a in extra):
            raise UserError("ai-sub supplies --settings for quota collection; place other settings in the profile home.")
        settings = read_json(Path(profile["home"]) / "settings.json", {})
        previous = settings.get("statusLine") or {}
        # Preserve the existing user status-line command without persisting its inputs.
        if previous.get("command"):
            env["AI_SUB_PREVIOUS_STATUSLINE"] = previous["command"]
        else:
            env.pop("AI_SUB_PREVIOUS_STATUSLINE", None)
        hook = shlex.join([sys.executable, str(Path(__file__).resolve()), "statusline", profile["name"]])
        overlay = {"statusLine": {"type": "command", "command": hook}}
        if handoff_settings:
            overlay.update(handoff_settings)
        if hooks:
            overlay["hooks"] = hooks
        command += ["--settings", json.dumps(overlay, separators=(",", ":"))]
    else:
        command += ["-c", 'forced_login_method="chatgpt"']
        if hooks:
            for event, groups in hooks.items():
                handler = groups[0]["hooks"][0]
                value = ('[{hooks=[{type="command",command=' + json.dumps(handler["command"])
                         + ',timeout=' + str(handler["timeout"]) + '}]}]')
                command += ["-c", f"hooks.{event}={value}"]
    return command + extra, env


def chosen_profiles(store, provider=None, name=None):
    if name:
        if not provider:
            raise UserError("Specify a provider with the account name.")
        return [store.profile(provider, name)]
    return [p for p in store.config()["profiles"].values() if not provider or p["provider"] == provider]


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for action in ("add", "configure"):
        s = sub.add_parser(action)
        s.add_argument("provider", choices=PROVIDERS)
        s.add_argument("name")
        s.add_argument("--email")
        s.add_argument("--inbox")
        s.add_argument("--chrome-profile")
        if action == "add":
            s.add_argument("--current", action="store_true", help="Register current login without copying credentials")
    sub.add_parser("list")
    for action in ("use", "login", "verify", "inbox", "usage-page", "reauth-info"):
        s = sub.add_parser(action)
        s.add_argument("provider", choices=PROVIDERS)
        s.add_argument("name", nargs="?")
    s = sub.add_parser("run")
    s.add_argument("provider", choices=PROVIDERS)
    s.add_argument("args", nargs=argparse.REMAINDER)
    s = sub.add_parser("auto", help="Configure cooperative mid-session account handoffs")
    s.add_argument("mode", choices=("on", "off", "status"))
    s.add_argument("provider", choices=PROVIDERS, nargs="?")
    s.add_argument("--threshold", type=int, default=None, help="Used percent that requests a handoff (50–95; default 90)")
    fallback = s.add_mutually_exclusive_group()
    fallback.add_argument("--fallback", metavar="claude/NAME", help="For auto on codex: use this Claude profile when no Codex standby qualifies")
    fallback.add_argument("--clear-fallback", action="store_true", help="For auto on codex: return to Codex-only handoffs")
    sub.add_parser("sessions", help="List managed runs and private continuation files")
    s = sub.add_parser("dashboard", help="Open the local usage chart or return its data")
    s.add_argument("--json", action="store_true", help="Read normalized chart data without fetching provider usage")
    s.add_argument("--once", action="store_true", help="Write a snapshot and exit instead of updating the chart")
    s.add_argument("--no-open", action="store_true", help="Print the chart path without opening a browser")
    s.add_argument("--refresh", action="store_true", help="Fetch Codex usage once before opening the chart")
    for action in ("status", "recommend", "watch", "history"):
        s = sub.add_parser(action)
        s.add_argument("provider", choices=PROVIDERS, nargs="?")
        s.add_argument("name", nargs="?")
        if action in ("status", "recommend"):
            s.add_argument("--refresh", action="store_true")
        if action == "status":
            s.add_argument("--json", action="store_true")
        if action == "watch":
            s.add_argument("--interval", type=int, default=SAMPLE_SECONDS)
            s.add_argument("--once", action="store_true")
        if action == "history":
            s.add_argument("--days", type=int, default=7)
    s = sub.add_parser("statusline", help="Internal Claude quota collector (JSON on stdin)")
    s.add_argument("name")
    sub.add_parser("doctor")
    return p


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    store = Store()
    action = args.command
    if action == "add":
        profile = store.add(args.provider, args.name, args.email, args.inbox, args.chrome_profile, args.current)
        print(f'Registered {profile_key(profile)}. Home: {profile["home"]}')
        print(f'Next: ai-sub verify {args.provider} {args.name}' if args.current else
              f'Next: ai-sub login {args.provider} {args.name}')
    elif action == "configure":
        profile = store.profile(args.provider, args.name)
        validate_metadata(args.email, args.inbox, args.chrome_profile)
        email_changed = args.email is not None and args.email != profile.get("email")
        with store.edit() as config:
            profile = config["profiles"][profile_key(profile)]
            for argument, key in ((args.email, "email"), (args.inbox, "inbox"), (args.chrome_profile, "chrome_profile")):
                if argument is not None:
                    profile[key] = argument
            if email_changed:
                profile["verified_email"] = None
                profile.pop("verified_at", None)
                profile["generation"] = profile.get("generation", 1) + 1
        if email_changed:
            store.save(profile, [], "configuration", error="Account email changed; verify and refresh usage.")
        print("Profile metadata updated.")
    elif action == "list":
        config = store.config()
        for key, profile in config["profiles"].items():
            default = " *" if config["defaults"].get(profile["provider"]) == profile["name"] else ""
            print(f'{key}{default}  {clean_text(profile.get("email") or profile.get("verified_email") or "email not set")}')
    elif action == "use":
        profile = store.profile(args.provider, args.name)
        with store.edit() as config:
            config["defaults"][args.provider] = profile["name"]
        print(f'Next ai-sub run {args.provider} uses {profile["name"]}. Running sessions keep their account.')
    elif action == "login":
        login(store, store.profile(args.provider, args.name))
    elif action == "verify":
        print(json.dumps(identity(store, store.profile(args.provider, args.name)), indent=2))
    elif action in ("inbox", "usage-page", "reauth-info"):
        profile = store.profile(args.provider, args.name)
        if action == "inbox":
            if not profile.get("inbox"):
                raise UserError("Set the inbox first: ai-sub configure PROVIDER NAME --inbox https://YOUR-MAIL-SITE/")
            chrome_open(profile["inbox"], profile.get("chrome_profile"))
        elif action == "usage-page":
            url = "https://chatgpt.com/codex/settings/usage" if args.provider == "codex" else "https://claude.ai/settings/usage"
            chrome_open(url, profile.get("chrome_profile"))
        else:
            pending = read_json(store.home / "pending" / (profile_key(profile).replace("/", "-") + ".json"))
            print(json.dumps({"profile": profile_key(profile), "email": profile.get("email"),
                              "inbox": profile.get("inbox"), "chrome_profile": profile.get("chrome_profile"),
                              "pending_login": pending}, indent=2))
    elif action == "run":
        extra = args.args
        name = extra.pop(0) if extra and not extra[0].startswith("-") else None
        profile = store.profile(args.provider, name)
        extra = extra[1:] if extra[:1] == ["--"] else extra
        from handoff import policy, supervise
        if policy(store, args.provider)["enabled"]:
            return supervise(store, profile, extra)
        identity(store, profile)
        command, env = session_command(store, profile, extra)
        print(f'Starting {profile_key(profile)}', file=sys.stderr, flush=True)
        os.execvpe(command[0], command, env)
    elif action in ("auto", "sessions"):
        from handoff import configure, sessions
        if action == "auto":
            configure(store, args.mode, args.provider, args.threshold, args.fallback, args.clear_fallback)
        else:
            sessions(store)
    elif action == "dashboard":
        from dashboard import launch, snapshot
        if args.json:
            if args.refresh:
                raise UserError("Use status --refresh to collect usage; dashboard --json only reads observations.")
            print(json.dumps(snapshot(store)))
        else:
            launch(store, once=args.once, open_browser=not args.no_open, refresh=args.refresh)
    elif action == "statusline":
        profile = store.profile("claude", args.name)
        raw = sys.stdin.read(2_000_000)
        own_line = statusline(store, profile, raw)
        previous = os.environ.get("AI_SUB_PREVIOUS_STATUSLINE")
        if previous:
            try:
                result = subprocess.run(previous, shell=True, input=raw, capture_output=True, text=True, timeout=3)
                if result.returncode == 0 and result.stdout.strip():
                    print(result.stdout.rstrip())
            except subprocess.SubprocessError:
                pass
        print(own_line)
    elif action in ("status", "recommend", "watch", "history"):
        profiles = chosen_profiles(store, args.provider, args.name)
        if getattr(args, "refresh", False):
            for profile in profiles:
                refresh(store, profile)
        if action == "status":
            report(store, profiles, args.json)
        elif action == "recommend":
            available, key, _ = recommend(store, profiles)
            print(f"{key}: at least {available:g}% remaining across its reported windows. No account was switched.")
        elif action == "watch":
            if args.interval < 60:
                raise UserError("Use an interval of at least 60 seconds.")
            while True:
                for profile in profiles:
                    refresh(store, profile)
                print(dt.datetime.now().astimezone().isoformat())
                report(store, profiles)
                sys.stdout.flush()
                if args.once:
                    break
                time.sleep(args.interval)
        else:
            if not 1 <= args.days <= 90:
                raise UserError("History retains up to 90 days; choose 1–90.")
            keys = {profile_key(p) for p in profiles}
            with contextlib.closing(store.db()) as db:
                rows = db.execute("SELECT profile,observed,source,windows,error FROM samples "
                                  "WHERE observed >= ? ORDER BY observed", (time.time() - args.days * 86400,))
                for key, observed, source, windows, error in rows:
                    if key in keys:
                        print(json.dumps({"profile": key, "observed_at": reset_text(observed),
                                          "source": source, "windows": json.loads(windows), "error": error}))
    elif action == "doctor":
        print(f"State: {store.home}")
        for provider in PROVIDERS:
            print(f"{provider}: {shutil.which(provider) or 'not installed'}")
        print("Chrome: " + ("installed" if Path("/Applications/Google Chrome.app").exists() else "not found"))
        print("Credential override variables present: " + ", ".join(k for k in AUTH_ENV + ROUTING_ENV if os.environ.get(k)))
        print("Codex live reads require network and access to its CLI home/Keychain.")
        print("Claude quota collection starts after a response in ai-sub run claude.")
        print("Email-code handling needs an authenticated inbox and browser/connector access; it is not a daemon feature.")


if __name__ == "__main__":
    sys.modules.setdefault("ai_sub", sys.modules[__name__])
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(130)
    except (UserError, OSError, subprocess.SubprocessError, ValueError, sqlite3.Error) as exc:
        # Never echo raw subprocess output or untrusted credential-containing provider errors.
        message = str(exc) if isinstance(exc, UserError) else f"{type(exc).__name__}: operation failed; check permissions/configuration."
        print("ai-sub: " + message, file=sys.stderr)
        sys.exit(1)
