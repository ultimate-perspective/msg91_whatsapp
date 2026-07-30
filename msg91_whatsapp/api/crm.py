"""Account-aware replacements for frappe/crm's WhatsApp APIs.

A customer can hold concurrent conversations with several of our business
numbers, so the CRM's WhatsApp tab needs to be able to:

- list the numbers (channels) available for a lead/deal, each with its own 24h
  window state  -> ``get_whatsapp_channels``
- show only the messages exchanged on the selected number
  -> ``get_whatsapp_messages(..., whatsapp_account=...)``
- send from the selected number -> ``create_whatsapp_message`` /
  ``send_whatsapp_template``

These are wired through ``override_whitelisted_methods`` so frappe/crm does not
need to be forked. The extra argument is optional everywhere, so the stock
frontend keeps working unchanged.
"""

from contextlib import contextmanager

import frappe

from crm.api.whatsapp import create_whatsapp_message as _crm_create_whatsapp_message
from crm.api.whatsapp import get_whatsapp_messages as _crm_get_whatsapp_messages
from crm.api.whatsapp import send_whatsapp_template as _crm_send_whatsapp_template
from msg91_whatsapp.msg91_whatsapp.doctype.whatsapp_session.whatsapp_session import (
    get_session,
)
from msg91_whatsapp.utils import normalize_phone


@frappe.whitelist()
def get_whatsapp_messages(reference_doctype, reference_name, whatsapp_account=None):
    """Upstream's message list, annotated with (and optionally filtered by) account."""
    messages = _crm_get_whatsapp_messages(reference_doctype, reference_name) or []
    if not messages:
        return messages

    accounts = _accounts_for(messages)
    for message in messages:
        message["whatsapp_account"] = accounts.get(message.get("name"))

    if whatsapp_account:
        messages = [m for m in messages if m["whatsapp_account"] == whatsapp_account]
    return messages


@frappe.whitelist()
def create_whatsapp_message(
    reference_doctype,
    reference_name,
    message,
    to,
    attach=None,
    reply_to=None,
    content_type="text",
    whatsapp_account=None,
):
    """Send a free-form message from a specific business number."""
    with sending_from(whatsapp_account):
        return _crm_create_whatsapp_message(
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            message=message,
            to=to,
            attach=attach,
            reply_to=reply_to,
            content_type=content_type,
        )


@frappe.whitelist()
def send_whatsapp_template(
    reference_doctype, reference_name, template, to, whatsapp_account=None
):
    """Send a template from a specific business number."""
    with sending_from(whatsapp_account):
        return _crm_send_whatsapp_template(
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            template=template,
            to=to,
        )


@contextmanager
def sending_from(whatsapp_account):
    """Pin the outgoing number for the duration of an upstream send.

    Upstream builds and inserts the WhatsApp Message itself (and inserting is
    what sends it), so we can't set the account afterwards. The overridden
    ``before_insert`` picks this flag up instead.
    """
    previous = frappe.flags.get("msg91_whatsapp_account")
    frappe.flags.msg91_whatsapp_account = whatsapp_account
    try:
        yield
    finally:
        frappe.flags.msg91_whatsapp_account = previous


@frappe.whitelist()
def get_whatsapp_channels(reference_doctype, reference_name, to=None):
    """The numbers this lead/deal can be messaged on, each with its window state.

    Drives the number dropdown and decides whether the composer allows free-form
    text or must force a template.
    """
    to = to or _phone_for(reference_doctype, reference_name)

    channels = []
    for account in frappe.get_all(
        "WhatsApp Account",
        filters={"status": "Active"},
        fields=["name", "account_name", "msg91_integrated_number", "is_default_outgoing"],
        order_by="is_default_outgoing desc, account_name asc",
    ):
        session = get_session(to, account["name"]) if to else None
        is_open = bool(session and session.is_open())
        channels.append(
            {
                "account": account["name"],
                "account_name": account["account_name"],
                "number": account.get("msg91_integrated_number"),
                "is_default": bool(account.get("is_default_outgoing")),
                "window_open": is_open,
                "expires_at": session.expires_at if session else None,
                "last_inbound_at": session.last_inbound_at if session else None,
                "can_send_freeform": is_open,
            }
        )
    return {"to": to, "channels": channels}


def _accounts_for(messages):
    names = [m.get("name") for m in messages if m.get("name")]
    if not names:
        return {}
    rows = frappe.get_all(
        "WhatsApp Message",
        filters={"name": ["in", names]},
        fields=["name", "whatsapp_account"],
    )
    return {row["name"]: row["whatsapp_account"] for row in rows}


def _phone_for(reference_doctype, reference_name):
    """Best-effort recipient number for a lead/deal."""
    if reference_doctype == "CRM Lead":
        lead = frappe.db.get_value(
            "CRM Lead", reference_name, ["mobile_no", "phone"], as_dict=True
        )
        return normalize_phone(lead and (lead.mobile_no or lead.phone))

    if reference_doctype == "CRM Deal":
        lead = frappe.db.get_value("CRM Deal", reference_name, "lead")
        if lead:
            return _phone_for("CRM Lead", lead)

    return None
