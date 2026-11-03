"""Keyword RAG over runbooks + past incidents (stdlib only).

No embeddings, no new dependencies: the demo has four short runbooks,
so term overlap ranks them correctly. Retrieval is advisory only —
it can suggest what to check and what to try, but it is never
evidence. Evidence lines must still come from the real log lines
(see investigator.py), never from a runbook.

Main entry point: get_context(incident) -> str. Returns "" when
nothing matches, so callers can skip it silently.
"""
import re
from pathlib import Path

from . import store

RUNBOOK_DIR = Path(__file__).resolve().parent / "runbooks"

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "on", "for", "and", "or", "with", "by", "at",
    "from", "as", "it", "its", "this", "that", "these", "those",
    "if", "then", "than", "so", "no", "not", "do", "does", "did",
    "can", "could", "will", "would", "should", "one", "two", "per",
}

# Synonyms that connect a detector metric to the words runbooks use.
_FAULT_SYNONYMS = {
    "latency": {
        "latency", "slow", "p95", "timeout", "timeouts", "timed", "slo",
        "500ms", "spike", "degraded",
    },
    "errors": {
        "error", "errors", "500", "failing", "failed", "failure",
        "failures", "spike", "degraded", "handler",
    },
}

# Statuses worth learning from: finished incidents only. An incident
# still open has no resolution to learn from yet.
_PAST_CASE_STATUSES = ("resolved", "failed", "rejected", "superseded")

_runbooks_cache = None


def _tokenize(text):
    """Lowercase word tokens, minus stopwords. '500ms' stays one token."""
    return {
        tok
        for tok in re.findall(r"[a-z0-9]+", (text or "").lower())
        if tok not in _STOPWORDS and len(tok) > 1
    }


def _fault_types(incident):
    """{'latency'} / {'errors'} / both, from the detector metric plus
    the evidence keywords.

    The metric alone misleads: a latency break raises the error rate
    too, so the detector usually fires the error rule first — an
    incident with metric error_rate can still be a latency fault, and
    the log lines ("downstream DB query timed out", "p95 latency")
    say so. Both signals count.
    """
    faults = set()
    metric = (incident or {}).get("metric", "")
    if metric == "error_rate":
        faults.add("errors")
    elif metric == "latency_p95_ms":
        faults.add("latency")
    evidence_tokens = set()
    investigation = (incident or {}).get("investigation") or {}
    evidence_tokens |= _tokenize(investigation.get("cause", ""))
    for line in investigation.get("evidence") or []:
        evidence_tokens |= _tokenize(line)
    if evidence_tokens & {"slow", "latency", "p95", "timeout", "timeouts",
                          "timed"}:
        faults.add("latency")
    if evidence_tokens & {"failing", "failed", "error", "errors", "500",
                          "handler"}:
        faults.add("errors")
    return faults


def _fault_type(incident):
    """Single fault type for past-case matching: 'latency' wins ties,
    because a latency fault detected via the error rule is still a
    latency fault at its root."""
    faults = _fault_types(incident)
    if "latency" in faults:
        return "latency"
    if "errors" in faults:
        return "errors"
    return ""


def _parse_runbook(path):
    """Title, applies-to (services + fault types), sections, body."""
    text = path.read_text()
    title = path.stem.replace("-", " ").title()
    applies_services, applies_faults = set(), set()
    sections = {}
    current = None
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.lower().startswith("applies to:"):
            applies = line.split(":", 1)[1].lower()
            for name in ("orders", "inventory", "payments"):
                if name in applies:
                    applies_services.add(name)
            for fault in ("latency", "errors"):
                if fault in applies:
                    applies_faults.add(fault)
        elif line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    sections = {
        name: "\n".join(lines).strip() for name, lines in sections.items()
    }
    body = "\n".join(sections.values())
    return {
        "path": path,
        "title": title,
        "services": applies_services,
        "faults": applies_faults,
        "sections": sections,
        "body": body,
        "body_tokens": _tokenize(body),
        "title_tokens": _tokenize(title),
    }


