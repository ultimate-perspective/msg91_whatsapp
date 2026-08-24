"""The single entry point for writing to the funnel event log.

Every signal source calls `record()`: the outbound override, the inbound
`WhatsApp Message` hook, and the MSG91 status webhook. Nothing else writes
`WhatsApp Funnel Event` rows.
"""

import json

import frappe
from frappe.utils import now_datetime

from msg91_whatsapp.funnel import campaigns, contacts, engine
from msg91_whatsapp.utils import normalize_phone

DOCTYPE = "WhatsApp Funnel Event"

INBOUND_EVENTS = {"Inbound Received", "Opted Out"}


def record(
    event_type,
    phone,
    occurred_at=None,
    account=None,
    whatsapp_message=None,
    request_id=None,
    template_name=None,
    uuid=None,
    detail=None,
    payload=None,
    profile_name=None,
    campaign=None,
):
    """Log one event and fold it into the contact's cached summary.

    Returns the event doc, or None if it was a duplicate. Never raises: a
    bookkeeping failure must not take down a message that actually went out.
    """
    try:
        return _record(
            event_type,
            phone,
            occurred_at=occurred_at,
            account=account,
            whatsapp_message=whatsapp_message,
            request_id=request_id,
            template_name=template_name,
            uuid=uuid,
            detail=detail,
            payload=payload,
            profile_name=profile_name,
            campaign=campaign,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"MSG91: failed to record {event_type}")
        return None


def _record(
    event_type,
    phone,
    occurred_at=None,
    account=None,
    whatsapp_message=None,
    request_id=None,
    template_name=None,
    uuid=None,
    detail=None,
    payload=None,
    profile_name=None,
    campaign=None,
):
    phone = normalize_phone(phone)
    if not phone:
        return None

    if is_duplicate(event_type, request_id, uuid):
        return None

    at = occurred_at or now_datetime()
    contact = contacts.get_or_create(phone, profile_name=profile_name)

    event = frappe.new_doc(DOCTYPE)
    event.update(
        {
            "event_type": event_type,
            "occurred_at": at,
            "contact_phone": phone,
            "lead": contact.get("lead"),
            "whatsapp_account": account,
            "campaign": campaign,
            "direction": "Inbound" if event_type in INBOUND_EVENTS else "Outbound",
            "whatsapp_message": whatsapp_message,
            "request_id": request_id,
            "template_name": template_name,
            "uuid": uuid,
            "detail": detail,
            "payload": json.dumps(payload, indent=2, default=str) if payload else None,
        }
    )
    event.insert(ignore_permissions=True)

    campaigns.note_engagement(phone, event_type)
    contacts.apply_event(contact, event_type, occurred_at=at, detail=detail)
    # Re-score immediately, so a reply that makes someone Hot shows up as Hot
    # rather than waiting for the next sweep. `evaluate` saves the contact.
    engine.evaluate(contact)

    return event


def is_duplicate(event_type, request_id, uuid):
    """MSG91 retries callbacks, and Meta redelivers webhooks. Both are at-least-once."""
    if request_id:
        return bool(
            frappe.db.exists(DOCTYPE, {"event_type": event_type, "request_id": request_id})
        )
    if uuid:
        return bool(frappe.db.exists(DOCTYPE, {"event_type": event_type, "uuid": uuid}))
    return False
