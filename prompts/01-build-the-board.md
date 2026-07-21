# PROMPT 01: Build the board

Paste everything below the line into Claude Code, opened in the root of the `obb-setter-dashboard`
repo, with this kit copied in. Safe to run immediately: it touches no live system and writes nothing
to SID.

---

You are building the **OBB Setter Floor Dashboard**: a live, access-gated board over the SID CRM
(GoHighLevel) that reports what the SDR floor actually did, per setter, per home-care client, per
lead. It is an install of a proven high-ticket funnel dashboard, reskinned and reconfigured, so do
not invent structure.

**Read these first, in this order. They are the single source of truth and everything on the board
derives from them:**

1. `intake.yaml`: the client, the floor, the roster, the goals
2. `config/brand.json`: colors, fonts, logo
3. `config/ghl-config.json`: SID location, tag taxonomy, field IDs
4. `config/dashboard-config.json`: tabs, tiles, columns, goal bands
5. `docs/dashboard-spec.md`: exact layout and rules
6. `docs/data-schema.md`: the exact JSON shape the board consumes
7. `docs/compliance.md`: what may and may not appear on this board

## Context you need to hold

OBB runs outbound SDR calling for home-care agencies. Facebook Lead Forms feed SID, the setter floor
works the leads, appointments get booked for the agency. This board is the scoreboard for that floor.

They have no setter reporting today. Attempts are tracked by a broken automation that counts a double
dial as two attempts. Lead data lives in Slack instead of the CRM. There is no way to QC when data
was entered or get per-rep funnel stats. **The Setters tab is the deliverable that fixes this.**

There is also history: a previous dashboard sat on a public URL where a guessable client acronym was
the only access control. Nothing you build goes on a public URL, and no protected health information
reaches the data file. Read `docs/compliance.md` before writing the pipeline.

## What to build, in order

**1. `index.html`**: the board, exactly per `docs/dashboard-spec.md`. Three tabs (Floor, Setters,
Clients), the data-quality strip above everything, the tile row, insights, priority lanes, funnel,
trend chart, the sortable setter table with sparklines and badges, the client table with expandable
per-client funnels. Read brand tokens from `config/brand.json` into CSS variables and drive the
entire look from them. No hard-coded colors, labels, thresholds, or numbers anywhere in the HTML.

**2. `pipeline/fetch_sid.py`**: pull contacts, opportunities, tags, users, form submissions, and the
event-date and attempt custom fields from the SID location in `config/ghl-config.json`. Token from
the `SID_TOKEN` environment variable, never hard-coded. Count contacts by their event-DATE custom
field inside the window, cumulative, never by current pipeline stage. Classify each contact to a
client by its deepest-stage tag.

**3. `pipeline/build_data.py`**: transform into `data/dashboard-data.json` in exactly the shape in
`docs/data-schema.md`. Compute rates, medians (not means), lags, day-one coverage, form compliance,
and the ranked insights. Run every rule in the "Rules the pipeline must honor" section of the schema
doc, including the `ATTEMPT_INTEGRITY` check and the funnel-widening check. Write `null`, never a
zero, for anything that cannot be computed.

**4. `.env.example`, `.gitignore`, `RUNBOOK.md`**: env var names with no values, ignore `.env`,
`__pycache__`, and any token file. The runbook covers running the pull, reading the board, and what
each data-quality flag means.

## Build against the sample first

Copy `data/dashboard-data.sample.json` to `data/dashboard-data.json` and render. Confirm the layout,
the brand, the badges, the goal-band coloring, and the empty states before anything touches live
data. The sample deliberately includes a fired data-quality flag and one below-SLA setter so you can
verify both render correctly.

The palette is live and contrast-checked. Two rules follow from OBB's background being a mid-tone
slate rather than a near-black, and both are in `docs/dashboard-spec.md` under "Color rules":
the accent `#2974ED` is a fill color and never text on the background, and status is carried by a
filled chip rather than colored text. Read `config/brand.json > _contrastRule` before styling.

## Rules that came from the reference build (keep them)

- Count cumulative event-date fields, never current-stage snapshots.
- An attempt is a completed bundle (double dial, voicemail, email, SMS), not a dial. Show attempts
  and dials as separate columns.
- Deepest-stage tag wins when classifying; terminal tags (`dq`, `nurture`) rank lowest.
- A downstream stage may never exceed its upstream stage. If it does, fall back to the stage tag and
  raise a data-quality flag.
- Speed to lead is a median, and the SLA clock does not run outside floor hours.
- New setters get a badge and are exempt from warnings, not hidden.
- Tokens live in env only, never in the repo or in chat.
- No PHI in `data/dashboard-data.json`. Aggregates and setter names only.
- No em dashes or en dashes in any copy. Use commas, colons, or parentheses.

## When you are done, give me

The local path to `index.html`, a screenshot or description of what renders on each tab, any TODO in
the config that is now blocking live data, and the exact commands to run the pull.

Do not deploy anything, do not write to SID, and do not create a public URL. Those are prompts 02
and 04.
