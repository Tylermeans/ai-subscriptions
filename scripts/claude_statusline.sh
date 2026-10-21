#!/bin/zsh
# Claude Code status line: host · repo:branch[*] · subdir · model. Installed by install.py.
# ai-sub chains this (AI_SUB_PREVIOUS_STATUSLINE) and prints its account/quota line below it.
input=$(cat)
dir=$(print -r -- "$input" | jq -r '.workspace.current_dir // .cwd // empty')
model=$(print -r -- "$input" | jq -r '.model.display_name // empty')
[[ -z $dir ]] && dir=$PWD

host=${HOST%%.*}
c_dim=$'\e[2m' c_repo=$'\e[1;36m' c_br=$'\e[35m' c_dirty=$'\e[33m' c_off=$'\e[0m'

if top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null); then
  repo=${top:t}
  branch=$(git -C "$dir" symbolic-ref --short -q HEAD || git -C "$dir" rev-parse --short HEAD 2>/dev/null)
  dirty=""
  [[ -n $(git -C "$dir" --no-optional-locks status --porcelain -uno 2>/dev/null | head -1) ]] && dirty="${c_dirty}*"
  sub=${dir#$top}; sub=${sub#/}
  where="${c_repo}${repo}${c_off}:${c_br}${branch}${dirty}${c_off}"
  [[ -n $sub ]] && where+=" ${c_dim}${sub}${c_off}"
else
  where="${c_repo}${dir/#$HOME/~}${c_off} ${c_dim}(no repo)${c_off}"
fi

print -r -- "${c_dim}${host}${c_off} · ${where}${model:+ · ${c_dim}${model}${c_off}}"
