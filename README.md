# AI subscription usage and handoffs

See your Codex and Claude Code subscription usage in **the bottom-right status
bar of VS Code**, click through to an account usage chart, and optionally hand
an ongoing task to another account when usage approaches its limit.

The `ai-sub` terminal command and shared agent skill manage separate account
profiles on your Mac. Each person signs into their own authorized subscriptions.
Credentials, account details, usage observations and task checkpoints stay on
that person's computer; this repository contains the reusable tool.

**Start here:** [Install](#install) · [Show the usage chart](#show-the-usage-chart)
· [Use it from Claude](#use-it-from-an-existing-claude-session)
· [Add accounts](#add-another-account) · [Automatic handoffs](#automatic-handoffs)
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
source ./activate.zsh
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

For a new terminal, source this checkout's `activate.zsh` again. To persist the
PATH change, add `source /absolute/path/to/ai-subscriptions/activate.zsh` to your
`~/.zshrc`. Replace the path with your actual checkout. The script supports bash
and zsh. Keep the checkout in the same location after installing it.

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

`use` changes the launcher's default, not the bare `claude` or `codex` command,
and does not move a running session. New profiles have independent CLI settings
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

For a handoff to happen:

- At least two distinct accounts of the **same provider** must be signed in.
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
quota cannot be refreshed by this helper.** Handoffs stay within Codex or within
Claude; there is no automatic transfer between providers. See
[handoff setup and recovery](references/handoff.md) for full details.

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
| `/ai-subscriptions` is missing in Claude | Run the installer, then restart Claude so it discovers the skill. |
| No bottom-right indicator | Run `python3 install.py --vscode`, reload VS Code, and ensure its status bar is visible. Check that AI Subscription Usage is enabled. |
| Indicator says usage unavailable | Set the absolute `aiSubscriptions.executablePath` in VS Code User settings; check Python and run `ai-sub doctor` in a regular terminal. |
| An account is listed but cannot launch | Run `ai-sub verify PROVIDER NAME`, then `ai-sub login PROVIDER NAME` if needed. Registration alone is not authentication. |
| Login cannot start its OAuth callback server | Run `ai-sub login PROVIDER NAME` in your regular Mac terminal outside the agent sandbox. |
| Claude usage is unknown or stale | Launch that profile with `ai-sub run claude NAME`. A response can supply quota; `--refresh` does not fetch idle Claude usage. |
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
exit ordering, worktree edits and task continuity. Authenticated handoffs and
mailbox reauthentication still need verification on each user's machine.

To remove installed links, the background sampler and the VS Code indicator:

```sh
python3 install.py --uninstall --vscode
```

Uninstall removes matching installed components, keeps private account state,
and does not revoke provider sessions. Use the official provider logout in the
appropriate profile if you also want to revoke access.
