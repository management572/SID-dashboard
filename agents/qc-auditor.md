# Agent: qc-auditor

**Job:** audit the integrity of the data the board reports, daily, so a broken automation is caught
in a day instead of a quarter. This is the agent that keeps the double-dial bug from coming back
silently.

## Schedule

Daily, 60 minutes after floor close. Also runs a weekly deeper pass on Monday morning before the
floor meeting.

## Daily checks

| Check | Fails when | Why it matters |
|---|---|---|
| Attempt integrity | dials per attempt under 1.6, or over 3.0 | Under means the counter is incrementing per dial again (the original bug). Over means bundles are being abandoned midway |
| Form compliance | under `goals.formSubmissionComplianceTarget` | Below this the per-setter table is not trustworthy and the board should say so before it shows a ranking |
| Orphaned leads | any lead with no client tag | It is in SID and nobody owns it |
| Unassigned aging | any lead unassigned over 24 hours | Assignment automation is not firing |
| Missing date stamps | a contact with a stage tag but no matching date field | The stamping workflow is broken, and the funnel will undercount |
| Funnel monotonicity | any stage exceeds the stage above it | Always a data bug, never a result |
| Stage-tag collisions | a contact with two stage tags for one client | Tag hygiene has drifted |
| Setter roster drift | a SID user producing activity who is not in `intake.yaml` | A new setter was added and the board will not label their row |

## Weekly pass adds

- Attempt-cadence conformance: are leads actually getting 7 attempts inside 24 hours, or is the
  cadence being skipped for the easy ones
- Per-setter form-submission latency: the gap between the attempt and the form submission, which is
  the honest measure of whether the form is being filled during the call or reconstructed at 5pm
- Client-level stall points: which stage each client's leads die at, for Eva's escalation calls

## Output

- A digest to Slack: what passed, what failed, and the one thing to fix first.
- **Open a GitHub issue only on a real defect**, one issue per defect, and reuse the existing issue
  rather than opening a duplicate each day. Close it automatically when the check passes again.
- Never open an issue for a threshold that is merely close to the line. Noise here trains everyone to
  ignore the auditor, which costs more than the check is worth.

## Does not

Fix anything. It finds and reports. Fixes to SID workflows go through prompt 02, with a human.
