"""Investigator — the only LLM part. One Gemini call per incident.

Live path (opt-in only):
- Set GEMINI_LIVE=1 and the investigator calls the registered
  custom.gemini helper:
    python3 ~/workspace/custom-connectors/custom.gemini/bin/client.py
      generate --model <model> --prompt "..."
  That helper holds the stored credential; no API key ever appears in
  this code, in env vars, in files, or in logs.
- Models are tried in order (GEMINI_MODEL first, then the fallbacks):
  gemini-3.5-flash-lite, gemini-3.1-flash-lite. One attempt per model
  (~15s timeout), then one retry of the first model.

Default (GEMINI_LIVE unset / not "1"):
- Deterministic mock. The demo works with no key and no network call.

Either way:
- Input is only real data: the metric that fired, recent metrics, and
  the last 20 log lines from the simulator. Nothing is invented.
- Output must parse to {cause, evidence, confidence, proposed_fix}.
- Hallucination guard: every evidence line must appear (verbatim, or
  as a substring) in the log lines we sent. If any line fails that
  check, retry once, then fall back to the mock.
- A failed / timed-out live call never breaks the demo: it falls back
  to the mock and records source="mock-fallback".

Live Gemini status (Nov 2, 2026, verified by direct generate calls):
- WORKING via the stored key: gemini-3.5-flash (replied "OK live",
  twice, and completed a full live investigation end to end) and
  gemini-3.5-flash-lite (replied "OK live"); gemma-4-26b-a4b-it also
  answered but is verbose, so it is not in the try list.
- FAILING via the stored key: gemini-3.8-flash -> HTTP 503 Service
  Unavailable (3 times), gemini-flash-latest -> HTTP 503,
  gemini-2.5-flash -> HTTP 404, gemini-2.5-flash-lite -> HTTP 404,
  gemini-2.0-flash -> HTTP 404, gemini-2.0-flash-lite -> HTTP 404.
  The API's model list confirms all of these names exist — the
  404s/503s are availability, not typos.
- NOTE: gemini-3.8-flash ANSWERED in Google AI Studio for the same
  account ("Hello, how can I help you today?"), so the model works —
  the stored API key/project was the suspect for the 503s, not the
  model. A fresh key was submitted via Secure Vault and retested:
  gemini-3.5-flash-lite WORKS with the new key (replied "OK live" in
  ~1-4s) and gemini-3.1-flash-lite WORKS too ("OK live" in 11s, after
  one transient hang past 45s on the first attempt), so the default
  model is now gemini-3.5-flash-lite with fallback
  gemini-3.1-flash-lite. Still failing with the new key: gemini-3.8-flash
  hangs / times out at 60s, gemini-2.5-flash, gemini-2.5-flash-lite and
  gemini-2.5-pro all return HTTP 404.
- On Render the helper binary does not exist, so live calls fail fast
  (OSError) and the demo falls back to the mock there.
"""
import json
import os
import re
import subprocess
import sys

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# Fallback models, tried in order after GEMINI_MODEL (deduped at call time).
GEMINI_FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
HELPER = os.path.expanduser(
    "~/workspace/custom-connectors/custom.gemini/bin/client.py"
)


def _mock_result(incident, logs):
    """Deterministic fallback. Quotes only real log lines."""
    error_logs = [l for l in logs if "ERROR" in l or "WARN" in l]
    evidence = (error_logs or logs)[-3:]
    if incident["metric"] == "error_rate":
        cause = (
            f"{incident['service']} is failing requests: error rate "
            f"{int(incident['value'] * 100)}% over the last minute, above the "
            f"{int(incident['threshold'] * 100)}% SLO."
        )
        confidence = 82
        fix = f"Restart the {incident['service']} service and check its error logs before reopening traffic."
    else:
        cause = (
            f"{incident['service']} is slow: p95 latency {incident['value']}ms, "
            f"above the {incident['threshold']}ms SLO, with downstream timeouts in the logs."
        )
        confidence = 84
        fix = (
            f"Restart the {incident['service']} service and roll back the last "
            "deploy if latency stays above 500ms after restart."
        )
    return {
        "cause": cause,
        "evidence": evidence,
        "confidence": confidence,
        "proposed_fix": fix,
        "source": "mock",
    }


def _evidence_is_real(evidence, logs):
    """True only if every quoted line matches a log line we actually sent."""
    if not evidence or len(evidence) != 3:
        return False
    for line in evidence:
        line = str(line).strip()
        if not line:
            return False
        # Exact match, or the quoted line is a substring of a real log line.
        if not any(line in real or real.strip() == line for real in logs):
            return False
    return True


