"""MSG91 WhatsApp sender.

Two entry points, two different MSG91 endpoints:

- ``send_template``     -> approved template message via the ``/bulk/`` endpoint.
                           Works OUTSIDE the 24h session window (business-
                           initiated). Nested ``payload`` schema. Used for the
                           ``outreach_seen`` and re-engagement funnel stages.
- ``send_session_text`` -> free-form text via the non-bulk
                           ``/whatsapp-outbound-message/`` endpoint. Only valid
                           INSIDE the 24h session window. Flat schema
                           (``recipient_number`` + ``text``). Used for the
                           ``interacted`` / ``warm`` stages.

The auth key is read from the encrypted ``MSG91 Settings`` password field and is
never hard-coded.
"""

import frappe
import requests

LOGGER = "msg91_whatsapp"


def _settings():
    settings = frappe.get_single("MSG91 Settings")
    if not settings.enabled:
        frappe.throw("MSG91 Settings is not enabled.")
    if not settings.integrated_number:
        frappe.throw("MSG91 Settings is missing the Integrated Number.")
    return settings


def _base(settings):
    return (settings.base_url or "https://api.msg91.com/api/v5").rstrip("/")


def _bulk_endpoint(settings):
    """Template messages (business-initiated)."""
    return f"{_base(settings)}/whatsapp/whatsapp-outbound-message/bulk/"


def _session_endpoint(settings):
    """Free-form session messages (inside the 24h window)."""
    return f"{_base(settings)}/whatsapp/whatsapp-outbound-message/"


def _headers(auth_key):
    return {"Content-Type": "application/json", "authkey": auth_key}


@frappe.whitelist(methods=["POST"])
def send_template(to, template_name, components=None, language=None):
    """Send an approved WhatsApp template through MSG91's /bulk/ endpoint.

    :param to: recipient number, digits only with country code (e.g. 9198...).
    :param template_name: approved MSG91 template name (e.g. ``test_1``).
    :param components: dict of component values, e.g.
        ``{"body_1": {"type": "text", "value": "Dhruvil"}}``. Supports
        ``header_1``/``body_N``/``button_N`` for any header/body/button param.
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
    return _post(settings, _bulk_endpoint(settings), payload)


@frappe.whitelist(methods=["POST"])
def send_session_text(to, body):
    """Send a free-form text message via MSG91's non-bulk endpoint.

    Only valid INSIDE the 24h session window (after the customer has messaged
    us). Outside the window MSG91/Meta rejects it — fall back to a template.
    """
    settings = _settings()
    payload = {
        "integrated_number": settings.integrated_number,
        "recipient_number": str(to),
        "content_type": "text",
        "text": body,
    }
    return _post(settings, _session_endpoint(settings), payload)


def _post(settings, url, payload):
    auth_key = settings.get_password("auth_key")
    if not auth_key:
        frappe.throw("MSG91 Settings is missing the Auth Key.")

    try:
        resp = requests.post(url, headers=_headers(auth_key), json=payload, timeout=30)
    except requests.RequestException as exc:
        frappe.log_error(frappe.get_traceback(), "MSG91 send request failed")
        frappe.throw(f"MSG91 request failed: {exc}")

    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text}

    frappe.logger(LOGGER).info({"url": url, "status": resp.status_code, "response": data})

    if resp.status_code >= 400 or (isinstance(data, dict) and data.get("hasError")):
        frappe.throw(f"MSG91 send failed ({resp.status_code}): {resp.text}")

    return data
