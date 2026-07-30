"""24-hour session window tracking, per (contact phone x business number).

WhatsApp only allows free-form ("session") messages for 24h after the customer's
last inbound message. Outside that window you must send an approved template.

This doctype also carries the conversation binding: once a customer is talking to
one of your numbers, replies go out from that same number.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import add_to_date, get_datetime, now_datetime

from msg91_whatsapp.utils import normalize_phone

SESSION_HOURS = 24
DOCTYPE = "WhatsApp Session"


class WhatsAppSession(Document):
    def before_save(self):
        self.window_open = 1 if self.is_open() else 0

    def is_open(self):
        if not self.expires_at:
            return False
        return get_datetime(self.expires_at) > now_datetime()


def touch_inbound(phone, account, profile_name=None, message=None):
    """Customer messaged us: (re)open the 24h window on this account."""
    session = _get_or_create(phone, account)
    now = now_datetime()
    session.last_inbound_at = now
    session.expires_at = add_to_date(now, hours=SESSION_HOURS)
    if profile_name:
        session.profile_name = profile_name
    if message:
        session.last_message = message[:1000]
    session.save(ignore_permissions=True)
    return session


def touch_outbound(phone, account):
    """We messaged them: record activity (does NOT extend the window)."""
    session = _get_or_create(phone, account)
    session.last_outbound_at = now_datetime()
    session.save(ignore_permissions=True)
    return session


def get_session(phone, account):
    name = frappe.db.exists(
        DOCTYPE, {"contact_phone": normalize_phone(phone), "whatsapp_account": account}
    )
    return frappe.get_doc(DOCTYPE, name) if name else None


def is_window_open(phone, account):
    session = get_session(phone, account)
    return bool(session and session.is_open())


def get_active_account(phone):
    """The business number this customer is already talking to.

    Picks the account with the most recent inbound message, so replies stay on
    the same number the conversation started on.
    """
    rows = frappe.get_all(
        DOCTYPE,
        filters={"contact_phone": normalize_phone(phone)},
        fields=["whatsapp_account", "last_inbound_at"],
        order_by="last_inbound_at desc",
        limit=1,
    )
    return rows[0]["whatsapp_account"] if rows else None


@frappe.whitelist()
def get_window_status(phone, account=None):
    """UI helper: can we send a free-form message to this number right now?"""
    account = account or get_active_account(phone)
    if not account:
        return {"account": None, "open": False, "expires_at": None, "can_send_freeform": False}

    session = get_session(phone, account)
    is_open = bool(session and session.is_open())
    return {
        "account": account,
        "open": is_open,
        "expires_at": session.expires_at if session else None,
        "can_send_freeform": is_open,
    }


def _get_or_create(phone, account):
    phone = normalize_phone(phone)
    name = frappe.db.exists(DOCTYPE, {"contact_phone": phone, "whatsapp_account": account})
    if name:
        return frappe.get_doc(DOCTYPE, name)

    session = frappe.new_doc(DOCTYPE)
    session.contact_phone = phone
    session.whatsapp_account = account
    return session
