---
description: Run the test suite, scan the repo for secrets, and probe a running Spotter server.
argument-hint: <server address, e.g. https://spotter.example.app>
---

Check this Spotter build. Run each step, then report one line per step: passed or failed, with the reason.

1. **Tests.** Run the full suite with the repo's virtual environment
   (`.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere): `python -m pytest -q`, then
   again with `-W error`. Quote the summary lines.

2. **Secrets.** Search the tracked files (`git ls-files`) for anything that looks like a secret:
   provider API keys (`sk_live_`, `sk_test_`), private keys (`BEGIN .*PRIVATE KEY`), database
   addresses with a password (`postgres(ql)?://[^:]+:[^@]+@`), and assignments to `WORKOS_API_KEY`,
   `SESSION_SECRET` or `DATABASE_URL` with a value. Also confirm `.env` and `*.db` are not tracked.
   Report each hit by file and line, never by printing its value. Values in tests that are plainly
   made up for the test are fine; say so.

3. **Probe.** If an address was given, run `python tools/probe.py $ARGUMENTS` and quote its PASS and
   FAIL lines. Then fetch `$ARGUMENTS/health`, `$ARGUMENTS/` and `$ARGUMENTS/settings` (without
   following redirects) and report the status codes: `OK`, 200, and a redirect to `/sign-in`. With
   no address, say the probe was skipped.

Change nothing. If a step fails, say what failed and stop there for that step.
