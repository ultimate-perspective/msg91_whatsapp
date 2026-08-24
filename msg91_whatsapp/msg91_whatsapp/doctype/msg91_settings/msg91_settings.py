import frappe
from frappe.model.document import Document


class MSG91Settings(Document):
    def validate(self):
        self.set_webhook_url()

    def set_webhook_url(self):
        """Show the callback URL to paste into MSG91, token included."""
        if not self.get("webhook_token"):
            self.webhook_url = None
            return

        # While the form is being saved the field holds either the freshly
        # typed value or a row of asterisks standing in for the stored one.
        token = self.webhook_token
        if not token or set(token) == {"*"}:
            token = self.get_password("webhook_token", raise_exception=False)
        self.webhook_url = (
            f"{frappe.utils.get_url()}"
            "/api/method/msg91_whatsapp.api.webhook.status"
            f"?token={token}"
        )

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
