import { useCallback, useEffect, useState } from "react";

type Service = {
  name: string;
  status: string;
  latency_ms: number;
  error_rate: number;
  broken: boolean;
  break_type: string | null;
};

type MetricSample = {
  ts: string;
  latency_ms: number;
  error_rate: number;
  status: string;
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
const OPEN_STATUSES = [
  "detected",
  "investigating",
  "needs_info",
  "needs_approval",
  "approved",
  "executing",
];
const LATENCY_THRESHOLD_MS = 500;

async function post(path: string, body: unknown) {
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function fmtTime(ts: string) {
  return new Date(ts).toLocaleTimeString();
}

function fmtDuration(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

function fmtMetric(inc: Incident) {
  if (inc.metric === "error_rate") {
    return `error rate ${Math.round(inc.value * 100)}% (threshold ${Math.round(
      inc.threshold * 100,
    )}%)`;
  }
  return `p95 latency ${inc.value}ms (threshold ${inc.threshold}ms)`;
}

/** Latency sparkline (last samples) with a dashed 500ms threshold line. */
function Sparkline({ samples }: { samples: MetricSample[] }) {
  const W = 240;
  const H = 56;
  if (samples.length < 2) {
    return <p className="muted small">Collecting samples…</p>;
  }
  const maxV = Math.max(600, ...samples.map((s) => s.latency_ms));
  const x = (i: number) =>
    samples.length === 1 ? 0 : (i / (samples.length - 1)) * W;
  const y = (v: number) => H - 4 - (v / maxV) * (H - 8);
  const pts = samples.map((s, i) => `${x(i).toFixed(1)},${y(s.latency_ms).toFixed(1)}`).join(" ");
  const thresholdY = y(LATENCY_THRESHOLD_MS);
  return (
    <svg
      className="spark"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Latency over the last samples"
    >
      <line
        x1="0"
        x2={W}
        y1={thresholdY}
        y2={thresholdY}
        className="threshold-line"
      />
      <text x={W - 2} y={thresholdY - 3} textAnchor="end" className="threshold-label">
        500ms
      </text>
      <polyline points={pts} className="spark-line" />
      <circle
        cx={x(samples.length - 1)}
        cy={y(samples[samples.length - 1].latency_ms)}
        r="3"
        className="spark-dot"
      />
    </svg>
  );
}

export default function App() {
  const [services, setServices] = useState<Service[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [metrics, setMetrics] = useState<Record<string, MetricSample[]>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [lastPoll, setLastPoll] = useState<Date | null>(null);

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
      setLastPoll(new Date());
      // Latency history per service, for the sparklines.
      const m = await Promise.all(
        h.services.map((s: Service) =>
          fetch(`${API}/metrics?service=${s.name}&limit=60`)
            .then((r) => r.json())
            .then((d) => [s.name, d.samples] as const)
            .catch(() => [s.name, []] as const),
        ),
      );
      setMetrics(Object.fromEntries(m));
    } catch {
      setError(
        "Backend not reachable — start it with: uvicorn backend.main:app --reload --port 8000",
      );
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  const openIncidents = incidents.filter((i) => OPEN_STATUSES.includes(i.status));
  const active = openIncidents[openIncidents.length - 1]; // newest open
  const past = [...incidents].reverse().filter((i) => !OPEN_STATUSES.includes(i.status));
  const resolved = incidents.filter((i) => i.status === "resolved");
  const healthyCount = services.filter((s) => s.status === "healthy").length;
  const avgResolve =
    resolved.length === 0
      ? null
      : resolved.reduce(
          (sum, i) =>
            sum +
            (new Date(i.resolved_at as string).getTime() -
              new Date(i.started_at).getTime()) /
              1000,
          0,
        ) / resolved.length;
  // Investigator source: from the newest incident that has one, else the
  // honest default — the mock is the default investigator.
  const invSource =
    [...incidents].reverse().find((i) => i.investigation)?.investigation?.source ??
    "mock";

  const doBreak = async (service: string, type: string) => {
    setBusy(true);
    try {
      await post("/demo/break", { service, type });
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const doRecover = async (service: string) => {
    setBusy(true);
    try {
      await post("/demo/recover", { service });
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const doReset = async () => {
    if (!window.confirm("Reset the demo? This clears incidents, metrics and logs. The audit trail is kept.")) return;
    setBusy(true);
    try {
      await post("/demo/reset", {});
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
      <header className="header">
        <div>
          <h1>Governed AIOps Agent</h1>
          <p className="sub">
            Watches orders, inventory and payments, detects anomalies, explains
            them, and asks a human to approve any fix. Nothing auto-fixes.
          </p>
        </div>
        <div className="header-right">
          <span className="pill live">
            <span className="pulse" /> LIVE
            {lastPoll ? ` · ${lastPoll.toLocaleTimeString()}` : ""}
          </span>
          <span className="pill">investigator: {invSource}</span>
          <button className="ghost" disabled={busy} onClick={doReset}>
            Reset demo
          </button>
        </div>
      </header>

      {error && <p className="error">{error}</p>}

      <div className="stats">
        <div className="stat">
          <span className="stat-value">
            {healthyCount}/{services.length || 3}
          </span>
          <span className="stat-label">services healthy</span>
        </div>
        <div className="stat">
          <span className="stat-value">{openIncidents.length}</span>
          <span className="stat-label">open incidents</span>
        </div>
        <div className="stat">
          <span className="stat-value">{resolved.length}</span>
          <span className="stat-label">resolved</span>
        </div>
        <div className="stat">
          <span className="stat-value">
            {avgResolve === null ? "—" : fmtDuration(avgResolve)}
          </span>
          <span className="stat-label">avg time to resolve</span>
        </div>
      </div>

      <section className="card">
        <h2>Services</h2>
        <div className="svc-grid">
          {services.map((s) => (
            <div className={`svc ${s.status}`} key={s.name}>
              <div className="svc-head">
                <strong>
                  <span className={`dot ${s.status}`} />
                  {s.name}
                </strong>
                <span className={`badge ${s.status}`}>{s.status}</span>
              </div>
              <p className="svc-metrics">
                <span>
                  <strong>{s.latency_ms}ms</strong> latency
                </span>
                <span>
                  <strong>{Math.round(s.error_rate * 100)}%</strong> errors
                </span>
                {s.break_type && <span className="fault">fault: {s.break_type}</span>}
              </p>
              <Sparkline samples={metrics[s.name] ?? []} />
              <div className="svc-actions">
                <button
                  className="danger"
                  disabled={busy || s.broken}
                  onClick={() => doBreak(s.name, "latency")}
                >
                  Break · latency
                </button>
                <button
                  className="danger"
                  disabled={busy || s.broken}
                  onClick={() => doBreak(s.name, "errors")}
                >
                  Break · errors
                </button>
                <button
                  className="ghost"
                  disabled={busy || !s.broken}
                  onClick={() => doRecover(s.name)}
                >
                  Recover
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <h2>
          Active incident
          {openIncidents.length > 1 && (
            <span className="muted small">
              {" "}
              · {openIncidents.length} open, showing newest
            </span>
          )}
        </h2>
        {!active && (
          <p className="muted">
            No active incident. Break a service above to start one — detection
            takes about 30 seconds.
          </p>
        )}
        {active && (
          <>
            <p className="incident-line">
              <strong>{active.id}</strong> — {active.service} ·{" "}
              {fmtMetric(active)} · status:{" "}
              <span className={`badge ${active.status}`}>{active.status}</span>
            </p>
            {active.investigation ? (
              <>
                <div className="confidence">
                  <div className="conf-head">
                    <span>
                      Confidence <strong>{active.investigation.confidence}%</strong>
                    </span>
                    <span className="muted small">
                      source: {active.investigation.source}
                    </span>
                  </div>
                  <div className="meter">
                    <div
                      className="meter-fill"
                      style={{ width: `${active.investigation.confidence}%` }}
                    />
                    <span className="meter-mark" style={{ left: "70%" }} title="70 — below this, no approval" />
                    <span className="meter-mark" style={{ left: "90%" }} title="90 — still needs a human" />
                  </div>
                  <div className="meter-labels">
                    <span>0</span>
                    <span>70 · no approve</span>
                    <span>90 · human still approves</span>
                    <span>100</span>
                  </div>
                </div>
                <p>
                  <strong>Cause:</strong> {active.investigation.cause}
                </p>
                <p>
                  <strong>Proposed fix:</strong> {active.investigation.proposed_fix}
                </p>
                <strong>Evidence (real log lines only):</strong>
                <pre>{active.investigation.evidence.join("\n")}</pre>
              </>
            ) : (
              <p className="muted">
                <span className="pulse" /> Investigating — reading logs and
                metrics…
              </p>
            )}
            {active.guardian && (
              <p className="guardian">
                <strong>Guardian:</strong> {active.guardian.decision} —{" "}
                {active.guardian.reason}
              </p>
            )}
            <div className="incident-actions">
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
                <span className="muted small">
                  Approve is disabled — Guardian has not cleared this incident.
                </span>
              )}
            </div>
            {active.resolution && (
              <p>
                <strong>Resolution:</strong> {active.resolution}
              </p>
            )}
          </>
        )}
      </section>

      <section className="card">
        <h2>Incident history</h2>
        {past.length === 0 && (
          <p className="muted">No finished incidents yet — resolved and rejected ones show up here.</p>
        )}
        {past.length > 0 && (
          <table className="history">
            <thead>
              <tr>
                <th>ID</th>
                <th>Service</th>
                <th>Metric</th>
                <th>Status</th>
                <th>Conf.</th>
                <th>Started</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {past.map((i) => (
                <tr key={i.id}>
                  <td>{i.id}</td>
                  <td>{i.service}</td>
                  <td>{fmtMetric(i)}</td>
                  <td>
                    <span className={`badge ${i.status}`}>{i.status}</span>
                  </td>
                  <td>{i.investigation ? `${i.investigation.confidence}%` : "—"}</td>
                  <td>{fmtTime(i.started_at)}</td>
                  <td>{i.resolution ?? (i.resolved_at ? fmtTime(i.resolved_at) : "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card">
        <h2>Audit trail</h2>
        {audit.length === 0 && <p className="muted">No audit events yet.</p>}
        <ol className="timeline">
          {[...audit].reverse().slice(0, 30).map((e, idx) => (
            <li key={idx}>
              <span className="tl-dot" />
              <span className="tl-time">{fmtTime(e.ts)}</span>
              <span className="tl-body">
                <strong>{e.actor}</strong> · {e.action} · {e.incident_id}
                {e.detail ? <span className="muted"> — {e.detail}</span> : ""}
              </span>
            </li>
          ))}
        </ol>
      </section>

      <footer className="foot muted small">
        Detection is hard thresholds only (error &gt;20% / 1 min, or p95
        latency &gt;500ms / 2 mins). The investigator may only quote real log
        lines. Nothing auto-fixes — a human always approves.
      </footer>
    </div>
  );
}
