"""Audit trail — one function, used by every step of the pipeline.

Every decision (detector fired, investigator finished, guardian ruled,
human approved/rejected, executor ran) is appended here with a
timestamp, incident id and actor. The dashboard reads this list.
"""
from . import store


def log(incident_id, actor, action, detail=""):
    return store.add_audit(incident_id, actor, action, detail)


def all_entries():
    return list(store.AUDIT)
