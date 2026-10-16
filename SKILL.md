---
name: ai-subscriptions
description: Switch Codex and Claude Code subscription profiles, track short-window and weekly usage, enable cooperative mid-session handoffs with task checkpoints, and guide Chrome sign-in and email reauthentication. Use for subscription account switching, quota checks, task continuity between accounts, or subscription login; not API-key billing.
---

Use the bundled terminal command `ai-sub`. If it is not on PATH, run
`python3 scripts/ai_sub.py` relative to this skill's resolved directory. The same
installation serves both Codex and Claude Code; its state lives in `.state/`
beside this file unless `AI_SUB_HOME` overrides it.

## Account and session workflow

1. Run `ai-sub list` and `ai-sub status` to identify registered profiles and data
   freshness. Account labels are separate for `codex` and `claude`.
2. Register another subscription with `ai-sub add PROVIDER NAME --email ADDRESS`.
   Use `--current` only to register the CLI's current home without copying tokens.
   New profiles have independent CLI configuration and history. Existing plugins,
   settings, and conversations are not migrated automatically.
3. `ai-sub login PROVIDER NAME` starts the official login. Run it in an interactive
   terminal that stays alive while browser steps complete. For browser/email steps,
   read [references/reauth.md](references/reauth.md).
4. `ai-sub verify PROVIDER NAME` checks subscription authentication and the expected
   email. When the user asks to switch, run `ai-sub use PROVIDER NAME`; a new
   `ai-sub run PROVIDER` session then uses that profile. A specific launch can use
   `ai-sub run PROVIDER NAME -- CLI_ARGUMENTS`.

`use` changes the launcher default for new sessions. Automatic handoffs are a
separate opt-in workflow described below. Do not log out a current session to
implement a switch. Do not overwrite or copy provider credential files.
Account labels, expected emails, inbox URLs, and Chrome profile directory names
are stored in the private registry. Official CLIs retain their OAuth credentials;
new Codex profiles require the OS Keychain, and Claude uses native credential
storage. Passwords and reauthentication codes do not belong in the skill, registry,
chat replies, usage database, or shell arguments.

## Quota workflow

- `ai-sub dashboard` opens the local graphical usage chart. It rereads saved
  observations every 15 seconds while the command runs; `--refresh` fetches
  Codex usage once first. Missing windows stay unknown, including weekly-only
  Codex reports. `dashboard --json` is a read-only normalized data interface.
- For an always-visible VS Code indicator, run `python3 install.py --vscode`
  from the checkout in the user's regular terminal, then reload VS Code. The
  right-hand status-bar item opens the full chart. It displays local usage;
  native VS Code agent extensions are not supervised by it.
- `ai-sub status --refresh` fetches Codex limits using `account/rateLimits/read`.
- Claude limits arrive through the official status-line payload when launched
  with `ai-sub run claude NAME`, after a model response. `--refresh` does not make
  a paid model request or pretend to refresh an idle Claude account.
- `ai-sub watch --interval 240` monitors in a terminal. The global installer can
  also set up a 4-minute macOS background sampler; see the installation README.
- `ai-sub history --days 7` shows recorded observations, with up to 90 days retained.
- `ai-sub recommend PROVIDER --refresh` ranks accounts with complete, fresh
  short-window and weekly limits. It never changes the selected account.

Use the provider's reset timestamp, not a calendar-week assumption. Unknown,
expired, or older-than-5-minute data is not evidence of available quota. Preserve
server-reported blocks even when a displayed percentage is low. Percentages from
different snapshots or subscriptions cannot be summed into weekly usage. Data
starts when monitoring is enabled; there is no fabricated historical backfill.

## Automatic task handoffs

When the user requests automatic switching, use `ai-sub auto on [PROVIDER]`
(default threshold: 90% used). Launch with `ai-sub run PROVIDER [NAME]` from the
actual repository/worktree. `ai-sub auto status` shows policy, and `ai-sub sessions`
shows managed runs and checkpoint paths. `ai-sub auto off [PROVIDER]` disables it.
Read [references/handoff.md](references/handoff.md) for setup and supported modes.

A managed session requests a checkpoint when fresh quota reaches its threshold
and another verified account of the same provider has fresh, complete capacity.
On an `AI-SUB HANDOFF REQUEST`, finish active work, collect subagent results and
wait for background jobs. Return the requested `ai-sub-handoff` JSON block in
your final response with an accurate task status, constraints, tests and next
steps. The native Stop hook saves it; no shell/file-write action is necessary.
Do not commit, stash or undo repo changes just to hand off. Do not claim a job
finished unless it did. Preserve unresolved approvals as blockers.

The replacement starts in the exact same directory only after the outgoing CLI
exits. Read its supplied checkpoint and repo instructions, inspect current files,
and continue only unfinished authorized work. A completed task or one waiting
for user input must remain idle. Checkpoints summarize task context; they do not
transfer full native conversation history or expand permissions.

Existing bare CLI, desktop and VS Code extension sessions cannot be adopted.
Codex requires all injected hooks to be trusted in `/hooks` for each profile,
then a restart. Claude requires native workspace trust and empty background-task
reports. Missing/stale quota, absent hooks or an unsafe checkpoint prevents a
switch. Do not kill an arbitrary running agent to force a handoff.

For implementation limits and supported interfaces, read
[references/providers.md](references/providers.md). Use `ai-sub doctor` for a local
capability check. Do not claim an inbox integration or live usage check worked until
it was actually connected and verified.
