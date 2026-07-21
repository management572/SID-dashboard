#!/usr/bin/env python3
"""
discover_sid.py: read-only inventory of the SID (GoHighLevel) location, so the TODO IDs in
config/ghl-config.json can be filled in without anyone hunting through the GHL UI.

It lists, for the location in config/ghl-config.json:
  - custom fields   (id, name, fieldKey, dataType)  -> eventDateFieldIds, attemptFieldIds, form field
  - users           (id, name)                        -> the users map + assignedSetter labels
  - pipelines       (id, name, stages)                -> pipeline.id and wonStageId
  - forms           (id, name)                        -> introCallForm.formId
  - tags            (name)                            -> the stage/priority tag taxonomy

It writes config/sid-discovery.json (IDs and names only, no PHI) and prints a readable summary.
This is the safe, read-only companion to prompt 02: it shows what already exists so you only build
what is missing. It never writes to SID.

Auth: SID_TOKEN environment variable only. Never hard-coded, never printed.

Usage:
    export SID_TOKEN=...
    python pipeline/discover_sid.py
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "ghl-config.json")
OUT_PATH = os.path.join(ROOT, "config", "sid-discovery.json")
MAX_RETRIES = 4
RETRY_BASE_SEC = 2

# Field name hints: which discovered custom field likely maps to which config slot. Advisory only;
# a human confirms the final mapping, because a wrong date field silently miscounts the funnel.
FIELD_HINTS = {
    "leadDate": ["lead date", "lead in", "date added", "opt in"],
    "firstAttemptDate": ["first attempt", "attempt 1", "first dial", "first touch"],
    "firstConnectDate": ["first connect", "connected", "first contact"],
    "bookedDate": ["booked", "appointment booked", "booked date"],
    "showDate": ["show", "held", "showed"],
    "wonDate": ["won", "signed", "closed won"],
    "attemptCount": ["attempt count", "attempts", "number of attempts"],
    "lastAttemptDate": ["last attempt", "last dial"],
    "dayOneAttemptCount": ["day one", "day 1", "24h attempts"],
    "assignedSetter": ["assigned setter", "setter", "sdr", "owner"],
}


def die(msg):
    sys.stderr.write("discover_sid: " + msg + "\n")
    sys.exit(1)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    loc = cfg.get("locationId", "")
    if not loc or str(loc).startswith("TODO"):
        die("config/ghl-config.json locationId is not set.")
    return cfg


def api_get(cfg, token, path, params=None):
    base = cfg.get("apiBase", "https://services.leadconnectorhq.com").rstrip("/")
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    headers = {
        "Authorization": "Bearer " + token,
        "Version": cfg.get("apiVersion", "2021-07-28"),
        "Accept": "application/json",
        # LeadConnector sits behind Cloudflare, which blocks the default Python-urllib User-Agent
        # (Error 1010). Send a normal browser-like UA so the request is not filtered as a bot.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")[:300]
            except Exception:
                pass
            if e.code in (401, 403):
                die("SID returned %s (auth). Check the token scope for this location. %s" % (e.code, body))
            if e.code == 404:
                return {}  # endpoint not present on this plan; treat as empty
            last = "HTTP %s: %s" % (e.code, body)
        except urllib.error.URLError as e:
            last = "network error: %s" % e.reason
        time.sleep(RETRY_BASE_SEC * (2 ** attempt))
    sys.stderr.write("discover_sid: gave up on %s (%s)\n" % (path, last))
    return {}


def main():
    cfg = load_config()
    token = os.environ.get(cfg.get("tokenEnv", "SID_TOKEN"))
    if not token:
        die("SID_TOKEN is not set. Export it before running (see .env.example).")
    loc = cfg["locationId"]
    print("discover_sid: reading location %s (read only)\n" % loc)

    custom_fields = (api_get(cfg, token, "/locations/%s/customFields" % loc).get("customFields") or [])
    users = (api_get(cfg, token, "/users/", {"locationId": loc}).get("users") or [])
    pipelines = (api_get(cfg, token, "/opportunities/pipelines", {"locationId": loc}).get("pipelines") or [])
    forms = (api_get(cfg, token, "/forms/", {"locationId": loc, "limit": 100}).get("forms") or [])
    tags = (api_get(cfg, token, "/locations/%s/tags" % loc).get("tags") or [])

    fields_slim = [{
        "id": f.get("id"),
        "name": f.get("name"),
        "fieldKey": f.get("fieldKey"),
        "dataType": f.get("dataType"),
    } for f in custom_fields]

    users_slim = [{
        "id": u.get("id"),
        "name": u.get("name") or (str(u.get("firstName", "")) + " " + str(u.get("lastName", ""))).strip(),
        "role": u.get("roles", {}).get("role") if isinstance(u.get("roles"), dict) else u.get("role"),
    } for u in users]

    pipelines_slim = [{
        "id": p.get("id"),
        "name": p.get("name"),
        "stages": [{"id": s.get("id"), "name": s.get("name")} for s in (p.get("stages") or [])],
    } for p in pipelines]

    forms_slim = [{"id": f.get("id"), "name": f.get("name")} for f in forms]
    tags_slim = sorted([t.get("name") for t in tags if t.get("name")])

    suggestions = suggest_field_map(fields_slim)

    out = {
        "locationId": loc,
        "counts": {
            "customFields": len(fields_slim), "users": len(users_slim),
            "pipelines": len(pipelines_slim), "forms": len(forms_slim), "tags": len(tags_slim),
        },
        "customFields": fields_slim,
        "users": users_slim,
        "pipelines": pipelines_slim,
        "forms": forms_slim,
        "tags": tags_slim,
        "suggestedFieldMap": suggestions,
        "_note": "IDs and names only, no PHI. Confirm suggestedFieldMap before pasting into "
                 "config/ghl-config.json: a wrong date field silently miscounts the funnel.",
    }
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    print_summary(out)
    print("\ndiscover_sid: wrote config/sid-discovery.json")


def suggest_field_map(fields):
    out = {}
    for slot, hints in FIELD_HINTS.items():
        match = None
        for f in fields:
            name = (f.get("name") or "").lower()
            if any(h in name for h in hints):
                match = {"id": f.get("id"), "name": f.get("name")}
                break
        out[slot] = match
    return out


def print_summary(out):
    c = out["counts"]
    print("Found: %d custom fields, %d users, %d pipelines, %d forms, %d tags"
          % (c["customFields"], c["users"], c["pipelines"], c["forms"], c["tags"]))
    print("\nSuggested field map (confirm before use):")
    for slot, m in out["suggestedFieldMap"].items():
        print("  %-20s %s" % (slot, (m["name"] + "  [" + m["id"] + "]") if m else "NOT FOUND, create in prompt 02"))
    if out["forms"]:
        print("\nForms:")
        for f in out["forms"]:
            print("  %-40s [%s]" % (f["name"], f["id"]))
    if out["pipelines"]:
        print("\nPipelines:")
        for p in out["pipelines"]:
            print("  %s [%s], %d stages" % (p["name"], p["id"], len(p["stages"])))


if __name__ == "__main__":
    main()
