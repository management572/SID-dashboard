#!/usr/bin/env python3
"""
build_data.py: transform data/raw/*.json into data/dashboard-data.json (schema in docs/data-schema.md).

This build is wired to SID's real Belt Engine model (see SID_System_Documentation.md), not the
generic kit template:

  - Lead state comes from custom DATE fields and two SINGLE_OPTIONS outcome fields, NOT pipeline
    stages. Fields are resolved by fieldKey.
  - A contact belongs to a client via the partner_acronym custom FIELD, not a stage tag.
  - Funnel: Lead In = entered_24h_at, Worked = first_dial_at, Contacted/Connect = assigned_at
    (a connected call over 15s auto-claims the lead), Booked = schedule_call_date or
    Intro Call Outcome == Booked, Held = Client Call Outcome == Complete. Won is downstream and
    intentionally omitted.
  - Queue lanes collapse the 8 belt tiers onto 4 board lanes per config.queueLaneMap.
  - Dials are not available (no dialer API), so the dials column and dials/attempt integrity ratio
    are null; instead a stuck sid-dial-1 toggle raises a data-quality flag.
  - Setter attribution: funnel metrics key off assigned_to (the claim), forms key off the Intro Call
    Form Submitted By. The two name systems do not match and attempts are not attributable before a
    claim; both gaps are raised as data-quality flags rather than papered over.

Anything that cannot be computed is null, never 0.

Usage:
    python pipeline/build_data.py            # reads data/raw, writes data/dashboard-data.json
    python pipeline/build_data.py --sample   # rebuild from the committed sample (no raw needed)
"""

import argparse
import datetime as dt
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")
RAW_DIR = os.path.join(ROOT, "data", "raw")
OUT_PATH = os.path.join(ROOT, "data", "dashboard-data.json")
SAMPLE_PATH = os.path.join(ROOT, "data", "dashboard-data.sample.json")

FLOOR_HOURS = (10, 22)                    # EST 10:00-22:00, for the SLA clock (rule 6)
DAY_SECONDS = 86400


# ------------------------------------------------------------------ helpers
def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def cfg_load():
    return (load_json(os.path.join(CONFIG_DIR, "ghl-config.json"), {}),
            load_json(os.path.join(CONFIG_DIR, "dashboard-config.json"), {}))


def median_or_none(values):
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 1) if vals else None


def ratio(numer, denom):
    """Rate, or null when the denominator is zero. Never 0.0 by accident."""
    return round(numer / denom, 4) if denom else None


def parse_ms(value):
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc)
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None


# fieldKey -> GHL field id, built from data/raw/custom_fields.json. The GET /contacts/ list returns
# custom fields keyed by ID only, but config uses fieldKeys, so we resolve one to the other.
FIELD_KEY_TO_ID = {}


def field_value(contact, field_ref):
    """Read a contact custom field by fieldKey (config) or id. The contacts list keys custom fields
    by id, so resolve the config fieldKey to its id before matching."""
    if not field_ref or str(field_ref).startswith("TODO"):
        return None
    refs = {field_ref, FIELD_KEY_TO_ID.get(field_ref)} - {None}
    for f in contact.get("customFields", []) or contact.get("customField", []) or []:
        if (f.get("id") in refs or f.get("fieldId") in refs
                or f.get("fieldKey") in refs or f.get("key") in refs):
            for k in ("value", "fieldValue", "fieldValueString"):
                if f.get(k) is not None:
                    return f.get(k)
            return None
    return None


def in_window(d, since, until):
    return bool(d and since <= d <= until)


def hours(a, b):
    return round((b - a).total_seconds() / 3600, 1) if a and b and b >= a else None


def first_name(name):
    return (str(name).strip().split() or [""])[0].lower() if name else ""


def sla_minutes(created, first_attempt):
    """Minutes from lead creation to first attempt, clock paused outside floor hours (rule 6)."""
    if not created or not first_attempt or first_attempt < created:
        return None
    minutes, cur, guard = 0.0, created, 0
    step = dt.timedelta(minutes=1)
    while cur < first_attempt and guard < 60 * 24 * 14:
        if FLOOR_HOURS[0] <= cur.hour < FLOOR_HOURS[1]:
            minutes += 1
        cur += step
        guard += 1
    return round(minutes, 1) if minutes else round((first_attempt - created).total_seconds() / 60, 1)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


# ------------------------------------------------------------------ classification (belt model)
def acronym_of(contact, client_field):
    v = field_value(contact, client_field)
    return str(v).strip().lower() if v else None


def outcome_of(contact, field_key):
    v = field_value(contact, field_key)
    return str(v).strip() if v else None


def tier_lane(contact, lane_map):
    """Map a contact's belt tier tag to a board queue lane via config.queueLaneMap."""
    tags = [t.lower().strip() for t in contact.get("tags", [])]
    for lane, tier_tags in lane_map.items():
        for tt in tier_tags:
            if tt.lower() in tags:
                return lane
    return None


# ------------------------------------------------------------------ periods
def fmt_range(s, u):
    return "%s to %s" % (s.strftime("%d %b %Y"), u.strftime("%d %b %Y")) if (s and u) else None


