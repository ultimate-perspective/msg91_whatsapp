"""Funnel signal handlers.

P2: an inbound WhatsApp reply is the ``interacted`` signal — it also opens the
24h session window. We upsert a ``WhatsApp Funnel Contact`` keyed by the
customer's phone number and advance it down-funnel.

Wired via ``doc_events`` on frappe_whatsapp's ``WhatsApp Message`` (after_insert).
"""

import frappe
from frappe.utils import add_to_date, now_datetime

FUNNEL_DOCTYPE = "WhatsApp Funnel Contact"


def on_whatsapp_message(doc, method=None):
    """after_insert hook on `WhatsApp Message`. Only reacts to inbound messages."""
    if (doc.get("type") or "").lower() != "incoming":
        return

    phone = (doc.get("from") or "").strip()
    if not phone:
        return

    contact = get_or_create_contact(phone, profile_name=doc.get("profile_name"))

    now = now_datetime()
    contact.last_inbound_at = now
    contact.session_expires_at = add_to_date(now, hours=24)
    contact.replied = 1

    body = doc.get("message")
    if body:
        contact.last_message = frappe.utils.strip_html_tags(body)[:1000]

    # An inbound reply means they at least interacted.
    contact.advance_to("Interacted")
    contact.save(ignore_permissions=True)


def get_or_create_contact(phone, profile_name=None):
    name = frappe.db.exists(FUNNEL_DOCTYPE, {"phone": phone})
    if name:
        contact = frappe.get_doc(FUNNEL_DOCTYPE, name)
        if profile_name and not contact.profile_name:
            contact.profile_name = profile_name
        return contact

    contact = frappe.new_doc(FUNNEL_DOCTYPE)
    contact.phone = phone
    if profile_name:
        contact.profile_name = profile_name
    return contact
