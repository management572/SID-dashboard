#!/usr/bin/env python3
"""
fetch_sid.py: pull the raw SID (GoHighLevel) records the board needs, into data/raw/.

This script only READS from SID and writes JSON files to disk. It never writes to SID and never
touches a live URL. The transform and all metric computation happen in build_data.py; this file's
one job is to page through the API and dump exactly what came back.

What it pulls, for the location in config/ghl-config.json:
  - contacts        (with tags and the event-date + attempt custom fields)
  - opportunities   (for won status and pipeline stage reference)
  - users           (to label the Setters tab)
  - form submissions (the Intro Call Form: the attribution + compliance source)
  - custom fields    (id -> name/key map, so build_data can resolve field IDs)

Auth: the token comes from the SID_TOKEN environment variable only. It is never read from a config
file, never hard-coded, and never printed. See docs/compliance.md rule 4.

Usage:
    export SID_TOKEN=...            # a SID location-scoped private integration token
    python pipeline/fetch_sid.py                 # pull the default window from config
    python pipeline/fetch_sid.py --days 30       # override the window
    python pipeline/fetch_sid.py --since 2026-07-01 --until 2026-07-21

Output: data/raw/*.json plus data/raw/_meta.json (the window and counts).
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "ghl-config.json")
RAW_DIR = os.path.join(ROOT, "data", "raw")

PAGE_LIMIT = 100
MAX_RETRIES = 4
RETRY_BASE_SEC = 2


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    loc = cfg.get("locationId", "")
    if not loc or str(loc).startswith("TODO"):
        die("config/ghl-config.json locationId is not set. Fill it in before pulling live data.")
    return cfg


def token_or_die(cfg):
    env_name = cfg.get("tokenEnv", "SID_TOKEN")
    tok = os.environ.get(env_name)
    if not tok:
        die("Environment variable %s is not set. Export the SID token before running "
            "(see .env.example). It must never live in the repo." % env_name)
    return tok


def die(msg):
    sys.stderr.write("fetch_sid: " + msg + "\n")
    sys.exit(1)


def api_get(cfg, token, path, params):
    """GET one page from the LeadConnector API, with retry on network / 429 / 5xx."""
    base = cfg.get("apiBase", "https://services.leadconnectorhq.com").rstrip("/")
    url = base + path
    if params:
        # Coerce every value to str: GHL pagination cursors (startAfter) come back as ints, and
        # urlencode/quote raises "'int' object has no attribute 'decode'" on a non-str value.
        url += "?" + urllib.parse.urlencode({k: str(v) for k, v in params.items() if v is not None})
    headers = {
        "Authorization": "Bearer " + token,
        "Version": cfg.get("apiVersion", "2021-07-28"),
        "Accept": "application/json",
        # LeadConnector sits behind Cloudflare, which blocks the default Python-urllib User-Agent
        # (Error 1010). Send a normal browser-like UA so the request is not filtered as a bot.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    last_err = None
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")[:400]
            except Exception:
                pass
            if e.code in (429, 500, 502, 503, 504):
                last_err = "HTTP %s: %s" % (e.code, body)
            elif e.code in (401, 403):
                die("SID returned %s (auth). Check the token scope for this location. %s" % (e.code, body))
            else:
                die("SID returned HTTP %s for %s. %s" % (e.code, path, body))
        except urllib.error.URLError as e:
            last_err = "network error: %s" % e.reason
        time.sleep(RETRY_BASE_SEC * (2 ** attempt))
    die("giving up on %s after %d retries. Last error: %s" % (path, MAX_RETRIES, last_err))


def paginate(cfg, token, path, params, items_key):
    """Follow LeadConnector cursor pagination and collect every item."""
    out = []
    params = dict(params)
    params.setdefault("limit", PAGE_LIMIT)
    page = 0
    while True:
        page += 1
        data = api_get(cfg, token, path, params)
        items = data.get(items_key) or data.get("items") or []
        out.extend(items)
        meta = data.get("meta") or {}
        next_url = meta.get("nextPageUrl") or meta.get("nextPage")
        start_after = meta.get("startAfter")
        start_after_id = meta.get("startAfterId")
        if start_after and start_after_id:
            params["startAfter"] = start_after
            params["startAfterId"] = start_after_id
        elif next_url:
            # Some endpoints return an absolute nextPageUrl; extract its query.
            q = urllib.parse.urlparse(next_url).query
            if not q:
                break
            params = dict(urllib.parse.parse_qsl(q))
        else:
            break
        if not items or page > 1000:
            break
        time.sleep(0.15)  # be polite to the API
    return out


def window(cfg, args):
    tz_days = args.days if args.days is not None else default_days(cfg)
    if args.until:
        until = dt.datetime.fromisoformat(args.until)
    else:
        until = dt.datetime.now(dt.timezone.utc)
    if args.since:
        since = dt.datetime.fromisoformat(args.since)
    else:
        since = until - dt.timedelta(days=tz_days)
    return since, until


def default_days(cfg):
    # Map the config default period to a day count. The board's default is short on purpose.
    return 7


def ms(ts):
    return int(ts.timestamp() * 1000)


def main():
    ap = argparse.ArgumentParser(description="Pull raw SID records into data/raw/.")
    ap.add_argument("--days", type=int, default=None, help="window size in days (default 7)")
    ap.add_argument("--since", help="ISO date, overrides --days")
    ap.add_argument("--until", help="ISO date, defaults to now")
    args = ap.parse_args()

    cfg = load_config()
    token = token_or_die(cfg)
    location_id = cfg["locationId"]
    since, until = window(cfg, args)

    os.makedirs(RAW_DIR, exist_ok=True)
    print("fetch_sid: location=%s window=%s..%s" % (location_id, since.date(), until.date()))

    # 1) custom fields: id -> {name, fieldKey, dataType}. This endpoint returns the full list in one
    #    response and rejects a `limit` query param (422), so call it directly, not via paginate().
    fields = api_get(cfg, token, "/locations/%s/customFields" % location_id, {}).get("customFields", []) or []
    dump("custom_fields", fields)

    # 2) users (setters). Same: full list in one response, no `limit` param.
    users = api_get(cfg, token, "/users/", {"locationId": location_id}).get("users", []) or []
    dump("users", users)

    # 3) contacts, with their tags and custom field values. The GET /contacts/ endpoint does not
    #    accept a server-side date filter (startAfterUpdatedAt is rejected 422), so page through the
    #    location and let build_data.py filter by the event-date fields inside the window.
    contacts = paginate(cfg, token, "/contacts/", {
        "locationId": location_id,
    }, "contacts")
    dump("contacts", contacts)

    # 4) opportunities (won status + pipeline stage)
    pipeline_id = cfg.get("pipeline", {}).get("id", "")
    opp_params = {"location_id": location_id}
    if pipeline_id and not str(pipeline_id).startswith("TODO"):
        opp_params["pipeline_id"] = pipeline_id
    opportunities = paginate(cfg, token, "/opportunities/search", opp_params, "opportunities")
    dump("opportunities", opportunities)

    # 5) Intro Call Form submissions (attribution + compliance)
    form_id = cfg.get("introCallForm", {}).get("formId", "")
    submissions = []
    if form_id and not str(form_id).startswith("TODO"):
        submissions = paginate(cfg, token, "/forms/submissions", {
            "locationId": location_id,
            "formId": form_id,
            "startAt": since.date().isoformat(),
            "endAt": until.date().isoformat(),
        }, "submissions")
    else:
        print("fetch_sid: introCallForm.formId not set yet, skipping form submissions "
              "(form compliance will be null until prompt 02 creates the form).")
    dump("form_submissions", submissions)

    meta = {
        "locationId": location_id,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "pulledAt": until.isoformat(),
        "counts": {
            "customFields": len(fields),
            "users": len(users),
            "contacts": len(contacts),
            "opportunities": len(opportunities),
            "formSubmissions": len(submissions),
        },
    }
    dump("_meta", meta)
    print("fetch_sid: done. " + json.dumps(meta["counts"]))


def dump(name, obj):
    path = os.path.join(RAW_DIR, name + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
    n = len(obj) if isinstance(obj, list) else 1
    print("  wrote data/raw/%s.json (%d)" % (name, n))


if __name__ == "__main__":
    main()