def period_windows(now):
    """(since, until, prevSince, prevUntil) per period. Comparisons are calendar-aligned for
    Today/MTD/QTD/YTD (same elapsed span in the previous day/month/quarter/year) and the immediately
    preceding equal window for the rolling 7d/30d. All has no comparison."""
    day = dt.timedelta(days=1)
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    w = {}
    elapsed_today = now - sod
    w["Today"] = (sod, now, sod - day, sod - day + elapsed_today)
    w["7d"] = (now - 7 * day, now, now - 14 * day, now - 7 * day)
    w["30d"] = (now - 30 * day, now, now - 60 * day, now - 30 * day)
    mstart = sod.replace(day=1)
    prev_mstart = (mstart - day).replace(day=1)
    w["MTD"] = (mstart, now, prev_mstart, prev_mstart + (now - mstart))
    qmonth = ((now.month - 1) // 3) * 3 + 1
    qstart = sod.replace(month=qmonth, day=1)
    prev_qstart = qstart.replace(year=now.year - 1, month=10) if qmonth == 1 else qstart.replace(month=qmonth - 3)
    w["QTD"] = (qstart, now, prev_qstart, prev_qstart + (now - qstart))
    ystart = sod.replace(month=1, day=1)
    prev_ystart = ystart.replace(year=now.year - 1)
    w["YTD"] = (ystart, now, prev_ystart, prev_ystart + (now - ystart))
    w["All"] = (dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc), now, None, None)
    return w


# Floor metrics that carry a period-over-period delta on the board.
DELTA_KEYS = ["leadsToday", "leadsInRange", "introsBookedToday", "unworkedNow", "unworkedPastSla",
              "speedToLeadMedianMin", "dayOneCoverage", "formCompliance", "attemptsLogged",
              "dialsLogged", "attemptsDialed", "connectsInRange", "bookings", "leadToConnectPct",
              "droppedCalls", "talkTimeSec", "avgDialDurationSec", "avgConnectDurationSec"]


def compute_deltas(cur_floor, prev_floor):
    if not prev_floor:
        return None
    out = {}
    for k in DELTA_KEYS:
        cv, pv = cur_floor.get(k), prev_floor.get(k)
        pct = None
        if cv is not None and pv not in (None, 0):
            pct = round((cv - pv) / pv, 4)
        out[k] = {"cur": cv, "prev": pv, "pct": pct}
    return out


# ------------------------------------------------------------------ build
def build_bundle(facts, submissions, calls, ghl, dash, since, until, connect_min, dropped_statuses=()):
    """Everything the board renders for one window: floor, clients, setters, dialer, insights."""
    lane_map = ghl.get("queueLaneMap", {})
    divisions = [d for d in ghl.get("divisions", []) if d.get("active", True)]
    goals = dash.get("goals", {})

    # Same first-dial basis as the floor and the setter table: the firstAttempt field is unreliable
    # (it lands equal to the lead date, so per-client speed to lead collapsed to 0.0 minutes).
    first_dial = first_outbound_by_contact(calls)
    clients = []
    for div in divisions:
        rows = [f for f in facts if f["acronym"] == div.get("acronym")]
        client = build_client(div, rows, since, until, first_dial)
        if client["leads"] or client["worked"] or client["booked"]:   # active = activity in window
            clients.append(client)
    clients.sort(key=lambda c: -(c.get("leads") or 0))

    floor = build_floor(facts, calls, since, until, goals, lane_map, connect_min, dropped_statuses)
    setters, other_dialers = build_setters(facts, submissions, calls, ghl, goals, since, until, connect_min)
    forms_total = sum((s.get("formsSubmitted") or 0) for s in setters)
    fc = ratio(forms_total, floor.get("connectsInRange"))
    floor["formCompliance"] = min(1.0, fc) if fc is not None else None

    return {
        "dateRange": fmt_range(since, until),
        "windowDays": max(1, round((until - since).total_seconds() / 86400)),
        "floor": floor,
        "clients": clients,
        "setters": setters,
        "otherDialers": other_dialers,
        "timeseries": build_timeseries(facts, since, until),
        "dialer": build_dialer(calls, since, until, connect_min, dropped_statuses, floor.get("bookings") or 0),
        "insights": build_insights(floor, clients, setters, goals),
    }


