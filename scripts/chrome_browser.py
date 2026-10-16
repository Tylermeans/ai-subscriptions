#!/usr/bin/env python3
"""BROWSER adapter: open only official provider authorization origins in Chrome."""
import os
import sys
from urllib.parse import urlparse
from ai_sub import chrome_open, UserError

ALLOWED = {"auth.openai.com", "auth0.openai.com", "chatgpt.com", "platform.openai.com",
           "claude.ai", "platform.claude.com", "console.anthropic.com", "accounts.anthropic.com"}

def main():
    urls = [arg for arg in sys.argv[1:] if arg.startswith("https://")]
    if len(urls) != 1 or urlparse(urls[0]).hostname not in ALLOWED:
        raise UserError("Browser adapter accepts one official provider authorization URL.")
    chrome_open(urls[0], os.environ.get("AI_SUB_CHROME_PROFILE"))

if __name__ == "__main__":
    try:
        main()
    except (UserError, OSError):
        print("Could not open Chrome. Open the CLI's login link in Chrome manually.", file=sys.stderr)
        sys.exit(1)
