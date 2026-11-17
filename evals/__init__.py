"""Spotter's evals: plain sentences sent to an assistant (the Claude CLI, Codex or a Gemini model), judged by what
the server received.

python -m evals.run    runs every case in cases.json (see docs/evals.md)
python -m evals.need   says whether a change needs an eval run

judge.py         the pure parts: loading cases, the output parser, the judging, the token mask, the table
issuer.py        the fake sign-in provider and token signing, copied from tests/conftest.py
recorder.py      the ASGI wrapper that records every tools/call the server receives, and each data read's answer
run.py           the harness: the local server, the throwaway people, the runs, the table
codex_client.py  the Codex client: codex exec --json, its command and its events
gemini_client.py the Gemini client: this process as the MCP host, generateContent paced and backed off
four_weeks.py, sixteen_weeks.py  the two seeds that take no data
need.py          whether a change needs an eval run
"""
