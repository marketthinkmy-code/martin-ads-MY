---
name: meta-ops
description: >
  How to operate the STOCK BLOOM Meta ads automation (adbot). Use when a Claude Code
  routine or session needs to build the 1-1-10 campaign, run the CPL pause monitor, or
  run the weekly OFF/ON cycle. Each routine runs ONE committed command and reports the result.
---

# Meta ops (adbot)

The business logic lives in the committed `adbot` Python package. A routine's job is to run
the right command and report the final `SUMMARY:` line — never to make ad-spend decisions itself.

## Setup inside a run
```bash
source .venv/bin/activate 2>/dev/null || ./setup.sh
```
Secrets come from the environment (`META_SYSTEM_USER_TOKEN`, `GOOGLE_SERVICE_ACCOUNT_JSON`,
`ANTHROPIC_API_KEY`). The environment's network allowlist must include `graph.facebook.com`,
`www.googleapis.com`, and `api.anthropic.com`.

## Commands
| When | Command |
|---|---|
| Validate everything before going live | `python -m adbot doctor` |
| Download Drive media + upload to Meta | `python -m adbot sync` |
| Build (+auto-activate) the 1-1-10 | `python -m adbot build` |
| Pause ads over the CPL threshold | `python -m adbot monitor` |
| Weekly kill switch (Wed 15:00 GMT+8) | `python -m adbot weekly_off` |
| Resume what Wednesday paused (Thu 00:00) | `python -m adbot weekly_on` |

Add `--dry-run` to any command except `doctor` to preview without writing.

## Safety rules
- Everything is created PAUSED first; activation happens only if `build.activate_after_build`
  is true (it is, at the small CBO budget) or a human activates.
- `monitor` only ever PAUSES; it never re-activates. Re-activation is `weekly_on` or a human.
- The weekly cycle is scoped to ads under campaigns named with the configured prefix and
  coordinated by the `ADBOT_WEEKLY_OFF` ad label — do not pause/resume ads by hand mid-cycle.
- If a command errors, report the error verbatim and STOP. Do not improvise Meta changes.

## What to report
Relay the final `SUMMARY:` line (counts + any pauses/resumes) as the run's outcome. That line
is what the operator reads in the Claude mobile app.
