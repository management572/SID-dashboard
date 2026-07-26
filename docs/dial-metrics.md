# SDR dial metrics: $/dial and $/lead

The Clients tab pairs what a client pays us with how much dialer effort it takes to serve them.
Three sources feed it, and only one of them is reachable from the GHL pipeline, so they are wired
differently on purpose.

| Figure | Source | How it gets here |
|---|---|---|
| MRR / ARR / deal count | Closed deals posted in the `#2-wins` Slack channel | Committed snapshot: `data/client-revenue.json` |
| Ad leads | Supabase `facebook_leads` joined to `account_acronyms` | Committed snapshot: `data/client-revenue.json` |
| Contacts / dials | GHL contacts in sub-account `6nyOgOLPgBOHWJRtt3jQ` | **Live** each pipeline run, as `clientDials` |

Every client is keyed by its partner acronym (`AHC`, `LPC`, ...), which is what joins the three.

## Why revenue is a committed file, not a live pull

Neither Slack nor Supabase is reachable from the pipeline's credentials, and neither should be:
the pipeline runs on a schedule against GHL only. So revenue and ad leads are a snapshot that is
replaced when a fresh export is dropped in. `updated` in the file records when.

To refresh it, regenerate the per-client export and rewrite `data/client-revenue.json` in the same
shape (`clients` keyed by uppercase acronym, each with `mrr`, `arr`, `dealCount`, `leads`,
`contacts`, `dials`). The `totals` block is recomputed from the same export.

The board still renders without this file: the money columns simply fall away.

## Why dials are live

Dials come from the **Times Attempted** contact field, not `sid_attempt_count`:

| Field | ID | Use |
|---|---|---|
| `contact.partner_acronym` | `ZYcoQLYKRCXt8T4F7usl` | Which client the contact belongs to |
| `contact.times_attempted` | `nCoJsOEcJMJYYpVF40VW` | The dial counter that actually increments |

`sid_attempt_count` is stuck on the `sid-dial-1` tags and understates badly, which is why the floor
metrics read dials from the call feed instead. `times_attempted` is the counter the belt maintains
per contact, so it is the right basis for a per-client lifetime total.

`build_client_dials()` in `pipeline/build_data.py` sums it per acronym and emits `clientDials` at the
top level of `data/dashboard-data.json`. It is deliberately **not** windowed by period: the field is a
running total on the contact, so slicing it by date would undercount.

The dashboard takes `max(live, snapshot)` rather than preferring live outright. If `times_attempted`
comes back empty the way its `sid_*` predecessor did, a silent drop to zero would read as "we stopped
dialling this client" instead of "the field did not populate".

## Calculations

```
dollarPerDial = totalArr / totalDials    (blank when either is zero)
dollarPerLead = totalArr / totalLeads    (blank when either is zero)
```

ARR is used rather than MRR so the ratio reads as annual value per unit of dialer effort.

## Benchmarks

These are **revenue earned per unit of dialer effort, so higher is better**. A client returning
$1,211 of annual value per dial is a better use of the floor than one returning $80 — this is not a
cost per acquisition, and reading it as one inverts the ranking. The bands live in the `benchmarks`
block of `data/client-revenue.json` and can be retuned there without touching code.

| Metric | Green | Amber | Red |
|---|---|---|---|
| $/Dial | $600 and above | $200–$600 | under $200 |
| $/Lead | $500 and above | $150–$500 | under $150 |

Set `higherIsBetter: false` on a benchmark to flip a metric back to cost semantics.

## Deal Won and the two efficiency charts

The client drawer's funnel runs Lead In → Worked → Contacted → Booked → Held → **Deal Won**, where
Deal Won is `dealCount` from the revenue snapshot. Because deals carry no date, that last bar is
lifetime while the stages before it are period-scoped, so it is drawn in green and tagged
`lifetime`. The drawer's panel breaks the money out further: deals won, average deal MRR, MRR, ARR.

The Revenue tab carries two different readings of "efficiency", and they point opposite ways on
purpose:

- **Revenue per dial** — money returned per unit of effort. Higher is better.
- **Dial efficiency** — median minutes from lead in to first dial. Lower is better, banded on
  `goals.speedToLeadBandsMin` (green under 5, red over 30).

A client whose median speed to lead is exactly 0.0 min is held out of the time chart rather than
drawn green. That value means the first-dial timestamp never resolved, not that the floor called
back instantly, and a wall of green would read as an achievement instead of a gap in the feed. The
count of held-out clients is printed under the chart.

Per-client speed to lead is computed from the **call feed** (`first_outbound_by_contact`), the same
basis as the floor tile and the setter column. The `firstAttempt` custom field lands equal to the
lead date, which collapsed every client's median to 0.0.

## Timeframes: read this before comparing columns

The Clients table mixes two clocks, and the subtitle says so on the board:

- **Leads, Dials, $/Dial, $/Lead, MRR** are lifetime totals. They do not move when you change the
  period selector.
- **Status** is the book rate for the selected period, against `bookRateTarget`. It does move.

Inside a row's drawer, the funnel and stage lags are the selected period's floor story, so its
"Lead In" count is the period's SID leads, not the lifetime ad-lead figure shown in the SDR
performance panel. The two are labelled differently for that reason and are not meant to tie out.

A client with revenue but no floor movement in the window shows **No activity** rather than
**Behind** — an empty window is not a performance failure.
