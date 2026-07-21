# PROMPT 04: Deploy the autonomous agents and publish

Paste below the line into Claude Code in the same repo, after prompt 03. **This one publishes a URL
and schedules automation.** Every outward step stops for approval.

---

You are making the OBB Setter Floor Dashboard run itself: scheduled data pulls, a live floor monitor,
a daily QC audit, and an access-gated deploy. Nobody should ever have to press a button to refresh
this board.

Read `agents/README.md` and the three agent definitions in `agents/` first, plus `docs/compliance.md`.

## 1. The data agents (GitHub Actions, in this repo)

Build `.github/workflows/` from the agent definitions:

| Agent | Schedule | Does |
|---|---|---|
| **sid-sync** | every 30 min during floor hours, plus a full rebuild nightly | Runs the pull, rebuilds `data/dashboard-data.json`, commits if changed |
| **floor-monitor** | every 15 min during floor hours only | Detects SLA breaches and P1 leads aging, posts to `obb-lead-alerts` |
| **qc-auditor** | daily, after floor close | Audits attempt integrity, form compliance, orphaned leads, unassigned queue. Posts a digest, opens a GitHub issue on a real defect |

Requirements for all three:

- Secrets come from GitHub Actions secrets (`SID_TOKEN`, `SLACK_WEBHOOK_URL`). Never in the repo,
  never echoed into a log. Redact tokens from all output.
- **Idempotent and safe to re-run.** A double-run must not double-post an alert or double-commit.
- **Alert deduplication.** The floor monitor must not re-alert the same lead every 15 minutes. Alert
  once, then only on escalation (a new severity band), and keep the dedupe state in the repo or in
  the workflow cache, not in a database.
- **Fail loudly, degrade safely.** If the SID pull fails, the workflow fails visibly and the board
  keeps serving the last good data with a stale badge. Never commit a partial or empty data file over
  a good one: validate against `docs/data-schema.md` before writing.
- **Quiet hours.** No alerts outside floor hours or on non-floor days, per `intake.yaml > floor`.
- **Alert payload is the minimum set** in `docs/compliance.md`: priority, client, first name plus last
  initial, minutes waiting, SID deep link. No notes, no contact details, nothing about the person's
  situation. Slack is not a HIPAA-covered surface here.

## 2. The deploy (approval required before this runs)

- Repo visibility **private**. Confirm it, do not assume it.
- Cloudflare Pages project pointed at this repo, building nothing (it is static).
- **Cloudflare Access policy attached before the first successful deploy**, allowlisted to the OBB
  email domain plus named addresses. Then test from a signed-out browser and paste the result: the
  correct outcome is a login prompt, not the board.
- Confirm no token appears anywhere in `git log -p`.

Stop and get explicit approval from Robert before creating the Pages project or attaching a domain.
An unlisted URL is not an access model. This client has been burned by exactly that before.

## 3. Handover

Write `RUNBOOK.md` covering: what each agent does and when, how to read each data-quality flag, how
to add a new home-care client (one row in three configs), how to rotate `SID_TOKEN`, what to do when
the board goes stale, and who to call. Assume the reader is Jeremy or Victor, not the person who
built it.

## When you are done, give me

The workflow files, the schedule in plain English, proof the Access policy blocks a signed-out
browser, the first successful agent run log with tokens redacted, and the runbook path.