def _call_gemini_helper(prompt, model):
    """One live call via the custom.gemini helper. Returns text or None."""
    try:
        proc = subprocess.run(
            [sys.executable, HELPER, "generate",
             "--model", model, "--prompt", prompt],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
        return data.get("text") or None
    except ValueError:
        return None


def _try_live_models(prompt, logs):
    """Try the model list in order, then one retry of the first model.

    Returns a parsed result dict, or None if every attempt failed or
    produced unusable / hallucinated output.
    """
    models = []
    for m in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]:
        if m and m not in models:
            models.append(m)
    attempts = models + [models[0]]  # one retry of the first model
    for model in attempts:
        text = _call_gemini_helper(prompt, model)
        parsed = _parse_result(text, logs)
        if parsed:
            parsed["model"] = model
            return parsed
    return None


def _parse_result(text, logs):
    """Pull a JSON object out of the model text and validate it."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return None
    cause = str(parsed.get("cause", "")).strip()
    evidence = parsed.get("evidence", [])
    try:
        confidence = int(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    fix = str(parsed.get("proposed_fix", "")).strip()
    if not cause or not fix or not 0 <= confidence <= 100:
        return None
    if not _evidence_is_real(evidence, logs):
        return None
    return {
        "cause": cause,
        "evidence": [str(e).strip() for e in evidence],
        "confidence": confidence,
        "proposed_fix": fix,
        "source": "gemini",
    }


def investigate(incident, logs, recent_metrics):
    """Run the investigation. Returns a result dict with a 'source' field.

    The returned result also carries 'confidence_breakdown' and 'input',
    both computed from the real data that was reviewed — never hardcoded:
    - evidence_match: how many of the 3 quoted evidence lines are real
      log lines (verified per line against the logs sent in)
    - metric_severity_x: triggered value ÷ threshold (e.g. 1.6x)
    - log_signal_n: ERROR/WARN lines among the log lines reviewed
    - input: what the investigator was given (metric, counts, window)
    """
    logs = list(logs)[-20:]
    metrics_text = "\n".join(
        f"{m['ts']} latency={m['latency_ms']}ms error_rate={m['error_rate']} status={m['status']}"
        for m in recent_metrics[-12:]
    )
    logs_text = "\n".join(logs)
    prompt = (
        "You are an on-call engineer. Given these logs and metrics, what is "
        "the most likely cause? Reply with JSON only, no other text, using "
        "exactly these keys: cause, evidence, confidence, proposed_fix.\n"
        "evidence must be exactly 3 log lines, copied VERBATIM from the log "
        "lines below — do not invent, shorten or paraphrase them.\n"
        "confidence is an integer 0-100. proposed_fix is one concrete action.\n\n"
        f"Service: {incident['service']}\n"
        f"Triggered metric: {incident['metric']} = {incident['value']} "
        f"(threshold {incident['threshold']})\n\n"
        f"Recent metrics:\n{metrics_text}\n\nLog lines:\n{logs_text}"
    )

    if os.environ.get("GEMINI_LIVE", "0") != "1":
        result = _mock_result(incident, logs)
    else:
        result = _try_live_models(prompt, logs)
        if result is None:
            result = _mock_result(incident, logs)
            result["source"] = "mock-fallback"

    result["confidence_breakdown"] = _confidence_breakdown(
        incident, result, logs
    )
    result["input"] = {
        "metric": incident["metric"],
        "value": incident["value"],
        "threshold": incident["threshold"],
        "log_lines_reviewed": len(logs),
        "metrics_samples_reviewed": min(len(recent_metrics), 12),
        "window": "last 20 log lines + last 12 metric samples (1 min)",
    }
    return result


def _confidence_breakdown(incident, result, logs):
    """Three factors behind the confidence score, from real reviewed data."""
    evidence = result.get("evidence") or []
    matched = sum(
        1
        for line in evidence
        if str(line).strip()
        and any(str(line).strip() in real or real.strip() == str(line).strip()
                for real in logs)
    )
    threshold = incident["threshold"] or 1
    severity = round(incident["value"] / threshold, 1)
    signal = sum(1 for line in logs if "ERROR" in line or "WARN" in line)
    return {
        "evidence_match": f"{matched}/{len(evidence) or 3}",
        "evidence_match_n": matched,
        "evidence_total": len(evidence) or 3,
        "metric_severity_x": severity,
        "log_signal_n": signal,
        "log_lines_reviewed": len(logs),
    }
