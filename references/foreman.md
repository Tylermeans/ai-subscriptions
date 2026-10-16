# Foreman and portable task checkpoints

The Codex → Claude move in this repository currently belongs to the interactive
`ai-sub run` supervisor. It does **not** reroute Foreman's existing workers.
Foreman launches `codex exec` and `claude -p` itself, with its own workspace
profile selection, credential injection, tool permissions, sandbox and write
guards. Wrapping that worker in an interactive terminal supervisor would bypass
its process ownership and reporting contracts. Keep those responsibilities in
Foreman when adding a worker adapter.

## Checkpoint version 2

`ai-sub sessions` reports the absolute path of a private `handoff-N.json` file.
It is an ordinary JSON document with these fields:

| Field | Meaning |
| --- | --- |
| `version` | `2` for checkpoints with source and target launch metadata |
| `cwd` | Exact absolute repository/worktree directory |
| `from_profile`, `to_profile` | Provider-qualified labels, such as `codex/current` and `claude/backup` |
| `session_id` | Outgoing native session identifier; never a cross-provider resume ID |
| `saved` | Unix timestamp when the checkpoint was saved |
| `user_prompts` | Original task and later user directions |
| `checkpoint` | Objective, summary, constraints, completed work, next steps, tests, blockers, background jobs, completion/wait flags |
| `permission_mode` | Outgoing native hook's reported permission mode |
| `source_launch`, `target_launch` | Provider, native option list and ai-sub settings overlay; metadata, not authority to execute arbitrary commands |
| `repository` | Git root, branch, HEAD and short status, added after the outgoing CLI exits |

A checkpoint file alone is **not** proof that its writer exited. The interactive
supervisor ensures exit ordering itself; another process owner must independently
check its child and active jobs before using the same worktree. Files stay local
with mode 0600 in directories with mode 0700. They can contain confidential task
notes and should not be committed or included in telemetry.

## Requirements for a Foreman worker adapter

1. Let Foreman choose the target provider/profile within the task's workspace and
   routing policy. Do not use ai-sub's global default to cross workspace or data
   boundaries. Foreman's authentication remains owned by Foreman.
2. Request a structured checkpoint while the source still has usable quota.
   Wait for active tools, agents and background work; a rate-limit exception or
   a killed process does not establish a safe handoff.
3. Persist task notes and preserve the existing worktree. Confirm the outgoing
   child exited before starting another writer, and recheck target capacity.
4. Generate a fresh prompt from the checkpoint plus current task instructions.
   Keep completed tasks idle and tasks waiting for owner input parked. Re-read
   repository instructions and inspect files before continuing.
5. Build the new provider's arguments through Foreman's own runner. Retain its
   sandbox, tool guards, workspace restrictions, budget and reporting. Do not
   execute a checkpoint's `target_launch` or replay Codex flags into Claude.
6. Record the provider transition and continuation in Foreman's task/run ledger.
   Retain the checkpoint on authentication or startup failure for recovery.

This document describes the interface and remaining worker integration work;
the interactive handoff tests do not validate a Foreman worker handoff.
