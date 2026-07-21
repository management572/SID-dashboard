# Data schema: `data/dashboard-data.json`

The board reads this one file. `pipeline/build_data.py` must write exactly this shape. Do not rename
keys. If a value cannot be computed, write `null`, never a zero (a zero reads as a real measurement
and will get someone yelled at).

```jsonc
{
  "title": "OBB: Setter Floor Dashboard",
  "dateRange": "14 Jul 2026 to 20 Jul 2026",
  "period": "7d",
  "sample": false,                    // true when rendering placeholder data
  "source": "SID",
  "generatedAt": "2026-07-20T14:05:00Z",
  "freshnessMin": 12,                 // minutes since the last successful pull. Board shows a stale badge over 60

  // ---- floor tab: the right-now health of the sales floor ----
  "floor": {
    "leadsToday": 84,
    "leadsInRange": 512,
    "unworkedNow": 19,                // leads with zero attempts, currently inside floor hours
    "unworkedPastSla": 6,             // subset of the above past speedToLeadSlaMin
    "speedToLeadMedianMin": 7.4,      // median minutes, lead created -> first attempt stamped
    "dayOneCoverage": 0.88,           // leads with attempt 1 inside 24h / leads eligible
    "formCompliance": 0.91,           // worked leads with an Intro Call Form / worked leads
    "attemptsLogged": 2140,
    "dialsLogged": 4380,              // dials, NOT attempts. dials / attempts should sit near 2.0
    "attemptIntegrity": 2.05,         // dials per attempt. Flags red if under 1.6: the double-dial bug is back
    "queue": { "p1": 12, "p2": 48, "p3": 130, "p4": 322 },
    "onFloorNow": 4                   // setters with activity in the last 30 min, during floor hours
  },

  // ---- one entry per home-care client agency, plus the roll-up ----
  "clients": [
    {
      "key": "acme-home-care",
      "label": "Acme Home Care",
      "market": "Tampa, FL",
      "active": true,
      "leads": 180,
      "worked": 166,
      "contacted": 61,
      "booked": 24,
      "shows": 17,
      "wons": 6,
      "workedPct": 0.92,
      "connectRate": 0.37,
      "bookRate": 0.39,
      "showRate": 0.71,
      "speedToLeadMedianMin": 6.1,
      "lags": {                       // median hours between events
        "leadToFirstAttempt": 0.1,
        "leadToConnect": 4.2,
        "connectToBooked": 0.3,
        "bookedToHeld": 52.0
      }
    }
  ],

  // ---- setters tab: THE priority-#1 deliverable ----
  "setters": [
    {
      "name": "Michelle",
      "sidUserId": "abc123",
      "role": "Lead SDR",
      "assigned": 96,
      "attempts": 540,                // completed bundles, never raw dials
      "dials": 1105,
      "connects": 41,
      "connectRate": 0.28,            // connects / dials
      "booked": 14,
      "bookRate": 0.34,               // booked / connects
      "shows": 10,
      "showRate": 0.71,               // held / booked
      "wons": 4,
      "speedToLeadMedianMin": 4.9,
      "formsSubmitted": 88,
      "formCompliance": 0.94,         // formsSubmitted / leads this setter worked
      "dayOneCoverage": 0.93,
      "isNew": false,                 // inside goals.rampDays of start_date -> NEW badge, exempt from warnings
      "belowSla": false,              // speedToLeadMedianMin over the SLA -> warning badge
      "byDay": {                      // small sparkline on the row
        "days": ["Mon","Tue","Wed","Thu","Fri"],
        "attempts": [104, 118, 96, 122, 100],
        "booked": [3, 4, 2, 3, 2]
      }
    }
  ],

  // ---- trend series for the floor charts. Keys are ISO dates ----
  "timeseries": {
    "days": ["2026-07-14","2026-07-15","2026-07-16","2026-07-17","2026-07-18"],
    "leads":   [96, 104, 88, 121, 103],
    "worked":  [90, 99, 84, 110, 96],
    "booked":  [12, 15, 9, 17, 13],
    "speedToLeadMedianMin": [6.2, 5.4, 9.1, 7.0, 8.3]
  },

  // ---- generated insights: ranked, plain-language, actionable ----
  "insights": [
    {
      "rank": 1,
      "severity": "alert",            // alert | warn | good
      "headline": "6 P1 leads unworked past 30 minutes",
      "detail": "All 6 are Acme Home Care, all assigned to the unstaffed queue.",
      "metric": "unworkedPastSla",
      "leadsAtRisk": 6
    }
  ],

  // ---- data-quality flags. These surface ABOVE the numbers when they fire ----
  "dataQuality": [
    {
      "code": "ATTEMPT_INTEGRITY",
      "severity": "alert",
      "message": "Dials per attempt fell to 1.2, which means the attempt counter is incrementing per dial again. Per-setter attempt counts are inflated until this is fixed.",
      "affects": ["setters.attempts", "floor.attemptsLogged"]
    }
  ]
}
```

## Rules the pipeline must honor

1. **Count cumulative event-date fields, never current pipeline stage.** A snapshot of "who is
   sitting in the Booked stage right now" undercounts every mid-funnel number, because contacts move
   on. Count every contact whose `bookedDate` falls inside the window.
2. **An attempt is a bundle, not a dial.** `attempts` reads the `attemptCount` custom field.
   `dials` reads dial activity. They are different numbers and both are shown, on purpose: the ratio
   between them is the integrity check. If `dials / attempts` drops under 1.6, raise the
   `ATTEMPT_INTEGRITY` data-quality flag and keep the board rendering.
3. **Never let a downstream stage exceed its upstream stage.** If `booked` exceeds `contacted`, fall
   back to the stage tag and flag it. A funnel that widens is always a data bug, never a result.
4. **Deepest stage wins.** A contact carrying several stage tags classifies to the furthest one.
   Terminal tags (`dq`, `nurture`) rank lowest and never override a real stage.
5. **Speed to lead is a median, not a mean.** One lead worked three days late will drag a mean into
   nonsense and hide a floor that is otherwise fast.
6. **Exclude leads created outside floor hours from the SLA clock.** Start the clock at the next
   floor open. A lead that arrives at 11pm is not a breach at 11:30pm.
7. **New setters are excluded from warning badges** for `goals.rampDays`, but their numbers still
   show. Ramp is visible, not punished.
8. **No PHI in this file.** Aggregates and setter names only. If a drill-down needs a contact, write
   first name, last initial, and the SID contact ID, nothing else. See `compliance.md`.
