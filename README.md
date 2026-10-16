# AI subscription usage and handoffs

See your Codex and Claude Code subscription usage in **the bottom-right status
bar of VS Code**, click through to an account usage chart, and optionally hand
an ongoing task to another account when usage approaches its limit. A configured
**Codex → Claude fallback** can continue that task in Claude with a checkpoint
and the same worktree.

The `ai-sub` terminal command and shared agent skill manage separate account
profiles on your Mac. Each person signs into their own authorized subscriptions.
Credentials, account details, usage observations and task checkpoints stay on
that person's computer; this repository contains the reusable tool.

**Start here:** [Install](#install) · [Show the usage chart](#show-the-usage-chart)
· [Use it from Claude](#use-it-from-an-existing-claude-session)
· [Use plain claude or codex](#use-plain-claude-or-codex)
· [Add accounts](#add-another-account) · [Automatic handoffs](#automatic-handoffs)
· [Codex → Claude](#codex--claude-fallback)
· [Troubleshooting](#troubleshooting)

## Install

You need macOS, Python 3.10 or newer, and the official Codex and/or Claude Code
CLI on your PATH. Google Chrome is used for browser login. VS Code is optional;
install it if you want the status-bar indicator. The tool and extension have no
third-party runtime dependencies.

Run these commands in your **regular terminal**, outside an agent's shell tool:

```sh
git clone https://github.com/tylermeans/ai-subscriptions.git
cd ai-subscriptions
python3 install.py --background
source ./native.zsh # only for this already-open terminal
ai-sub list
ai-sub doctor
```

The installer registers existing CLI homes as `codex/current` and
`claude/current`, when present. The label `current` names a profile; it does not
mean every running agent is using it. Verify your existing login with:

```sh
ai-sub verify claude current
# For Codex instead: ai-sub verify codex current
```

If verification says sign-in is needed, run `ai-sub login claude current` in the
same regular terminal and complete the official browser login. If no existing
profile was registered, follow [Add another account](#add-another-account).

`--background` installs a macOS user LaunchAgent that samples Codex usage every
4 minutes. Omit it if you prefer manual collection. Claude usage is collected
from sessions launched through `ai-sub run`; the background sampler does not
query idle Claude accounts. **Installing the sampler does not enable switching.**

**Setup is once per machine.** The installer configures your shell so new
terminals automatically route interactive `claude` and `codex` commands through
ai-sub. You do not need to type `ai-sub run` or source a file in each new terminal.
Already-open shells need the one-time `source ./native.zsh` above, or you can
close them and open new terminals. Keep the checkout in the same location.

For zsh, the installer adds a marked block to `~/.zshrc` (respecting `ZDOTDIR`).
For bash, it uses `~/.bashrc` and the active login profile. Existing content and
symlinks are preserved; changed files get a private `.ai-sub-backup-*` backup.
Repeated installs do not add duplicate blocks. `--shell zsh` or `--shell bash`
overrides shell detection. `--no-native` skips this setup for people who prefer
explicit `ai-sub run` commands; project-only installation also skips it.

The installer links `~/.local/bin/ai-sub` and the skill under `~/.agents`,
`~/.codex` and `~/.claude`. Existing unrelated files are not overwritten. Restart
an agent to discover a newly installed skill. If global paths are unavailable,
`python3 install.py --project` installs workspace skill links, and `bin/ai-sub`
works directly from this checkout.

## Show the usage chart

### Always show usage at the bottom right of VS Code

From the `ai-subscriptions` checkout, run:

```sh
python3 install.py --vscode
```

In VS Code, run **Developer: Reload Window** from the Command Palette
(`Cmd+Shift+P`). The usage indicator appears automatically on the **right side
of the bottom status bar** in each window. Hover for account details; click it
for the full usage chart. You can also run **AI Subscriptions: Show Usage Chart**
from the Command Palette.

Example indicator:

```text
C:work 7d 4%  ·  A:personal ?  ·  A:work ?
```

`C` means Codex, `A` means Claude, and the following name is your account label.
All percentages mean **used**, not remaining. `?` means data is unavailable;
`~` marks an observation that needs refreshing; `!` means a provider usage block.
A profile marked “Default for new sessions” is the launcher's default, not a
claim about which account an unrelated native extension is using.

The chart shows every account's reported short-window and weekly usage, reset
times, observation time and automatic-handoff setting. Missing windows say
**Not reported**. A weekly-only report displays its weekly percentage without
inventing a five-hour figure. Readings older than **5 minutes** remain visible as
**stale / last seen** and cannot trigger or qualify for automatic switching.
The chart and indicator expire readings even when no new data arrives. Refreshing
the display never changes a measurement's observation time.

The display rereads local observations every **15 seconds**. This does not
fetch fresh provider limits. Use **AI Subscriptions: Fetch Codex Usage** in the
chart or Command Palette for a live Codex check. For Claude, start a session
with `ai-sub run claude ACCOUNT`; quota appears after a response when its CLI
supplies that data. Idle Claude data becomes stale after 5 minutes.

To keep Codex readings current, enable the four-minute background sampler with
`python3 install.py --background`. Rerun this after updating an older installation
that used a 15-minute schedule. Provider/network failures and computer sleep can
delay collection; the five-minute freshness limit still applies. Claude requires
new active-session quota reports and cannot be kept fresh by the sampler alone.

If VS Code cannot locate the command, set `aiSubscriptions.executablePath` in
**User settings** to the absolute path of your installed `ai-sub`. Set
`aiSubscriptions.stateDirectory` only if you use a custom `AI_SUB_HOME`. The
extension reads local account state even in a remote VS Code window; it does
not monitor another machine's accounts automatically.

For manual installation, `python3 scripts/build_vscode.py` creates
`dist/ai-subscriptions-0.2.1.vsix`. Install it using **Extensions: Install from
VSIX…** or `code --install-extension dist/ai-subscriptions-0.2.1.vsix --force`.
The package is built from an explicit source-file allowlist and contains no
account data. It is installed locally, not published to the VS Code Marketplace.

### Open the chart without VS Code

```sh
ai-sub dashboard
```

This opens a local chart in Chrome and updates saved observations every 15
seconds while the terminal stays open. `Ctrl-C` stops updates. No web server or
hosted dashboard is required. For an initial live Codex check, use
`ai-sub dashboard --refresh`. For a snapshot that exits immediately, use
`ai-sub dashboard --once`.

For terminal-only monitoring, use `ai-sub watch --interval 240`.

## Use it from an existing Claude session

**Inside Claude Code**, enter:

```text
/ai-subscriptions
```

This loads the skill for account management and usage questions. **It cannot
attach automatic switching to an already-running unmanaged session.** To
continue that task under the supervisor:

1. Let active commands finish. Ask Claude for a handoff summary containing the
   objective, completed work, changed files, next steps, tests, blockers and any
   approvals still needed. Copy the summary.
2. Enter `/exit` to return to your regular shell.
3. Start a managed session in the **same repository or worktree**:

   ```sh
   cd /path/to/your/existing/repo-or-worktree
   ai-sub auto on claude
   ai-sub run claude current
   ```

4. Paste the summary into the new Claude session and ask it to continue. Keep
   any unresolved approvals or blockers in that summary.

Use a different label, such as `work`, in place of `current` to start that
account. `ai-sub list` shows your labels. Once a managed session is running,
subsequent automatic handoffs create their own continuation checkpoints.

Run shell commands such as `ai-sub login` and `ai-sub run` in your **terminal**,
not as chat messages and not as a nested interactive process inside Claude's
shell tool. The launcher needs an interactive terminal for its supervisor.

## Start a new task

Open a terminal in the repository/worktree you want the agent to use. These
commands also work in VS Code's integrated terminal:

| What you want | Terminal command |
| --- | --- |
| Claude with the default profile | `ai-sub run claude` |
| Claude with the `work` profile | `ai-sub run claude work` |
| Codex with the default profile | `ai-sub run codex` |
| Codex with the `personal` profile | `ai-sub run codex personal` |
| Claude with Chrome integration | `ai-sub run claude work -- --chrome` |
| Start with an initial task | `ai-sub run claude work -- "Fix the failing inventory test; do not deploy."` |

Pass native CLI options or an initial task after `--`. Use an existing worktree;
managed mode does not create a new one or resume native session history.
See [supported managed arguments](references/handoff.md#supported-launch-modes).

In Codex, `$ai-subscriptions` invokes the shared skill. As with Claude, automatic
handoffs require launching through `ai-sub run`; invoking the skill alone does
not supervise a running session.

## Use plain claude or codex

Account names are optional: `ai-sub run claude` uses your selected Claude
profile. `ai-sub run claude work` chooses `work` for that launch. Neither command
logs you out of another profile. Each profile keeps its own official CLI login.

The default installation makes the familiar terminal commands start through
ai-sub. To upgrade an existing installation, run this **once per machine** in
your regular terminal:

```sh
python3 /absolute/path/to/ai-subscriptions/install.py
```

Open a new terminal. The commands are ready automatically:

```sh
ai-sub use claude work          # choose a saved account; does not log in/out
ai-sub auto on claude           # enable automatic handoffs
claude                         # same as ai-sub run claude
claude --permission-mode auto  # preserve Claude's native Auto mode
codex                          # same as ai-sub run codex
```

Replace `work` with a label from `ai-sub list`. The selected account and automatic
handoff policy persist; those two setup commands are not needed every session.
To activate an already-open terminal immediately, source the checkout's
`native.zsh` once. It also sets up PATH. Existing `claude`/`codex` aliases or
functions produce a visible conflict message and are not overwritten.

On another Mac, clone this repository and run the same installer under the macOS
user who will run the CLI. Sign in to each account once on that machine and
enable its automatic policy with `ai-sub auto on claude` (or both providers with
`ai-sub auto on`). Account logins and usage stay on each machine; no credentials
are transferred from another Mac. The official CLI binaries remain available to
their normal updaters.

These are shell functions invoking the installed official CLIs. They do not
replace binaries or hot-swap credentials inside a running CLI. Automatic moves
still require fresh standby quota and a safe checkpoint; a replacement CLI
continues in the same worktree with task notes, not full native chat history.
Native resume/fork and other unsupported managed options produce a visible
error rather than silently disabling supervision.

Headless calls (`claude -p`, `codex exec`, redirected input/output) and native
administration commands pass through with their existing environment. Foreman
workers and native editor extensions are not intercepted. Those calls use
their own native account configuration, not the ai-sub selected profile.
`command claude` or `command codex` explicitly bypasses these shell functions.
To disable them in the current shell, run `unset -f claude codex` followed by
`unset _AI_SUB_NATIVE_ENABLED`. `python3 install.py --uninstall` removes the
installer's startup blocks and links while preserving account data and unrelated
shell edits. An edited or different checkout's block is left intact with an error.

## Add another account

Replace the example address with the email for the subscription you own or are
authorized to use. Names are local labels: lowercase letters, digits, `_` or `-`.
Names are separate for each provider, so `codex/work` and `claude/work` can coexist.

For another Claude subscription:

```sh
ai-sub add claude work --email you@company.com
ai-sub login claude work
ai-sub verify claude work
ai-sub list
```

For another Codex subscription:

```sh
ai-sub add codex personal --email you@example.com
ai-sub login codex personal
ai-sub verify codex personal
```

`add` creates an isolated profile; **it does not sign in**. `login` runs the
official provider login and checks the resulting account. Opening another
terminal alone does not register another subscription. Two labels for the same
email do not count as distinct standby accounts.

To select the default for future launches:

```sh
ai-sub use claude work
ai-sub run claude
```

`use` changes the launcher's default, including interactive commands launched
through `native.zsh`; it does not move a running session or change the native
binary's default credentials. New profiles have independent CLI settings
and history. Existing plugins, MCP servers and conversations are not copied;
configure each profile for the tools its work needs.

If an existing login was not registered by the installer, use
`ai-sub add PROVIDER current --current` once. Use `--current` only for an existing
CLI home, not when adding a second login. To change an expected email later,
use `ai-sub configure PROVIDER NAME --email ADDRESS` and verify again.

## Automatic handoffs

Automatic switching is **off on a fresh installation**. Enable it explicitly:

```sh
ai-sub auto on claude --threshold 90
ai-sub auto status
```

Use `codex` for Codex, or `ai-sub auto on` for both providers. The default threshold
is 90% used; supported values are 50–95%. Start the task with `ai-sub run` after
enabling it. `ai-sub auto off claude` disables future handoffs while leaving the
active CLI running.

Before starting a new managed session, ai-sub checks the selected account's
quota. If fresh usage is already at the threshold, it selects a verified standby
with fresh capacity **before launching a CLI**. If none qualifies, it explains
the problem and stops. An empty or stale reading does not erase an earlier full
window whose recorded reset is still in the future. This startup check does not
need a checkpoint because the new task has not begun.

For a handoff to happen:

- A distinct account of the same provider, or an explicitly configured Claude
  fallback for Codex, must be signed in and verified.
- The standby must have fresh short-window **and** weekly quota, valid reset
  timestamps, no provider block, and usage below the threshold. A weekly-only
  report currently does **not** qualify as standby capacity.
- Codex needs all ai-sub hooks reviewed and trusted in `/hooks` for **each**
  profile, then a managed-session restart. Claude uses native workspace trust.
- The outgoing agent must provide a safe checkpoint with no unfinished tools,
  subagents or background jobs. Claude must also report an empty task registry.

The supervisor checks about once a minute. At the threshold, it requests a
checkpoint, waits for the outgoing CLI to exit, and starts another account in
**the same directory/worktree**. The replacement reads the task objective,
completed work, constraints, next steps, tests and blockers. Existing edits stay
in place. A completed task or a task waiting for user input starts an idle CLI.

This starts a new native conversation with continuation notes; it does not copy
the previous conversation's full memory. It does not adopt existing bare CLI,
desktop, extension or background-worker sessions. The VS Code indicator displays
usage but does not supervise native Claude/Codex extensions.

No eligible standby, stale data, an unsafe checkpoint or an account that exhausts
quota before checkpointing can prevent automatic continuation. **Idle Claude
quota cannot be refreshed by this helper.** Same-provider switching is the default;
Codex → Claude requires the explicit fallback below. Claude → Codex is not yet
supported. See
[handoff setup and recovery](references/handoff.md) for full details.

If an existing Claude session has already hit its limit, it may be unable to
produce a checkpoint. Cancel its automatic retry with Esc, exit Claude with
`/exit`, then run this **at your shell prompt** with your available profile:

```sh
cd /absolute/path/to/your/worktree
ai-sub run claude backup -- --permission-mode auto
```

Typing `claude ...` or `ai-sub ...` into Claude's conversation prompt does not
change the account of that running session. Repository edits remain on disk;
without a checkpoint the previous conversation is not automatically transferred.
Give the new session the task scope and any existing handoff notes. Supported
Claude mode changes (`manual`, `plan`, `acceptEdits`, `auto`, `dontAsk`) are now
preserved during Claude-to-Claude handoffs. Modes that bypass permission checks
remain unsupported, and failures that prevent handoff are printed in the terminal.

## Codex → Claude fallback

Use a registered Claude profile as the fallback (replace `backup` with its label):

```sh
ai-sub verify claude backup
ai-sub auto on codex --threshold 90 --fallback claude/backup
cd /absolute/path/to/your/worktree
ai-sub run codex current -- "Continue the current fix; do not deploy."
```

At the threshold, ai-sub first tries eligible Codex accounts. If none qualifies,
it tries **only** the named Claude profile. The two providers may use the same
email address because their subscriptions are separate. The Claude profile must
have a verified identity and both quota windows observed within the last five
minutes, below the source Codex threshold. Run useful work through
`ai-sub run claude backup` in another directory to collect Claude's status-line
observations. Signing in alone does not supply usage data.

The outgoing Codex session still needs trusted hooks and a safe checkpoint.
Claude starts only after Codex exits, with the same files, task constraints,
completed work, next steps and tests. It receives a new conversation rather than
a converted Codex transcript. `ai-sub sessions` shows the checkpoint path.

For this transfer, Codex must use `workspace-write` and `on-request` (the managed
defaults). Read-only and other approval modes remain eligible for same-provider
handoffs, but cannot cross to Claude automatically. Claude starts in **manual
permission mode with required sandboxing**, unsandboxed retries disabled, and
approval prompts for shell commands, edits and web tools. Codex model names,
flags and approvals are not replayed into Claude. Claude's own configuration and
managed policy still apply; custom Codex security policies are not translated.
If Claude cannot start or loses eligibility during shutdown, the checkpoint stays
available for recovery. No automatic move back to Codex is attempted.

```sh
ai-sub auto status codex
ai-sub auto on codex --clear-fallback  # keep same-provider switching
ai-sub auto off codex                 # stop future handoffs from Codex
```

Changing this setting does not adopt an already-running bare Codex session.
Restart sessions created with an older ai-sub version to load the new supervisor.
The Claude fallback works even if `auto` is off for Claude; that setting controls
whether Claude can subsequently switch to another Claude account.

**Foreman:** this implements the interactive `ai-sub run` path. Foreman owns its
headless worker processes, authentication and workspace guards; its workers are
not automatically wrapped or rerouted by this setting. The portable checkpoint
format and the separate worker integration requirements are documented in
[the Foreman integration notes](references/foreman.md).

## Check usage and handoff progress

Run these in another terminal while your agent works:

| Command | What it shows or does |
| --- | --- |
| `ai-sub dashboard` | Open the graphical usage chart |
| `ai-sub status` | Latest saved account measurements and freshness |
| `ai-sub status --refresh` | Fetch Codex limits, then show all saved measurements |
| `ai-sub status claude` | Saved Claude usage, including unknown/stale data |
| `ai-sub watch --interval 240` | Repeated terminal reports and Codex refreshes |
| `ai-sub sessions` | Managed runs, handoff status and saved checkpoint paths |
| `ai-sub auto status` | Whether switching is enabled and its threshold |
| `ai-sub history --days 7` | Recorded usage observations over seven days |
| `ai-sub recommend codex --refresh` | Rank eligible accounts without switching |
| `ai-sub usage-page claude work` | Open that provider's official usage page |

Reset times come from the provider; “weekly” does not mean a calendar week.
History starts when observations are collected and retains up to 90 days. It
is not an invoice or a reconstruction of earlier usage. Account percentages
should not be added together as a shared usage total.

## Login, email and local data

You can associate an inbox and Chrome profile with an account:

```sh
ai-sub configure claude work --inbox https://mail.google.com/mail/u/0/ --chrome-profile 'Profile 1'
ai-sub reauth-info claude work
ai-sub inbox claude work
```

The shared skill can guide Chrome sign-in and retrieve a requested code/button
when an authenticated browser/email integration is available. **The CLI does
not scrape inboxes or automatically complete email challenges.** Passwords,
passkeys, CAPTCHAs and browser-integration restrictions may require you.
See [the reauthentication workflow](references/reauth.md).

Account metadata, usage observations, browser chart files and task checkpoints
live in the private `.state/` directory beside this README (or `AI_SUB_HOME`).
The official CLIs manage their login credentials. State and credentials are not
included in Git or the VS Code package. Each coworker installs their own copy;
share the repository, not the state directory. Task prompts and checkpoint notes
may contain confidential project context; do not put secrets in them.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| `ai-sub: command not found` | Run `source /absolute/path/to/ai-subscriptions/activate.zsh` in that terminal. |
| `ai-sub run claude` selects the wrong account | Run `ai-sub list`, then `ai-sub use claude NAME` to choose the default. A known exhausted default can be skipped at startup only when another verified profile has complete quota less than five minutes old. |
| Plain `claude` does not use ai-sub | Run `python3 install.py` once and open a new terminal. Check `type claude` reports a function. Existing sessions cannot be adopted. |
| `/ai-subscriptions` is missing in Claude | Run the installer, then restart Claude so it discovers the skill. |
| No bottom-right indicator | Run `python3 install.py --vscode`, reload VS Code, and ensure its status bar is visible. Check that AI Subscription Usage is enabled. |
| Indicator says usage unavailable | Set the absolute `aiSubscriptions.executablePath` in VS Code User settings; check Python and run `ai-sub doctor` in a regular terminal. |
| An account is listed but cannot launch | Run `ai-sub verify PROVIDER NAME`, then `ai-sub login PROVIDER NAME` if needed. Registration alone is not authentication. |
| Login cannot start its OAuth callback server | Run `ai-sub login PROVIDER NAME` in your regular Mac terminal outside the agent sandbox. |
| Claude usage is unknown or stale | Launch that profile with `ai-sub run claude NAME`. A response can supply quota; `--refresh` does not fetch idle Claude usage. |
| Claude keeps retrying a weekly limit | Cancel the retry, exit to your shell, then launch an available profile explicitly. A failed turn cannot create the task checkpoint needed for a safe mid-session handoff. |
| Auto is on but no switch occurs | Check `ai-sub sessions` and `ai-sub status`. A fresh eligible standby, trusted hooks and a safe checkpoint are all required. |
| Only a weekly window appears | The chart shows it accurately, but current standby selection also requires a short window. Missing is not zero. |
| Native resume/worktree/print options are rejected | Use a supported interactive launch in an existing worktree, or disable automatic mode for native passthrough. |

## Update, validate and remove

To update, run these commands from the checkout:

```sh
git pull --ff-only
python3 install.py --background --vscode
```

Omit options for components you do not use. Reload VS Code after updating the
extension, and restart managed sessions to use updated hooks. Private account
state is retained.

Development checks:

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py install.py
node --test vscode/test/*.test.js
python3 scripts/build_vscode.py
```

Tests use temporary profiles and simulated provider responses; they do not need
credentials or make model calls. Handoff tests exercise both providers' process
exit ordering, Codex → Claude launch options, worktree edits and task continuity.
They also check that quota expiring during shutdown prevents replacement launch.
Authenticated handoffs and
mailbox reauthentication still need verification on each user's machine.

To remove installed links, the background sampler and the VS Code indicator:

```sh
python3 install.py --uninstall --vscode
```

Uninstall removes matching installed components, keeps private account state,
and does not revoke provider sessions. Use the official provider logout in the
appropriate profile if you also want to revoke access.
