# Managed task handoffs

The launcher can change accounts during an ongoing task, using a new native
session and an explicit checkpoint. It does not transfer credentials, native
session databases, in-memory model state, or transcripts between accounts.

## Setup

1. Add and sign into at least two distinct accounts for the same provider:

   ```sh
   ai-sub add claude backup --email you@other-company.com
   ai-sub login claude backup
   ai-sub verify claude backup
   ```

2. Enable the supervisor and start in the repository/worktree you want to use:

   ```sh
   ai-sub auto on                 # both providers, 90% used threshold
   ai-sub auto on claude --threshold 85
   cd /absolute/path/to/worktree
   ai-sub run claude backup
   # An optional initial task must follow --:
   ai-sub run codex current -- "Continue the inventory fix; do not deploy."
   ```

3. For Codex, review **all** ai-sub hooks in `/hooks`, exit, and restart the
   managed session. Do this for each profile. The hook commands have stable
   definitions across runs; run identifiers travel through the child environment.
   Code updates that change hook definitions may require another native review.
   The launcher checks `hooks/list` for all required hooks before allowing a
   handoff. It never bypasses trust. Claude applies its native workspace trust
   check to the injected settings overlay.

4. Collect current quotas. `ai-sub status --refresh` reads Codex limits without
   a model turn. Claude reports its limits after a response through the native
   status line; this tool cannot refresh an idle Claude profile. Use an actual
   task in each Claude profile to collect observations. Do not launch two agents
   to edit the same files just to collect quota; a standby can work elsewhere.

Fresh capacity requires both short-window and weekly data, valid future reset
times, a snapshot no older than 5 minutes, no provider block, and all reported
windows below the configured threshold. A same-provider standby's verified email
must differ from the outgoing account's. Missing or stale measurements prevent a handoff;
the tool does not guess a standby account has reset.

Before the first CLI launch, the same eligibility rules select a standby when
the requested account's fresh usage has already reached its threshold. The
original task prompt is sent once to the selected account, with no invented
checkpoint. A last known full window with a future reset also prevents starting
on that profile when subsequent readings are empty or stale. Startup can still
select an independently verified standby with fresh, complete quota. If none
qualifies, no CLI is started. The old reading never becomes fresh and cannot
trigger a mid-session handoff. A newer valid reading or an account metadata
rebind supersedes it.

