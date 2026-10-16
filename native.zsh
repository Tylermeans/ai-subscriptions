# Optional shell integration for zsh and bash. Source in your interactive shell.
# Functions do not affect Foreman workers or the binaries used by ai-sub itself.
if [ -n "${ZSH_VERSION:-}" ]; then
  _ai_sub_native_source="${(%):-%N}"
elif [ -n "${BASH_VERSION:-}" ]; then
  _ai_sub_native_source="${BASH_SOURCE[0]}"
else
  printf '%s\n' 'ai-sub: native.zsh requires zsh or bash.' >&2
  return 1
fi
source "$(dirname -- "$_ai_sub_native_source")/activate.zsh"
unset _ai_sub_native_source

# Do not replace existing aliases/functions without telling the user to resolve
# the conflict. In particular, preserve custom permission flags in their aliases.
if [ "${_AI_SUB_NATIVE_ENABLED:-}" != 1 ]; then
  if alias claude >/dev/null 2>&1 || alias codex >/dev/null 2>&1 ||
     typeset -f claude >/dev/null 2>&1 || typeset -f codex >/dev/null 2>&1; then
    printf '%s\n' 'ai-sub: an existing claude/codex alias or function conflicts with native.zsh; it was left unchanged.' >&2
    return 1
  fi
  function claude { command ai-sub native claude -- "$@"; }
  function codex { command ai-sub native codex -- "$@"; }
  _AI_SUB_NATIVE_ENABLED=1
fi
