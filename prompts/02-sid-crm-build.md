# PROMPT 02: Build the SID side

Paste below the line into Claude Code in the same repo, after prompt 01. **This one changes a live
CRM.** It is written to produce a build sheet and stop for approval before touching anything.

---

You are building the SID (GoHighLevel) side of the OBB Setter Floor Dashboard: the tags, custom
fields, workflows, and Intro Call Form that the board reads from. Without this, the board has nothing
real to report.

**Read `docs/sid-taxonomy.md` in full before doing anything.** It is the complete build sheet. Also
read `config/ghl-config.json` (the IDs you will fill in) and `docs/compliance.md`.

## The thing to get right first

**One attempt equals one completed bundle: double dial, voicemail, email, SMS.**

The existing automation increments the attempt counter inside the dial step, so a double dial reads
as two attempts and every per-setter number is roughly doubled. The counter must increment **once,
at the end of the bundle**. The full workflow shape is in `docs/sid-taxonomy.md` section 1. Everything
else on this board is downstream of getting this single step right.

## Work in this order

1. **Audit what already exists in SID.** List the current tags, custom fields, workflows, and forms in
   the location. Do not create a duplicate of something that exists under a different name. Report
   what you found and what conflicts with the taxonomy before proposing changes.
2. **Produce the build sheet as a diff**: every tag, field, workflow, and form field to create,
   rename, or leave alone, with the reason. Group it by what breaks if it is skipped.
3. **Stop and present it.** Do not create anything in SID until Robert or Jeremy (Director of Ops)
   approves the sheet. Say clearly which items are destructive or affect leads currently in flight.
4. **On approval, build in dependency order:** custom fields, then tags, then workflows, then the
   Intro Call Form. A workflow that references a field that does not exist yet fails silently in GHL
   and is painful to debug later.
5. **Backfill check.** Existing contacts have no event-date fields stamped. Report how many contacts
   are affected and propose a backfill (derive `leadDate` from contact creation, `bookedDate` from
   appointment records where possible). Do not run a backfill without explicit approval: it writes to
   every historical record.
6. **Paste every resulting ID** into `config/ghl-config.json`: field IDs, pipeline ID, won-stage ID,
   form ID, setter field ID, and the SID user ID to setter name map. The board reads IDs, not names.
7. **Verify with one live lead.** Walk a single test lead end to end (form submission, attempt bundle,
   connect, booked) and confirm each date field stamps, `attemptCount` increments exactly once, and
   the Intro Call Form assigns the setter. Report the actual field values after each step.

## The Intro Call Form is the write path

The Slack buttons (Re-engage, Leave Prospect Updates, Closed Deal) stop carrying data. They can stay
as notifications. Every worked lead gets one form submission per attempt cycle. The setter dropdown
is both the attribution key and the auto-assign trigger. The submission timestamp is the QC clock.

Form-submission compliance is a first-class metric on the board, because below roughly 95 percent the
per-setter table stops being trustworthy and the board will say so before it shows a ranking.

Notes on the form stay in SID. They never reach the dashboard data file.

## Rules

- Naming grammar: `{client-prefix} {stage}` for tags, `{dept}-{process}-{step}` for workflows. Lower
  case, single spaces, no punctuation.
- One stage tag per client at a time. One priority tag at a time.
- Every date field is stamped by workflow the instant the event fires, never manually.
- P1 alerts into `obb-lead-alerts` carry priority, client, first name plus last initial, minutes
  waiting, and the SID deep link. Nothing else. See `docs/compliance.md`.
- Do not create anything in SID without approval. Do not run a backfill without approval. Do not
  delete or rename an existing workflow that has leads in flight without naming exactly which leads.

## When you are done, give me

The build sheet as executed, the filled `config/ghl-config.json`, the result of the single-lead
verification with actual field values at each step, and anything still blocking (the dialer decision
in `intake.yaml > sources.dialer` is expected to still be open).
