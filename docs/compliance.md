# Compliance and data handling

OBB's clients are home-care agencies. Their leads are prospective care recipients and their
families. That makes lead data sensitive by default and potentially PHI in context, which is why SID
runs on the HIPAA-upgraded GoHighLevel plan.

The dashboard this kit builds was designed after a real incident: a previous dashboard sat on a
public URL where the client acronym in the path was the only thing standing between anyone on the
internet and every client's leads, call cadence, and pipeline. No login, no encryption. The controls
below exist specifically so that cannot recur.

---

## Non-negotiables

**1. The repo is private.** GitHub visibility private, always. If the repo is ever made public, the
config files alone expose SID location IDs, field IDs, and the client roster.

**2. The board is never on a public URL.** Cloudflare Pages plus Cloudflare Access, allowlist by
email domain plus named external addresses. No "unlisted URL" as a security model, ever. An unlisted
URL is a public URL that has not been found yet.

**3. No PHI reaches `data/dashboard-data.json`.** The board is aggregate. What is allowed in that
file:

| Allowed | Never |
|---|---|
| Counts, rates, medians | Diagnoses, conditions, care needs |
| Setter names and SID user IDs | Home addresses |
| Client agency names and markets | Dates of birth, ages |
| First name plus last initial, on a drill-down only | Full names, phone numbers, email addresses |
| SID contact ID as a deep link | Free-text notes from calls or forms |

If a number on the board requires a contact-level record to be useful, the answer is a deep link
into SID (where access is already controlled and audited), not a copy of the record.

**4. Tokens live in the environment only.** `SID_TOKEN`, `SLACK_WEBHOOK_URL`, and anything else go in
GitHub Actions secrets and in a local `.env` that is gitignored. Never in the repo, never pasted into
a chat, never in a config JSON. The kit ships `.env.example` with names and no values.

**5. Alerts carry the minimum.** A Slack alert into `obb-lead-alerts` contains: priority lane, client
agency, first name plus last initial, minutes waiting, and the SID deep link. It does not contain
notes, contact details, or anything about the person's situation. Slack is not a HIPAA-covered
surface in this stack.

**6. The data file is not a database.** `data/dashboard-data.json` holds the current window only. Do
not accumulate historical contact-level rows in the repo. Aggregated daily rows in `timeseries` are
fine and carry no individual data.

---

## Access model

| Who | What they see | How |
|---|---|---|
| Floor lead, SDR training | Full board, all clients | Cloudflare Access, OBB email domain |
| Setters | Setters tab, own row highlighted | Same board, no separate build in v1 |
| Ops, CS, leadership | Full board | Cloudflare Access, OBB email domain |
| Home-care client agencies | Nothing, in v1 | Per-client client-facing views are v2 and need their own gating design and a signed data agreement first |

Do not hand a client agency a link to this board because it happens to have a client toggle. The
toggle is an internal filter, not an access boundary.

---

## Before go-live, confirm

- [ ] Repo visibility is private
- [ ] Cloudflare Access policy is attached to the Pages project and tested from a signed-out browser
- [ ] `.env` is gitignored and no token appears in `git log -p`
- [ ] `data/dashboard-data.json` contains no field outside the schema in `data-schema.md`
- [ ] The old public dashboard URL is decommissioned, not just unlinked
- [ ] Jeremy (Ops) and Eric have signed off on the access allowlist
