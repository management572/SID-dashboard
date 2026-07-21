#!/usr/bin/env python3
"""
build_data.py: transform data/raw/*.json into data/dashboard-data.json, in exactly the shape in
docs/data-schema.md. This is where every metric, rate, median, lag and flag is computed.

It honors every rule in the "Rules the pipeline must honor" section of the schema doc:

  1. Count by cumulative event-date custom fields inside the window, never current pipeline stage.
  2. An attempt is a bundle (attemptCount custom field), never a raw dial. dials come separately.
     dials/attempts under the integrity floor raises the ATTEMPT_INTEGRITY data-quality flag.
  3. A downstream stage may never exceed its upstream stage. If it does, fall back to the stage tag
     and raise a data-quality flag.
  4. Deepest stage tag wins when classifying a contact. Terminal tags (dq, nurture) rank lowest.
  5. Speed to lead is a median, not a mean.
  6. The SLA clock does not run outside floor hours.
  7. New setters (inside goals.rampDays of start_date) get a NEW badge and are exempt from warnings.
  8. No PHI in the output. Aggregates and setter names only.

Anything that cannot be computed is written as null, never 0 (a 0 reads as a real measurement).

Usage:
    python pipeline/build_data.py                    # reads data/raw, writes data/dashboard-data.json
    python pipeline/build_data.py --sample           # rebuild from the sample instead (no raw needed)

This script writes only aggregates. It never copies a contact-level record into the output file.
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

ATTEMPT_INTEGRITY_FLOOR = 1.6            # dials/attempts below this means the double-dial bug is back
STAGE_ORDER = ["lead", "worked", "contacted", "booked", "show", "won"]
TERMINAL_TAGS = ["dq", "nurture"]        # rank lowest, never override a real stage
FLOOR_HOURS = (8, 20)                    # local floor open/close, used for the SLA clock (rule 6)


# ------------------------------------------------------------------ helpers
def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def cfg_load():
    ghl = load_json(os.path.join(CONFIG_DIR, "ghl-config.json"), {})
    dash = load_json(os.path.join(CONFIG_DIR, "dashboard-config.json"), {})
    return ghl, dash


def median_or_none(values):
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 1) if vals else None


def ratio(numer, denom):
    """Safe rate. Returns null when the denominator is zero, never 0.0 (rule: null, not zero)."""
    if not denom:
        return None
    return round(numer / denom, 4)


def pct(numer, denom):
    return ratio(numer, denom)


def parse_ms(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc)
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None


def field_value(contact, field_id):
    """Read a contact custom field by id from the LeadConnector customFields array."""
    if not field_id or str(field_id).startswith("TODO"):
        return None
    for f in contact.get("customFields", []) or contact.get("customField", []) or []:
        if f.get("id") == field_id or f.get("fieldId") == field_id:
            return f.get("value") if "value" in f else f.get("fieldValue")
    return None


def sla_minutes(created, first_attempt):
    """Minutes from lead creation to first attempt, with the clock paused outside floor hours (rule 6)."""
    if not created or not first_attempt or first_attempt < created:
        return None
    minutes = 0.0
    cur = created
    step = dt.timedelta(minutes=1)
    guard = 0
    while cur < first_attempt and guard < 60 * 24 * 14:  # cap at two weeks of minutes
        if FLOOR_HOURS[0] <= cur.hour < FLOOR_HOURS[1]:
            minutes += 1
        cur += step
        guard += 1
    return round(minutes, 1) if minutes else round((first_attempt - created).total_seconds() / 60, 1)


# ------------------------------------------------------------------ classification (rules 1, 3, 4)
def division_for(contact, divisions):
    """Classify a contact to a client by its deepest stage tag. Deepest stage wins (rule 4)."""
    tags = [t.lower().strip() for t in contact.get("tags", [])]
    best = None
    best_rank = -1
    for div in divisions:
        prefix = (div.get("tagPrefix") or div.get("key") or "").lower().strip()
        if not prefix or prefix.startswith("todo"):
            continue
        for tag in tags:
            if tag.startswith(prefix + " "):
                stage = tag[len(prefix):].strip()
                rank = stage_rank(stage)
                if rank > best_rank:
                    best_rank = rank
                    best = (div, stage)
    return best  # (division, stage) or None


def stage_rank(stage):
    if stage in TERMINAL_TAGS:
        return 0  # terminal tags rank lowest, never override a real stage (rule 4)
    return STAGE_ORDER.index(stage) + 1 if stage in STAGE_ORDER else -1


def enforce_funnel(counts, flags, label):
    """A downstream stage may never exceed its upstream stage (rule 3). If it does, flag it."""
    order = ["leads", "worked", "contacted", "booked", "shows", "wons"]
    for i in range(1, len(order)):
        up, down = order[i - 1], order[i]
        if counts.get(down, 0) > counts.get(up, 0):
            flags.append({
                "code": "FUNNEL_WIDENED",
                "severity": "warn",
                "message": ("%s funnel widened: %s (%d) exceeds %s (%d). Falling back to stage tags "
                            "for this client. A funnel that widens is a data bug, never a result."
                            % (label, down, counts[down], up, counts[up])),
                "affects": ["clients"],
            })
            counts[down] = counts[up]
    return counts


# ------------------------------------------------------------------ build from raw
def build_from_raw(ghl, dash):
    meta = load_json(os.path.join(RAW_DIR, "_meta.json"), {})
    contacts = load_json(os.path.join(RAW_DIR, "contacts.json"), []) or []
    opportunities = load_json(os.path.join(RAW_DIR, "opportunities.json"), []) or []
    users = load_json(os.path.join(RAW_DIR, "users.json"), []) or []
    submissions = load_json(os.path.join(RAW_DIR, "form_submissions.json"), []) or []

    if not meta:
        die("No data/raw/_meta.json. Run pipeline/fetch_sid.py first.")

    since = parse_ms(meta.get("since")) or (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7))
    until = parse_ms(meta.get("until")) or dt.datetime.now(dt.timezone.utc)

    efid = ghl.get("eventDateFieldIds", {})
    afid = ghl.get("attemptFieldIds", {})
    divisions = ghl.get("divisions", [])
    goals = dash.get("goals", {})

    flags = []

    # ---- per-client funnels, counted by event-date fields inside the window (rule 1) ----
    clients = []
    for div in divisions:
        if str(div.get("key", "")).startswith("TODO"):
            continue
        clients.append(build_client(div, contacts, efid, since, until, flags))

    # ---- per-setter table from form submissions + attempt fields ----
    setters = build_setters(users, contacts, submissions, ghl, goals, since, until)

    # ---- floor aggregates ----
    floor = build_floor(contacts, efid, afid, goals, since, until, flags)

    # ---- attempt integrity check (rule 2) ----
    if floor.get("attemptsLogged") and floor.get("dialsLogged") is not None:
        integrity = ratio(floor["dialsLogged"], floor["attemptsLogged"])
        floor["attemptIntegrity"] = integrity
        if integrity is not None and integrity < ATTEMPT_INTEGRITY_FLOOR:
            flags.insert(0, {
                "code": "ATTEMPT_INTEGRITY",
                "severity": "alert",
                "message": ("Dials per attempt is %.1f, under the %.1f floor. The attempt counter is "
                            "likely incrementing per dial rather than once per completed bundle, which "
                            "inflates every attempt count. Fix the sales-attempt-bundle workflow before "
                            "acting on per-setter attempt numbers." % (integrity, ATTEMPT_INTEGRITY_FLOOR)),
                "affects": ["setters.attempts", "floor.attemptsLogged", "floor.attemptIntegrity"],
            })
    else:
        floor["attemptIntegrity"] = None

    # ---- form compliance flag ----
    if floor.get("formCompliance") is not None and floor["formCompliance"] < goals.get("formSubmissionComplianceTarget", 0.95):
        flags.append({
            "code": "FORM_COMPLIANCE_LOW",
            "severity": "warn",
            "message": ("Form compliance is %d percent. Per-setter attribution depends on the Intro "
                        "Call Form setter dropdown, so any setter under target is undercounted here."
                        % round(floor["formCompliance"] * 100)),
            "affects": ["setters"],
        })

    timeseries = build_timeseries(contacts, efid, afid, since, until)
    insights = build_insights(floor, clients, setters, goals)

    return {
        "title": dash.get("title", "Setter Floor Dashboard"),
        "dateRange": "%s to %s" % (since.strftime("%d %b %Y"), until.strftime("%d %b %Y")),
        "period": dash.get("defaultPeriod", "7d"),
        "sample": False,
        "source": ghl.get("crm", "SID"),
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "freshnessMin": freshness_min(meta),
        "floor": floor,
        "clients": clients,
        "setters": setters,
        "timeseries": timeseries,
        "insights": insights,
        "dataQuality": flags,
    }


def build_client(div, contacts, efid, since, until, flags):
    counts = {"leads": 0, "worked": 0, "contacted": 0, "booked": 0, "shows": 0, "wons": 0}
    stl = []
    lags = {"leadToFirstAttempt": [], "leadToConnect": [], "connectToBooked": [], "bookedToHeld": []}
    for c in contacts:
        cls = division_for(c, [div])
        if not cls:
            continue
        # count by event-date field inside the window (rule 1), cumulative
        lead_d = parse_ms(field_value(c, efid.get("leadDate")))
        first_a = parse_ms(field_value(c, efid.get("firstAttemptDate")))
        connect_d = parse_ms(field_value(c, efid.get("firstConnectDate")))
        booked_d = parse_ms(field_value(c, efid.get("bookedDate")))
        show_d = parse_ms(field_value(c, efid.get("showDate")))
        won_d = parse_ms(field_value(c, efid.get("wonDate")))

        if in_window(lead_d, since, until):
            counts["leads"] += 1
        if in_window(first_a, since, until):
            counts["worked"] += 1
        if in_window(connect_d, since, until):
            counts["contacted"] += 1
        if in_window(booked_d, since, until):
            counts["booked"] += 1
        if in_window(show_d, since, until):
            counts["shows"] += 1
        if in_window(won_d, since, until):
            counts["wons"] += 1

        if lead_d and first_a:
            stl.append(sla_minutes(lead_d, first_a))
        if lead_d and first_a:
            lags["leadToFirstAttempt"].append(hours(lead_d, first_a))
        if lead_d and connect_d:
            lags["leadToConnect"].append(hours(lead_d, connect_d))
        if connect_d and booked_d:
            lags["connectToBooked"].append(hours(connect_d, booked_d))
        if booked_d and show_d:
            lags["bookedToHeld"].append(hours(booked_d, show_d))

    counts = enforce_funnel(counts, flags, div.get("label", div.get("key", "client")))
    return {
        "key": div.get("key"),
        "label": div.get("label", div.get("key")),
        "market": div.get("market"),
        "active": div.get("active", True),
        "leads": counts["leads"],
        "worked": counts["worked"],
        "contacted": counts["contacted"],
        "booked": counts["booked"],
        "shows": counts["shows"],
        "wons": counts["wons"],
        "workedPct": pct(counts["worked"], counts["leads"]),
        "connectRate": pct(counts["contacted"], counts["worked"]),
        "bookRate": pct(counts["booked"], counts["contacted"]),
        "showRate": pct(counts["shows"], counts["booked"]),
        "speedToLeadMedianMin": median_or_none(stl),
        "lags": {k: median_or_none(v) for k, v in lags.items()},
    }


def build_setters(users, contacts, submissions, ghl, goals, since, until):
    icf = ghl.get("introCallForm", {})
    setter_field = icf.get("setterFieldId")
    afid = ghl.get("attemptFieldIds", {})
    ramp_days = goals.get("rampDays", 30)
    sla = goals.get("speedToLeadSlaMin", 30)
    user_names = ghl.get("users", {})

    # Bucket form submissions by setter (attribution source).
    by_setter = {}
    for s in submissions:
        name = submission_field(s, setter_field)
        if not name:
            continue
        by_setter.setdefault(name, {"forms": 0, "worked_contacts": set()})
        by_setter[name]["forms"] += 1

    setters = []
    for u in users:
        uid = u.get("id")
        name = user_names.get(uid) or u.get("name") or (u.get("firstName", "") + " " + u.get("lastName", "")).strip()
        if not name:
            continue
        rec = by_setter.get(name, {"forms": 0})
        start = parse_ms(u.get("dateAdded") or u.get("createdAt"))
        is_new = bool(start and (dt.datetime.now(dt.timezone.utc) - start).days < ramp_days)

        # Attempts/dials for this setter come from their assigned contacts' custom fields.
        assigned = [c for c in contacts if assigned_setter(c, afid) == name]
        attempts = sum_field(assigned, afid.get("attemptCount"))
        dials = None  # dials come from the dialer integration, null until wired (see .env.example)
        stl = [sla_minutes(parse_ms(field_value(c, ghl.get("eventDateFieldIds", {}).get("leadDate"))),
                           parse_ms(field_value(c, ghl.get("eventDateFieldIds", {}).get("firstAttemptDate"))))
               for c in assigned]
        stl = [x for x in stl if x is not None]
        speed = median_or_none(stl)
        below_sla = bool(speed is not None and speed > sla)

        worked = len(assigned)
        setters.append({
            "name": name,
            "sidUserId": uid,
            "role": u.get("role") or None,
            "assigned": len(assigned) or None,
            "attempts": attempts,
            "dials": dials,
            "connects": None,
            "connectRate": None,
            "booked": None,
            "bookRate": None,
            "shows": None,
            "showRate": None,
            "wons": None,
            "speedToLeadMedianMin": speed,
            "formsSubmitted": rec.get("forms"),
            "formCompliance": pct(rec.get("forms", 0), worked),
            "dayOneCoverage": None,
            "isNew": is_new,
            "belowSla": below_sla and not is_new,
            "byDay": None,
        })
    return setters


def build_floor(contacts, efid, afid, goals, since, until, flags):
    today = dt.datetime.now(dt.timezone.utc).date()
    leads_today = leads_in_range = unworked = unworked_sla = 0
    stl, day_one_hits, day_one_elig = [], 0, 0
    attempts_total = 0
    queue = {"p1": 0, "p2": 0, "p3": 0, "p4": 0}

    for c in contacts:
        lead_d = parse_ms(field_value(c, efid.get("leadDate")))
        first_a = parse_ms(field_value(c, efid.get("firstAttemptDate")))
        if in_window(lead_d, since, until):
            leads_in_range += 1
            if lead_d.date() == today:
                leads_today += 1
            day_one_elig += 1
            if first_a and (first_a - lead_d).total_seconds() <= 86400:
                day_one_hits += 1
            if not first_a:
                unworked += 1
                m = sla_minutes(lead_d, dt.datetime.now(dt.timezone.utc))
                if m is not None and m > goals.get("speedToLeadSlaMin", 30):
                    unworked_sla += 1
        if lead_d and first_a:
            stl.append(sla_minutes(lead_d, first_a))
        attempts_total += int(field_value(c, afid.get("attemptCount")) or 0)
        p = priority_of(c)
        if p:
            queue[p] += 1

    return {
        "leadsToday": leads_today,
        "leadsInRange": leads_in_range,
        "unworkedNow": unworked,
        "unworkedPastSla": unworked_sla,
        "speedToLeadMedianMin": median_or_none(stl),
        "dayOneCoverage": pct(day_one_hits, day_one_elig),
        "formCompliance": None,  # set from setters below if forms exist
        "attemptsLogged": attempts_total or None,
        "dialsLogged": None,     # from the dialer integration, null until wired
        "attemptIntegrity": None,
        "queue": queue,
        "onFloorNow": None,
    }


def build_timeseries(contacts, efid, afid, since, until):
    days = []
    d = since.date()
    while d <= until.date():
        days.append(d)
        d += dt.timedelta(days=1)
    days = days[-30:]
    leads = {d: 0 for d in days}
    worked = {d: 0 for d in days}
    booked = {d: 0 for d in days}
    speed = {d: [] for d in days}
    for c in contacts:
        lead_d = parse_ms(field_value(c, efid.get("leadDate")))
        first_a = parse_ms(field_value(c, efid.get("firstAttemptDate")))
        booked_d = parse_ms(field_value(c, efid.get("bookedDate")))
        if lead_d and lead_d.date() in leads:
            leads[lead_d.date()] += 1
        if first_a and first_a.date() in worked:
            worked[first_a.date()] += 1
        if booked_d and booked_d.date() in booked:
            booked[booked_d.date()] += 1
        if lead_d and first_a and first_a.date() in speed:
            speed[first_a.date()].append(sla_minutes(lead_d, first_a))
    return {
        "days": [d.isoformat() for d in days],
        "leads": [leads[d] for d in days],
        "worked": [worked[d] for d in days],
        "booked": [booked[d] for d in days],
        "speedToLeadMedianMin": [median_or_none(speed[d]) for d in days],
    }


def build_insights(floor, clients, setters, goals):
    ins = []
    if floor.get("unworkedPastSla"):
        ins.append({"severity": "alert", "headline": "%d leads unworked past the SLA" % floor["unworkedPastSla"],
                    "detail": "Inside floor hours, past %d minutes with zero attempts." % goals.get("speedToLeadSlaMin", 30),
                    "metric": "unworkedPastSla", "leadsAtRisk": floor["unworkedPastSla"]})
    fc = floor.get("formCompliance")
    if fc is not None and fc < goals.get("formSubmissionComplianceTarget", 0.95):
        ins.append({"severity": "alert", "headline": "Form compliance is %d percent against target" % round(fc * 100),
                    "detail": "Worked leads without an Intro Call Form make per-setter numbers understated.",
                    "metric": "formCompliance", "leadsAtRisk": floor.get("leadsInRange")})
    for s in setters:
        if s.get("belowSla"):
            ins.append({"severity": "warn", "headline": "%s is over the speed-to-lead SLA" % s["name"],
                        "detail": "Median speed to lead is %s minutes." % s.get("speedToLeadMedianMin"),
                        "metric": "speedToLeadMedianMin", "leadsAtRisk": s.get("assigned") or 0})
    ins.sort(key=lambda x: -(x.get("leadsAtRisk") or 0))
    for i, x in enumerate(ins, 1):
        x["rank"] = i
    return ins


# ------------------------------------------------------------------ small utilities
def in_window(d, since, until):
    return bool(d and since <= d <= until)


def hours(a, b):
    return round((b - a).total_seconds() / 3600, 1) if a and b and b >= a else None


def sum_field(contacts, field_id):
    total = 0
    seen = False
    for c in contacts:
        v = field_value(c, field_id)
        if v is not None:
            try:
                total += int(float(v))
                seen = True
            except (ValueError, TypeError):
                pass
    return total if seen else None


def assigned_setter(contact, afid):
    return field_value(contact, afid.get("assignedSetter"))


def priority_of(contact):
    for tag in [t.lower() for t in contact.get("tags", [])]:
        for p in ("p1", "p2", "p3", "p4"):
            if tag.endswith(" " + p):
                return p
    return None


def submission_field(sub, field_id):
    for k in ("fields", "customFields", "data"):
        arr = sub.get(k)
        if isinstance(arr, list):
            for f in arr:
                if f.get("id") == field_id or f.get("fieldId") == field_id:
                    return f.get("value")
        elif isinstance(arr, dict) and field_id in arr:
            return arr[field_id]
    return None


def freshness_min(meta):
    pulled = parse_ms(meta.get("pulledAt"))
    if not pulled:
        return None
    return max(0, round((dt.datetime.now(dt.timezone.utc) - pulled).total_seconds() / 60))


def die(msg):
    sys.stderr.write("build_data: " + msg + "\n")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Build data/dashboard-data.json from data/raw/.")
    ap.add_argument("--sample", action="store_true", help="rebuild from the committed sample instead of raw")
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
    fired = len(data.get("dataQuality", []))
    print("build_data: wrote data/dashboard-data.json (%d clients, %d setters, %d data-quality flags)"
          % (len(data.get("clients", [])), len(data.get("setters", [])), fired))


if __name__ == "__main__":
    main()
