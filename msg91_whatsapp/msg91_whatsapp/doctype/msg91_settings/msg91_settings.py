import frappe
from frappe.model.document import Document


class MSG91Settings(Document):
    @frappe.whitelist()
    def send_test(self):
        """Fire a free-form test message to `test_to` with `test_body`.

        Only succeeds if the recipient has an open 24h session window.
        """
        from msg91_whatsapp.api.send import send_session_text

        if not self.test_to or not self.test_body:
            frappe.throw("Set Test To and Test Body first.")
        return send_session_text(self.test_to, self.test_body)