def build_data_quality(bundle, contacts, ghl, goals, calls=None, meta=None):
    floor, setters = bundle["floor"], bundle["setters"]
    flags = []

    # Call metrics come from the GHL call feed, bounded to a recent window; be explicit that longer
    # windows only cover that far back, and that some completed calls report a null duration.
    win = (meta or {}).get("callWindowDays") or (ghl.get("calls", {}) or {}).get("windowDays", 45)
    if calls is not None:
        flags.append({
            "code": "CALL_COVERAGE", "severity": "info",
            "message": ("Dials, connects and talk time are read live from GHL call records (setters dial "
                        "from the CRM) covering the last %d days, so the Today/7d/30d/MTD/QTD windows are "
                        "exact; YTD and All only include calls from the last %d days. GHL reports a null "
                        "duration on some completed calls, so talk time is a floor, not an exact total."
                        % (win, win)),
            "affects": ["floor.talkTimeSec", "floor.dialsLogged", "floor.connectsInRange", "setters.dials"],
        })
    other = bundle.get("otherDialers") or []
    if other:
        who = ", ".join("%s (%d dials)" % (o["name"], o["dials"]) for o in other[:6])
        flags.append({
            "code": "NONROSTER_DIALERS", "severity": "info",
            "message": ("These GHL users are dialing from the CRM but are not on the active SDR roster, so "
                        "their calls count in the floor totals but not in a setter row: %s. Confirm whether "
                        "they should be added to the roster." % who),
            "affects": ["setters"],
        })
    stuck = sum(1 for c in contacts if ghl.get("attemptToggleTag", "sid-dial-1").lower() in [t.lower() for t in c.get("tags", [])])
    if stuck > max(10, 0.02 * (floor.get("leadsInRange") or 0)):
        flags.append({
            "code": "ATTEMPT_INTEGRITY", "severity": "alert",
            "message": ("%d contacts are stuck carrying the sid-dial-1 toggle tag, which means dial pairs "
                        "are not completing and attempt counts are not incrementing correctly. Check WF-2 "
                        "before trusting attempt numbers." % stuck),
            "affects": ["setters.attempts", "floor.attemptsLogged"],
        })
    if floor.get("formCompliance") is not None and floor["formCompliance"] < goals.get("formSubmissionComplianceTarget", 0.95):
        flags.append({
            "code": "FORM_COMPLIANCE_LOW", "severity": "warn",
            "message": "Form compliance is %d percent of connected calls, under target. Per-setter "
                       "attribution depends on the Intro Call Form, so low-compliance setters are undercounted."
                       % round(floor["formCompliance"] * 100),
            "affects": ["setters"],
        })
    form_only = [s["name"] for s in setters if not s.get("claimAttributed")]
    if form_only:
        flags.append({
            "code": "SETTER_ATTRIBUTION", "severity": "warn",
            "message": ("%s have no matching GHL user account, so their outbound calls cannot be attributed "
                        "and their Dials / Connects / Talk Time show a dash (only their Intro Call Form "
                        "activity is counted). Give them GHL user logins to dial under for full per-setter "
                        "call attribution." % (" and ".join(form_only))),
            "affects": ["setters.dials", "setters.connects", "setters.talkTimeSec"],
        })
    return flags


def build_from_raw(ghl, dash):
    meta = load_json(os.path.join(RAW_DIR, "_meta.json"), {})
    if not meta:
        die("No data/raw/_meta.json. Run pipeline/fetch_sid.py first.")
    contacts = load_json(os.path.join(RAW_DIR, "contacts.json"), []) or []
    submissions = load_json(os.path.join(RAW_DIR, "form_submissions.json"), []) or []
    cfields = load_json(os.path.join(RAW_DIR, "custom_fields.json"), []) or []
    calls = parse_calls(load_json(os.path.join(RAW_DIR, "calls.json"), []) or [])

    global FIELD_KEY_TO_ID
    FIELD_KEY_TO_ID = {f.get("fieldKey"): f.get("id") for f in cfields if f.get("fieldKey") and f.get("id")}

    efid = ghl.get("eventDateFieldIds", {})
    afid = ghl.get("attemptFieldIds", {})
    outc = ghl.get("outcomeFields", {})
    lane_map = ghl.get("queueLaneMap", {})
    goals = dash.get("goals", {})
    intro_key = (outc.get("introCallOutcome") or {}).get("fieldKey")
    intro_booked = (outc.get("introCallOutcome") or {}).get("booked", "Booked")
    held_key = (outc.get("clientCallOutcome") or {}).get("fieldKey")
    held_val = (outc.get("clientCallOutcome") or {}).get("held", "Complete")
    ba = ghl.get("bookedAppointment", {})

    facts = [contact_facts(c, efid, afid, intro_key, intro_booked, held_key, held_val, ba) for c in contacts]

    # The sid_assigned_at connect stamp is empty, so derive each contact's connect timestamp from the
    # real call feed (first outbound completed call >= connect_min seconds). This lights up the funnel
    # Contacted stage, connect rate and lead-to-connect speed that the mirror field cannot.
    connect_min = int((ghl.get("calls", {}) or {}).get("connectMinSec")
                      or (ghl.get("connectedCall", {}) or {}).get("minDurationSec", 15))
    dropped_statuses = (ghl.get("calls", {}) or {}).get("droppedStatuses") or []
    connect_by_contact = first_connect_by_contact(calls, connect_min)
    for f in facts:
        if not f["connect"]:
            f["connect"] = connect_by_contact.get(f["id"])
        # A booking with no booking date is windowed by the connecting call (now that it is resolved).
        if f["booked"] and not f["bookedDate"]:
            f["bookedDate"] = f["connect"]

    now = now_utc()
    default = dash.get("defaultPeriod", "7d")
    periods = {}
    for key, (s, u, ps, pu) in period_windows(now).items():
        bundle = build_bundle(facts, submissions, calls, ghl, dash, s, u, connect_min, dropped_statuses)
        prev_floor = build_floor(facts, calls, ps, pu, goals, lane_map, connect_min, dropped_statuses) if ps else None
        bundle["prevDateRange"] = fmt_range(ps, pu)
        bundle["deltas"] = compute_deltas(bundle["floor"], prev_floor)
        periods[key] = bundle

    return {
        "title": dash.get("title", "Setter Floor Dashboard"),
        "period": default,
        "defaultPeriod": default,
        "sample": False,
        "source": ghl.get("crm", "SID"),
        "generatedAt": now.replace(microsecond=0).isoformat(),
        "freshnessMin": freshness_min(meta),
        "periods": periods,
        "clientDials": build_client_dials(facts),
        "dataQuality": build_data_quality(periods.get(default) or next(iter(periods.values())),
                                          contacts, ghl, goals, calls, meta),
    }


