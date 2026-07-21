# Autonomous agents

Three agents keep the board alive without anyone pressing a button. They run as GitHub Actions in
this repo, which means the schedule, the code, and the run history all live in one place the client
already owns.

| Agent | File | Schedule | Writes |
|---|---|---|---|
| Data sync | `sid-sync.md` | every 30 min in floor hours, full rebuild nightly | `data/dashboard-data.json` |
| Floor monitor | `floor-monitor.md` | every 15 min, floor hours only | Slack `obb-lead-alerts` |
| QC auditor | `qc-auditor.md` | daily after floor close | Slack digest, GitHub issue on a defect |

## Rules all three share

**Secrets.** `SID_TOKEN` and `SLACK_WEBHOOK_URL` come from GitHub Actions secrets. Never in the repo,
never printed to a log. Redact before any output.

**Idempotent.** Safe to re-run. A double run does not double-post or double-commit.

**Degrade, do not corrupt.** Validate every generated data file against `docs/data-schema.md` before
writing. A failed or partial pull leaves the last good file in place and lets the board show a stale
badge. An empty board is worse than an old board, and a wrong board is worse than both.

**Quiet outside floor hours.** Floor hours, days, and timezone come from `intake.yaml > floor`. No
alerts on evenings, weekends, or holidays. An alert nobody can act on trains people to ignore alerts.

**Minimum payload.** Any message leaving this repo carries priority, client agency, first name plus
last initial, minutes waiting, and the SID deep link. Nothing else, ever. See `docs/compliance.md`.

**Escalate to a human, do not act.** These agents report and alert. They do not reassign leads, edit
contacts, change workflows, or write anything back to SID. Writing to the CRM is a human decision in
this build.
