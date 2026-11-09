#!/usr/bin/env bash
# PreToolUse hook: before a `git commit` that Claude Code runs, run the test suite; block the commit
# (exit 2, reason on stderr) when it fails. Any other command passes straight through.
# Works in Git Bash on Windows and in bash on Linux. Reads the hook input (JSON) on stdin.

input=$(cat)

# Cheap first cut: no "commit" anywhere, nothing to check.
case "$input" in
  *commit*) ;;
  *) exit 0 ;;
esac

here=$(cd "$(dirname "$0")" && pwd)
cd "${CLAUDE_PROJECT_DIR:-$here/../..}" || { echo "Cannot find the project folder." >&2; exit 2; }

if [ -e .venv/Scripts/python.exe ]; then
  python=.venv/Scripts/python.exe
elif [ -e .venv/bin/python ]; then
  python=.venv/bin/python
else
  python=python3
fi

# is_git_commit.py exits 10 for "not a commit"; anything else, including a crash, gates the command.
printf '%s' "$input" | "$python" "$here/is_git_commit.py"
[ $? -eq 10 ] && exit 0

if output=$("$python" -m pytest -q 2>&1); then
  exit 0
fi

printf '%s\n' "$output" | tail -n 40 >&2
echo "Commit blocked: the test suite failed. Fix the failures above, then commit again." >&2
exit 2
