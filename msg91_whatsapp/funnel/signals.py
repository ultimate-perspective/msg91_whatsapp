"""Inbound signal handling.

An inbound WhatsApp message does two things:

1. Opens/refreshes the customer's 24h session window on the business number they
   messaged (``WhatsApp Session``) — this is what makes free-form replies legal.
2. Advances the lead funnel (``WhatsApp Funnel Contact``).

Wired via ``doc_events`` on frappe_whatsapp's ``WhatsApp Message`` (after_insert).
"""

import frappe
from frappe.utils import add_to_date, now_datetime

from msg91_whatsapp.msg91_whatsapp.doctype.whatsapp_session.whatsapp_session import (
    touch_inbound,
)

FUNNEL_DOCTYPE = "WhatsApp Funnel Contact"


def on_whatsapp_message(doc, method=None):
    """after_insert hook on `WhatsApp Message`. Only reacts to inbound messages."""
    if (doc.get("type") or "").lower() != "incoming":
        return

    phone = (doc.get("from") or "").strip()
    if not phone:
        return

    body = frappe.utils.strip_html_tags(doc.get("message") or "")[:1000]

    _open_session(doc, phone, body)
    _advance_funnel(doc, phone, body)


def _open_session(doc, phone, body):
    """The 24h window belongs to the number the customer actually messaged."""
    account = doc.get("whatsapp_account")
    if not account:
        return
    touch_inbound(phone, account, profile_name=doc.get("profile_name"), message=body)


def _advance_funnel(doc, phone, body):
    contact = _get_or_create_contact(phone, profile_name=doc.get("profile_name"))

    now = now_datetime()
    contact.last_inbound_at = now
    contact.session_expires_at = add_to_date(now, hours=24)
    contact.replied = 1
    if body:
        contact.last_message = body

    # An inbound reply means they at least interacted.
    contact.advance_to("Interacted")
    contact.save(ignore_permissions=True)


def _get_or_create_contact(phone, profile_name=None):
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