def build_client_dials(facts):
    """Lifetime contacts and dials per client acronym, summed from the Times Attempted counter.

    This is deliberately not windowed: times_attempted is a running total on the contact, so slicing
    it by a date window would understate it. The client board pairs these with revenue to get $/dial.
    """
    agg = {}
    for f in facts:
        acr = (f.get("acronym") or "").strip().upper()
        if not acr:
            continue
        row = agg.setdefault(acr, {"contacts": 0, "dials": 0})
        row["contacts"] += 1
        row["dials"] += f.get("timesAttempted") or 0
    return dict(sorted(agg.items()))


def is_yes(value, yes_set):
    """True when a SINGLE_OPTIONS toggle holds an affirmative value."""
    return bool(value) and str(value).strip().lower() in yes_set


def contact_facts(c, efid, afid, intro_key, intro_booked, held_key, held_val, ba):
    """Compute the belt facts for one contact once, so client/floor/setter builds share them."""
    lead_d = parse_ms(field_value(c, efid.get("leadDate")))
    first_a = parse_ms(field_value(c, efid.get("firstAttemptDate")))
    connect_d = parse_ms(field_value(c, efid.get("firstConnectDate")))
    intro_outcome = outcome_of(c, intro_key)
    held_outcome = outcome_of(c, held_key)

    # Booked = the "Is booked appointment?" toggle is Yes (the live source of truth); the legacy
    # schedule-date and intro-outcome fields are kept as fallbacks but are not currently written.
    yes_set = {str(v).strip().lower() for v in (ba.get("yesValues") or ["yes"])}
    booked_flag = is_yes(field_value(c, ba.get("fieldKey")), yes_set)
    sched_d = parse_ms(field_value(c, efid.get("bookedDate")))
    date_booked = parse_ms(field_value(c, ba.get("dateField")))
    booked = booked_flag or bool(sched_d) or (intro_outcome == intro_booked)
    booked_d = date_booked or sched_d
    if booked and not booked_d:              # booked but no booking date: window by the connecting call
        booked_d = connect_d
    booked_by = field_value(c, ba.get("bookedByField"))

    held = (held_outcome == held_val)
    attempts = to_int(field_value(c, afid.get("attemptCount")))
    assigned_to = field_value(c, afid.get("assignedSetter"))
    return {
        "id": c.get("id"),
        "acronym": (str(field_value(c, "contact.partner_acronym")).strip().lower()
                    if field_value(c, "contact.partner_acronym") else None),
        "leadDate": lead_d, "firstAttempt": first_a, "connect": connect_d, "bookedDate": booked_d,
        "booked": booked, "bookedBy": booked_by, "held": held, "attempts": attempts, "assignedTo": assigned_to,
        # Times Attempted is the dial counter the belt actually increments (sid_attempt_count is stuck),
        # so it is the source for per-client dial totals on the client board.
        "timesAttempted": to_int(field_value(c, "contact.times_attempted")),
        "lastCallSec": to_int(field_value(c, "contact.sid_last_call_duration_sec")),
        "tags": [t.lower() for t in c.get("tags", [])],
    }


