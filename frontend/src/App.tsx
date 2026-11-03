import { useCallback, useEffect, useRef, useState } from "react";

type Service = {
  name: string;
  status: string;
  latency_ms: number;
  error_rate: number;
  broken: boolean;
  break_type: string | null;
  total_requests: number;
  requests_per_min: number;
  uptime_pct?: number;
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
    confidence_breakdown?: {
      evidence_match: string;
      evidence_match_n: number;
      evidence_total: number;
      metric_severity_x: number;
      log_signal_n: number;
      log_lines_reviewed: number;
    };
    input?: {
      metric: string;
      value: number;
      threshold: number;
      log_lines_reviewed: number;
      metrics_samples_reviewed: number;
      window: string;
    };
  } | null;
  guardian: { decision: string; can_approve: boolean; reason: string } | null;
  approved_by: string | null;
  resolved_at: string | null;
  resolution: string | null;
  timeline?: { stage: string; ts: string; detail: string }[];
  detection?: {
    metric: string;
    value: number;
    threshold: number;
    window_samples: number;
    window_label: string;
    breach_seconds: number;
  };
};

type AuditEntry = {
  ts: string;
  incident_id: string;
  actor: string;
  action: string;
  detail: string;
};

type LogEntry = {
  ts: string;
  service: string;
  level: string;
  message: string;
};

type EvalRun = {
  service: string;
  break_type: string;
  detected: boolean;
  detect_seconds: number | null;
  metric: string | null;
  cause_correct: boolean;
  confidence: number | null;
  source: string | null;
  evidence_valid: boolean;
  guardian: string | null;
  resolve_seconds: number | null;
  final_status: string | null;
};

