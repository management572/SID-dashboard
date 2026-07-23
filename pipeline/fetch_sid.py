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

# Conversation message types that represent a phone call. Setters dial from the GHL CRM, so each
# dial is one of these messages carrying meta.call.duration, a direction and the userId who placed it.
CALL_TYPES = ("TYPE_CALL", "TYPE_IVR_CALL")


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
            elif e.code == 400 and "timeout" in body.lower():
                # GHL wraps its own 30s upstream timeout as a 400 ("Request Timeout after
                # 30000ms"). It is transient and clears on retry, so treat it like a 5xx.
                last_err = "HTTP 400 (upstream timeout): %s" % body
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
        start_after = meta.get("startAfter")
        start_after_id = meta.get("startAfterId")
        next_url = meta.get("nextPageUrl")
        if start_after is not None and start_after_id:
            # GHL v2 cursor pagination. Coerce to str: startAfter is an int timestamp.
            params["startAfter"] = str(start_after)
            params["startAfterId"] = str(start_after_id)
        elif isinstance(next_url, str) and next_url:
            # Some endpoints return an absolute nextPageUrl instead; extract its query.
            q = urllib.parse.urlparse(next_url).query
            if not q:
                break
            params = dict(urllib.parse.parse_qsl(q))
        else:
            # No usable cursor (e.g. a numeric nextPage we cannot follow, or the last page). Stop.
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


def to_ms(v):
    """Epoch-ms from an ISO string or a numeric epoch (ms or s), or None."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return int(v if v > 1e12 else v * 1000)
    try:
        return int(dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, OverflowError, OSError):
        return None


def _conv_cursor(cv):
    """The pagination cursor for /conversations/search is the last item's sort value."""
    s = cv.get("sort")
    if isinstance(s, list) and s:
        return s[0]
    return s


def pull_conv_calls(cfg, token, conv_id, since_ms, max_pages):
    """Pull the call messages of one conversation (newest first), stopping once past the window."""
    out = []
    params = {"limit": 100}
    for _ in range(max_pages):
        try:
            res = api_get(cfg, token, "/conversations/%s/messages" % conv_id, params)
        except SystemExit:
            break
        block = res.get("messages")
        arr = block.get("messages") if isinstance(block, dict) else (block or [])
        if not arr:
            break
        oldest = None
        for m in arr:
            mt = str(m.get("messageType") or m.get("type") or "")
            if "CALL" not in mt.upper():
                continue
            ts = to_ms(m.get("dateAdded"))
            if ts is not None:
                oldest = ts if (oldest is None or ts < oldest) else oldest
            meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
            call = meta.get("call") if isinstance(meta.get("call"), dict) else {}
            dur = call.get("duration")
            out.append({
                "contactId": m.get("contactId"),
                "userId": m.get("userId"),
                "direction": m.get("direction"),
                "status": m.get("status") or call.get("status"),
                "duration": dur if isinstance(dur, (int, float)) else None,
                "messageType": mt,
                "dateAdded": m.get("dateAdded"),
            })
        if oldest is not None and oldest < since_ms:
            break
        nxt = block.get("nextPage") if isinstance(block, dict) else None
        last_id = block.get("lastMessageId") if isinstance(block, dict) else None
        if not nxt or not last_id:
            break
        params["lastMessageId"] = last_id
        time.sleep(0.1)
    return out