def build_client(div, rows, since, until, first_dial=None):
    leads = sum(1 for f in rows if in_window(f["leadDate"], since, until))
    worked = sum(1 for f in rows if in_window(f["firstAttempt"], since, until))
    contacted = sum(1 for f in rows if in_window(f["connect"], since, until))
    booked = sum(1 for f in rows if f["booked"] and (in_window(f["bookedDate"], since, until) or in_window(f["connect"], since, until)))
    shows = sum(1 for f in rows if f["held"] and (in_window(f["bookedDate"], since, until) or in_window(f["connect"], since, until)))
    # A downstream stage can never exceed its upstream (rule 3).
    worked = min(worked, leads) if leads else worked
    contacted = min(contacted, worked) if worked else contacted
    booked = min(booked, contacted) if contacted else booked
    shows = min(shows, booked) if booked else shows
    # Speed to lead: lead-in to the first real outbound dial from the call feed, windowed by that
    # dial. Falls back to the firstAttempt field only when there is no call feed to read.
    fd = first_dial or {}
    if fd:
        stl = []
        for f in rows:
            hit = fd.get(f.get("id"))
            if not hit or not f["leadDate"]:
                continue
            ts = hit[0]
            if in_window(ts, since, until):
                stl.append(sla_minutes(f["leadDate"], ts))
    else:
        stl = [sla_minutes(f["leadDate"], f["firstAttempt"]) for f in rows if f["leadDate"] and f["firstAttempt"]]
    return {
        "key": div.get("key"), "label": div.get("label", div.get("key")),
        "market": div.get("market"), "active": True,
        "leads": leads, "worked": worked, "contacted": contacted, "booked": booked, "shows": shows,
        "workedPct": ratio(worked, leads), "connectRate": ratio(contacted, worked),
        "bookRate": ratio(booked, contacted), "showRate": ratio(shows, booked),
        "speedToLeadMedianMin": median_or_none(stl),
        "lags": {
            "leadToFirstAttempt": median_or_none([hours(f["leadDate"], f["firstAttempt"]) for f in rows]),
            "leadToConnect": median_or_none([hours(f["leadDate"], f["connect"]) for f in rows]),
            "connectToBooked": median_or_none([hours(f["connect"], f["bookedDate"]) for f in rows]),
            "bookedToHeld": None,
        },
    }


# ------------------------------------------------------------------ call feed (real dial/connect/talk)
def parse_calls(raw):
    """Normalize the raw conversation call events pulled by fetch_sid into typed records."""
    out = []
    for c in raw or []:
        ts = parse_ms(c.get("dateAdded"))
        if not ts:
            continue
        dur = c.get("duration")
        out.append({
            "contactId": c.get("contactId"),
            "userId": c.get("userId"),
            "direction": (c.get("direction") or "").lower(),
            "status": (c.get("status") or "").lower(),
            "duration": int(dur) if isinstance(dur, (int, float)) else None,
            "ts": ts,
        })
    return out


def first_connect_by_contact(calls, connect_min):
    """Earliest outbound completed call >= connect_min seconds, per contact. Used as the funnel
    connect timestamp when the sid_assigned_at mirror field is empty (which it is)."""
    m = {}
    for c in calls:
        if c["direction"] == "outbound" and c["status"] == "completed" and (c["duration"] or 0) >= connect_min:
            cid = c["contactId"]
            if cid and (cid not in m or c["ts"] < m[cid]):
                m[cid] = c["ts"]
    return m


def first_outbound_by_contact(calls):
    """Earliest outbound call per contact -> (ts, userId). The setter who first dials a lead owns its
    speed-to-lead, so this attributes speed-to-lead (and lead ownership) via the call feed, since the
    sid_assigned_to claim field is not being written."""
    m = {}
    for c in calls:
        if c["direction"] != "outbound":
            continue
        cid = c["contactId"]
        if not cid:
            continue
        cur = m.get(cid)
        if cur is None or c["ts"] < cur[0]:
            m[cid] = (c["ts"], c["userId"])
    return m


def call_metrics(calls, since, until, connect_min, dropped_statuses=()):
    """Floor-level and per-user dialer metrics from real outbound call events in the window.
      dials       = outbound call attempts placed
      attempts    = distinct contacts dialed (unique leads worked)
      completed   = calls the far end answered
      connects    = completed calls at/over connect_min seconds (a real conversation)
      dropped     = calls in a failure status (busy/failed/dropped/...)
      talkSec     = summed duration of completed calls (a floor: GHL reports null on some)
      avgDial/avgConnect = mean seconds per dial-with-duration / per connect
    Per-user counts key off the GHL userId on each call for setter attribution."""
    dials = completed = connects = dropped = talk = connect_talk = 0
    dur_known = []
    contacts = set()
    per_user = {}
    dropped_set = {str(s).lower() for s in (dropped_statuses or ())}
    for c in calls:
        if c["direction"] != "outbound" or not (since <= c["ts"] <= until):
            continue
        u = per_user.setdefault(c["userId"], {"dials": 0, "connects": 0, "talkSec": 0})
        dials += 1
        u["dials"] += 1
        if c["contactId"]:
            contacts.add(c["contactId"])
        if c["duration"] is not None:
            dur_known.append(c["duration"])
        if c["status"] in dropped_set:
            dropped += 1
        if c["status"] == "completed":
            completed += 1
            d = c["duration"] or 0
            talk += d
            u["talkSec"] += d
            if d >= connect_min:
                connects += 1
                connect_talk += d
                u["connects"] += 1
    return {
        "dials": dials, "attempts": len(contacts), "completed": completed, "connects": connects,
        "dropped": dropped, "talkSec": talk, "connectTalkSec": connect_talk,
        "avgDialDurationSec": (round(sum(dur_known) / len(dur_known)) if dur_known else None),
        "avgConnectDurationSec": (round(connect_talk / connects) if connects else None),
        "leadToConnectPct": ratio(connects, dials),
        "perUser": per_user,
    }


