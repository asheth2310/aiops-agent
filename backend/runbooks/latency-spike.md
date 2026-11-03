# Latency spike

Applies to: orders, inventory, payments — fault type: latency

## Symptoms

- p95 latency above the 500ms SLO for two consecutive 1-minute windows
  (the detector fires on 24 degraded samples, 5s apart).
- Log lines like:
  `ERROR <service>: downstream DB query timed out after 850ms`
  `WARN <service>: p95 latency 850ms exceeds 500ms SLO`
- Error rate also climbs (roughly 30–45%) because slow requests time out,
  so the error-rate rule often fires first.
- Requests per minute drops: a degraded service serves fewer requests.

## Likely causes

1. A downstream database query gone slow (the most common one in this
   demo — the timeout message in the logs points straight at it).
2. A bad deploy that added an expensive query or a missing index.
3. A saturated dependency: the downstream service is slow, not this one.
4. A retry storm from a caller hammering the slow endpoint.

## Checks to run

- Read the last 20 log lines: is the slowness on every request or only
  on requests that hit the database?
- Compare p50 vs p95: if p50 is healthy and p95 is high, only some
  requests (usually the DB ones) are slow.
- Check the other services: if orders, inventory and payments are all
  slow at once, the shared downstream database is the suspect, not
  any single service.

## Fix steps

1. Restart the affected service (the executor's simulated restart).
2. Wait for the 10-second health check: latency under 200ms means fixed.
3. If latency stays above 500ms after the restart, roll back the last
   deploy — a restart cannot fix a slow query that shipped in code.
4. If the logs still show DB timeouts after restart, the fix belongs
   in the database (slow query / missing index), not in this service.

## When NOT to auto-fix

- Never auto-fix when all three services are slow at once: restarting
  one service cannot fix a shared downstream database, and restarting
  all three in a row makes the outage worse.
- Never auto-fix on a latency spike with no ERROR log lines: without
  evidence of what is slow, a restart is a guess, not a fix.
- Any fix still needs a human approval in this system (guardian rule) —
  the runbook advises, it never authorises.