def load_runbooks():
    """All runbooks in backend/runbooks/, cached per process."""
    global _runbooks_cache
    if _runbooks_cache is None:
        runbooks = []
        if RUNBOOK_DIR.is_dir():
            for path in sorted(RUNBOOK_DIR.glob("*.md")):
                try:
                    runbooks.append(_parse_runbook(path))
                except OSError:
                    continue
        _runbooks_cache = runbooks
    return _runbooks_cache


def reload_runbooks():
    """Drop the cache (tests / runbook edits)."""
    global _runbooks_cache
    _runbooks_cache = None
    return load_runbooks()


def _query_tokens(incident):
    """Tokens describing the incident: service, fault types + synonyms,
    and words from the investigation cause and quoted evidence lines."""
    tokens = set()
    service = (incident or {}).get("service", "")
    if service:
        tokens.add(service)
    for fault in _fault_types(incident):
        tokens |= _FAULT_SYNONYMS[fault]
    investigation = (incident or {}).get("investigation") or {}
    tokens |= _tokenize(investigation.get("cause", ""))
    for line in investigation.get("evidence") or []:
        tokens |= _tokenize(line)
    return tokens


def _score_runbook(query_tokens, runbook, service, faults):
    """Service/fault match counts most; title and body overlap add up."""
    score = 0
    if service and service in runbook["services"]:
        score += 6
    if faults & runbook["faults"]:
        score += 6
    score += 3 * len(query_tokens & runbook["title_tokens"])
    score += len(query_tokens & runbook["body_tokens"])
    return score


def _section(runbook, *names, limit=800):
    """First matching section's text, capped. "" when absent."""
    for name in names:
        for heading, text in runbook["sections"].items():
            if heading.lower().startswith(name.lower()):
                return text[:limit]
    return ""


def _past_cases(incident, limit=3):
    """Most recent finished incidents, same service + overlapping
    fault types."""
    service = (incident or {}).get("service", "")
    faults = _fault_types(incident)
    if not service or not faults:
        return []
    cases = []
    for past in reversed(store.INCIDENTS):
        if past.get("id") == incident.get("id"):
            continue
        if past.get("service") != service:
            continue
        if not (_fault_types(past) & faults):
            continue
        if past.get("status") not in _PAST_CASE_STATUSES:
            continue
        cause = ((past.get("investigation") or {}).get("cause") or "").strip()
        resolution = (past.get("resolution") or "").strip()
        cases.append(
            f"- {past.get('id')} ({past.get('status')}): "
            f"{cause or 'cause not recorded'} -> "
            f"{resolution or 'resolution not recorded'}"
        )
        if len(cases) >= limit:
            break
    return cases


def get_context(incident):
    """Prompt-ready context for one incident, or "" when nothing matches.

    Top 1–2 runbooks by keyword overlap, each with its Symptoms and
    Fix steps, plus up to 3 similar past incidents (cause + how they
    were resolved). Runbook text is advisory only — it must never be
    quoted as evidence.
    """
    if not incident or not isinstance(incident, dict):
        return ""
    service = incident.get("service", "")
    faults = _fault_types(incident)
    query_tokens = _query_tokens(incident)

    ranked = []
    for runbook in load_runbooks():
        score = _score_runbook(query_tokens, runbook, service, faults)
        if score > 0:
            # A runbook written for this exact service outranks generic
            # ones: it has service-specific guidance (for payments, the
            # never-auto-retry-a-charge rule) that a generic runbook
            # cannot give. Among the rest, score decides.
            specific = (
                1 if service and runbook["services"] == {service} else 0
            )
            ranked.append((specific, score, runbook))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    top = ranked[:2]

    parts = []
    if top:
        parts.append(
            "Runbook context (advisory only — evidence must still come "
            "from real log lines, never from runbooks):"
        )
        for _specific, score, runbook in top:
            parts.append(f"\n## {runbook['title']} (relevance {score})")
            symptoms = _section(runbook, "Symptoms")
            if symptoms:
                parts.append(f"Symptoms:\n{symptoms}")
            fix = _section(runbook, "Fix steps")
            if fix:
                parts.append(f"Fix steps:\n{fix}")

    cases = _past_cases(incident)
    if cases:
        parts.append(
            "\nPast cases (same service + fault type, most recent first):"
        )
        parts.extend(cases)

    return "\n".join(parts).strip()