type EvalData = {
  generated_at?: string;
  baseline_seconds?: number;
  runs: EvalRun[];
  summary: {
    runs: number;
    detected: number;
    cause_correct: number;
    evidence_valid: number;
    avg_detect_seconds: number | null;
    median_detect_seconds: number | null;
    avg_resolve_seconds: number | null;
    false_positives: number;
    investigator_source: string | null;
  } | null;
  note?: string;
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

/** Percentile of a sorted numeric list (linear interpolation-free, nearest-rank). */
function percentile(sorted: number[], p: number) {
  if (sorted.length === 0) return null;
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[idx];
}

/** Expanded detail view for one service — all numbers from its real /metrics samples and /health. */
function ServiceDetail({
  service,
  samples,
  onClose,
}: {
  service: Service | undefined;
  samples: MetricSample[];
  onClose: () => void;
}) {
  if (!service) return null;
  const latencies = samples.map((s) => s.latency_ms).sort((a, b) => a - b);
  const p50 = percentile(latencies, 50);
  const p95 = percentile(latencies, 95);
  const p99 = percentile(latencies, 99);
  // Error budget: share of recent samples inside both SLOs
  // (latency <= 500ms AND error rate <= 20%).
  const inSlo = samples.filter((s) => s.latency_ms <= 500 && s.error_rate <= 0.2).length;
  const budgetPct = samples.length === 0 ? null : (inSlo / samples.length) * 100;
  const last10 = samples.slice(-10).reverse();

  return (
    <section className="card detail">
      <div className="detail-head">
        <h2>
          {service.name} — detail
          <span className="muted small">
            {" "}
            · from {samples.length} recent samples · uptime since demo start
          </span>
        </h2>
        <button className="ghost" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="detail-grid">
        <div className="stat">
          <span className="stat-value">{p50 === null ? "—" : `${p50}ms`}</span>
          <span className="stat-label">p50 latency</span>
        </div>
        <div className="stat">
          <span className="stat-value">{p95 === null ? "—" : `${p95}ms`}</span>
          <span className="stat-label">p95 latency (SLO 500ms)</span>
        </div>
        <div className="stat">
          <span className="stat-value">{p99 === null ? "—" : `${p99}ms`}</span>
          <span className="stat-label">p99 latency</span>
        </div>
        <div className="stat">
          <span className="stat-value">
            {budgetPct === null ? "—" : `${budgetPct.toFixed(0)}%`}
          </span>
          <span className="stat-label">
            error budget remaining ({inSlo}/{samples.length} samples inside SLO)
          </span>
        </div>
        <div className="stat">
          <span className="stat-value">
            {service.uptime_pct === undefined ? "—" : `${service.uptime_pct.toFixed(1)}%`}
          </span>
          <span className="stat-label">uptime since demo started</span>
        </div>
        <div className="stat">
          <span className="stat-value">{service.total_requests.toLocaleString("en-US")}</span>
          <span className="stat-label">total requests · {(service.requests_per_min ?? 0).toLocaleString("en-US")}/min</span>
        </div>
      </div>
      <h3 className="detail-sub">Last 10 samples</h3>
      {last10.length === 0 && <p className="muted">No samples yet.</p>}
      {last10.length > 0 && (
        <table className="history">
          <thead>
            <tr>
              <th>Time</th>
              <th>Latency</th>
              <th>Error rate</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {last10.map((s) => (
              <tr key={s.ts}>
                <td>{fmtTime(s.ts)}</td>
                <td>{s.latency_ms}ms</td>
                <td>{Math.round(s.error_rate * 100)}%</td>
                <td>
                  <span className={`badge ${s.status}`}>{s.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function App() {
  const [services, setServices] = useState<Service[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [metrics, setMetrics] = useState<Record<string, MetricSample[]>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [lastPoll, setLastPoll] = useState<Date | null>(null);
  const [chaosMode, setChaosMode] = useState(false);
  const [guidedHint, setGuidedHint] = useState(false);
  const [selectedService, setSelectedService] = useState<string | null>(null);
  const [evalData, setEvalData] = useState<EvalData | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  // Refs so the chaos interval always sees the latest state without
  // re-creating the timer on every poll.
  const servicesRef = useRef<Service[]>([]);
  const activeRef = useRef<Incident | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const [h, i, a, l] = await Promise.all([
        fetch(`${API}/health`).then((r) => r.json()),
        fetch(`${API}/incidents`).then((r) => r.json()),
        fetch(`${API}/audit`).then((r) => r.json()),
        fetch(`${API}/logs?limit=30`).then((r) => r.json()),
      ]);
      setServices(h.services);
      setIncidents(i.incidents);
      setAudit(a.audit);
      setLogs(l.logs);
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
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [refresh]);

  // Evaluation results are a recorded artifact, not live state — fetch
  // once on load, not on every poll.
  useEffect(() => {
    fetch(`${API}/eval`)
      .then((r) => r.json())
      .then((d) => setEvalData(d))
      .catch(() => setEvalData(null));
  }, []);

  // Keep the refs fresh for the chaos timer.
  useEffect(() => {
    servicesRef.current = services;
  }, [services]);

  const openIncidents = incidents.filter((i) => OPEN_STATUSES.includes(i.status));
  const active = openIncidents[openIncidents.length - 1]; // newest open

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  // Guided demo hint clears itself once the incident shows up.
  useEffect(() => {
    if (active) setGuidedHint(false);
  }, [active]);

  // Log stream auto-scrolls to the newest line.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs]);

  // Chaos mode: every 75s inject one random fault — but only when the
  // system is calm (no active incident, nothing broken). Toggling off
  // stops the timer. It never approves anything.
  useEffect(() => {
    if (!chaosMode) return;
    const id = setInterval(() => {
      if (activeRef.current) return;
      const svcs = servicesRef.current;
      if (svcs.length === 0 || svcs.some((s) => s.broken)) return;
      const svc = svcs[Math.floor(Math.random() * svcs.length)];
      const type = Math.random() < 0.5 ? "latency" : "errors";
      void doBreak(svc.name, type);
    }, 75000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chaosMode]);

  const past = [...incidents].reverse().filter((i) => !OPEN_STATUSES.includes(i.status));
  const resolved = incidents.filter((i) => i.status === "resolved");
  const healthyCount = services.filter((s) => s.status === "healthy").length;
  const totalRequests = services.reduce((sum, s) => sum + (s.total_requests ?? 0), 0);
  const totalReqMin = services.reduce((sum, s) => sum + (s.requests_per_min ?? 0), 0);
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
    setGuidedHint(false);
    try {
      await post("/demo/reset", {});
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  /** Guided demo: break inventory (latency) and tell the viewer what to
   * watch for. It never approves anything — the human still approves. */
  const runGuidedDemo = async () => {
    setGuidedHint(true);
    await doBreak("inventory", "latency");
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
          <button disabled={busy} onClick={runGuidedDemo}>
            ▶ Run guided demo
          </button>
          <button
            className={chaosMode ? "chaos-on" : "ghost"}
            onClick={() => setChaosMode((v) => !v)}
            aria-pressed={chaosMode}
            title="When on, injects one random fault every ~75s, only while everything is calm. Never auto-approves."
          >
            Chaos mode: {chaosMode ? "On" : "Off"}
          </button>
          <button className="ghost" disabled={busy} onClick={doReset}>
            Reset demo
          </button>
        </div>
      </header>
      {guidedHint && (
        <p className="guided-hint">
          ▶ Watch: the incident appears in ~30s below, then read the cause and
          approve the fix yourself. Nothing is auto-approved.
        </p>
      )}
      {chaosMode && (
        <p className="chaos-hint">
          Chaos mode is on — a random fault will be injected every ~75s whenever
          there is no active incident and nothing is broken.
        </p>
      )}

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
        <div className="stat">
          <span className="stat-value">{totalRequests.toLocaleString("en-US")}</span>
          <span className="stat-label">total requests</span>
        </div>
        <div className="stat">
          <span className="stat-value">{totalReqMin.toLocaleString("en-US")}</span>
          <span className="stat-label">requests / min</span>
        </div>
      </div>

      <section className="card">
        <h2>Services</h2>
        <p className="muted small chain">
          Simulated dependency chain: <strong>orders → inventory → payments</strong>
          {" "}· click a service for its detail view
        </p>
        <div className="svc-grid">
          {services.map((s) => (
            <div
              className={`svc ${s.status}${selectedService === s.name ? " selected" : ""}`}
              key={s.name}
              onClick={() => setSelectedService(selectedService === s.name ? null : s.name)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setSelectedService(selectedService === s.name ? null : s.name);
                }
              }}
            >
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
                <span>
                  <strong>{(s.requests_per_min ?? 0).toLocaleString("en-US")}</strong> req/min
                </span>
                {s.break_type && <span className="fault">fault: {s.break_type}</span>}
              </p>
              <Sparkline samples={metrics[s.name] ?? []} />
              <div className="svc-actions" onClick={(e) => e.stopPropagation()}>
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

      {selectedService && (
        <ServiceDetail
          service={services.find((s) => s.name === selectedService)}
          samples={metrics[selectedService] ?? []}
          onClose={() => setSelectedService(null)}
        />
      )}

      <section className="card">
        <h2>
          Live log stream
          <span className="muted small"> · newest at the bottom · updates every poll</span>
        </h2>
        {logs.length === 0 && (
          <p className="muted">Waiting for log lines from the backend…</p>
        )}
        <div className="log-stream" ref={logRef}>
          {[...logs].reverse().map((l, idx) => (
            <div className={`log-line log-${l.level.toLowerCase()}`} key={`${l.ts}-${idx}`}>
              <span className="log-time">{fmtTime(l.ts)}</span>
              <span className="log-service">{l.service}</span>
              <span className={`log-level log-level-${l.level.toLowerCase()}`}>{l.level}</span>
              <span className="log-msg">{l.message}</span>
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
                {/* Detection snapshot — what the detector saw, from real data. */}
                {active.detection && (
                  <div className="dossier-box">
                    <h3>Detection snapshot</h3>
                    <div className="kv">
                      <span>
                        Metric: <strong>{active.detection.metric}</strong>
                      </span>
                      <span>
                        Value: <strong>{active.detection.value}</strong>
                      </span>
                      <span>
                        Threshold / SLO: <strong>{active.detection.threshold}</strong>
                      </span>
                      <span>
                        Breach before detection:{" "}
                        <strong>{active.detection.breach_seconds}s</strong>
                      </span>
                      <span>
                        Detector window: <strong>{active.detection.window_label}</strong>
                      </span>
                      <span>
                        Samples in window: <strong>{active.detection.window_samples}</strong>
                      </span>
                    </div>
                  </div>
                )}
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
                  {active.investigation.confidence_breakdown && (
                    <div className="breakdown">
                      <h4>Why this score — computed from the data reviewed</h4>
                      <div className="break-row">
                        <span>
                          Evidence match:{" "}
                          <strong>
                            {active.investigation.confidence_breakdown.evidence_match} real log
                            lines
                          </strong>
                        </span>
                        <span className="break-bar">
                          <span
                            style={{
                              width: `${
                                (active.investigation.confidence_breakdown.evidence_match_n /
                                  Math.max(1, active.investigation.confidence_breakdown.evidence_total)) *
                                100
                              }%`,
                            }}
                          />
                        </span>
                      </div>
                      <div className="break-row">
                        <span>
                          Metric severity:{" "}
                          <strong>
                            {active.investigation.confidence_breakdown.metric_severity_x}× the
                            threshold
                          </strong>
                        </span>
                        <span className="break-bar">
                          <span
                            style={{
                              width: `${Math.min(
                                100,
                                (active.investigation.confidence_breakdown.metric_severity_x / 3) * 100,
                              )}%`,
                            }}
                          />
                        </span>
                      </div>
                      <div className="break-row">
                        <span>
                          Log signal:{" "}
                          <strong>
                            {active.investigation.confidence_breakdown.log_signal_n} ERROR/WARN
                            lines in{" "}
                            {active.investigation.confidence_breakdown.log_lines_reviewed}{" "}
                            reviewed
                          </strong>
                        </span>
                        <span className="break-bar">
                          <span
                            style={{
                              width: `${Math.min(
                                100,
                                (active.investigation.confidence_breakdown.log_signal_n /
                                  Math.max(1, active.investigation.confidence_breakdown.log_lines_reviewed)) *
                                  100,
                              )}%`,
                            }}
                          />
                        </span>
                      </div>
                    </div>
                  )}
                </div>
                <p>
                  <strong>Cause:</strong> {active.investigation.cause}
                </p>
                <p>
                  <strong>Proposed fix:</strong> {active.investigation.proposed_fix}
                </p>
                <strong>Evidence (real log lines only, with timestamps):</strong>
                <div className="evidence">
                  {active.investigation.evidence.map((line, idx) => {
                    const [ts, ...rest] = line.split(" ");
                    return (
                      <div className="evidence-line" key={idx}>
                        <span className="log-time">{fmtTime(ts)}</span>
                        <span className="log-msg">{rest.join(" ")}</span>
                      </div>
                    );
                  })}
                </div>
                {active.investigation.input && (
                  <p className="muted small">
                    Investigator was given: {active.investigation.input.metric} ={" "}
                    {active.investigation.input.value} (threshold{" "}
                    {active.investigation.input.threshold}) ·{" "}
                    {active.investigation.input.log_lines_reviewed} log lines ·{" "}
                    {active.investigation.input.metrics_samples_reviewed} metric samples ·{" "}
                    {active.investigation.input.window} · source:{" "}
                    {active.investigation.source}
                  </p>
                )}
                <div className="dossier-box">
                  <h3>Remediation plan</h3>
                  <ol className="plan">
                    <li>Restart the {active.service} service (the approved fix).</li>
                    <li>Run the 10s health check — service must be healthy and latency under 200ms.</li>
                    <li>Check {active.service} error logs for the original cause before reopening traffic.</li>
                    <li>Reopen traffic. If still degraded, roll back the last deploy.</li>
                  </ol>
                  <p className="muted small">
                    Risk: if approved, the restart either resolves the incident or is
                    recorded as failed — nothing is hidden. If rejected, {active.service}{" "}
                    stays degraded until someone recovers it manually, and the incident
                    stays open.
                  </p>
                </div>
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
            {(active.timeline?.length ?? 0) > 0 && (
              <div className="dossier-box">
                <h3>Incident timeline</h3>
                <ol className="timeline">
                  {active.timeline!.map((t, idx) => {
                    const next = active.timeline![idx + 1];
                    const dur = next
                      ? (new Date(next.ts).getTime() - new Date(t.ts).getTime()) / 1000
                      : (Date.now() - new Date(t.ts).getTime()) / 1000;
                    return (
                      <li key={idx}>
                        <span className="tl-dot" />
                        <span className="tl-time">{fmtTime(t.ts)}</span>
                        <span className="tl-body">
                          <strong>{t.stage}</strong>
                          {t.detail ? <span className="muted"> — {t.detail}</span> : ""}
                          <span className="muted small">
                            {" "}
                            · {next ? fmtDuration(dur) : `${fmtDuration(dur)} so far`}
                          </span>
                        </span>
                      </li>
                    );
                  })}
                </ol>
              </div>
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
        <h2>Evaluation</h2>
        {!evalData?.summary && (
          <p className="muted">
            No eval run yet — run <code>backend/eval/run_eval.py</code>. It
            starts the app, checks a 30s healthy baseline for false
            positives, then injects 6 faults (3 services × latency/errors)
            and measures detection, cause accuracy, evidence validity and
            resolution.
          </p>
        )}
        {evalData?.summary && (
          <>
            <div className="stats">
              <div className="stat">
                <span className="stat-num">
                  {evalData.summary.detected}/{evalData.summary.runs}
                </span>
                <span className="stat-label">faults detected</span>
              </div>
              <div className="stat">
                <span className="stat-num">
                  {evalData.summary.cause_correct}/{evalData.summary.runs}
                </span>
                <span className="stat-label">cause correct</span>
              </div>
              <div className="stat">
                <span className="stat-num">
                  {evalData.summary.evidence_valid}/{evalData.summary.runs}
                </span>
                <span className="stat-label">evidence valid</span>
              </div>
              <div className="stat">
                <span className="stat-num">
                  {evalData.summary.avg_detect_seconds ?? "—"}s
                </span>
                <span className="stat-label">
                  avg detect (median {evalData.summary.median_detect_seconds ?? "—"}s)
                </span>
              </div>
              <div className="stat">
                <span className="stat-num">
                  {evalData.summary.avg_resolve_seconds ?? "—"}s
                </span>
                <span className="stat-label">avg resolve</span>
              </div>
              <div className="stat">
                <span className="stat-num">{evalData.summary.false_positives}</span>
                <span className="stat-label">
                  false positives ({evalData.baseline_seconds ?? 30}s baseline)
                </span>
              </div>
            </div>
            <p className="muted small">
              Investigator: {evalData.summary.investigator_source ?? "—"}
              {evalData.generated_at ? ` · run ${fmtTime(evalData.generated_at)}` : ""}
            </p>
            <table className="history">
              <thead>
                <tr>
                  <th>Service</th>
                  <th>Fault</th>
                  <th>Detect</th>
                  <th>Cause</th>
                  <th>Evidence</th>
                  <th>Conf.</th>
                  <th>Resolve</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {evalData.runs.map((r, idx) => (
                  <tr key={idx}>
                    <td>{r.service}</td>
                    <td>{r.break_type}</td>
                    <td>{r.detect_seconds != null ? `${r.detect_seconds}s` : "—"}</td>
                    <td>{r.cause_correct ? "✓" : "✗"}</td>
                    <td>{r.evidence_valid ? "✓" : "✗"}</td>
                    <td>{r.confidence != null ? `${r.confidence}%` : "—"}</td>
                    <td>{r.resolve_seconds != null ? `${r.resolve_seconds}s` : "—"}</td>
                    <td>{r.final_status ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
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
