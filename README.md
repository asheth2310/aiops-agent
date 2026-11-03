# Governed AIOps Agent — v2

An agent watches three demo services (orders, inventory, payments),
detects anomalies with hard thresholds, explains them, and asks a human
to approve any fix. Nothing auto-fixes. Inspired by Quinnox's
Qinfinite / QyrusAI: governance-first autonomy, not a chatbot.

## How to run

Backend (from `aiops-agent/`):
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn backend.main:app --reload --port 8000
```

Frontend (from `aiops-agent/frontend/`):
```bash
npm install
npm run dev        # http://localhost:5173
```

Demo: open the dashboard, click "Break" on a service (latency or
errors), wait for the incident, read the cause + evidence, click
Approve, watch it recover. "Reset demo" clears incidents, metrics and
logs (the audit trail is kept) so you can run it again from clean.
"Run guided demo" breaks inventory (latency) and walks you through the
flow. "Chaos mode" injects one random fault every ~75s, only while
everything is calm — it never approves anything.

The dashboard stays alive even when healthy: it polls every 2s, the
live log stream tails real backend log lines, and the stats strip shows
total requests and requests/min ticking up. Click a service card for
its detail view (p50/p95/p99, error budget, uptime, last 10 samples).
The active incident is a full dossier: per-stage timeline with
durations, detection snapshot (value vs threshold, breach length,
detector window), confidence breakdown computed from the data reviewed,
timestamped evidence, what the investigator was given, a 4-step
remediation plan, and a risk note for approve vs reject.

## Showcase / demo in 2 minutes

The one line to open with: the agent watches 3 services, explains
every incident with evidence, and never fixes anything without a human
approving. The dashboard shows that pipeline as a strip under the
header — Detector → Investigator → Guardian → Human approval →
Executor — with the current step highlighted while an incident is open,
and the incident card shows a green **✦ Live Gemini** badge when the
explanation came from real Gemini (amber **Mock** badge otherwise).

1. **0:00** — Open the dashboard. Point at the pipeline strip and the
   three healthy services with their live sparklines.
2. **0:20** — Click **▶ Run guided demo**. It breaks inventory
   (latency). While the detector's 1-minute window fills (~30–60s),
   talk through the governance rules below.
3. **1:00** — The incident appears: detection snapshot, cause,
   evidence (real log lines only), confidence with its breakdown, and
   the Guardian's decision. Point at the source badge.
4. **1:30** — Click **Approve fix**. The executor restarts the service
   and the 10s health check resolves the incident; the audit trail
   shows detector → investigator → guardian → human → executor.
5. **1:50** — **Reset demo** to run it again, or flip **Chaos mode**
   on to keep faults coming while you answer questions.

Run it locally with `GEMINI_LIVE=1` for the live-Gemini demo (see
below); the public link shows live Gemini too, once the key is set in
the Render dashboard.

## Governance policy (5 lines)

1. Detection is hard thresholds only — no LLM decides an incident exists.
2. The Investigator may only quote real log lines; invented evidence is rejected and retried once.
3. Confidence below 70 means no Approve button at all.
4. Confidence 70 and above still needs a human to approve — the MVP never auto-fixes.
5. Every step (detect, investigate, guardian, approve/reject, fix) is written to the audit trail.

## Hosted

One Render web service (free tier) runs the Dockerfile: it builds the
React dashboard, then FastAPI serves the dashboard + API on one URL.
`render.yaml` is the Blueprint for it. Free tier spins down after
inactivity (first load ~50s) and in-memory state resets on restart.

## Live on Render

`render.yaml` sets `GEMINI_LIVE=1` and declares `GEMINI_API_KEY` with
`sync: false`, so Render prompts for the key in the dashboard
(**Environment** tab) and it is never stored in the repo. Once the key
is set, the hosted demo calls Gemini directly (REST) and the incident
card shows the green **✦ Live Gemini** badge. With no key set, the
hosted demo still works exactly the same — the investigator falls back
to the deterministic mock and the badge says **Mock**. Redeploy is
automatic after the key is saved (Render restarts the service).

| Run | Fault | Time to detect | Confidence | Cause correct? | Time to recover* |
|-----|-------|----------------|------------|----------------|------------------|
| 1 | latency | 36s | 82 | Yes | 10s |
| 2 | errors | 33s | 82 | Yes | 10s |
| 3 | latency | 30s | 82 | Yes | 10s |

*Recovery is the executor's 10s health-check wait after approval.
In all 3 runs the service stayed degraded until Approve was clicked,
and the audit trail showed detector → investigator → guardian →
human → executor. Investigator source was `mock` in all 3 runs (see below).

## Evaluation

`backend/eval/run_eval.py` starts the app, runs a 30s healthy baseline
(false-positive check), then 6 fault runs (3 services × latency/errors):
break → wait for detection → check cause + evidence → approve → check
resolution. Results land in `backend/eval/results.json` and in the
dashboard's Evaluation card (`GET /eval`). Measured Nov 2, 2026, with
the default mock investigator:

| | |
|---|---|
| Runs | 6/6 detected |
| Cause correct | 6/6 |
| Evidence valid (3/3 real log lines) | 6/6 |
| Avg time to detect | 54.4s (median 58.1s) |
| Avg time to resolve | 10.0s |
| False positives (30s baseline) | 0 |

Detection after a fresh reset takes ~58s because the detector's 1-min
window (12 samples) must refill first; the first run measured 36s
because the server had already been running through the baseline.
Per-run rows (service, fault, detect, cause, evidence, confidence,
guardian, resolve, outcome) are in `results.json`.

## Testing

```bash
.venv/bin/python -m pytest backend/tests -q   # 43 tests + 1 skipped (real-DB test needs TEST_DATABASE_URL), backend only
```

## Persistence + RAG

- **Persistence (optional):** set `DATABASE_URL` to a Postgres
  connection string (Render dashboard > Environment, or Supabase)
  and incidents + the audit trail survive restarts. Tables are
  created automatically on startup; if the database is unreachable
  the app logs a warning and keeps running in memory. Unset =
  in-memory, exactly as before. Details in
  `docs/persistence-rag.md`.
- **RAG over runbooks:** before the investigator answers, it reads
  the matching runbook (`backend/runbooks/`: latency spike,
  error-rate spike, DB query timeouts, payments degradation) and up
  to 3 similar past incidents. Runbook text is advisory only — the
  evidence guard still requires every quoted evidence line to be a
  real log line, so a runbook sentence can never pass as evidence.

Tests cover the detector (error rule, latency rule needs two windows,
no refire after recovery, no second incident while one is open), the
guardian (confidence bands, never auto-fixes), the investigator
(evidence guard rejects invented lines, mock quotes real lines only),
and the API (`/health`, `/metrics`, `/logs`, `/eval`, `/demo/reset`),
plus the direct Gemini REST path (mocked HTTP: key header + model
URL, fallback-model retry, hallucinated-evidence rejection, mock
fallback with no key and no helper).
CI (`.github/workflows/ci.yml`) runs the backend tests and the
frontend build on every push/PR to main.

## Gemini — live status: VERIFIED WORKING (mock is still the default)

Live Gemini reaches the investigator two ways, in this order
(`GEMINI_LIVE=1`):
1. **Direct REST** — when `GEMINI_API_KEY` is set (env var, or the
   Render dashboard's Environment tab), the backend calls
   `POST .../v1beta/models/{model}:generateContent` with the key in
   the `x-goog-api-key` header. This is the only live path on Render.
   The key value never appears in code, files, or logs.
2. **Local helper** — without a key, on this machine, the stored
   `custom.gemini` credential is used via its helper
   (`~/workspace/custom-connectors/custom.gemini/bin/client.py`).
Set `GEMINI_LIVE=1` in `.env` (see `.env.example`) to try live calls
per incident. The investigator tries the models in order —
`gemini-3.5-flash-lite`, then `gemini-3.1-flash-lite` — one attempt
each (~15s timeout), then one retry of the first model via the helper,
and falls back to the mock on any failure. `GEMINI_MODEL` overrides
the first model.

Verified Nov 2, 2026 by direct generate calls and one full live
investigation end to end: `gemini-3.5-flash` replied "OK live" twice
and investigated a real incident with source `gemini`, confidence 95,
3/3 real evidence lines. `gemini-3.5-flash-lite` also replied
"OK live". `gemma-4-26b-a4b-it` answered too but is verbose, so it is
not in the try list.

Failing through the stored key: `gemini-3.8-flash` → HTTP 503
(3 times), `gemini-flash-latest` → HTTP 503, `gemini-2.5-flash` →
HTTP 404, `gemini-2.5-flash-lite` → HTTP 404, `gemini-2.0-flash` →
HTTP 404, `gemini-2.0-flash-lite` → HTTP 404. The API's model list
confirms all these names exist — the failures are availability, not
typos. Note: `gemini-3.8-flash` ANSWERED in Google AI Studio for the
same account, so the model works and the stored API key/project was
the suspect for the 503s. A fresh key was submitted and retested:
`gemini-3.5-flash-lite` WORKS with the new key ("OK live" in ~1-4s)
and `gemini-3.1-flash-lite` WORKS too ("OK live" in 11s, after one
transient hang past 45s on the first attempt) — so the default model
is now `gemini-3.5-flash-lite`, fallback `gemini-3.1-flash-lite`.
Still failing with the new key: `gemini-3.8-flash` hangs / times out
at 60s, `gemini-2.5-flash`, `gemini-2.5-flash-lite` and `gemini-2.5-pro`
all return HTTP 404.

On Render the helper binary does not exist, so the helper path fails
fast there and the direct REST path is used instead — live Gemini on
the hosted demo needs only `GEMINI_API_KEY` set in the Render
dashboard (see "Live on Render" above). With no key anywhere, the
demo falls back to the mock. Locally, `GEMINI_LIVE` stays `0` unless
you opt in.

## What was cut for the MVP

Kafka, Redis, Postgres, LangGraph, RAG / vector DB, real login and
RBAC (one hard-coded admin approver), auto-remediation, alerting.
Metrics, logs, incidents and audit are in-memory and
reset on restart. One failure type per demo run (latency or errors).

## Files

- `backend/simulator.py` — fake orders/inventory/payments services, break/recover
- `backend/detector.py` — error >20% in 1 min, or p95 >500ms for 2 mins
- `backend/investigator.py` — one Gemini call (or mock) + evidence check
- `backend/guardian.py` — confidence bands, approval rules
- `backend/executor.py` — simulated restart, 10s health check
- `backend/audit.py` + `backend/store.py` — audit trail, in-memory state, demo reset
- `backend/main.py` — FastAPI app, endpoints (`/health`, `/metrics`, `/logs`, `/incidents`, `/audit`, `/eval`, `/demo/*`), serves the built dashboard
- `backend/eval/run_eval.py` — evaluation harness (30s baseline + 6 fault runs → `backend/eval/results.json`)
- `backend/tests/` — pytest suite (detector, guardian, investigator, API); dev deps in `requirements-dev.txt`
- `.github/workflows/ci.yml` — CI: backend tests + frontend build on push/PR to main
- `frontend/src/App.tsx` — dashboard: service cards with latency sparklines, live log stream, active incident dossier, incident history, evaluation card, audit timeline
