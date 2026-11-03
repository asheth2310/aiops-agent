"""Investigator — the only LLM part. One Gemini call per incident.

Live path (opt-in only):
- Set GEMINI_LIVE=1 and the investigator calls the registered
  custom.gemini helper:
    python3 ~/workspace/custom-connectors/custom.gemini/bin/client.py
      generate --model gemini-2.5-flash --prompt "..."
  That helper holds the stored credential; no API key ever appears in
  this code, in env vars, in files, or in logs.

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

Live Gemini status (Nov 2, 2026): UNVERIFIED. The helper compiles, but
live calls fail with HTTP 404 from generativelanguage.googleapis.com
for gemini-2.5-flash and gemini-2.0-flash, with no pending approval.
Do not claim live Gemini works until a run returns text.
"""
import json
import os
import re
import subprocess
import sys

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
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


def _call_gemini_helper(prompt):
    """One live call via the custom.gemini helper. Returns text or None."""
    try:
        proc = subprocess.run(
            [sys.executable, HELPER, "generate",
             "--model", GEMINI_MODEL, "--prompt", prompt],
            capture_output=True, text=True, timeout=75,
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
    """Run the investigation. Returns a result dict with a 'source' field."""
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
        return _mock_result(incident, logs)

    for _attempt in range(2):  # first try + one retry on bad / hallucinated output
        text = _call_gemini_helper(prompt)
        parsed = _parse_result(text, logs)
        if parsed:
            return parsed
    result = _mock_result(incident, logs)
    result["source"] = "mock-fallback"
    return result
