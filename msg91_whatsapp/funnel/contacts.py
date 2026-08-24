"""The contact row: identity, plus a denormalized summary of its event history.

`WhatsApp Funnel Event` is the source of truth. The fields maintained here are a
cache of it, so that a rule asking "have they replied in the last 7 days?" is a
field read rather than a scan of every event ever recorded.
"""

import frappe
from frappe.utils import add_to_date, now_datetime

from msg91_whatsapp.funnel import leads
from msg91_whatsapp.utils import normalize_phone

DOCTYPE = "WhatsApp Funnel Contact"
SESSION_HOURS = 24

# event type -> the "when did this last happen" field it refreshes
TIMESTAMP_FIELDS = {
    "Outbound Sent": "last_outbound_at",
    "Delivered": "last_delivered_at",
    "Read": "last_read_at",
    "Clicked": "last_clicked_at",
    "Inbound Received": "last_inbound_at",
}

COUNTER_FIELDS = {
    "Outbound Sent": "outbound_count",
    "Inbound Received": "inbound_count",
}


def get_or_create(phone, profile_name=None):
    phone = normalize_phone(phone)
    name = frappe.db.exists(DOCTYPE, {"phone": phone})

    if name:
        contact = frappe.get_doc(DOCTYPE, name)
    else:
        contact = frappe.new_doc(DOCTYPE)
        contact.phone = phone

    if profile_name and not contact.profile_name:
        contact.profile_name = profile_name

    leads.lead_for_contact(contact)
    return contact


def apply_event(contact, event_type, occurred_at=None, detail=None):
    """Fold one event into the contact's cached summary. Does not save."""
    at = occurred_at or now_datetime()

    field = TIMESTAMP_FIELDS.get(event_type)
    if field:
        contact.set(field, at)

    counter = COUNTER_FIELDS.get(event_type)
    if counter:
        contact.set(counter, (contact.get(counter) or 0) + 1)

    if event_type == "Inbound Received":
        # Their message reopens the 24h free-form window.
        contact.replied = 1
        contact.session_expires_at = add_to_date(at, hours=SESSION_HOURS)
        if detail:
            contact.last_message = detail[:1000]

    if event_type == "Opted Out":
        contact.opted_out = 1

    return contact
