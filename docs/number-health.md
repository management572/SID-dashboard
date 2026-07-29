# Number Health

Which numbers the floor dials from, whether carriers have started filtering them, and which client
accounts each one is being used on.

## Why the connect rate is the signal, not the volume

A number that has been flagged as spam does not stop working. It keeps placing calls at the usual
rate; what changes is that fewer of them are answered, and more of them fail outright before they
ever ring. So dial volume looks completely normal on a number that has gone bad — the tell is the
**connect rate** collapsing while dials hold steady, and the **fail rate** climbing.

That is why the board leads with connect rate per number and sorts the chart worst-first: the top of
that chart is the retire-this-number list.

## The bands

Set in `numberHealth` in `config/ghl-config.json`, so they can be retuned without touching code.

| Band | Meaning | Rule |
|---|---|---|
| **Healthy** | Connecting normally | connect rate at or above `connectRateWarn` (15%) |
| **Watch** | Slipping | connect rate under 15% |
| **Likely flagged** | Probably filtered by carriers | connect rate under `connectRateBad` (8%), or fail rate at/over `failRateBad` (35%) |
| **Too few dials** | Not judged | fewer than `minDialsToRate` (25) dials in the window |

The last band matters. A number that placed nine calls can read 0% connect purely by chance, and
branding it as spam would get a perfectly good number retired. Below the threshold the board shows a
dash instead of a rate and says "Too few dials" rather than guessing.

## Connection by client

Tapping a number opens its per-client split: dials, connects and connect rate for each client
account the number was used on.

This split is the diagnosis. If a number connects fine for one client and badly for another, the
problem is that client's list — bad data, stale leads, wrong area — not the number. If a number
connects badly across *every* client it touches, the number itself is the problem. The two cases
look identical in the aggregate and only separate here.

## State

Each number is labelled with the state its area code was issued in, from `config/area-codes.json`.
Correct entries in that file rather than in code.

Area code is where the **number** was issued, not where the person answering it lives. It is the
number's identity, useful for spotting that (say) every flagged number shares an area code, which
points at a carrier or a block of numbers rather than at dialing behaviour. It is not a claim about
lead geography.

## Privacy

Only SID's own numbers are recorded. For an outbound call that is the `from`; for an inbound call it
is the `to`. **The other side of every call is the lead's personal number and is dropped at fetch
time** — it never reaches `data/dashboard-data.json`, which is a published artifact. See
`docs/compliance.md`.

`our_number()` in `pipeline/fetch_sid.py` picks the correct side by direction and normalises it to
`+1XXXXXXXXXX`. GHL is inconsistent about where it hangs the numbers on a message, so a few known
shapes are probed before giving up.

## When it populates

The call feed only began recording which number placed each call from the run after this shipped.
Calls pulled before that carry no number, so the tab is empty until a refresh completes and then
covers the call window from that point (`calls.windowDays`, currently 30 days) forward.
