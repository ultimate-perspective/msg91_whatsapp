"""MSG91 WhatsApp sender.

Two entry points:

- ``send_template``     -> approved template message. Works OUTSIDE the 24h
                           session window (business-initiated). Used for the
                           ``outreach_seen`` and re-engagement funnel stages.
- ``send_session_text`` -> free-form text. Only valid INSIDE the 24h session
                           window (i.e. after the user has messaged us). Used
                           for the ``interacted`` / ``warm`` stages.

The exact MSG91 payload schema is confirmed against the account's dashboard on
first live send; the template shape below follows MSG91's v5 bulk outbound API.
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


def _endpoint(settings):
    base = (settings.base_url or "https://api.msg91.com/api/v5").rstrip("/")
    return f"{base}/whatsapp/whatsapp-outbound-message/bulk/"


def _headers(auth_key):
    return {"Content-Type": "application/json", "authkey": auth_key}


@frappe.whitelist()
def send_template(to, template_name, components=None, language=None):
    """Send an approved WhatsApp template through MSG91.

    :param to: recipient number, digits only with country code (e.g. 9198...).
    :param template_name: approved MSG91 template name (e.g. ``test_1``).
    :param components: dict of component values, e.g.
        ``{"body_1": {"type": "text", "value": "Dhruvil"}}``.
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
                    "code": language or settings.default_language or "en",
                    "policy": "deterministic",
                },
                "namespace": settings.namespace,
                "to_and_components": [{"to": [str(to)], "components": components or {}}],
            },
        },
    }
    return _post(settings, payload)


@frappe.whitelist()
def send_session_text(to, body):
    """Send a free-form text message (only valid inside the 24h session window)."""
    settings = _settings()
    payload = {
        "integrated_number": settings.integrated_number,
        "content_type": "text",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "text",
            "text": {"body": body},
            "to": [str(to)],
        },
    }
    return _post(settings, payload)


def _post(settings, payload):
    auth_key = settings.get_password("auth_key")
    if not auth_key:
        frappe.throw("MSG91 Settings is missing the Auth Key.")

    url = _endpoint(settings)
    try:
        resp = requests.post(url, headers=_headers(auth_key), json=payload, timeout=30)
    except requests.RequestException as exc:
        frappe.log_error(frappe.get_traceback(), "MSG91 send request failed")
        frappe.throw(f"MSG91 request failed: {exc}")

    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text}

    frappe.logger(LOGGER).info(
        {"url": url, "status": resp.status_code, "to": payload.get("payload", {}).get("to"), "response": data}
    )

    if resp.status_code >= 400 or (isinstance(data, dict) and data.get("hasError")):
        frappe.throw(f"MSG91 send failed ({resp.status_code}): {resp.text}")

    return data
