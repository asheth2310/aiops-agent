# Payments service degradation

Applies to: payments — fault type: latency, errors

## Symptoms

- Payments is the slowest healthy service in this demo (80–140ms
  normal latency, vs 60–110ms for orders and inventory), so a small
  rise is normal — degradation means p95 above the 500ms SLO, or an
  error rate above the 20% SLO.
- Latency fault log lines:
  `ERROR payments: downstream DB query timed out after 850ms`
  `WARN payments: p95 latency 850ms exceeds 500ms SLO`
- Error fault log lines:
  `ERROR payments: request failed with HTTP 500 from payments handler`
  `WARN payments: error rate 35% exceeds 20% SLO`
- The service dependency chain is orders → inventory → payments, so a
  payments problem can also show up as slow checkouts upstream.

## Likely causes

1. The same causes as the generic latency / error runbooks (slow DB
   query, handler bug, bad deploy) — start there.
2. A payment-provider or downstream dependency being slow or
   erroring: payments is the service most likely to wait on an
   outside system.
3. A stuck or duplicated charge: if the logs mention a charge or a
   payment attempt more than once, treat it as a money problem first
   and an operations problem second.

## Checks to run

- Read the last 20 log lines and check whether the failure is a
  timeout (latency runbook) or an HTTP 500 (error runbook) — the fix
  differs.
- Check orders and inventory: if they are healthy and only payments
  is degraded, the fault is inside payments or its provider, not the
  shared database.
- Look for any mention of a charge, a duplicate, or a payment
  attempt in the logs before approving anything.

## Fix steps

1. Restart the payments service (the executor's simulated restart).
2. Wait for the 10-second health check: latency under 200ms and
   error rate under 20% means fixed.
3. If it is still degraded after the restart, roll back the last
   deploy; if it is still degraded after that, escalate — do not keep
   restarting a money-moving service.

## When NOT to auto-fix

- NEVER auto-fix anything that touches money: no automatic retries
  of a charge, no automatic refunds, no automatic re-processing of a
  payment. A duplicated charge is worse than a failed one — a failed
  payment can be retried by the customer, a double charge has to be
  refunded and explained.
- Never auto-fix if the logs mention a charge at all: stop and get a
  human, even if confidence is high.
- Never restart payments more than once for the same incident.
- Any fix still needs a human approval in this system (guardian rule) —
  the runbook advises, it never authorises. For payments, that human
  check matters more than for any other service.
