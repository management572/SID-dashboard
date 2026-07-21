# Automation: running the pipeline with no local step

The board's live data refreshes on GitHub's own servers, which can reach the GoHighLevel API. Once
the one secret below is set, the pipeline runs itself. Nothing has to run on anyone's laptop.

---

## The one manual step (about 60 seconds)

GitHub will not let any tool, including this one, write a repository secret for you. So the token has
to be pasted in once, by hand, by someone with access to the repo:

1. Open the repo on GitHub: **Settings** (top tab) -> **Secrets and variables** -> **Actions**.
2. Click **New repository secret**.
3. Name: `SID_TOKEN`
4. Value: your GoHighLevel private integration token (the `pit-...` value).
5. Click **Add secret**.

That is the only step. The token lives only in that secret, never in the repo, never in a log. After
this, everything below runs on its own.

(Optional, later: add `SLACK_WEBHOOK_URL` for lead alerts and `DIALER_TOKEN` for the dials column,
the same way.)

---

## What runs, and in what order

Everything is the **SID pipeline** workflow (`.github/workflows/sid-pipeline.yml`). Open the
**Actions** tab, pick **SID pipeline**, click **Run workflow**, and choose a mode.

### 1. Run `discover` first (read only)

This inventories the SID location and writes `config/sid-discovery.json`: the custom field IDs, user
IDs, pipeline and form IDs, and existing tags, plus a suggested mapping of which field is which. It
writes nothing to SID.

Use its output to fill the remaining `TODO_...` IDs in `config/ghl-config.json`:

- `divisions[]` (one row per client agency), `eventDateFieldIds`, `attemptFieldIds`,
  `introCallForm.formId` and `setterFieldId`, `pipeline.id`, and the `users` map.

If `discover` reports fields as `NOT FOUND`, those pieces do not exist in SID yet and must be built.
That build (tags, date fields, the Intro Call Form, the attempt-bundle workflow) is **prompt 02**. It
writes to your live CRM, so it is deliberately gated on your go-ahead, and some of it is done in the
GoHighLevel UI. It is the one part that is not fully hands-off, because it changes your CRM.

### 2. Run `refresh` (or let the schedule do it)

`refresh` pulls SID and rebuilds `data/dashboard-data.json` (aggregate counts only, no PHI), then
commits it. The board always reads the latest. A schedule runs `refresh` every 30 minutes once this
branch is merged to the default branch; until then, run it by hand from the Actions tab.

---

## Where this leaves the "no involvement" goal

| Step | Who | Why |
|---|---|---|
| Store the token as a GitHub secret | you, once | GitHub blocks any tool from writing secrets; the token cannot go in the repo |
| Discover the SID field IDs | automated | the `discover` workflow does it in GitHub infra |
| Fill config from discovery | me, from the discovery output | mechanical once the IDs are known |
| Build the SID CRM (tags, fields, form, workflow) | you plus me, gated | it writes to your live CRM and is partly GHL-UI work (prompt 02) |
| Refresh the board data | automated | the `refresh` workflow, on a schedule |
| Deploy behind Cloudflare Access | gated | prompt 04, never a public URL |

So after you paste that one secret and run `discover`, ping me and I take it from there: I read the
discovery output, fill the config, and drive the rest. The only thing that keeps needing you is
authorizing changes to your live GoHighLevel, because those are irreversible and yours to approve.

---

## Security note

The token was shared in chat, so it now sits in this conversation's history. Once the automation is
running off the GitHub secret, rotate the token in GoHighLevel and update the secret. That way the
only copy of the live token is the one in GitHub's secret store.
