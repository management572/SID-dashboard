# Dashboard spec: layout, tabs, and rules

One self-contained `index.html`. No framework, no bundler, no build step. It fetches the JSON in
`data/` and renders. Every color, label, goal, and threshold comes from `config/`, so a rebrand is a
token swap and a new client is a config row.

---

## Global chrome

**Masthead.** OBB logo and wordmark left. Board title ("Setter Floor Dashboard") centered or left of
the controls. Right side: last-updated stamp, a freshness badge (green under 60 minutes, amber over,
red over 180), and a `SAMPLE DATA` tag whenever `data.sample` is true. The sample tag is loud on
purpose, so nobody ever presents placeholder numbers in a meeting.

**Controls row.** Period chips from `dashboard-config.periods`, default `7d`. A client toggle built
from `divisions` plus an All Clients roll-up. A custom date-range picker.

**Data-quality strip.** If `data.dataQuality` is non-empty, it renders **above everything else**, in
the alert tone, before any number. A broken attempt counter is more important than any metric it
feeds, and the board should refuse to look confident when its inputs are wrong.

**Tabs.** Floor (default), Setters, Clients.

**Footer.** The data-source line and disclaimer from `dashboard-config.footer`, plus generated-at.

---

## Tab 1: Floor

The "is the floor healthy right now" view. This is what the floor lead keeps open.

1. **Tile row** from `dashboard-config.floorTiles`. Six tiles, each colored against its goal band.
   Tiles with `invert: true` reverse the bands (lower is better). `Unworked Right Now` renders as
   the hero tile, larger than the rest, because it is the only number on the board that is losing
   money while you look at it.
2. **Insights list** from `data.insights`, ranked, plain language, written as instructions rather
   than statistics. "6 P1 leads unworked past 30 minutes, all Acme Home Care" beats "SLA compliance
   94.2 percent." Rank by leads at risk, not by percentage gap, so a 40 percent miss on 3 leads never
   outranks a 5 percent miss on 300.
3. **Priority lanes** from `dashboard-config.priorityLanes`: four stacked bars showing P1 through P4
   queue depth, P1 in the alert tone. Clicking a lane filters the tab.
4. **Funnel** across the six stages in `funnelStages`, with the conversion rate between each pair
   printed on the connector.
5. **Trend chart**, inline SVG, from `data.timeseries`: leads in, worked, and booked as lines, with
   median speed-to-lead on a second axis. Five to thirty days depending on period.

## Tab 2: Setters

**This table is the Priority-#1 deliverable.** It is the thing OBB does not have today.

Sortable on every column, default sort by book rate descending. Columns from
`dashboard-config.setterColumns`. Per-row: a sparkline from `setter.byDay`, a `NEW` badge when
`isNew`, and a warning badge when `belowSla` (suppressed for new setters).

Cells with a `target` in the column config color against it. Cells with `invert` reverse.

**Attempts and Dials are separate columns and stay separate.** The ratio between them is how the
floor lead sees the attempt definition working correctly without reading a config file. If the whole
column looks wrong, the data-quality strip already said so at the top of the page.

Below the table: a small "coverage" panel showing day-one coverage and form compliance per setter as
horizontal bars against target, since those two predict everything else in the table.

## Tab 3: Clients

Per home-care agency, columns from `dashboard-config.clientColumns`. One row per active client plus a
roll-up row. Sorted by leads descending.

Per-row expand reveals that client's six-stage funnel and the median lag between each stage, which is
what CS needs on an escalation call: not "how many leads," but "where do this client's leads stall."

---

## Color rules (read `config/brand.json` before styling anything)

OBB's background is a mid-tone slate (`#4A5469`), not a near-black. That changes how color behaves
and there are two rules that follow from it:

**1. The accent is a fill, not a text color.** `#2974ED` on the slate is 1.74:1, which is unreadable.
Use `accent` for button fills, bar fills, active chip fills, chart strokes, and focus rings, always
with white text on top. When accent-colored *text* sits directly on `ink` or `ink2`, use `accentText`
(`#A8CCFF`, 4.6:1).

**2. Status is a filled chip, not colored text.** Saturated status colors do not clear 4.5:1 as text
against a mid-tone background. Render a status as a solid `good` / `warn` / `alert` chip with white
text. Where the status must be carried by the text itself (a table cell colored against its goal
band, a sparkline), use the `goodText` / `warnText` / `alertText` tints instead.

Never mix the two layers: a solid status color as text, or a text tint as a fill, will look washed
out and will fail contrast.

## Rules

- Nothing is hard-coded. Every number, label, and threshold comes from `data/` or `config/`.
- A `null` renders as a dash with a tooltip explaining why it is unavailable. Never render `null` or
  `NaN` as `0`.
- Percentages get one decimal. Counts get none. Money gets none, with thousands separators.
- Medians are labeled "median" in the UI. A user who thinks they are looking at an average will make
  a wrong decision about an outlier.
- The board degrades rather than breaks. If `setters` is empty, the tab shows an empty state
  explaining that the Intro Call Form has no submissions in this window, not a blank page.
- No em dashes or en dashes in any copy. Use commas, colons, or parentheses.
- Responsive to 1280px minimum. This is a desk tool on a floor with monitors, not a phone app.
- Print stylesheet: the Setters tab prints cleanly on one page for the Monday floor meeting.
