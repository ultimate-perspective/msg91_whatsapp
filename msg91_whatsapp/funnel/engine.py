"""Score a contact by replaying its event log, then resolve that into a state.

Replay rather than running totals, because the user can edit a rule at any time.
An incremental score would preserve points awarded by a rule that no longer
exists, and nobody would ever be able to explain the number. Replay costs a
query per contact and is honest.
"""

import json

import frappe
from frappe.utils import cint, now_datetime, time_diff_in_hours

from msg91_whatsapp.funnel import conditions
from msg91_whatsapp.msg91_whatsapp.doctype.whatsapp_lead_rule.whatsapp_lead_rule import (
    active_rules,
)
from msg91_whatsapp.msg91_whatsapp.doctype.whatsapp_lead_state.whatsapp_lead_state import (
    by_rank,
)

EVENT_DOCTYPE = "WhatsApp Funnel Event"


def evaluate(contact, save=True):
    """Recompute score and state for one contact. Returns a summary dict."""
    events = _events_for(contact.phone)
    score, breakdown, forced_state, opted_out = _replay(contact, events)

    if opted_out:
        contact.opted_out = 1

    state = _resolve_state(contact, score, forced_state)

    changed = contact.state != state or cint(contact.score) != score
    contact.score = score
    contact.score_breakdown = json.dumps(breakdown, indent=2)
    if changed:
        contact.state = state
        contact.state_updated_at = now_datetime()

    if save:
        contact.save(ignore_permissions=True)
        if changed:
            mirror_to_lead(contact)

    return {"score": score, "state": state, "changed": changed, "breakdown": breakdown}


def _events_for(phone):
    return frappe.get_all(
        EVENT_DOCTYPE,
        filters={"contact_phone": phone},
        fields=["name", "event_type", "occurred_at", "detail", "template_name"],
        order_by="occurred_at asc",
    )


def _replay(contact, events):
    score = 0
    breakdown = []
    fired = {}
    forced = []
    opted_out = False

    base = _facts(contact)

    for event in events:
        for rule in active_rules(trigger="On Event", event_type=event.event_type):
            if cint(rule.max_times) and fired.get(rule.name, 0) >= cint(rule.max_times):
                continue

            facts = dict(base)
            facts["message_text"] = event.detail or ""
            facts["template_name"] = event.template_name or ""

            if not conditions.matches(rule.conditions, facts):
                continue

            fired[rule.name] = fired.get(rule.name, 0) + 1
            score += cint(rule.score_delta)
            breakdown.append(
                {
                    "rule": rule.name,
                    "on": event.event_type,
                    "at": str(event.occurred_at),
                    "points": cint(rule.score_delta),
                }
            )
            if rule.set_state:
                forced.append((cint(rule.priority), rule.set_state))
            if rule.set_opted_out:
                opted_out = True

    # Time-based rules see the final tally, so they can say things like
    # "score above 40 but silent for two weeks".
    scheduled_facts = dict(base)
    scheduled_facts["score"] = score

    for rule in active_rules(trigger="On Schedule"):
        if not conditions.matches(rule.conditions, scheduled_facts):
            continue
        score += cint(rule.score_delta)
        breakdown.append(
            {"rule": rule.name, "on": "schedule", "points": cint(rule.score_delta)}
        )
        if rule.set_state:
            forced.append((cint(rule.priority), rule.set_state))
        if rule.set_opted_out:
            opted_out = True

    forced_state = min(forced)[1] if forced else None
    return score, breakdown, forced_state, opted_out


def _facts(contact):
    return {
        "score": cint(contact.score),
        "inbound_count": cint(contact.inbound_count),
        "outbound_count": cint(contact.outbound_count),
        "nudge_count": cint(contact.nudge_count),
        "days_since_inbound": _days_since(contact.last_inbound_at),
        "days_since_outbound": _days_since(contact.last_outbound_at),
        "days_since_read": _days_since(contact.last_read_at),
        "days_since_clicked": _days_since(contact.last_clicked_at),
        "replied": cint(contact.replied),
        "opted_out": cint(contact.opted_out),
        "session_open": 1 if _session_open(contact) else 0,
        "current_state": contact.state or "",
        "lead_status": _lead_status(contact),
        "message_text": "",
        "template_name": "",
    }


def _days_since(timestamp):
    if not timestamp:
        return None
    return time_diff_in_hours(now_datetime(), timestamp) / 24.0


def _session_open(contact):
    if not contact.session_expires_at:
        return False
    return frappe.utils.get_datetime(contact.session_expires_at) > now_datetime()


def _lead_status(contact):
    if not contact.get("lead"):
        return ""
    return frappe.db.get_value("CRM Lead", contact.lead, "status") or ""


def _resolve_state(contact, score, forced_state):
    """Score picks the band; a rule can override it; regression needs permission."""
    states = {state.name: state for state in by_rank()}
    current = states.get(contact.state)

    if current and current.is_terminal:
        # Converted, Lost and Opted Out are end states. Only a human moves out.
        return contact.state

    target = forced_state or _band_for(score, states.values())
    if not target:
        return contact.state

    candidate = states.get(target)
    if not current or not candidate:
        return target

    moving_backwards = candidate.rank < current.rank
    if moving_backwards and not current.allow_regression and not forced_state:
        return contact.state

    return target


def _band_for(score, states):
    """Highest-ranked non-terminal state whose entry score the contact has reached."""
    qualifying = [
        state
        for state in states
        if not state.is_terminal and score >= cint(state.min_score)
    ]
    if not qualifying:
        return None
    return max(qualifying, key=lambda state: state.rank).name


def mirror_to_lead(contact):
    """Optionally push the state onto the CRM Lead everyone else actually looks at."""
    settings = frappe.get_cached_doc("MSG91 Settings")
    if not settings.auto_write_lead_status or not contact.get("lead"):
        return
    if not contact.state:
        return

    state = frappe.get_cached_doc("WhatsApp Lead State", contact.state)
    if not state.maps_to_status and not state.maps_to_lost_reason:
        return

    lead = frappe.get_doc("CRM Lead", contact.lead)
    changed = False

    if state.maps_to_status and lead.status != state.maps_to_status:
        lead.status = state.maps_to_status
        changed = True
    if state.maps_to_lost_reason and lead.get("lost_reason") != state.maps_to_lost_reason:
        lead.lost_reason = state.maps_to_lost_reason
        changed = True

    if changed:
        lead.save(ignore_permissions=True)


def evaluate_phone(phone):
    """Convenience entry point for the event recorder and the scheduler."""
    from msg91_whatsapp.funnel import contacts

    name = frappe.db.exists(contacts.DOCTYPE, {"phone": phone})
    if not name:
        return None
    return evaluate(frappe.get_doc(contacts.DOCTYPE, name))


def sweep():
    """Scheduled re-evaluation. Time-based rules have no event to ride in on."""
    for name in frappe.get_all("WhatsApp Funnel Contact", pluck="name"):
        try:
            evaluate(frappe.get_doc("WhatsApp Funnel Contact", name))
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"MSG91: funnel sweep failed for {name}")
        frappe.db.commit()
