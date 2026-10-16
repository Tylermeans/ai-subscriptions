# Source this file in zsh or bash. It only changes PATH in the current shell.
if [ -n "${ZSH_VERSION:-}" ]; then
  _ai_sub_source="${(%):-%N}"
else
  _ai_sub_source="${BASH_SOURCE[0]}"
fi
_ai_sub_bin="$(cd -- "$(dirname -- "$_ai_sub_source")/bin" && pwd)"
case ":$PATH:" in
  *":$_ai_sub_bin:"*) ;;
  *) export PATH="$_ai_sub_bin:$PATH" ;;
esac
unset _ai_sub_source _ai_sub_bin