def build_floor(facts, calls, since, until, goals, lane_map, connect_min, dropped_statuses=()):
    today = now_utc().date()
    leads_today = leads_in_range = unworked = unworked_sla = 0
    stl, day_one_hits, day_one_elig, attempts_total = [], 0, 0, 0
    queue = {k: 0 for k in (lane_map or {"p1": 0, "p2": 0, "p3": 0, "p4": 0})}
    now = now_utc()
    for f in facts:
        lead_d, first_a = f["leadDate"], f["firstAttempt"]
        if in_window(lead_d, since, until):
            leads_in_range += 1
            if lead_d.date() == today:
                leads_today += 1
            day_one_elig += 1
            if first_a and (first_a - lead_d).total_seconds() <= DAY_SECONDS:
                day_one_hits += 1
            if not first_a:
                unworked += 1
                m = sla_minutes(lead_d, now)
                if m is not None and m > goals.get("speedToLeadSlaMin", 30):
                    unworked_sla += 1
        attempts_total += f["attempts"] or 0
        lane = tier_lane_from_tags(f["tags"], lane_map)
        if lane:
            queue[lane] += 1
    # Speed to lead = lead-in to the first real outbound dial (from the call feed, same basis as the
    # per-setter column), windowed by the first dial; the sid_first_dial_at field is unreliable.
    fd_map = first_outbound_by_contact(calls)
    lead_map = {f["id"]: f["leadDate"] for f in facts if f.get("id")}
    for cid, (ts, _uid) in fd_map.items():
        if not in_window(ts, since, until):
            continue
        ld = lead_map.get(cid)
        if ld:
            m = sla_minutes(ld, ts)
            if m is not None:
                stl.append(m)
    # Dials, connects and talk time come from the real GHL call feed (conversation call messages),
    # because the sid_* mirror fields (sid_assigned_at, sid_last_call_duration_sec) are not written.
    # Connect = an outbound completed call at/over connect_min seconds -- the same definition SID uses.
    cm = call_metrics(calls, since, until, connect_min, dropped_statuses)

    # Intros booked: a booking whose booked date lands today (and in the window, for the sub-count).
    intros_today = sum(1 for f in facts if f["booked"] and f["bookedDate"] and f["bookedDate"].date() == today)
    intros_range = sum(1 for f in facts if f["booked"] and in_window(f["bookedDate"], since, until))

    return {
        "leadsToday": leads_today, "leadsInRange": leads_in_range,
        "introsBookedToday": intros_today, "introsBookedInRange": intros_range,
        "unworkedNow": unworked, "unworkedPastSla": unworked_sla,
        "speedToLeadMedianMin": median_or_none(stl),
        "dayOneCoverage": ratio(day_one_hits, day_one_elig),
        "formCompliance": None,           # set from setters/connects in build_from_raw
        # --- dialer row (all from the real GHL call feed) ---
        "dialsLogged": cm["dials"] or None,
        "attemptsDialed": cm["attempts"] or None,          # distinct contacts dialed
        "connectsInRange": cm["connects"],
        "bookings": intros_range,                          # booked appointments in the window
        "leadToConnectPct": cm["leadToConnectPct"],        # connects / dials
        "droppedCalls": cm["dropped"] or None,
        "talkTimeSec": cm["talkSec"] or None,
        "avgDialDurationSec": cm["avgDialDurationSec"],
        "avgConnectDurationSec": cm["avgConnectDurationSec"],
        "completedCalls": cm["completed"],
        # sid_attempt_count is not incrementing (stuck sid-dial-1 tags), so fall back to real dials.
        "attemptsLogged": (attempts_total or cm["dials"]) or None,
        "attemptIntegrity": None,
        "queue": queue,
        "onFloorNow": None,
    }


def tier_lane_from_tags(tags, lane_map):
    for lane, tier_tags in (lane_map or {}).items():
        for tt in tier_tags:
            if tt.lower() in tags:
                return lane
    return None


