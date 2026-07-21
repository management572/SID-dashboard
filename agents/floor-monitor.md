# Agent: floor-monitor

**Job:** catch a lead going cold while there is still time to do something about it. This is the
agent that makes the board worth having, because a dashboard nobody is looking at cannot save a lead.

## Schedule

Every 15 minutes, floor hours only. Silent outside them.

## What it watches

| Condition | Threshold | Severity |
|---|---|---|
| P1 lead unworked | past `goals.speedToLeadSlaMin` | alert |
| Any fresh lead unworked | past 2x the SLA | alert |
| Unworked queue depth | over 15 leads at once | alert |
| Unassigned leads inside floor hours | any, for over 20 minutes | warn |
| Setter with zero attempts | 90 minutes into a floor shift | warn |
| Attempt integrity | dials per attempt under 1.6 | alert, once per day |

Thresholds come from `config/dashboard-config.json > goals` and `intake.yaml`. Do not hard-code them
here: the floor lead will want to tune these in the first two weeks.

## Alert rules

- Post to Slack `obb-lead-alerts` (existing OBB channel convention).
- **Payload is the minimum set:** priority lane, client agency, first name plus last initial, minutes
  waiting, SID deep link. No notes, no phone, no email, nothing about the person's situation. See
  `docs/compliance.md`.
- **Deduplicate.** Alert once per lead per severity band. Re-alert only when severity escalates, and
  never more than twice for the same lead. State lives in a small committed JSON or the Actions cache.
- **Batch.** If more than 5 leads breach in one run, post one summary message with a count and the
  top 3, not 20 messages. A wall of alerts is the same as no alerts.
- **Suppress on a known outage.** If the last `sid-sync` failed, the data is stale, so post one
  "monitoring paused, data is stale" notice instead of alerting on old numbers.

## Does not

Reassign leads. Edit contacts. Write to SID. Call anyone. It reports to humans who decide.
