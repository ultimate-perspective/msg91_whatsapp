"""Direct MSG91 send helpers (used for testing and programmatic sends).

Normal CRM sending goes through frappe_whatsapp's ``WhatsApp Message`` doctype,
whose transport is overridden in ``msg91_whatsapp.overrides.whatsapp_message``.
These helpers talk to MSG91 directly, bypassing that record — handy for the
settings smoke test and for scripted sends.

- ``send_template``     -> approved template via MSG91's /bulk/ endpoint. Works
                           outside the 24h window (business-initiated).
- ``send_session_text`` -> free-form text via the non-bulk endpoint. Only valid
                           inside the 24h session window.
"""

import frappe

from msg91_whatsapp.api import client


def _settings():
    settings = client.get_settings()
    if not settings.enabled:
        frappe.throw("MSG91 Settings is not enabled.")
    if not settings.integrated_number:
        frappe.throw("MSG91 Settings is missing the Integrated Number.")
    return settings


@frappe.whitelist(methods=["POST"])
def send_template(to, template_name, components=None, language=None):
    """Send an approved WhatsApp template through MSG91.

    :param to: recipient number, digits only with country code (e.g. 9198...).
    :param template_name: approved MSG91 template name (e.g. ``test_1``).
    :param components: dict of template params, e.g.
        ``{"body_1": {"type": "text", "value": "Dhruvil"}}``. Supports
        ``header_1`` / ``body_N`` / ``button_N``.
    :param language: template language code; falls back to the configured default.
    """
    settings = _settings()
    if isinstance(components, str):
        components = frappe.parse_json(components)

    payload = {
        "integrated_number": settings.integrated_number,
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": language or settings.default_language or "en_US",
                    "policy": "deterministic",
                },
                "namespace": settings.namespace,
                "to_and_components": [{"to": [str(to)], "components": components or {}}],
            },
        },
    }
    return client.post(payload, template=True, settings=settings)


@frappe.whitelist(methods=["POST"])
def send_session_text(to, body):
    """Send a free-form text message (only valid inside the 24h session window)."""
    settings = _settings()
    payload = {
        "integrated_number": settings.integrated_number,
        "recipient_number": str(to),
        "content_type": "text",
        "text": body,
    }
    return client.post(payload, template=False, settings=settings)
