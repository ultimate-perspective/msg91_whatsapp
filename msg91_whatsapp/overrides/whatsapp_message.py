"""Route frappe_whatsapp sends through MSG91 instead of Meta's Graph API.

frappe_whatsapp funnels every outbound message (template and free-form alike)
through ``WhatsAppMessage.notify()``. By overriding just that one method we keep
its entire UI, template store and conversation view, and only swap the transport.

Per-number: a ``WhatsApp Account`` with ``msg91_enabled`` goes out via MSG91
using its own ``msg91_integrated_number``; any other account still goes direct to
Meta via the parent implementation.
"""

import frappe
from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
    WhatsAppMessage,
)

from msg91_whatsapp.api import client, translate
from msg91_whatsapp.msg91_whatsapp.doctype.whatsapp_session.whatsapp_session import (
    get_active_account,
    is_window_open,
    touch_outbound,
)


class MSG91WhatsAppMessage(WhatsAppMessage):
    def before_insert(self):
        self._bind_conversation_account()
        super().before_insert()

    def _bind_conversation_account(self):
        """Keep replies on the number the conversation already started on."""
        if self.type == "Outgoing" and not self.whatsapp_account and self.to:
            account = get_active_account(self.to)
            if account:
                self.whatsapp_account = account

    def notify(self, data):
        account = frappe.get_doc("WhatsApp Account", self.whatsapp_account)

        if not account.get("msg91_enabled"):
            return super().notify(data)

        integrated_number = account.get("msg91_integrated_number")
        if not integrated_number:
            frappe.throw(
                f"WhatsApp Account {account.name} has MSG91 enabled but no "
                "MSG91 Integrated Number set."
            )

        is_template = translate.is_template(data)
        if not is_template:
            self._guard_session_window(account)

        payload = translate.to_msg91(data, integrated_number)
        response = client.post(payload, template=is_template)

        self.message_id = client.extract_message_id(response)
        self._record_outbound(account)
        return response

    def _guard_session_window(self, account):
        """Free-form messages are only legal inside the customer's 24h window."""
        if is_window_open(self.to, account.name):
            return
        frappe.throw(
            f"The 24-hour reply window for {self.to} is closed. "
            "Send an approved template message instead."
        )

    def _record_outbound(self, account):
        try:
            touch_outbound(self.to, account.name)
        except Exception:
            # Never fail a delivered message because bookkeeping failed.
            frappe.log_error(frappe.get_traceback(), "MSG91: outbound session update failed")
