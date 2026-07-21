import frappe
from frappe.model.document import Document


class MSG91Settings(Document):
    @frappe.whitelist()
    def send_test(self):
        """Fire a test template message to `test_to` using `test_template`.

        Templates send via MSG91's /bulk/ endpoint and work regardless of the
        24h session window.
        """
        from msg91_whatsapp.api.send import send_template

        if not self.test_to or not self.test_template:
            frappe.throw("Set Test To and Test Template first.")

        components = frappe.parse_json(self.test_components) if self.test_components else None
        return send_template(
            self.test_to,
            self.test_template,
            components=components,
            language=self.default_language,
        )
