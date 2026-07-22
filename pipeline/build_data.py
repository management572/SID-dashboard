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
              "talkTimeSec", "connectsInRange"]


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
def build_bundle(facts, submissions, ghl, dash, since, until):
    """Everything the board renders for one window: floor, clients, setters, timeseries, insights."""
    lane_map = ghl.get("queueLaneMap", {})
    divisions = [d for d in ghl.get("divisions", []) if d.get("active", True)]
    goals = dash.get("goals", {})

    clients = []
    for div in divisions:
        rows = [f for f in facts if f["acronym"] == div.get("acronym")]
        client = build_client(div, rows, since, until)
        if client["leads"] or client["worked"] or client["booked"]:   # active = activity in window
            clients.append(client)
    clients.sort(key=lambda c: -(c.get("leads") or 0))

    floor = build_floor(facts, since, until, goals, lane_map)
    setters = build_setters(facts, submissions, ghl, goals, since, until)
    forms_total = sum((s.get("formsSubmitted") or 0) for s in setters)
    fc = ratio(forms_total, floor.get("connectsInRange"))
    floor["formCompliance"] = min(1.0, fc) if fc is not None else None

    return {
        "dateRange": fmt_range(since, until),
        "floor": floor,
        "clients": clients,
        "setters": setters,
        "timeseries": build_timeseries(facts, since, until),
        "insights": build_insights(floor, clients, setters, goals),
    }


def build_data_quality(bundle, contacts, ghl, goals):
    floor, setters = bundle["floor"], bundle["setters"]
    flags = []
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
            "message": ("%s submit the Intro Call Form but are not in the SID Assigned To dropdown and have "
                        "no matching GHL user, so only their form activity is attributed (attempts, connects "
                        "and held show a dash). Add them to Assigned To in SID for full attribution. Also, SID "
                        "does not record which setter dialed before a lead is claimed, so per-setter Attempts "
                        "count claimed leads only." % (" and ".join(form_only))),
            "affects": ["setters.attempts", "setters.connects", "setters.shows"],
        })
    return flags


def build_from_raw(ghl, dash):
    meta = load_json(os.path.join(RAW_DIR, "_meta.json"), {})
    if not meta:
        die("No data/raw/_meta.json. Run pipeline/fetch_sid.py first.")
    contacts = load_json(os.path.join(RAW_DIR, "contacts.json"), []) or []
    submissions = load_json(os.path.join(RAW_DIR, "form_submissions.json"), []) or []
    cfields = load_json(os.path.join(RAW_DIR, "custom_fields.json"), []) or []

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

    facts = [contact_facts(c, efid, afid, intro_key, intro_booked, held_key, held_val) for c in contacts]

    now = now_utc()
    default = dash.get("defaultPeriod", "7d")
    periods = {}
    for key, (s, u, ps, pu) in period_windows(now).items():
        bundle = build_bundle(facts, submissions, ghl, dash, s, u)
        prev_floor = build_floor(facts, ps, pu, goals, lane_map) if ps else None
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
        "dataQuality": build_data_quality(periods.get(default) or next(iter(periods.values())), contacts, ghl, goals),
    }


def contact_facts(c, efid, afid, intro_key, intro_booked, held_key, held_val):
    """Compute the belt facts for one contact once, so client/floor/setter builds share them."""
    lead_d = parse_ms(field_value(c, efid.get("leadDate")))
    first_a = parse_ms(field_value(c, efid.get("firstAttemptDate")))
    connect_d = parse_ms(field_value(c, efid.get("firstConnectDate")))
    booked_d = parse_ms(field_value(c, efid.get("bookedDate")))
    intro_outcome = outcome_of(c, intro_key)
    held_outcome = outcome_of(c, held_key)
    booked = bool(booked_d) or (intro_outcome == intro_booked)
    held = (held_outcome == held_val)
    attempts = to_int(field_value(c, afid.get("attemptCount")))
    assigned_to = field_value(c, afid.get("assignedSetter"))
    return {
        "acronym": (str(field_value(c, "contact.partner_acronym")).strip().lower()
                    if field_value(c, "contact.partner_acronym") else None),
        "leadDate": lead_d, "firstAttempt": first_a, "connect": connect_d, "bookedDate": booked_d,
        "booked": booked, "held": held, "attempts": attempts, "assignedTo": assigned_to,
        "lastCallSec": to_int(field_value(c, "contact.sid_last_call_duration_sec")),
        "tags": [t.lower() for t in c.get("tags", [])],
    }


def build_client(div, rows, since, until):
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


