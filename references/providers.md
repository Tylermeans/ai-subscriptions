# Provider interfaces and constraints

Verified against local Codex CLI 0.162.1 and Claude Code 2.1.292 on 2026-10-15.
The helper uses only supported CLI or status-line interfaces, not scraped OAuth
tokens or undocumented subscription HTTP endpoints.

## Codex

`CODEX_HOME` isolates each profile. New homes have
`cli_auth_credentials_store = "keyring"` and `forced_login_method = "chatgpt"`.
Registering an existing home preserves its credential backend and settings; it
does not migrate existing plaintext credentials into Keychain. Native Codex owns
refresh and storage of its credentials.

A short-lived stdio app-server connection performs initialize/initialized,
`account/read` and `account/rateLimits/read`; no thread or model turn is started.
The collector retains primary and secondary windows for each returned bucket,
reset timestamps, and quota/spend blocks. Native app-server startup may write
its own CLI state and requires access to that profile home. Requests are bounded
by timeouts and failures replace freshness with an explicit unknown state.

Sources:
- https://developers.openai.com/codex/auth
- https://developers.openai.com/codex/app-server

## Claude Code

`CLAUDE_CONFIG_DIR` isolates configuration, history and directory-keyed macOS
Keychain entries. An existing default profile is launched with the variable unset
to preserve the native default entry. Claude can fall back to a 0600 credentials
file when Keychain cannot accept a write; the helper does not read or copy it.

`claude auth login --claudeai --email ADDRESS` starts subscription login.
`claude auth status --json` verifies its identity before launching a session.
If JSON omits the email, the helper also checks the native `--text` status.
An expected email must be observable there; otherwise verification stops instead
of assuming the browser selected the right account. A Console/API login without
a subscription type is rejected.
The session receives a `--settings` overlay for a status-line collector. Its
`rate_limits.five_hour` and `rate_limits.seven_day` data is supplied by Claude
after a response, when available for that account. The prior **user** status-line
command is passed its original stdin and shown above the quota line. A
project-specific or separately supplied status line is not composed; the
launcher's explicit overlay wins. Print-only/noninteractive Claude invocations
may not emit status-line updates. Idle accounts require a new active-session
observation or inspection of the official usage UI.

The collector intentionally does not use context-window percentages, local
token estimates, or API-dollar totals as subscription quota. Status-line inputs
may contain project/session metadata; only normalized quota fields are stored.

Sources:
- https://code.claude.com/docs/en/authentication
- https://code.claude.com/docs/en/env-vars
- https://code.claude.com/docs/en/statusline
- https://code.claude.com/docs/en/chrome
- https://code.claude.com/docs/en/skills

## Launching and boundaries

The launcher removes common API-key, OAuth-token and provider-routing environment
overrides from its child environment so they do not accidentally select another
account. It does not edit the parent shell. Managed/provider settings and custom
API-key helpers are still controlled by the official CLI; use clean isolated
profiles for direct subscriptions. This tool does not override managed policy.

The registry, pending-login metadata and quota database have user-only filesystem
permissions. Managed runs also save private task prompts and agent-authored
checkpoints; see [handoff behavior and limits](handoff.md). Authentication secrets
and email messages are not deliberately extracted into these files. Native CLIs
may maintain their own logs and authentication cache.
Each profile home is stable: moving the installation changes paths for new
profiles; keep existing registered home paths accessible so Keychain identity is
preserved. Do not put `.state/` into Git, cloud sharing, or a skill upload bundle.
