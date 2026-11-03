# Persistence (Postgres) + RAG over runbooks

Two upgrades, both optional, both fail-safe: with no configuration the
app behaves exactly as before (in-memory, mock investigator). Nothing
here changes the demo flow — break → detect → investigate → approve →
recover → audit — it only makes the story survive a restart and makes
the investigator smarter.

## 1. Persistence — how it works

- **Mode selection:** `DATABASE_URL` env var. Unset or empty → the
  original in-memory store, byte-for-byte same behaviour. Set → the
  same store API, backed by Postgres (`backend/persistence.py`).
- **What is persisted:** incidents (status, cause, confidence,
  evidence, timeline, investigation, guardian, detection snapshot)
  and audit log entries. The high-frequency metric ring buffer and the
  log buffers stay in memory on purpose — persisting a sample every
  5s buys nothing for the demo.
- **How it stays in sync:** the incident list is a write-through list
  (`store._IncidentList`): appending an incident upserts its row.
  `add_audit` / `add_timeline` write through the same way, and
  `main.py` calls `store.save_incident(incident)` after the
  investigate / approve / reject mutation batches — those three
  points cover every place the codebase changes an incident
  (`executor.py` mutations are all followed by `add_timeline`, so
  they persist too). No caller changes its logic; it only gains a
  save call.
- **Startup:** `main.py` calls `store.init_persistence()` before the
  first simulator tick. It creates the tables (`CREATE TABLE IF NOT
  EXISTS incidents / audit`), loads saved incidents + audit into the
  in-memory lists, and resumes the incident counter so ids never
  collide (`INC-004` after `INC-003`, etc.).
- **Restart + open incidents:** services start healthy, so an incident
  left open at restart is marked `superseded` on load (timeline +
  audit entry say so) instead of blocking the detector for that
  service with nothing actually broken. Resolved / rejected incidents
  load back exactly as they were.
- **Demo reset:** `/demo/reset` clears incidents (rows deleted) and
  metrics/logs, keeps the audit trail, and appends a `demo_reset`
  entry — in both backends.
- **Fail-safe:** if the database is unreachable at startup or dies
  mid-run, the app logs a warning to stderr and keeps running in
  memory. Persistence can never crash the demo. If the driver is not
  installed, persistence stays off with the same warning.
- **Driver:** one dependency, `psycopg2-binary` in `requirements.txt`.
  `psycopg` (v3) is accepted as a fallback if it is installed instead.

### Wiring it on Render (hosted demo)

Do NOT put the connection string in `render.yaml` or in the repo.
(`render.yaml` is owned by the other in-flight change; adding the
variable there is a one-line follow-up once that lands — see below.)

1. **Render free Postgres:** Render dashboard → New → PostgreSQL →
   create it (free tier). Open the database → copy the **External
   Database URL**.
2. In the `aiops-agent` web service → Environment → add
   `DATABASE_URL` = the pasted URL → Save. Render redeploys; on
   startup the tables are created automatically.
3. **Honest note:** Render's free Postgres expires after ~30 days.
   For a permanent demo database, Supabase is the better choice:
   create a free project (500 MB, does not expire) → Project Settings
   → Database → copy the connection string (the **Session pooler** URI
   if one is offered, otherwise the direct one) → paste it as the
   same `DATABASE_URL` variable. Either way it is just a Postgres
   connection string; the app does not care which host it points at.
4. Optional one-line follow-up in `render.yaml` (after the other
   agent's edit lands), so the variable is declared in the Blueprint:

   ```yaml
   envVars:
     - key: DATABASE_URL
       sync: false   # set the value in the Render dashboard, never here
   ```

   With `sync: false` Render prompts for the value instead of reading
   one from the file.

### Wiring it locally

```bash
# any local Postgres, e.g. a throwaway one on port 5433:
DATABASE_URL=postgresql://user:pass@localhost:5433/aiops \
  .venv/bin/uvicorn backend.main:app --reload --port 8000
```

Or put `DATABASE_URL=...` in a local `.env` (never committed —
`.env` is gitignored). Unset it to go back to in-memory.

## 2. RAG over runbooks — how it works

- **Runbooks:** four Markdown files in `backend/runbooks/`, written
  from the actual demo (no invented services or failures):
  `latency-spike.md`, `error-rate-spike.md`, `db-query-timeouts.md`,
  `payments-degradation.md`. Each has Symptoms, Likely causes, Checks
  to run, Fix steps, and When NOT to auto-fix. The payments runbook
  is the strictest: never auto-retry a charge, never auto-fix if the
  logs mention a charge.
- **Retrieval:** `backend/rag.py`, stdlib only, no embeddings. It
  tokenizes the incident (service name, fault type from the detector
  metric, words from the investigation cause + quoted evidence lines)
  and scores each runbook: +6 for a service match, +6 for a fault-type
  match, +3 per query token in the title, +1 per token in the body.
  The top 1–2 runbooks with a score above 0 are returned.
- **Past cases:** the same function also pulls the 3 most recent
  finished incidents with the same service + fault type (from the
  store — so it includes incidents loaded back from Postgres) and
  includes their recorded cause + resolution.
- **Entry point:** `rag.get_context(incident) -> str`. Returns `""`
  when nothing matches, so callers can skip it silently.

### Integrating it into the investigator (NOT yet applied)

`investigator.py` is owned by the other in-flight change, so this is
a snippet to apply after that lands — one import, three lines, in
`investigate()`, right after the `prompt = (...)` string is built and
before the `if os.environ.get("GEMINI_LIVE", ...)` branch:

```python
from . import rag

# ... prompt = (...)  (unchanged: metric + recent metrics + log lines)

# RAG: runbook context is advisory only. It is appended to the prompt
# AFTER the real logs, and the evidence guard below still requires
# every quoted evidence line to appear verbatim in the log lines we
# sent — a runbook sentence can never pass as evidence, because
# runbook text is not in the logs.
context = rag.get_context(incident)
if context:
    prompt = prompt + "\n\n" + context
```

Rules that integration must keep:

1. **Evidence still comes only from real logs.** The existing
   `_evidence_is_real` guard enforces this automatically: any
   evidence line copied from a runbook fails the verbatim check, the
   result is rejected, and the investigator retries / falls back to
   the mock. Do not weaken that guard for RAG.
2. **Runbook context is advisory.** The prompt labels it as such
   (`get_context` already includes the "advisory only" heading).
   The model may use it to choose checks and a fix; it may not use
   it as proof of what happened.
3. **No behaviour change when nothing matches.** `get_context`
   returns `""` and the prompt is untouched, so incidents with no
   runbook keep today's exact prompt.
4. **No new dependency, no new network call.** Retrieval is local
   keyword matching over four files + the incident store.

## 3. Tests

- `backend/tests/test_persistence.py` — in-memory default, fallback
  when the DB is unreachable, incident + audit round-trip across a
  simulated restart (fake driver), reset semantics, and a real-DB
  test that runs only when `TEST_DATABASE_URL` is set (skipped in CI).
- `backend/tests/test_rag.py` — ranking (inventory latency → latency
  runbook first, payments errors → payments runbook first), `""`
  when nothing matches, past cases included / omitted correctly.
- Run: `python -m pytest backend/tests -q` from the repo root.