def build_setters(facts, submissions, calls, ghl, goals, since, until, connect_min):
    icf = ghl.get("introCallForm", {})
    setter_field = icf.get("setterFieldId")
    outcome_field = icf.get("outcomeFieldId")
    booked_val = (ghl.get("outcomeFields", {}).get("introCallOutcome", {}) or {}).get("booked", "Booked")

    # Real per-setter dial / connect / talk-time, attributed by the GHL userId on each outbound call.
    per_user = call_metrics(calls, since, until, connect_min)["perUser"]

    # Speed-to-lead and lead ownership per setter, attributed by whoever placed the first outbound dial
    # to each lead (the sid_assigned_to claim field is empty, so this is the only working attribution).
    first_dial = first_outbound_by_contact(calls)
    lead_by_cid = {f["id"]: f["leadDate"] for f in facts if f.get("id")}
    per_user_stl, per_user_leads = {}, {}
    for cid, (ts, uid) in first_dial.items():
        if not (since <= ts <= until):
            continue
        per_user_leads[uid] = per_user_leads.get(uid, 0) + 1
        ld = lead_by_cid.get(cid)
        if ld:
            m = sla_minutes(ld, ts)
            if m is not None:
                per_user_stl.setdefault(uid, []).append(m)

    # Count form submissions (and booked-from-form) per setter first-name (Submitted By).
    forms_by, forms_booked_by = {}, {}
    for s in submissions:
        nm = first_name(submission_field(s, setter_field))
        if not nm:
            continue
        forms_by[nm] = forms_by.get(nm, 0) + 1
        if submission_field(s, outcome_field) == booked_val:
            forms_booked_by[nm] = forms_booked_by.get(nm, 0) + 1

    # The roster is the confirmed active SDR floor. If none configured, fall back to whoever appears.
    roster = ghl.get("activeSetters")
    if not roster:
        seen = {first_name(f["assignedTo"]) for f in facts if f["assignedTo"]} | set(forms_by)
        roster = [{"name": n.title(), "formName": n.title(), "assignedToName": None} for n in seen if n]

    setters = []
    matched_uids = set()
    for r in roster:
        key = first_name(r.get("formName") or r.get("name"))
        claim_key = first_name(r.get("assignedToName")) if r.get("assignedToName") else None
        uid = r.get("ghlUserId")
        cu = per_user.get(uid) if uid else None
        if uid and cu:
            matched_uids.add(uid)
        # A setter is call-attributed when they have a GHL user id (Michelle, Sarah). Sophia and Heart
        # are not GHL users, so their calls cannot be attributed and their call columns stay dashed.
        attributed = bool(uid)
        mine = [f for f in facts if claim_key and first_name(f["assignedTo"]) == claim_key]
        # Dials / connects / talk time from the real call feed for this setter's userId.
        dials = cu["dials"] if cu else None
        connects = cu["connects"] if cu else (None if attributed else None)
        talk = cu["talkSec"] if cu else None
        attempts = dials if attributed else None
        # Held stays claim-based (needs the Client Call Outcome field); booked prefers the form.
        claim_shows = sum(1 for f in mine if f["held"])
        # Speed to lead + assigned leads come from the call feed (first-dial attribution), not the empty
        # claim field, so they populate for setters who have a GHL user id (Michelle, Sarah).
        speed = median_or_none(per_user_stl.get(uid, [])) if uid else None
        assigned = per_user_leads.get(uid) if uid else None
        forms = forms_by.get(key)
        # Booked per setter: count "Is booked appointment?" = Yes attributed via Booked By, windowed by
        # the booking date; fall back to the Intro Call Form booked count when Booked By is not set.
        booked_by_count = sum(1 for f in facts
                              if f["booked"] and first_name(f.get("bookedBy")) == key
                              and in_window(f["bookedDate"], since, until))
        booked = booked_by_count or forms_booked_by.get(key)
        setters.append({
            "name": r.get("name"), "sidUserId": uid, "role": None,
            "assigned": assigned,
            "attempts": attempts,
            "dials": dials,
            "connects": connects,
            "connectRate": ratio(connects, dials) if (dials and connects is not None) else None,
            "talkTimeSec": talk,
            "booked": (booked or None),
            "bookRate": ratio(booked, connects) if (booked and connects) else None,
            "shows": (claim_shows or None),
            "showRate": ratio(claim_shows, booked) if (claim_shows and booked) else None,
            "speedToLeadMedianMin": speed,
            "formsSubmitted": forms,
            "formCompliance": (min(1.0, ratio(forms or 0, connects)) if (attributed and connects) else None),
            "dayOneCoverage": None,
            "isNew": False,                            # start_date unknown, cannot compute ramp
            "belowSla": bool(speed is not None and speed > goals.get("speedToLeadSlaMin", 30)),
            "claimAttributed": attributed,             # false = not a GHL user (Heart, Sophia)
            "byDay": None,
        })
    # Non-roster GHL users who are dialing (e.g. Mary Lim, May Paula): surface their volume so the
    # floor totals reconcile and attribution gaps are visible, without adding them as roster rows.
    users = ghl.get("users", {})
    other = []
    for uid, m in per_user.items():
        if uid and uid not in matched_uids and m["dials"]:
            other.append({"name": users.get(uid, uid), "dials": m["dials"], "connects": m["connects"]})
    other.sort(key=lambda x: -x["dials"])
    # Keep the full active roster visible even at zero (ramp and quiet reps stay on the board).
    setters = sorted(setters, key=lambda s: (-(s.get("connects") or 0), -(s.get("formsSubmitted") or 0)))
    return setters, other


def build_timeseries(facts, since, until):
    days = []
    d = since.date()
    while d <= until.date():
        days.append(d); d += dt.timedelta(days=1)
    days = days[-30:]
    idx = {d: i for i, d in enumerate(days)}
    leads = [0] * len(days); worked = [0] * len(days); booked = [0] * len(days)
    speed = [[] for _ in days]
    for f in facts:
        if f["leadDate"] and f["leadDate"].date() in idx:
            leads[idx[f["leadDate"].date()]] += 1
        if f["firstAttempt"] and f["firstAttempt"].date() in idx:
            worked[idx[f["firstAttempt"].date()]] += 1
            if f["leadDate"]:
                speed[idx[f["firstAttempt"].date()]].append(sla_minutes(f["leadDate"], f["firstAttempt"]))
        if f["bookedDate"] and f["bookedDate"].date() in idx:
            booked[idx[f["bookedDate"].date()]] += 1
    return {
        "days": [d.isoformat() for d in days],
        "leads": leads, "worked": worked, "booked": booked,
        "speedToLeadMedianMin": [median_or_none(s) for s in speed],
    }