def pull_calls(cfg, token, location_id, since_dt):
    """Authoritative call feed: page conversations newest-first and pull their call messages within
    [since, now]. This is the real dial/connect/talk-time source (setters dial from the CRM); the
    sid_* mirror fields are not being written. READ-ONLY. Bounded for runtime; never aborts the run.

    Returns a flat list of call events: contactId, userId, direction, status, duration (s), timestamp.
    """
    ccfg = cfg.get("calls", {}) or {}
    max_convs = int(ccfg.get("maxConversations", 4000))
    max_msg_pages = int(ccfg.get("maxMessagePagesPerConversation", 4))
    since_ms = int(since_dt.timestamp() * 1000)

    calls = []
    scanned = pulled = 0
    newest_lmd = oldest_lmd = None
    consecutive_old = 0
    params = {"locationId": location_id, "limit": 100, "sortBy": "last_message_date", "sort": "desc"}
    try:
        while scanned < max_convs:
            data = api_get(cfg, token, "/conversations/search", params)
            convs = data.get("conversations") or []
            if not convs:
                break
            cursor, stop = None, False
            for cv in convs:
                scanned += 1
                cursor = _conv_cursor(cv) or cursor
                lmd = to_ms(cv.get("lastMessageDate") or cv.get("dateUpdated"))
                if lmd is not None:
                    newest_lmd = lmd if (newest_lmd is None or lmd > newest_lmd) else newest_lmd
                    oldest_lmd = lmd if (oldest_lmd is None or lmd < oldest_lmd) else oldest_lmd
                # A conversation whose last activity predates the window has no in-window calls. Skip it,
                # but do NOT terminate the scan: the search is not reliably sorted, so a stray old
                # conversation must not cut off the newer ones behind it. Only stop after a long run of
                # consecutive old conversations (deep past the window) to bound cost.
                if lmd is not None and lmd < since_ms:
                    consecutive_old += 1
                    if consecutive_old >= 1000:
                        stop = True
                        break
                    continue
                consecutive_old = 0
                cid = cv.get("id")
                if not cid:
                    continue
                # Pull messages for every in-window conversation. (The conversation.messageTypes hint is
                # not a reliable enumeration of a conversation's calls, so filtering on it dropped recent
                # call-bearing conversations; message pulls are cheap, so just always pull.)
                pulled += 1
                calls.extend(pull_conv_calls(cfg, token, cid, since_ms, max_msg_pages))
                if scanned >= max_convs:
                    break
            if stop or scanned >= max_convs or cursor is None:
                break
            params["startAfterDate"] = str(cursor)
            time.sleep(0.05)
    except SystemExit:
        print("fetch_sid: conversations pull stopped on API error; keeping %d call events." % len(calls))
    except Exception as e:  # never let the call feed abort the contacts-based build
        print("fetch_sid: conversations pull error (%s); keeping %d call events." % (e, len(calls)))
    def _d(msv):
        return dt.datetime.fromtimestamp(msv / 1000, tz=dt.timezone.utc).date().isoformat() if msv else "n/a"
    print("fetch_sid: calls scanned %d conversations, pulled messages for %d, %d call events since %s "
          "(conversation last-activity range %s .. %s)"
          % (scanned, pulled, len(calls), since_dt.date(), _d(oldest_lmd), _d(newest_lmd)))
    _log_call_histogram(calls)
    return calls


def _log_call_histogram(calls):
    """Diagnostic: where do the pulled call events fall in time, by direction? This tells us whether
    recent-window undercounts are a pull-coverage problem or a downstream counting problem."""
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    day = 86400000
    buckets = {1: 0, 3: 0, 7: 0, 14: 0, 30: 0}
    outbound = inbound = other = 0
    dirs = {}
    recent = []
    for c in calls:
        ts = to_ms(c.get("dateAdded"))
        d = (c.get("direction") or "none")
        dirs[d] = dirs.get(d, 0) + 1
        if d == "outbound":
            outbound += 1
        elif d == "inbound":
            inbound += 1
        else:
            other += 1
        if ts is None:
            continue
        age = now_ms - ts
        for k in buckets:
            if age <= k * day:
                buckets[k] += 1
        if d == "outbound":
            recent.append(ts)
    recent.sort(reverse=True)
    top = [dt.datetime.fromtimestamp(t / 1000, tz=dt.timezone.utc).isoformat() for t in recent[:5]]
    print("fetch_sid: call directions %s" % dirs)
    print("fetch_sid: call events in last 1/3/7/14/30 days = %s" % buckets)
    print("fetch_sid: outbound=%d inbound=%d other=%d; 5 newest outbound: %s"
          % (outbound, inbound, other, top))


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

    # 6) call activity from conversation messages: the real dial / connect / talk-time feed. Bounded
    #    to a recent window (config.calls.windowDays) so the message pulls stay within runtime.
    call_days = int(cfg.get("calls", {}).get("windowDays", 45))
    calls_since = until - dt.timedelta(days=call_days)
    calls = pull_calls(cfg, token, location_id, calls_since)
    dump("calls", calls)

    meta = {
        "locationId": location_id,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "pulledAt": until.isoformat(),
        "callsSince": calls_since.isoformat(),
        "callWindowDays": call_days,
        "counts": {
            "customFields": len(fields),
            "users": len(users),
            "contacts": len(contacts),
            "opportunities": len(opportunities),
            "formSubmissions": len(submissions),
            "calls": len(calls),
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
