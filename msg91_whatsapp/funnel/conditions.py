"""Evaluate a rule's conditions against a bag of facts.

Deliberately not `eval`. The user is a sales operator in a desk form, not a
developer, and a rule that can execute arbitrary Python is a rule that can take
the site down from a text field.
"""

from frappe.utils import cint, flt

TEXT_FACTS = {"message_text", "template_name", "current_state", "lead_status"}
NUMERIC_FACTS = {
    "score",
    "inbound_count",
    "outbound_count",
    "nudge_count",
    "days_since_inbound",
    "days_since_outbound",
    "days_since_read",
    "days_since_clicked",
}
BOOLEAN_FACTS = {"replied", "opted_out", "session_open"}


def matches(conditions, facts):
    """All conditions must hold. No conditions means the rule always matches."""
    return all(_check(condition, facts) for condition in conditions or [])


def _check(condition, facts):
    actual = facts.get(condition.fact)
    operator = condition.operator
    expected = condition.value

    if operator == "is set":
        return actual not in (None, "", 0)
    if operator == "is not set":
        return actual in (None, "", 0)

    if actual is None:
        # "45 days since they read it" is unknowable if they never read it, so a
        # missing fact fails every comparison rather than silently counting as 0.
        return False

    if condition.fact in NUMERIC_FACTS:
        return _compare_numeric(operator, flt(actual), flt(expected))

    if condition.fact in BOOLEAN_FACTS:
        return _compare_boolean(operator, cint(actual), cint(expected))

    return _compare_text(operator, str(actual), str(expected or ""))


def _compare_numeric(operator, actual, expected):
    if operator in ("equals",):
        return actual == expected
    if operator in ("not equals",):
        return actual != expected
    if operator == "greater than":
        return actual > expected
    if operator == "greater than or equals":
        return actual >= expected
    if operator == "less than":
        return actual < expected
    if operator == "less than or equals":
        return actual <= expected
    return False


def _compare_boolean(operator, actual, expected):
    if operator == "equals":
        return actual == expected
    if operator == "not equals":
        return actual != expected
    return False


def _compare_text(operator, actual, expected):
    actual_lower = actual.lower()
    expected_lower = expected.lower()

    if operator == "equals":
        return actual_lower == expected_lower
    if operator == "not equals":
        return actual_lower != expected_lower
    if operator == "contains":
        return expected_lower in actual_lower
    if operator == "not contains":
        return expected_lower not in actual_lower
    if operator in ("in", "not in"):
        options = [part.strip().lower() for part in expected.split(",") if part.strip()]
        hit = any(option in actual_lower for option in options)
        return hit if operator == "in" else not hit
    return False
