import { useCallback, useEffect, useState } from "react";

type Service = {
  name: string;
  status: string;
  latency_ms: number;
  error_rate: number;
  broken: boolean;
  break_type: string | null;
};

type Incident = {
  id: string;
  service: string;
  metric: string;
  value: number;
  threshold: number;
  status: string;
  started_at: string;
  investigation: {
    cause: string;
    evidence: string[];
    confidence: number;
    proposed_fix: string;
    source: string;
  } | null;
  guardian: { decision: string; can_approve: boolean; reason: string } | null;
  approved_by: string | null;
  resolved_at: string | null;
  resolution: string | null;
};

type AuditEntry = {
  ts: string;
  incident_id: string;
  actor: string;
  action: string;
  detail: string;
};

const API = "";

async function post(path: string, body: unknown) {
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export default function App() {
  const [services, setServices] = useState<Service[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [h, i, a] = await Promise.all([
        fetch(`${API}/health`).then((r) => r.json()),
        fetch(`${API}/incidents`).then((r) => r.json()),
        fetch(`${API}/audit`).then((r) => r.json()),
      ]);
      setServices(h.services);
      setIncidents(i.incidents);
      setAudit(a.audit);
      setError("");
    } catch {
      setError("Backend not reachable — start it with: uvicorn backend.main:app --reload --port 8000");
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const active = incidents.find((i) =>
    ["detected", "investigating", "needs_info", "needs_approval", "approved", "executing"].includes(i.status)
  );

  const doBreak = async (type: string) => {
    setBusy(true);
    try {
      await post("/demo/break", { service: "inventory", type });
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!active) return;
    setBusy(true);
    try {
      await post(`/incidents/${active.id}/approve`, {
        approved_by: "aagam",
        action: "restart_service",
      });
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    if (!active) return;
    setBusy(true);
    try {
      await post(`/incidents/${active.id}/reject`, {
        reason: "Not convinced by the evidence — needs more logs.",
        rejected_by: "aagam",
      });
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wrap">
      <h1>Governed AIOps Agent</h1>
      <p className="sub">
        Watches orders + inventory, detects anomalies, explains them, and asks a
        human to approve any fix. Nothing auto-fixes in this MVP.
      </p>
      {error && <p style={{ color: "#f87171" }}>{error}</p>}

      <div className="card">
        <h2>Services</h2>
        <div className="row">
          {services.map((s) => (
            <div className="svc" key={s.name}>
              <strong>
                <span className={`dot ${s.status}`} />
                {s.name}
              </strong>
              <span className="badge">{s.status}</span>
              <p className="muted">
                latency {s.latency_ms}ms · error rate {Math.round(s.error_rate * 100)}%
                {s.break_type ? ` · fault: ${s.break_type}` : ""}
              </p>
            </div>
          ))}
        </div>
        <button className="danger" disabled={busy} onClick={() => doBreak("latency")}>
          Break inventory (latency)
        </button>
        <button className="danger" disabled={busy} onClick={() => doBreak("errors")}>
          Break inventory (errors)
        </button>
        <button
          className="ghost"
          disabled={busy}
          onClick={async () => {
            await post("/demo/recover", { service: "inventory" });
            await refresh();
          }}
        >
          Recover manually
        </button>
      </div>

      <div className="card">
        <h2>Active incident</h2>
        {!active && <p className="muted">No active incident. Break inventory to start one.</p>}
        {active && (
          <>
            <p>
              <strong>{active.id}</strong> — {active.service} · {active.metric} ={" "}
              {active.value} (threshold {active.threshold}) · status: {active.status}
            </p>
            {active.investigation ? (
              <>
                <p>
                  <strong>Cause:</strong> {active.investigation.cause}
                </p>
                <p>
                  <strong>Confidence:</strong> {active.investigation.confidence}%{" "}
                  <span className="muted">(source: {active.investigation.source})</span>
                </p>
                <p>
                  <strong>Proposed fix:</strong> {active.investigation.proposed_fix}
                </p>
                <strong>Evidence (real log lines only):</strong>
                <pre>{active.investigation.evidence.join("\n")}</pre>
              </>
            ) : (
              <p className="muted">Investigating…</p>
            )}
            {active.guardian && (
              <p className="muted">
                Guardian: {active.guardian.decision} — {active.guardian.reason}
              </p>
            )}
            <button
              disabled={busy || !active.guardian?.can_approve}
              onClick={approve}
            >
              {busy ? "Executing…" : "Approve fix"}
            </button>
            <button className="ghost" disabled={busy} onClick={reject}>
              Reject
            </button>
            {!active.guardian?.can_approve && active.guardian && (
              <p className="muted">Approve is disabled — Guardian has not cleared this incident.</p>
            )}
            {active.resolution && (
              <p>
                <strong>Resolution:</strong> {active.resolution}
              </p>
            )}
          </>
        )}
      </div>

      <div className="card">
        <h2>Audit trail</h2>
        {audit.length === 0 && <p className="muted">No audit events yet.</p>}
        <ul className="audit">
          {[...audit].reverse().slice(0, 30).map((e, idx) => (
            <li key={idx}>
              {new Date(e.ts).toLocaleTimeString()} · <strong>{e.actor}</strong> ·{" "}
              {e.action} · {e.incident_id}
              {e.detail ? ` — ${e.detail}` : ""}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
