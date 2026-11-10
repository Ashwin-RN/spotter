"""Spotter's evals: plain sentences sent to Claude through the Claude CLI, judged by what the server received.

python -m evals.run    runs every case in cases.json (see docs/evals.md)

judge.py    the pure parts: loading cases, the CLI output parser, the judging, the token mask
issuer.py   the fake sign-in provider and token signing, copied from tests/conftest.py
recorder.py the ASGI wrapper that records every tools/call the server receives
run.py      the harness: the local server, the throwaway people, the CLI runs, the table
"""
