# RUNBOOK: OBB Setter Floor Dashboard

Operating instructions for the board: how to look at it, how to pull fresh numbers, and what every
data-quality flag means. This is the day-to-day reference. Setup and deploy live in `prompts/`.

---

## 1. Look at the board

The board is one static page (`index.html`) that reads three files at load: `config/brand.json`,
`config/dashboard-config.json`, and `data/dashboard-data.json`. It needs to be served over http,
because browsers block `fetch()` on `file://` URLs.

```bash
# from the repo root
python3 -m http.server 8971
# then open http://127.0.0.1:8971/index.html
```

On first open, before any live pull, the board renders the committed sample
(`data/dashboard-data.sample.json`). A loud red `SAMPLE DATA` tag sits in the masthead so nobody
presents placeholder numbers by accident. The freshness badge next to it is green under 60 minutes,
amber over, red over 180.

### Reading each tab

- **Floor**: is the floor healthy right now. Six tiles (each colored against its goal band), the
  ranked "what to do next" list, the priority queue depth, the funnel, and the trend chart. The
  `Unworked Right Now` hero tile is the only number losing money while you look at it.
- **Setters**: the priority-#1 deliverable. Sortable on every column, default sort by book rate.
  Attempts and Dials are separate columns on purpose: the ratio is the attempt definition working
  correctly. A `NEW` badge means the setter is inside ramp and exempt from the SLA warning; an `SLA`
  badge means median speed to lead is over the SLA.
- **Clients**: one row per home-care agency plus the roll-up. Expand a row for that client's funnel
  and the median lag between each stage, which is what CS needs on an escalation call.

The client toggle in the controls row filters the Clients tab and re-computes the Floor funnel for
the selected agency. Period switching activates only with live data (see below).

---

## 2. Pull fresh numbers

Two scripts. `fetch_sid.py` reads SID and dumps raw JSON; `build_data.py` transforms it into the one
file the board reads. Neither writes to SID, and neither publishes anything.

```bash
# one-time: copy the env template and fill it in (never commit .env)
cp .env.example .env
# edit .env and set SID_TOKEN=...

# load the token into the shell, then run the pull
export $(grep -v '^#' .env | xargs)
python3 pipeline/fetch_sid.py            # -> data/raw/*.json
python3 pipeline/build_data.py           # -> data/dashboard-data.json
```

Windows: `python pipeline\fetch_sid.py`, and set `SID_TOKEN` with `set` or in the environment.

Options:

```bash
python3 pipeline/fetch_sid.py --days 30                 # a 30-day window instead of the default 7
python3 pipeline/fetch_sid.py --since 2026-07-01 --until 2026-07-21
python3 pipeline/build_data.py --sample                # rebuild the render file from the sample, no pull
```

The token comes from the `SID_TOKEN` environment variable only. It never lives in a config file, in
the repo, or in chat. See `docs/compliance.md`.

### Before the first live pull, these config TODOs must be filled (prompt 02 produces the IDs)

`config/ghl-config.json` still has placeholder IDs. Live numbers stay `null` until they are set:

- `divisions[]`: one row per client agency with a real `key`, `label`, and `tagPrefix`.
- `eventDateFieldIds`: the DATE custom field IDs (leadDate, firstAttemptDate, firstConnectDate,
  bookedDate, showDate, wonDate). The funnel counts by these, so it stays empty without them.
- `attemptFieldIds`: attemptCount, lastAttemptDate, dayOneAttemptCount, assignedSetter.
- `introCallForm`: formId and setterFieldId. Form compliance and per-setter attribution need these.
- `pipeline.id` and `users` map (SID user ID to setter display name).

The location ID (`6nyOgOLPgBOHWJRtt3jQ`) is already set. Dials stay `null` until a dialer token is
wired (`DIALER_TOKEN` in `.env`); the board shows a dash and skips the attempt-integrity alarm rather
than showing a false one.

---

## 3. What each data-quality flag means

Flags render in a strip **above every number**, because a broken input matters more than any metric
it feeds. The board keeps rendering when a flag fires; it just refuses to look confident.

| Code | Tone | What it means | What to do |
|---|---|---|---|
| `ATTEMPT_INTEGRITY` | alert | Dials per attempt fell under 1.6, so the attempt counter is likely incrementing per dial again instead of once per completed bundle. Every attempt count below is inflated. | Fix the sales-attempt-bundle workflow in SID before acting on per-setter attempt numbers. |
| `FORM_COMPLIANCE_LOW` | warn | Fewer worked leads have an Intro Call Form on file than the target. Per-setter attribution depends on the form's setter dropdown, so low-compliance setters are undercounted. | Get the floor submitting the Intro Call Form on every worked lead. |
| `FUNNEL_WIDENED` | warn | A downstream stage exceeded its upstream stage for a client, which is always a data bug. The board fell back to stage tags and capped the count. | Check that client's date-field workflows fired in order. |

---

## 4. The rules the numbers obey (so you can trust them)

- Counts are cumulative by event **date field**, never by current pipeline stage. A contact that
  moved past Booked still counts in Booked for the window it was booked.
- An attempt is a completed bundle (double dial, voicemail, email, SMS), not a dial. Attempts and
  dials are separate columns.
- Speed to lead is a **median**, and the SLA clock does not run outside floor hours.
- A downstream stage can never exceed its upstream stage; if it does, the board flags it.
- New setters (inside `goals.rampDays`) show their numbers but are exempt from the SLA warning.
- Anything that cannot be computed renders as a dash, never a zero.
- No PHI ever reaches `data/dashboard-data.json`: aggregates, setter names, and client agency names
  only. See `docs/compliance.md`.

---

## 5. Files

```
index.html                     the board (open this)
config/brand.json              colors, fonts, logo. A rebrand is a token swap here
config/dashboard-config.json   tabs, tiles, columns, goals, bands
config/ghl-config.json         SID location, tag taxonomy, field IDs
pipeline/fetch_sid.py          pulls raw SID records into data/raw/
pipeline/build_data.py         transforms raw into data/dashboard-data.json
data/dashboard-data.sample.json  committed sample, used before the first live pull
data/dashboard-data.json       generated, gitignored, current window only
docs/                          spec, schema, taxonomy, compliance
```

Do not deploy from this runbook. Publishing behind Cloudflare Access is prompt 04, and it is gated on
approval. Nothing here goes on a public URL.
