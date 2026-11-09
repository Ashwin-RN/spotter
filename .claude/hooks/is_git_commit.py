"""Reads the hook input (JSON) on stdin. Exits 0 when its shell command runs `git commit`, 10 when it
does not. Any other exit (a crash) is treated by the caller as a commit, so the tests still run.

The command is split into shell words, so `git -C dir commit`, `git --no-pager commit`, `git.exe commit`,
extra spaces and a commit late in an `&&` chain are all found, while `git commit-tree` and a quoted
"git commit" inside another command's argument are not. A string handed to `bash -c` or
`powershell -Command` is checked the same way.
"""

import json
import re
import shlex
import sys

NOT_A_COMMIT = 10
# git's global options that take their value as the next word.
VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env",
                 "--exec-path", "--attr-source"}
SHELL_STRING_FLAGS = {"-c", "-lc", "-Command", "-command", "/c", "/C"}
FALLBACK = re.compile(r"""git(\.exe)?(\s+(-[cC]\s+\S+|--[a-z-]+(=\S+)?))*\s+commit([\s"'\\;&|)]|$)""")


def _is_git(word: str) -> bool:
    return re.split(r"[\\/]", word)[-1].lower() in ("git", "git.exe")


def runs_commit(command: str, depth: int = 0) -> bool:
    try:
        words = list(shlex.shlex(command, posix=True, punctuation_chars=True))
    except ValueError:  # unbalanced quotes: fall back to a pattern over the raw text
        return bool(FALLBACK.search(command))
    for i, word in enumerate(words):
        if depth < 3 and i > 0 and words[i - 1] in SHELL_STRING_FLAGS and runs_commit(word, depth + 1):
            return True
        if not _is_git(word):
            continue
        j = i + 1
        while j < len(words) and words[j].startswith("-"):
            j += 2 if words[j] in VALUE_OPTIONS else 1
        if j < len(words) and words[j] == "commit":
            return True
    return False


def main() -> int:
    raw = sys.stdin.read()
    try:
        command = json.loads(raw)["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        command = raw
    if not isinstance(command, str):
        command = raw
    return 0 if runs_commit(command) else NOT_A_COMMIT


if __name__ == "__main__":
    sys.exit(main())
