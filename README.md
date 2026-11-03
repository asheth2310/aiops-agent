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

| Run | Fault | Time to detect | Confidence | Cause correct? | Time to recover* |
|-----|-------|----------------|------------|----------------|------------------|
| 1 | latency | 36s | 82 | Yes | 10s |
| 2 | errors | 33s | 82 | Yes | 10s |
| 3 | latency | 30s | 82 | Yes | 10s |

*Recovery is the executor's 10s health-check wait after approval.
In all 3 runs the service stayed degraded until Approve was clicked,
and the audit trail showed detector → investigator → guardian →
human → executor. Investigator source was `mock` in all 3 runs (see below).

## Gemini — live status: UNVERIFIED, mock is the default

Live Gemini goes through the stored `custom.gemini` credential via its
helper (`~/workspace/custom-connectors/custom.gemini/bin/client.py`),
never a raw key in code, env, or files. Set `GEMINI_LIVE=1` in `.env`
to try one live call per incident (model `gemini-3.8-flash`).

As of Nov 2, 2026 live calls fail against
`generativelanguage.googleapis.com`: `gemini-2.5-flash` returns HTTP 404
("no longer available to new users") and `gemini-3.8-flash` returns
HTTP 503, with no pending approval. With `GEMINI_LIVE=1` the
investigator retries once and falls back to the mock in ~1.4s
(verified). Do not claim live Gemini works until a run returns text
with `source: gemini`.

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
- `backend/main.py` — FastAPI app, endpoints (`/health`, `/metrics`, `/incidents`, `/audit`, `/demo/*`), serves the built dashboard
- `frontend/src/App.tsx` — dashboard: service cards with latency sparklines, active incident, history, audit timeline
