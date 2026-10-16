# AI Subscription Usage

An always-visible indicator on the **right side of VS Code's bottom status bar**.
Click it to open a chart of your Codex and Claude subscription accounts, including
reported usage windows, reset times and data freshness.

Install the `ai-sub` terminal tool first using the
[repository setup instructions](https://github.com/tylermeans/ai-subscriptions#install).
Then install the VSIX using **Extensions: Install from VSIX…** or:

```sh
code --install-extension dist/ai-subscriptions-0.2.1.vsix --force
```

Reload VS Code after installation. The indicator starts automatically in each
window; click it or run **AI Subscriptions: Show Usage Chart** from the Command
Palette. Hover over the indicator for every account's latest reported windows.
`?` means missing data, and `~` means the displayed observation needs refreshing.
Percentages mean **used**, not remaining.

Readings expire after **5 minutes**, including while an open chart or indicator
waits for new data. Old values stay visible as stale / last seen and cannot be
used for automatic switching. Refreshing the display does not renew their age.
Enable Codex sampling every four minutes with `python3 install.py --background`;
rerun this command when updating an older 15-minute sampler. Idle Claude
readings need a new quota report from an active session.

The extension reads local observations every 15 seconds. It does not poll provider
services automatically, read credentials or access task transcripts. Use
**AI Subscriptions: Fetch Codex Usage** for an explicit live Codex check. Claude
usage is collected after responses in sessions started with `ai-sub run claude`.
Missing usage is never shown as zero.

In User settings, configure `aiSubscriptions.executablePath` if `ai-sub` is not at
`~/.local/bin/ai-sub` or on VS Code's PATH. Set `aiSubscriptions.stateDirectory`
only if you use a custom `AI_SUB_HOME`. These settings use local user values,
not values supplied by a repository. Remote VS Code windows show accounts on
the local machine where this UI extension runs.

This extension displays usage. Automatic account handoffs still require launching
the CLI through `ai-sub run`; it cannot supervise the native Claude/Codex extensions.
