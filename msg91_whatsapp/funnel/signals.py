"""Turn every `WhatsApp Message` row into a funnel event.

Wired via ``doc_events`` on frappe_whatsapp's ``WhatsApp Message``
(after_insert), which is the one place where both directions land with a name:

- inbound  -> opens the customer's 24h session window and logs `Inbound Received`
- outbound -> logs `Outbound Sent`, carrying MSG91's request id so the delivery
              report that arrives minutes later can find its way back here

Everything after this point reads the event log, not this module.
"""

import frappe

from msg91_whatsapp.funnel import events
from msg91_whatsapp.msg91_whatsapp.doctype.whatsapp_session.whatsapp_session import (
    touch_inbound,
)


def on_whatsapp_message(doc, method=None):
    direction = (doc.get("type") or "").lower()

    if direction == "incoming":
        _on_inbound(doc)
    elif direction == "outgoing":
        _on_outbound(doc)


def _on_inbound(doc):
    phone = (doc.get("from") or "").strip()
    if not phone:
        return

    body = frappe.utils.strip_html_tags(doc.get("message") or "")[:1000]
    account = doc.get("whatsapp_account")

    if account:
        # The 24h window belongs to the number the customer actually messaged.
        touch_inbound(phone, account, profile_name=doc.get("profile_name"), message=body)

    events.record(
        "Inbound Received",
        phone,
        account=account,
        whatsapp_message=doc.name,
        uuid=doc.get("message_id"),
        detail=body,
        profile_name=doc.get("profile_name"),
    )


def _on_outbound(doc):
    phone = (doc.get("to") or "").strip()
    if not phone:
        return

    events.record(
        "Outbound Sent",
        phone,
        account=doc.get("whatsapp_account"),
        whatsapp_message=doc.name,
        request_id=doc.get("msg91_request_id"),
        template_name=doc.get("template"),
    )
