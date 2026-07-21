# PROMPT 03: Connect live data and reconcile

Paste below the line into Claude Code in the same repo, after prompts 01 and 02. Read-only against
SID: it pulls data, it does not write to the CRM.

---

You are replacing the sample data in the OBB Setter Floor Dashboard with live SID data, then proving
the numbers are right before anyone is shown the board.

Prerequisites: `config/ghl-config.json` has real IDs (from prompt 02), and `SID_TOKEN` is set in the
local environment. If either is missing, stop and say what is missing.

## Run it

1. Run `pipeline/fetch_sid.py` for a 7-day window and write the raw pull to `data/raw/` (gitignored).
2. Run `pipeline/build_data.py` to produce `data/dashboard-data.json`. Confirm `sample` is now `false`.
3. Load the board and read every tab.

## Then reconcile, because the first pull is always wrong somewhere

Do not present this board until each of these has been checked against SID directly, and report the
actual numbers on both sides for each:

| Check | How | What a failure means |
|---|---|---|
| Lead count | Board `floor.leadsInRange` vs a SID smart list filtered on `leadDate` in the same window | Window boundaries or timezone are off |
| Attempt integrity | Board `floor.attemptIntegrity` (dials per attempt) | Near 2.0 is healthy. Under 1.6 means the attempt counter is incrementing per dial again: go back to prompt 02 |
| One setter, end to end | Pick one setter, recount their booked from SID by hand | Attribution via the Intro Call Form setter dropdown is broken |
| Funnel monotonicity | No stage exceeds the stage above it | Missing date-field stamps, falling back to tags |
| Form compliance | Worked leads with a form on file, vs SID form submission count | The floor is not using the form yet, so per-setter numbers are not yet trustworthy |
| Speed to lead | Spot-check three leads by hand: created-at to `firstAttemptDate` | The SLA clock is running outside floor hours, or the median is being computed as a mean |
| Timezone | A lead created at 6pm ET appears on the correct day | Everything shifts by a day at the window edges |

## Expect the first pull to show ugly numbers

Form compliance will likely be low because the floor has been using Slack buttons, not the form. Day-
one coverage may be well under target. That is the point: this board exists to make the current state
visible. Do not tune, filter, or cap anything to make the first render look better. If a number looks
wrong, prove it is a data bug before changing it, and if it is real, leave it.

## Report

Give me a reconciliation table with board value, SID value, and delta for every check above. Then the
three numbers you trust least and why. Then the board's own data-quality flags, if any fired.

Do not deploy. That is prompt 04.