For optional `claude` / `codex` shell functions that call this launcher, see
[Use plain claude or codex](../README.md#use-plain-claude-or-codex). The account
label is optional and defaults to the one selected with `ai-sub use`.

## Codex to Claude

An explicit fallback allows a different provider to continue the task:

```sh
ai-sub auto on codex --threshold 90 --fallback claude/backup
ai-sub run codex current -- "Finish the authorized fix; do not deploy."
```

`claude/backup` must already be registered. Eligible Codex accounts take priority;
the named Claude profile is considered next. Other Claude profiles are never
selected implicitly, and Claude → Codex is not implemented. The same verified
email is allowed across different providers. Identity checks and the complete,
five-minute quota requirement apply to the Claude target. A weekly-only source
Codex report can trigger a move; the target still needs both windows. The source
provider's threshold governs target eligibility, including after process exit.

Use `ai-sub auto on codex --clear-fallback` to remove the fallback without turning
off Codex account switching. Removing the fallback, turning off Codex automatic
handoffs, or changing target identity/quota invalidates a pending transfer.
The supervisor rechecks target eligibility after outgoing shutdown and native
identity verification, before starting the replacement. A failure there retains
the checkpoint but does not launch a new CLI.

Only managed Codex `workspace-write` + `on-request` sessions can cross providers.
Other sandbox/approval combinations remain within Codex. Claude uses fresh
native options: `--permission-mode manual`, its own model configuration, and a
settings overlay that requires sandboxing (`enabled` and `failIfUnavailable`),
disables unsandboxed retries and automatic sandbox approvals, and adds ask rules
for shell, edit and web tools. These settings are retained through subsequent
Claude account handoffs. Codex model/search/sandbox flags never reach Claude.
Existing Claude settings and managed policies still apply; this is not a
translation of arbitrary Codex permissions, MCP servers or plugins. See the
[Claude sandbox contract](https://code.claude.com/docs/en/sandboxing) and
[permission rule precedence](https://code.claude.com/docs/en/permissions).

The run changes its active provider to Claude and uses Claude's lifecycle hooks,
including its background-job registry requirement. Further same-provider moves
follow Claude's `auto` setting. Disabling Claude `auto` does not prevent it from
receiving the explicitly configured Codex fallback; it disables subsequent
handoffs out of Claude. There is no automatic return to Codex.

## What happens during a handoff

The supervisor polls approximately once a minute while its CLI child is active.
When any fresh quota window reaches the threshold, or the provider reports a
fresh block, it checks alternate profiles and verifies the selected identity.
No healthy account is rotated merely because another has more quota.

The next prompt/tool hook or native Stop hook gives the outgoing agent an
`AI-SUB HANDOFF REQUEST`. The agent must finish active work, collect outstanding
subagent results, wait for background commands and scheduled wakeups, and end
with the requested JSON checkpoint in an `ai-sub-handoff` fenced block.
The checkpoint contains:

- Objective and current state.
- User constraints, permissions still needed and key decisions.
- Completed work and relevant paths.
- Precise next steps and actual test results.
- Blockers and any remaining background jobs.
- Whether the task is complete or waiting for user input.

The Stop hook requires a valid checkpoint and no tracked active tools or
subagents. Claude must additionally report empty `background_tasks` and
`session_crons` arrays; an unavailable registry prevents a switch. Codex supplies
no equivalent registry, so its tracked lifecycle plus the agent's explicit
background-job declaration are required. This is cooperative: the supervisor
does not prove that every arbitrary detached process on the machine has stopped.

The Stop hook briefly holds the outgoing CLI at that boundary. The supervisor
rechecks policy, account identity metadata and quota freshness, durably writes
the checkpoint, and asks only its own CLI child to terminate. The replacement
starts only after that child has exited. A process that will not exit is never
forcibly killed to make a handoff happen.

The successor starts in the same absolute directory, with the checkpoint path
and the original task prompts. Git root, branch, HEAD and short status are
recorded after the outgoing process exits. Repo files, uncommitted edits and
existing worktrees remain in place; no stash, reset, commit or branch creation
is performed. The agent reads the checkpoint and repo instructions and checks
current files before continuing. Completed tasks and tasks awaiting the user
start an idle CLI instead of automatically sending a continuation prompt.

At most eight automatic handoffs occur per managed run. Profile changes,
directory changes, unsupported permission-mode changes, missing hooks, active jobs, invalid
checkpoints, stale data and failed identity verification suspend or cancel the
handoff. A missing checkpoint gets at most one Stop-hook retry per user turn.
An API failure or user interrupt alone is never evidence of a safe stopping
point. If the account exhausts its limit before checkpointing, manual recovery
may be necessary; choose an earlier threshold for large tasks.

## Supported launch modes

Managed mode supports interactive terminal sessions, including a terminal
inside VS Code. Start in an existing directory/worktree; the launcher deliberately
rejects resume/fork, new-worktree, remote, background, print-only, custom settings,
and native administrative subcommands in managed mode.

Allowed arguments:

| Provider | Options |
| --- | --- |
| Codex | `--model`/`-m`, `--sandbox`/`-s` (`read-only` or `workspace-write`), `--ask-for-approval`/`-a`, `--search`, `--no-alt-screen`, `--no-daemon` |
| Claude | `--model`, `--effort`, `--permission-mode` (`manual`/`default`, `plan`, `acceptEdits`, `auto`, `dontAsk`), `--chrome`, `--no-chrome`, `--verbose` |

Without explicit permission options, managed Codex uses `workspace-write` and
`on-request`; managed Claude uses `manual`. These explicit options are preserved
across same-provider handoffs. The cross-provider policy is described above.
Codex uses `--no-daemon` so the supervised session owns its
process lifecycle. Profile-specific plugins, MCP servers and other settings
are not copied; configure each profile for the tools its tasks need. A supported
Claude mode change reported by its native hooks is recorded and carried into the
replacement Claude session. It revokes any prior checkpoint lease so permission
changes cannot reuse an old stopping boundary. `bypassPermissions` and unknown
modes suspend handoffs. Codex permission-mode changes still require a restart;
they cannot be translated from hook metadata alone.

Use `ai-sub auto off PROVIDER` for the original native argument passthrough.
Existing bare `codex`/`claude`, desktop and VS Code extension sessions are not
adopted. Foreman's headless workers require their own integration; see
[the checkpoint contract](foreman.md).

## Status and recovery

```sh
ai-sub auto status
ai-sub sessions
ai-sub status --refresh
ai-sub auto off claude
```

`sessions` lists the last 20 runs, their last recorded state, directory and
continuation-file location. No-standby, hook and checkpoint failures are visible
there. Policy-off cancels pending switches when the supervisor next checks; the
active CLI continues. It does not force an account back to an earlier profile.

Private files are under `.state/runs/RUN_ID/` (or `$AI_SUB_HOME/runs/`). Each run
has its own lock and generation identifier so old hooks and other runs cannot
advance its handoff. Files are mode 0600 and run directories mode 0700.
`run.json` stores task prompts and state; `handoff-N.json` is a durable checkpoint.
Version 2 checkpoints include source and target provider launch options/settings
alongside the original fields. They contain no copied provider credentials.
These files are ignored by Git, remain until you remove them, and are retained
by uninstall. They can contain confidential task context; never share the state
directory as a skill bundle or paste credentials into task prompts/checkpoints.

If authentication or startup fails after stopping the outgoing agent, the
checkpoint remains. Sign in with `ai-sub login PROVIDER NAME`, then launch a new
session in the recorded directory and ask it to read the checkpoint shown by
`ai-sub sessions`. Check that the previous process has exited before continuing
in another terminal. An agent that never produced a checkpoint has no invented
continuation summary.

A Claude `StopFailure` with `error: rate_limit` is displayed as
`rate limited; no safe checkpoint`, with a recovery note. The supervisor prints
that note or any fault in the managed terminal so suspension is not silent.
The failure cannot authorize terminating a potentially busy process. Exit the
rate-limited CLI and start an available profile in the same worktree; provide
task context when no checkpoint exists. A fresh launch runs the startup capacity
check above. Retrying inside an exhausted conversation cannot change its account.

## Validation and provider contracts

Tests simulate both native CLI processes and real hook subprocesses. They check
process exit order, worktree/edit preservation, exact task notes, stale or
missing capacity, duplicate accounts, busy tools/subagents, interrupted turns,
invalid checkpoints, lease expiration, changed identities, and independent runs.
The installed Codex 0.162.1 accepts and discovers the injected hook configuration
through its native app-server; authentication is not needed for that check.
Cross-provider tests run distinct simulated Codex and Claude executables with real
hook subprocesses. They assert exit ordering, target options/settings, profile
environment, preserved edits, idle completion/wait states and stale-on-exit
refusal. Authenticated handoffs still require local acceptance testing with the
participating subscriptions; simulated success is not a live-account test.

Provider contracts: [Codex lifecycle hooks](https://learn.chatgpt.com/docs/hooks)
and [Claude Code lifecycle hooks](https://code.claude.com/docs/en/hooks).