def build_floor(facts, since, until, goals, lane_map):
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
        if lead_d and first_a:
            stl.append(sla_minutes(lead_d, first_a))
        attempts_total += f["attempts"] or 0
        lane = tier_lane_from_tags(f["tags"], lane_map)
        if lane:
            queue[lane] += 1
    # form compliance is forms / connected calls; forms are counted in build_setters, so compute the
    # denominator (connects) here and let insights/floor read the ratio set below.
    connects = sum(1 for f in facts if in_window(f["connect"], since, until))

    # Intros booked: a booking whose booked date lands today (and in the window, for the sub-count).
    intros_today = sum(1 for f in facts if f["booked"] and f["bookedDate"] and f["bookedDate"].date() == today)
    intros_range = sum(1 for f in facts if f["booked"] and in_window(f["bookedDate"], since, until))

    # Talk time (approximate): SID stores only the last call duration per contact, not a running
    # total, so this sums the last-call seconds across contacts with activity in the window. It is a
    # floor of true talk time; a real total needs the call-log/dialer feed.
    talk_sec = sum(f["lastCallSec"] or 0 for f in facts
                   if in_window(f["firstAttempt"], since, until) or in_window(f["connect"], since, until))

    return {
        "leadsToday": leads_today, "leadsInRange": leads_in_range,
        "introsBookedToday": intros_today, "introsBookedInRange": intros_range,
        "unworkedNow": unworked, "unworkedPastSla": unworked_sla,
        "speedToLeadMedianMin": median_or_none(stl),
        "dayOneCoverage": ratio(day_one_hits, day_one_elig),
        "formCompliance": None,           # set from setters/connects in build_from_raw
        "attemptsLogged": attempts_total or None,
        "dialsLogged": None,              # no dialer API
        "attemptIntegrity": None,         # dials/attempt not computable without dials
        "talkTimeSec": talk_sec or None,
        "queue": queue,
        "connectsInRange": connects,
        "onFloorNow": None,
    }


def tier_lane_from_tags(tags, lane_map):
    for lane, tier_tags in (lane_map or {}).items():
        for tt in tier_tags:
            if tt.lower() in tags:
                return lane
    return None


def build_setters(facts, submissions, ghl, goals, since, until):
    icf = ghl.get("introCallForm", {})
    setter_field = icf.get("setterFieldId")
    outcome_field = icf.get("outcomeFieldId")
    booked_val = (ghl.get("outcomeFields", {}).get("introCallOutcome", {}) or {}).get("booked", "Booked")

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
    for r in roster:
        key = first_name(r.get("formName") or r.get("name"))
        claim_key = first_name(r.get("assignedToName")) if r.get("assignedToName") else None
        # Claim-side metrics only exist when the setter maps to an Assigned To value.
        mine = [f for f in facts if claim_key and first_name(f["assignedTo"]) == claim_key]
        claimed = bool(claim_key)
        connects = sum(1 for f in mine if f["connect"]) if claimed else None
        attempts = (sum(f["attempts"] or 0 for f in mine) or None) if claimed else None
        claim_booked = sum(1 for f in mine if f["booked"]) if claimed else 0
        claim_shows = sum(1 for f in mine if f["held"]) if claimed else 0
        stl = [sla_minutes(f["leadDate"], f["firstAttempt"]) for f in mine if f["leadDate"] and f["firstAttempt"]]
        speed = median_or_none(stl) if claimed else None
        forms = forms_by.get(key)
        # Booked: claim-based when the setter is claim-attributed, else form-based (Outcome = Booked).
        booked = claim_booked if claimed else forms_booked_by.get(key)
        setters.append({
            "name": r.get("name"), "sidUserId": r.get("ghlUserId"), "role": None,
            "assigned": (len(mine) or None) if claimed else None,
            "attempts": attempts,
            "dials": None,
            "connects": connects,
            "connectRate": None,                       # needs dials or pre-claim lead attribution
            "booked": (booked or None),
            "bookRate": ratio(booked, connects) if (claimed and connects) else None,
            "shows": (claim_shows or None) if claimed else None,
            "showRate": ratio(claim_shows, claim_booked) if (claimed and claim_booked) else None,
            "speedToLeadMedianMin": speed,
            "formsSubmitted": forms,
            "formCompliance": (min(1.0, ratio(forms or 0, connects)) if (claimed and connects) else None),
            "dayOneCoverage": None,
            "isNew": False,                            # start_date unknown, cannot compute ramp
            "belowSla": bool(speed is not None and speed > goals.get("speedToLeadSlaMin", 30)),
            "claimAttributed": claimed,                # false = form-only (Heart, Sophia)
            "byDay": None,
        })
    # Keep the full active roster visible even at zero (ramp and quiet reps stay on the board).
    return sorted(setters, key=lambda s: (-(s.get("bookRate") or 0), -(s.get("formsSubmitted") or 0)))


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
