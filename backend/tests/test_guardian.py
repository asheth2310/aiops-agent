"""Guardian: policy in code. Nothing auto-fixes, ever."""
from backend import guardian


def test_below_70_cannot_approve():
    for conf in (0, 50, 69):
        result = guardian.evaluate(conf)
        assert result["decision"] == "needs_info"
        assert result["can_approve"] is False


def test_70_to_89_needs_human_approval():
    for conf in (70, 82, 89):
        result = guardian.evaluate(conf)
        assert result["decision"] == "needs_approval"
        assert result["can_approve"] is True


def test_90_plus_still_needs_human_approval():
    # Even a very confident investigation never auto-fixes in the MVP.
    for conf in (90, 95, 100):
        result = guardian.evaluate(conf)
        assert result["decision"] == "needs_approval"
        assert result["can_approve"] is True


def test_never_auto_fixes():
    for conf in range(0, 101):
        result = guardian.evaluate(conf)
        assert "auto" not in result["decision"]
        assert result["decision"] in ("needs_info", "needs_approval")


def test_confidence_is_clamped():
    assert guardian.evaluate(-5)["decision"] == "needs_info"
    assert guardian.evaluate(150)["decision"] == "needs_approval"
