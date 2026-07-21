# OBB Setter Floor Dashboard: START HERE

This folder is the complete drop-in kit for OBB's setter reporting system: a live, gated dashboard
over the SID CRM that shows what the SDR floor actually did, per setter, per client, per lead.

It is an install of the **High-Ticket Funnel Dashboard OS** (Offer Accelerator IP), configured for
OBB's fulfillment engine: Facebook Lead Forms feed SID, the setter floor works the leads, and this
board is the scoreboard.

---

## What this fixes (OBB's Priority #1)

| Problem today | What the board does |
|---|---|
| No standard way to track SDR attempts | One attempt definition, enforced in the CRM and counted on the board |
| Automation counts a double dial as 2 attempts | An attempt is a **bundle** (double dial + VM + email + SMS = ONE attempt), stamped once |
| Lead data lives in Slack, not centralized | SID is the system of record; Slack becomes alerts only |
| No form-submission tracking, can't QC | Intro Call Form is the single write path; the board reports submission compliance per setter |
| No per-rep funnel stats | Setters tab: attempts, connects, book rate, show rate, speed-to-lead, per rep |
| Old dashboard was a public URL (HIPAA exposure) | Private repo, Cloudflare Access gating, zero PHI on the board |

---

## The three things in this folder

1. **`config/`**: the single source of truth. Brand tokens, SID (GHL) IDs, goals and SLA bands.
   Everything on the board derives from these three JSON files. Nothing is hard-coded.
2. **`prompts/`**: four copy-paste prompts, run in order, in Claude Code opened in the repo.
3. **`agents/`**: the autonomous data agents (sync, floor monitor, QC auditor) and the GitHub
   Actions schedule that runs them without anyone pressing a button.

---

## How to run it (in order)

Open Claude Code in the root of the OBB dashboard repo with this kit copied in, then paste each
prompt as its own message:

| Step | Prompt | What happens | Touches live systems? |
|---|---|---|---|
| 1 | `prompts/01-build-the-board.md` | Builds `index.html` + the pull pipeline, renders against sample data | No |
| 2 | `prompts/02-sid-crm-build.md` | Produces the SID build sheet: tags, date fields, workflows, Intro Call Form | Yes, gated on approval |
| 3 | `prompts/03-connect-live-data.md` | Swaps sample data for live SID, reconciles the numbers | Read only |
| 4 | `prompts/04-deploy-agents.md` | Deploys the sync agents, GitHub Actions cron, Cloudflare Pages + Access | Yes, gated on approval |

Step 1 is safe to run immediately and produces something you can look at. Steps 2 and 4 stop and
ask before changing anything in SID or publishing a URL.

---

## Before step 1, fill these

- `config/brand.json`: done, palette is live and contrast-checked. Still needs the logo file dropped
  into `assets/` as `logo.svg`.
- `config/ghl-config.json`: SID location ID, pipeline ID, custom-field IDs (step 2 produces the IDs).
- `intake.yaml`: client roster, floor hours, setter names, targets.

Everything else derives. A rebrand is a token swap. A new home-care client is one row in the roster.

---

## Two hard rules

**No PHI on the board, ever.** Aggregate counts, setter names, and client-agency names only. No
diagnoses, no addresses, no dates of birth, no free-text notes. See `docs/compliance.md`.

**No public URL, ever.** The board deploys behind Cloudflare Access with an allowlist. The repo is
private. This is the direct fix for the old publicly-guessable dashboard.

---

Method and full reference: `../../HIGH-TICKET-FUNNEL-DASHBOARD-OS.md`
Prepared by Offer Accelerator for Online Business Builders.
