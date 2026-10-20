# Chrome extension sign-in

`ai-sub use` changes which account new CLI sessions use. The Claude in Chrome
extension keeps its own browser sign-in. `ai-sub` opens the extension's settings
page in the right Chrome profile and records which profile it was last confirmed
as. The user clicks sign-out and sign-in.

Tracking is per provider and per Chrome profile directory (`chrome_profile`, or
`Default`). Profiles using separate Chrome profiles each have their own extension
sign-in, so switching between them may need no logout. Confirm the target
Chrome profile's extension shows the expected email.

## What agents cannot do (checked 2026-10-19)

- `mcp__claude-in-chrome__navigate` refuses `chrome-extension://` pages.
- Desktop computer-use grants browsers the "read" tier: screenshots work, but
  clicks and typing are blocked. It can verify the email on screen, nothing more.
- AppleScript JavaScript in Chrome is off by default. Do not ask the user to
  enable "Allow JavaScript from Apple Events" for this. It exposes every signed-in
  page to local scripts.
- No ChatGPT/Codex Chrome extension was installed in any Chrome profile. `ai-sub
  extension open codex` reports that no page is known.

## When the time is right

Switch only when all of these hold:

- The user ran or requested `ai-sub use PROVIDER NAME`, and `use` printed an
  extension notice, or `ai-sub extension status PROVIDER` shows `needs_switch`.
- No browser task is running through the extension: no claude-in-chrome tool
  calls in flight, no pending login or inbox step from [reauth.md](reauth.md).
  Signing Claude in Chrome out disconnects its browser tools.
- The agent has not been asked for a handoff checkpoint. Automatic handoffs do not
  switch extensions.

## Procedure

1. Run `ai-sub extension open PROVIDER`. It opens the settings page in the
   selected profile's Chrome profile and prints the expected email.
2. Ask the user to sign out and sign in as that email on the official provider
   page. The user handles password, passkey and CAPTCHA prompts. If an emailed
   code is required, follow [reauth.md](reauth.md) steps 4–6.
3. Confirm the extension now shows the expected email. Use a computer-use
   screenshot when available. Otherwise the user confirms.
4. Run `ai-sub extension done PROVIDER [NAME]`. Never record a switch without that
   confirmation.
5. Reconnect browser tools if needed (`/chrome` in Claude Code).

Not checked: whether Claude Code's Chrome integration requires the extension and
the CLI to use the same account.
