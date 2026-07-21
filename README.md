# OBB Setter Floor Dashboard

A live, access-gated board over the **SID** CRM (GoHighLevel) that reports what the SDR floor
actually did: per setter, per home-care client, per lead. Plus the autonomous agents that keep it
current and flag a lead going cold while there is still time to act.

Built on the High-Ticket Funnel Dashboard OS. Prepared by Offer Accelerator for Online Business
Builders.

---

## Quick start

```bash
# 1. copy this kit into the repo root, then
cp data/dashboard-data.sample.json data/dashboard-data.json
cp .env.example .env          # fill in SID_TOKEN, never commit this

# 2. in Claude Code, paste prompts/01-build-the-board.md
# 3. open index.html
```

Full sequence and what each step touches: **[START-HERE.md](START-HERE.md)**.

---

## Layout

```
obb-setter-dashboard/
  START-HERE.md              # read this first
  README.md
  intake.yaml                # client, floor, roster, goals. Single source of truth
  config/
    brand.json               # colors, fonts, logo. Rebrand is a token swap here
    ghl-config.json          # SID location, tag taxonomy, field IDs, alert channel
    dashboard-config.json    # tabs, tiles, columns, goal bands, privacy
  docs/
    sid-taxonomy.md          # the SID build sheet: tags, fields, workflows, Intro Call Form
    dashboard-spec.md        # layout, tabs, rendering rules
    data-schema.md           # exact JSON shape the board consumes
    compliance.md            # PHI rules, access model, go-live checklist
  prompts/
    01-build-the-board.md    # safe, no live systems
    02-sid-crm-build.md      # writes to SID, gated on approval
    03-connect-live-data.md  # read-only, includes the reconciliation checklist
    04-deploy-agents.md      # publishes and schedules, gated on approval
  agents/
    sid-sync.md              # 30 min refresh in floor hours
    floor-monitor.md         # 15 min SLA and P1 alerting to obb-lead-alerts
    qc-auditor.md            # daily data-integrity audit
  data/
    dashboard-data.sample.json
  assets/                    # logo
  pipeline/                  # written by prompt 01: fetch_sid.py, build_data.py
  index.html                 # written by prompt 01
```

---

## What it reports

**Floor tab.** Leads in, unworked right now, median speed to lead, day-one coverage, form
compliance, and P1 to P4 queue depth. The health of the floor at a glance.

**Setters tab.** The per-rep scoreboard OBB does not have today: assigned, attempts, dials, connects,
connect rate, booked, book rate, held, held rate, speed to lead, form compliance. Sortable, with new-
setter and below-SLA badges.

**Clients tab.** Per home-care agency funnel and where each client's leads stall, which is what CS
needs on an escalation call.

Above all of it, a **data-quality strip**. When the inputs are wrong, the board says so before it
shows a number.

---

## Two things that are not negotiable

**The attempt definition.** One attempt is one completed bundle: double dial, voicemail, email, SMS.
The counter increments once, at the end of the bundle. The board shows attempts and dials as separate
columns so a regression is visible without reading a config file. See `docs/sid-taxonomy.md`.

**No public URL, no PHI.** Private repo, Cloudflare Access allowlist, aggregate data only. This is
the direct fix for the previous dashboard that sat on a guessable public URL. See
`docs/compliance.md`.

---

## Secrets

`SID_TOKEN` and `SLACK_WEBHOOK_URL` live in `.env` (gitignored) locally and in GitHub Actions secrets
for the agents. Never in the repo, never in a config file, never pasted into a chat.
