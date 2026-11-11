"""The lines in lane A's files that keep a numeric format spec, read by tests/text_rules.py (T1).

One list, KEPT, of (path, the line's text) pairs: the path in posix form from the repository root,
the line's text as it stands in the file. A kept line is found by its text, not its number. Read as a
literal, never imported. Keep it short: every other number goes through app/formatting.py.

The list is empty today: no line in lane A's files keeps a format spec. An entry goes here only when
a line must keep one, and it comes out when that line goes.
"""

KEPT = []
