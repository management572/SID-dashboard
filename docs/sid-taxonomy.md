# SID build sheet: tags, fields, workflows, and the Intro Call Form

This is what has to exist inside SID before the board can report anything real. Nothing here is
optional, and the order matters: fields before workflows, workflows before the form, form before the
floor uses it.

Naming grammar throughout: `{client-prefix} {stage}` for tags, `{dept}-{process}-{step}` for
workflow names. Lower case, single spaces, no punctuation.

---

## 1. The attempt definition (fix this first)

**One attempt equals one completed bundle: double dial, voicemail, email, SMS.**

Today the automation increments a counter on each dial, so a double dial reads as two attempts.
Every downstream number (attempts per lead, day-one coverage, per-setter productivity) is inflated by
roughly 2x, which means the floor looks like it is working leads twice as hard as it is.

The fix: the counter increments **once, at the end of the bundle**, not inside the dial step.

```
Workflow: sales-attempt-bundle
  trigger      : setter starts an attempt (Intro Call Form opened, or dial 1 placed)
  step 1       : dial 1
  step 2       : dial 2 (the double dial)
  step 3       : voicemail drop
  step 4       : email send
  step 5       : SMS send
  step 6       : increment attemptCount by 1        <-- ONE increment, here, at the end
  step 7       : stamp lastAttemptDate = now
  step 8       : if this is the first attempt, stamp firstAttemptDate = now
  step 9       : if now is inside 24h of leadDate, increment dayOneAttemptCount by 1
```

The board reports `dials` and `attempts` as separate columns so this stays honest. Healthy is about
2.0 dials per attempt. If that ratio falls under 1.6, the counter is incrementing per dial again and
the board raises `ATTEMPT_INTEGRITY` above every other number.

**Cadence:** 7 attempts inside the first 24 hours (day-one sprint), then the 48-hour sweep, then
long-term nurture at 12 attempts.

---

## 2. Tags

Per home-care client, prefix `{p}`:

| Tag | Set when | Set by |
|---|---|---|
| `{p} lead` | Facebook Lead Form submission lands in SID | inbound workflow |
| `{p} worked` | first attempt bundle completes | `sales-attempt-bundle` |
| `{p} contacted` | live connect (human answered) | Intro Call Form outcome |
| `{p} booked` | appointment booked | Intro Call Form outcome = booked |
| `{p} show` | appointment held | calendar status or CS confirmation |
| `{p} won` | care agreement signed | client-reported, CS stamps |
| `{p} nurture` | past sweep, no engagement | priority workflow |
| `{p} dq` | disqualified | Intro Call Form outcome = dq |

Rules: one stage tag at a time per client. Deepest stage wins when classifying. `nurture` and `dq`
are terminal and never override a real stage.

**Priority lane tags** (exactly one at a time, recalculated on every engagement event):

| Tag | Meaning |
|---|---|
| `{p} p1` | engaged in the last 24h (opened, clicked, replied, inbound), call first |
| `{p} p2` | engaged in the last 7d, or a fresh lead under 24h old and unworked |
| `{p} p3` | no engagement, inside the 48-hour sweep |
| `{p} p4` | no engagement, past sweep, long-term nurture |

P1 pushes a Slack alert into `obb-lead-alerts`. Alert payload is the minimum set in
`compliance.md`, nothing more.

---

## 3. Custom fields

DATE fields (the counting spine, stamped the instant the event fires):

| Field | Stamped by |
|---|---|
| `leadDate` | inbound lead workflow |
| `firstAttemptDate` | `sales-attempt-bundle`, step 8 |
| `firstConnectDate` | Intro Call Form, on a connect outcome |
| `bookedDate` | Intro Call Form, outcome = booked |
| `showDate` | appointment held |
| `wonDate` | CS, on care agreement signed |

NUMBER and TEXT fields (the attempt engine):

| Field | Type | Notes |
|---|---|---|
| `attemptCount` | number | increments once per bundle |
| `dayOneAttemptCount` | number | increments only inside 24h of `leadDate` |
| `lastAttemptDate` | date | drives the sweep and nurture routing |
| `assignedSetter` | text or user | set by the Intro Call Form setter dropdown |

Paste every resulting field ID into `config/ghl-config.json`. The board reads IDs, not names,
because names get edited.

---

## 4. The Intro Call Form (the single write path)

This is the fix for "lead data lives in Slack" and "we cannot QC when data was entered." The Slack
buttons (Re-engage, Leave Prospect Updates, Closed Deal) stop being the write path. They can stay as
notifications; they no longer carry data.

Every worked lead gets exactly one submission per attempt cycle.

| Field | Type | Why it exists |
|---|---|---|
| Setter | dropdown, required | Attribution key. Also the auto-assign trigger: submitting assigns the lead to that setter |
| Client agency | dropdown, required | Sets the tag prefix, and the number the dialer should call from |
| Outcome | dropdown, required | `booked`, `callback`, `no answer`, `not interested`, `dq`, `wrong number`. Values must match the config exactly |
| Callback at | datetime, conditional | Required when outcome is `callback` |
| Booked at | datetime, conditional | Required when outcome is `booked` |
| Notes | text, optional | **Stays in SID. Never reaches the dashboard data file.** |

Submission timestamp is the QC clock: it tells you when the data was entered, not just what it says.
Form-submission compliance (worked leads with a form on file) is a first-class board metric because
below about 95 percent the per-setter numbers stop being trustworthy, and the board says so before
it shows a ranking.

---

## 5. Assignment

Leads auto-assign on Intro Call Form submission (setter dropdown). For unworked leads, round-robin
the active roster inside floor hours, and hold in an unassigned queue outside them. The board
surfaces `unworkedNow` and `p1QueueDepth` so an unassigned pile is visible in minutes, not at the
end of the week.

---

## 6. Known open blocker: dialer local presence

Multiple client agencies in the same metro means an auto-dialer can pick the wrong client's outbound
number. Wave could not resolve this. AloeWare (aloeware.io, about $20 per user per month) has a
local-presence solution and is the current candidate; Dialer.io is the more expensive alternative.

This does not block the board. The board reports `dials` from whatever dialer is connected, and
reports `attempts` from SID regardless. Set `sources.dialer` in `intake.yaml` when it is decided. If
no dialer is wired, the dials column renders as null and the attempt-integrity check is skipped
rather than showing a false alarm.
