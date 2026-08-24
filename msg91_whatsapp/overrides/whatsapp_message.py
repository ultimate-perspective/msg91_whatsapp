"""Route frappe_whatsapp sends through MSG91 instead of Meta's Graph API.

frappe_whatsapp funnels every outbound message (template and free-form alike)
through ``WhatsAppMessage.notify()``. By overriding just that one method we keep
its entire UI, template store and conversation view, and only swap the transport.

Every number is linked through MSG91, so MSG91 is the only transport — each
``WhatsApp Account`` sends from its own ``msg91_integrated_number``.
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
        """Decide which of our numbers this message goes out from.

        A customer can hold concurrent conversations on several of our numbers,
        so precedence is: explicitly set on the doc > the number the CRM UI
        picked (flag) > the number this customer is already talking to.
        """
        if self.type != "Outgoing" or self.whatsapp_account:
            return

        chosen = frappe.flags.get("msg91_whatsapp_account")
        if chosen:
            self.whatsapp_account = chosen
            return

        if self.to:
            account = get_active_account(self.to)
            if account:
                self.whatsapp_account = account

    def notify(self, data):
        account = frappe.get_doc("WhatsApp Account", self.whatsapp_account)

        integrated_number = account.get("msg91_integrated_number")
        if not integrated_number:
            frappe.throw(
                f"WhatsApp Account {account.name} has no MSG91 Integrated Number set."
            )

        is_template = translate.is_template(data)
        if not is_template:
            self._guard_session_window(account)

        payload = translate.to_msg91(data, integrated_number)
        response = client.post(payload, template=is_template)

        # `notify` runs inside before_insert, so these stick without an extra
        # write. The funnel event is emitted later, from the after_insert hook,
        # once the doc actually has a name to point at.
        request_id = client.extract_message_id(response)
        self.message_id = request_id
        self.msg91_request_id = request_id
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
