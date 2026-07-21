# Agent: sid-sync

**Job:** keep `data/dashboard-data.json` current so the board is never more than 30 minutes behind
the floor.

## Schedule

- Every 30 minutes during floor hours (`intake.yaml > floor.hours`, `floor.days`, `floor.timezone`)
- One full rebuild nightly, 90 minutes after floor close, over a 90-day window
- Manually dispatchable

Cron runs in UTC. Convert from the floor timezone and handle daylight saving, or the sync silently
shifts by an hour twice a year.

## Steps

1. Run `pipeline/fetch_sid.py` for the window (rolling 30 days on the interval run, 90 on the
   nightly).
2. Run `pipeline/build_data.py` to produce a candidate data file in a temp path.
3. **Validate the candidate** against `docs/data-schema.md`: required keys present, no stage exceeds
   its upstream stage, no field outside the schema, `sample` is `false`, and the lead count is within
   an order of magnitude of the previous run.
4. If validation fails: do not write. Fail the workflow with the specific reason and leave the last
   good file in place.
5. If the candidate is byte-identical to the current file, exit without committing. Do not create
   empty commits every 30 minutes.
6. Commit and push with a message naming the window and the headline counts.

## Guards

- **Never overwrite good data with empty or partial data.** This is the one failure that destroys
  trust in the board, and it is unrecoverable in the moment because the floor lead is already looking
  at it.
- Rate-limit aware: back off and retry on a 429 from the GHL API rather than hammering it.
- If the token is rejected, fail immediately with a clear message. Do not retry: a rejected token is
  usually a rotation, not a blip.
- Redact `SID_TOKEN` from every log line.

## Output

A commit, or a clean no-op, or a loud failure. Never a quiet partial success.
