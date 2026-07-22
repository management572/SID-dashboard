#!/usr/bin/env python3
"""probe_calls.py: READ-ONLY diagnostic answering one question -- where does this GHL location's
call activity actually live, and which fields hold it -- so the board can pull real dial/connect/
talk-time data instead of the belt-engine's sid_* mirror fields when that automation is stalled.

It NEVER writes to SID. It prints a report to stdout (read it from the Actions job log) and writes
config/sid-call-probe.json for inspection. That file is aggregate + STRUCTURE only: contact names,
emails, phone numbers and message bodies are never included -- call samples are redacted to a
whitelist of non-PHI keys (duration, direction, status, timestamps, ids) and meta is reduced to its
key names, never its values.

Usage (on the Actions runner, where GHL is reachable):
    SID_TOKEN=... python pipeline/probe_calls.py
    PROBE_MAX_PAGES=10 SID_TOKEN=... python pipeline/probe_calls.py   # scan more contacts
"""

import json
import os
import sys

# Reuse the tested, Cloudflare-friendly API helpers from the puller.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_sid as F  # noqa: E402

OUT = os.path.join(F.ROOT, "config", "sid-call-probe.json")

# The fields that could carry dial / connect / attempt / talk-time activity, both the SID belt-engine
# calculated mirrors (sid_*) and GHL's own native dialer fields (last_call, times_attempted, ...).
CALL_FIELDKEYS = [
    "contact.sid_assigned_at", "contact.sid_assigned_to", "contact.sid_attempt_count",
    "contact.sid_first_dial_at", "contact.sid_last_attempt_at", "contact.sid_last_call_duration_sec",
    "contact.sid_time_to_connect_min", "contact.sid_time_to_connect_days",
    "contact.sid_intro_call_outcome", "contact.sid_client_call_outcome", "contact.sid_schedule_call_date",
    "contact.last_call", "contact.times_attempted", "contact.dialing_status",
    "contact.rate_discussed_on_call",
]

# Only these keys of a call message are ever written out. No from/to (phone numbers), no body.
SAFE_CALL_KEYS = {"id", "type", "messageType", "direction", "status", "callStatus",
                  "callDuration", "duration", "dateAdded", "dateUpdated", "userId",
                  "contactId", "conversationId", "source"}


def build_id_map(fields):
    m = {}
    for f in fields:
        fid = f.get("id")
        key = f.get("fieldKey") or f.get("key")
        if fid and key:
            m[key] = fid
    return m


def field_present(contact, fieldkey, id_map):
    """Return the value of a contact custom field by fieldKey, or None. The /contacts/ list keys
    custom fields by id, so resolve fieldKey -> id first (same approach as build_data.field_value)."""
    refs = {fieldkey, id_map.get(fieldkey)}
    refs.discard(None)
    for f in contact.get("customFields", []) or []:
        if (f.get("id") in refs or f.get("fieldId") in refs
                or f.get("fieldKey") in refs or f.get("key") in refs):
            for k in ("value", "fieldValue", "fieldValueString"):
                v = f.get(k)
                if v not in (None, "", []):
                    return v
    return None


def redact_call(msg):
    """Keep only non-PHI keys; reduce meta to key names (never values, which hold phone numbers)."""
    out = {k: msg.get(k) for k in SAFE_CALL_KEYS if k in msg}
    meta = msg.get("meta")
    if isinstance(meta, dict):
        out["_metaKeys"] = sorted(meta.keys())
        call = meta.get("call")
        if isinstance(call, dict):
            out["_callKeys"] = sorted(call.keys())
            # duration / status / direction are safe to surface; from/to are not.
            out["_callMeta"] = {k: call.get(k) for k in ("duration", "status", "direction") if k in call}
    return out


def try_get(cfg, token, path, params):
    """api_get calls die()/SystemExit on a non-retryable error; swallow it so one dead endpoint does
    not abort the whole probe."""
    try:
        return F.api_get(cfg, token, path, params), None
    except SystemExit:
        return None, "request failed (see log above)"


