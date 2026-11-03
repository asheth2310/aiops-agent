# Error-rate spike

Applies to: orders, inventory, payments — fault type: errors

## Symptoms

- Error rate above the 20% SLO over the last minute (12 samples, 5s
  apart). The demo's error fault fails roughly 30–45% of requests.
- Log lines like:
  `ERROR <service>: request failed with HTTP 500 from <service> handler`
  `WARN <service>: error rate 35% exceeds 20% SLO`
- Latency stays normal-ish (150–220ms): the service is fast, it is
  just failing requests. That is how you tell this apart from a
  latency spike.

## Likely causes

1. A bug in the request handler (the most common one — HTTP 500 from
   the service's own handler, not from a downstream call).
2. A bad deploy: a new release that breaks a code path.
3. A bad config or a missing environment value the handler needs.
4. A downstream dependency returning errors that the handler does not
   handle (check whether the 500s mention a downstream call).

## Checks to run

- Read the last 20 log lines: are the failures all HTTP 500 from this
  service's handler, or do they name a downstream service?
- Check latency: normal latency + high errors means a handler bug, not
  slowness. High latency + high errors means look at the latency
  runbook instead.
- Check whether the errors started right after a deploy — if yes, the
  deploy is the prime suspect.

## Fix steps

1. Restart the affected service (the executor's simulated restart).
2. Wait for the 10-second health check: error rate back under 20%
   (and latency under 200ms) means fixed.
3. If errors continue after the restart, roll back the last deploy —
   a restart cannot fix a bug that shipped in code.
4. If the handler keeps failing on the same input, the fix is a code
   change, not an operations action: escalate, do not keep restarting.

## When NOT to auto-fix

- Never auto-fix when the errors mention a downstream service: the
  fault is not in this service, and restarting it changes nothing.
- Never restart the same service more than once for the same incident:
  a second restart after a failed first one is superstition, not
  troubleshooting — escalate instead.
- Any fix still needs a human approval in this system (guardian rule) —
  the runbook advises, it never authorises.
