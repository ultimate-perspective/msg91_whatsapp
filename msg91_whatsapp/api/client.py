"""Low-level MSG91 HTTP client — the single place we talk to MSG91."""

import frappe
import requests

LOGGER = "msg91_whatsapp"

BULK_PATH = "/whatsapp/whatsapp-outbound-message/bulk/"
SESSION_PATH = "/whatsapp/whatsapp-outbound-message/"
DEFAULT_BASE_URL = "https://api.msg91.com/api/v5"


def get_settings():
    return frappe.get_single("MSG91 Settings")


def base_url(settings=None):
    settings = settings or get_settings()
    return (settings.base_url or DEFAULT_BASE_URL).rstrip("/")


def auth_key(settings=None):
    settings = settings or get_settings()
    key = settings.get_password("auth_key")
    if not key:
        frappe.throw("MSG91 Settings is missing the Auth Key.")
    return key


def endpoint(template, settings=None):
    return base_url(settings) + (BULK_PATH if template else SESSION_PATH)


def post(payload, template=True, settings=None):
    """POST a payload to MSG91 and return the parsed response.

    :param template: True for template sends (bulk endpoint), False for
        free-form session sends (non-bulk endpoint).
    """
    settings = settings or get_settings()
    url = endpoint(template, settings)
    headers = {"Content-Type": "application/json", "authkey": auth_key(settings)}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
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


def extract_message_id(response):
    """MSG91 returns a request_id; fall back to any message id it exposes."""
    if not isinstance(response, dict):
        return ""
    return (
        response.get("request_id")
        or response.get("message_id")
        or (response.get("data") if isinstance(response.get("data"), str) else "")
        or ""
    )
