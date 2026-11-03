"""Guardian — policy in code, not in the LLM.

Rules (from the MVP plan):
- confidence < 70  -> needs_info, no Approve button
- confidence 70-89 -> needs_approval, human must approve
- confidence >= 90 -> needs_approval in the MVP as well
- nothing auto-fixes in the MVP, ever
"""


def evaluate(confidence):
    confidence = max(0, min(100, int(confidence)))
    if confidence < 70:
        return {
            "decision": "needs_info",
            "can_approve": False,
            "reason": (
                f"Confidence {confidence} is below 70 — too low to act on. "
                "Collect more logs before asking a human to approve."
            ),
        }
    if confidence < 90:
        return {
            "decision": "needs_approval",
            "can_approve": True,
            "reason": (
                f"Confidence {confidence} is in the 70-89 band — "
                "a human must approve before any fix runs."
            ),
        }
    return {
        "decision": "needs_approval",
        "can_approve": True,
        "reason": (
            f"Confidence {confidence} is high, but the MVP never auto-fixes — "
            "a human must still approve."
        ),
    }