def scan_contacts(cfg, token, loc, max_pages):
    contacts = []
    params = {"locationId": loc, "limit": F.PAGE_LIMIT}
    page = 0
    while True:
        page += 1
        data, err = try_get(cfg, token, "/contacts/", params)
        if err:
            break
        items = data.get("contacts") or data.get("items") or []
        contacts.extend(items)
        meta = data.get("meta") or {}
        sa, sid = meta.get("startAfter"), meta.get("startAfterId")
        if sa is not None and sid and page < max_pages and items:
            params["startAfter"] = str(sa)
            params["startAfterId"] = str(sid)
        else:
            break
    return contacts


def probe_conversations(cfg, token, loc):
    """Learn the shape of GHL's conversation/call records: search conversations, then pull messages
    for a few and pick out CALL messages."""
    report = {}
    call_samples = []
    conv, err = try_get(cfg, token, "/conversations/search", {"locationId": loc, "limit": 20})
    if err or conv is None:
        report["searchError"] = err or "no response"
        print("\n=== /conversations/search === ERROR:", report["searchError"])
        return report, call_samples
    convs = conv.get("conversations") or []
    report["searchKeys"] = sorted(conv.keys())
    report["returned"] = len(convs)
    report["total"] = conv.get("total")
    if convs:
        report["sampleConversationKeys"] = sorted(convs[0].keys())
    print("\n=== /conversations/search ===")
    print("  top keys:", sorted(conv.keys()))
    print("  returned %s conversations (total=%s)" % (len(convs), conv.get("total")))
    if convs:
        print("  conversation keys:", sorted(convs[0].keys()))

    for cv in convs[:10]:
        cid = cv.get("id")
        if not cid:
            continue
        mres, merr = try_get(cfg, token, "/conversations/%s/messages" % cid, {"limit": 50})
        if merr or mres is None:
            continue
        block = mres.get("messages")
        arr = block.get("messages") if isinstance(block, dict) else (block or mres.get("messages"))
        for m in (arr or []):
            mt = str(m.get("messageType") or m.get("type") or "")
            has_call_meta = isinstance(m.get("meta"), dict) and isinstance(m["meta"].get("call"), dict)
            if "CALL" in mt.upper() or m.get("callDuration") is not None or has_call_meta:
                call_samples.append(redact_call(m))
        if len(call_samples) >= 6:
            break
    report["callMessagesFound"] = len(call_samples)
    print("  found %d call messages in the first probed conversations" % len(call_samples))
    for cs in call_samples[:4]:
        print("   call:", json.dumps(cs)[:500])
    return report, call_samples


def main():
    cfg = F.load_config()
    token = F.token_or_die(cfg)
    loc = cfg["locationId"]
    max_pages = int(os.environ.get("PROBE_MAX_PAGES", "8"))
    print("probe_calls: location=%s  max_pages=%d" % (loc, max_pages))

    fields = F.api_get(cfg, token, "/locations/%s/customFields" % loc, {}).get("customFields", []) or []
    id_map = build_id_map(fields)

    contacts = scan_contacts(cfg, token, loc, max_pages)
    print("probe_calls: scanned %d contacts" % len(contacts))

    counts = {k: 0 for k in CALL_FIELDKEYS}
    samples = {k: [] for k in CALL_FIELDKEYS}
    for c in contacts:
        for k in CALL_FIELDKEYS:
            v = field_present(c, k, id_map)
            if v is not None:
                counts[k] += 1
                if len(samples[k]) < 3:
                    samples[k].append(v)

    print("\n=== populated call fields (of %d contacts) ===" % len(contacts))
    field_report = {}
    for k in CALL_FIELDKEYS:
        print("  %-42s %5d   e.g. %s" % (k, counts[k], samples[k][:2]))
        field_report[k] = {"populated": counts[k], "samples": samples[k]}

    conv_report, call_samples = probe_conversations(cfg, token, loc)

    out = {
        "locationId": loc,
        "scannedContacts": len(contacts),
        "callFields": field_report,
        "conversations": conv_report,
        "callMessageSamples": call_samples,
        "_note": "READ-ONLY probe. Aggregate counts + redacted structure only; no PHI.",
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print("\nprobe_calls: wrote %s" % OUT)


if __name__ == "__main__":
    main()
