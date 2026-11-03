# Governed AIOps Agent — One-Day MVP

An agent watches two demo services (orders + inventory), detects an
anomaly with hard thresholds, explains it, and asks a human to approve
any fix. Nothing auto-fixes. Inspired by Quinnox's Qinfinite / QyrusAI:
governance-first autonomy, not a chatbot.

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

Demo: open the dashboard, click "Break inventory (latency)", wait for
the incident, read the cause + evidence, click Approve, watch it recover.

## Governance policy (5 lines)

1. Detection is hard thresholds only — no LLM decides an incident exists.
2. The Investigator may only quote real log lines; invented evidence is rejected and retried once.
3. Confidence below 70 means no Approve button at all.
4. Confidence 70 and above still needs a human to approve — the MVP never auto-fixes.
5. Every step (detect, investigate, guardian, approve/reject, fix) is written to the audit trail.

## Demo results (measured Nov 2, 2026, in this VM, mock investigator)

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
to try one live call per incident (model `gemini-2.5-flash`).

As of Nov 2, 2026 live calls fail with HTTP 404 from
`generativelanguage.googleapis.com` for `gemini-2.5-flash` and
`gemini-2.0-flash`, with no pending approval. With `GEMINI_LIVE=1` the
investigator retries once and falls back to the mock in ~1.4s
(verified). Do not claim live Gemini works until a run returns text
with `source: gemini`.

## What was cut for the MVP

Kafka, Redis, Postgres, LangGraph, RAG / vector DB, real login and
RBAC (one hard-coded admin approver), auto-remediation, alerting,
deployment. Metrics, logs, incidents and audit are in-memory and
reset on restart. One failure type per demo run (latency or errors).

## Files

- `backend/simulator.py` — fake orders/inventory services, break/recover
- `backend/detector.py` — error >20% in 1 min, or p95 >500ms for 2 mins
- `backend/investigator.py` — one Gemini call (or mock) + evidence check
- `backend/guardian.py` — confidence bands, approval rules
- `backend/executor.py` — simulated restart, 10s health check
- `backend/audit.py` + `backend/store.py` — audit trail, in-memory state
- `backend/main.py` — FastAPI app and endpoints
- `frontend/src/App.tsx` — one-page dashboard (status, incident, audit)