def build_dialer(calls, since, until, connect_min, dropped_statuses, bookings):
    """Daily dials / attempts / connects for the bar chart, plus the conversion rates that show how
    the dialer itself is performing. Attempts = distinct contacts dialed that day."""
    days = []
    d = since.date()
    while d <= until.date():
        days.append(d); d += dt.timedelta(days=1)
    days = days[-30:]
    idx = {d: i for i, d in enumerate(days)}
    dials = [0] * len(days); connects = [0] * len(days)
    seen = [set() for _ in days]
    for c in calls:
        if c["direction"] != "outbound":
            continue
        dd = c["ts"].date()
        i = idx.get(dd)
        if i is None:
            continue
        dials[i] += 1
        if c["contactId"]:
            seen[i].add(c["contactId"])
        if c["status"] == "completed" and (c["duration"] or 0) >= connect_min:
            connects[i] += 1
    attempts = [len(s) for s in seen]

    cm = call_metrics(calls, since, until, connect_min, dropped_statuses)
    return {
        "days": [d.isoformat() for d in days],
        "dials": dials, "attempts": attempts, "connects": connects,
        "totals": {"dials": cm["dials"], "attempts": cm["attempts"], "connects": cm["connects"],
                   "dropped": cm["dropped"], "bookings": bookings},
        "rates": {
            "connectRate": cm["leadToConnectPct"],              # connects / dials
            "answerRate": ratio(cm["completed"], cm["dials"]),  # answered / dials
            "dropRate": ratio(cm["dropped"], cm["dials"]),      # dropped / dials
            "bookingRate": ratio(bookings, cm["connects"]),     # bookings / connects
        },
    }


def build_insights(floor, clients, setters, goals):
    ins = []
    if floor.get("unworkedPastSla"):
        ins.append({"severity": "alert", "headline": "%d leads unworked past the SLA" % floor["unworkedPastSla"],
                    "detail": "Inside floor hours, past %d minutes with zero attempts." % goals.get("speedToLeadSlaMin", 30),
                    "metric": "unworkedPastSla", "leadsAtRisk": floor["unworkedPastSla"]})
    fc = floor.get("formCompliance")
    if fc is not None and fc < goals.get("formSubmissionComplianceTarget", 0.95):
        ins.append({"severity": "alert", "headline": "Form compliance is %d percent of connected calls" % round(fc * 100),
                    "detail": "Connected calls without an Intro Call Form make per-setter numbers understated.",
                    "metric": "formCompliance", "leadsAtRisk": floor.get("connectsInRange")})
    for s in setters:
        if s.get("belowSla"):
            ins.append({"severity": "warn", "headline": "%s is over the speed-to-lead SLA" % s["name"],
                        "detail": "Median speed to lead is %s minutes." % s.get("speedToLeadMedianMin"),
                        "metric": "speedToLeadMedianMin", "leadsAtRisk": s.get("assigned") or 0})
    ins.sort(key=lambda x: -(x.get("leadsAtRisk") or 0))
    for i, x in enumerate(ins, 1):
        x["rank"] = i
    return ins


# ------------------------------------------------------------------ small utils
def to_int(v):
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (ValueError, TypeError):
        return 0


def uid_for(label, users):
    for uid, name in users.items():
        if name == label:
            return uid
    return None


def submission_field(sub, field_ref):
    for k in ("fields", "customFields", "data", "others"):
        arr = sub.get(k)
        if isinstance(arr, list):
            for f in arr:
                if f.get("id") == field_ref or f.get("fieldKey") == field_ref or f.get("key") == field_ref:
                    return f.get("value")
        elif isinstance(arr, dict) and field_ref in arr:
            return arr[field_ref]
    return None


def freshness_min(meta):
    pulled = parse_ms(meta.get("pulledAt"))
    return None if not pulled else max(0, round((now_utc() - pulled).total_seconds() / 60))


def die(msg):
    sys.stderr.write("build_data: " + msg + "\n")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Build data/dashboard-data.json.")
    ap.add_argument("--sample", action="store_true", help="rebuild from the committed sample")
    args = ap.parse_args()

    if args.sample:
        data = load_json(SAMPLE_PATH)
        if data is None:
            die("no sample file at " + SAMPLE_PATH)
        print("build_data: using the sample dataset")
    else:
        ghl, dash = cfg_load()
        data = build_from_raw(ghl, dash)

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    periods = data.get("periods") or {}
    default = data.get("defaultPeriod") or (next(iter(periods), None))
    b = periods.get(default, {}) if periods else data
    print("build_data: wrote data/dashboard-data.json (%d periods, default %s: %d clients, %d setters; %d data-quality flags)"
          % (len(periods), default, len(b.get("clients", [])), len(b.get("setters", [])), len(data.get("dataQuality", []))))


if __name__ == "__main__":
    main()
