# Downstream DB query timeouts

Applies to: orders, inventory, payments — fault type: latency

## Symptoms

- Log lines like:
  `ERROR <service>: downstream DB query timed out after 850ms`
  repeated on nearly every tick while the service is degraded.
- p95 latency 800ms or higher, and the error rate climbs with it
  (timed-out queries count as failed requests).
- The timeout value in the log is the clue: it is the query that is
  slow, not the service's own code.

## Likely causes

1. A slow query: a missing index, a full table scan, or a query that
   grew with the data (the most common cause).
2. A locked row or table: another transaction holds a lock the query
   needs, so it waits until it times out.
3. Connection-pool exhaustion: all pool connections are busy, so new
   queries queue and time out.
4. The database itself is overloaded or down: every service that talks
   to it slows down at the same time.

## Checks to run

- Are all three services (orders, inventory, payments) showing DB
  timeouts at once? If yes, the database is the problem, not any
  single service.
- Is the timeout always the same number of milliseconds? A fixed
  timeout value points at a query timeout setting, not random slowness.
- Read the evidence log lines the investigator quoted: the timeout
  message names the service, and the pattern across lines shows
  whether it is one query or all queries.

## Fix steps

1. Restart the affected service first (the executor's simulated
   restart) — it clears stuck connections and is the cheapest test.
2. Wait for the 10-second health check.
3. If timeouts continue after the restart, do NOT restart again: the
   fix is in the database — find the slow query, check for locks, and
   check pool usage.
4. If all services time out at once, escalate to whoever owns the
   database: no service restart fixes a sick database.

## When NOT to auto-fix

- Never auto-fix when the timeouts span all three services: the fault
  is shared infrastructure, and restarting services one by one just
  adds load to a struggling database.
- Never auto-fix a repeated timeout after one restart already failed:
  the second restart will fail exactly like the first.
- Never run a fix that touches the database itself (kill a query, drop
  a lock, restart the DB) without a human deciding it — that is
  outside what this agent is allowed to do, and it can destroy data.
- Any fix still needs a human approval in this system (guardian rule) —
  the runbook advises, it never authorises.
