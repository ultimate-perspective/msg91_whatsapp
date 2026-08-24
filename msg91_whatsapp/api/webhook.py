"""Ingest MSG91's outbound delivery reports.

frappe_whatsapp's own webhook only understands Meta's callbacks, and it matches
on a Meta message id we never had, so nothing we send through MSG91 was ever
tracked past "submitted". This endpoint closes that gap: it is what makes
delivered / read / failed / clicked real signals instead of empty fields.

Configure it in MSG91 Dashboard > WhatsApp > Webhook (New) > Create Webhook,
event "On Outbound Report Received", pointing at the URL shown in MSG91 Settings.
"""

import datetime
import hmac
import json

import frappe
from frappe.utils import get_datetime, now_datetime

from msg91_whatsapp.funnel import events

# MSG91's eventName -> our event type. Anything not listed is ignored.
EVENT_MAP = {
    "sent": "Outbound Sent",
    "send": "Outbound Sent",
    "delivered": "Delivered",
    "read": "Read",
    "failed": "Failed",
    "clicked": "Clicked",
    "url_click": "Clicked",
    "urlclick": "Clicked",
}

# our event type -> the status string frappe_whatsapp shows on the message
MESSAGE_STATUS = {
    "Outbound Sent": "sent",
    "Delivered": "delivered",
    "Read": "read",
    "Failed": "failed",
}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def status(token=None, **kwargs):
    """MSG91 posts one payload per recipient, so each call is one event."""
    _authenticate(token)

    for report in _reports():
        _handle(report)

    return {"ok": True}


def _authenticate(token):
    expected = frappe.get_cached_doc("MSG91 Settings").get_password(
        "webhook_token", raise_exception=False
    )
    if not expected:
        frappe.throw("MSG91 Settings has no Webhook Token set.", frappe.PermissionError)
    if not token or not hmac.compare_digest(str(token), str(expected)):
        frappe.throw("Invalid webhook token.", frappe.PermissionError)


def _reports():
    """MSG91 posts a JSON object; be tolerant of a batched list or a form post."""
    data = frappe.request.get_data(as_text=True) if frappe.request else ""

    if data:
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]

    form = dict(frappe.local.form_dict or {})
    form.pop("cmd", None)
    form.pop("token", None)
    return [form] if form else []


def _handle(report):
    event_type = EVENT_MAP.get(str(report.get("eventName") or "").strip().lower())
    if not event_type:
        return

    phone = report.get("customerNumber")
    if not phone:
        return

    request_id = report.get("requestId")
    uuid = report.get("uuid")
    message = _find_message(request_id, uuid)

    events.record(
        event_type,
        phone,
        occurred_at=_timestamp(report),
        account=_account_for(report.get("integratedNumber")),
        whatsapp_message=message.name if message else None,
        request_id=request_id,
        template_name=report.get("templateName"),
        uuid=uuid,
        detail=_detail(event_type, report),
        payload=report,
        campaign=_campaign_for(request_id),
    )

    if message:
        _update_message(message, event_type, uuid)


def _timestamp(report):
    """MSG91 sends `ts`; format varies, so fall back to now rather than lose the event."""
    raw = report.get("ts") or report.get("requestedAt")
    if not raw:
        return now_datetime()

    if isinstance(raw, (int, float)) or str(raw).isdigit():
        seconds = int(raw)
        # Millisecond epochs show up too.
        if seconds > 10_000_000_000:
            seconds //= 1000
        return datetime.datetime.fromtimestamp(seconds)

    try:
        return get_datetime(raw)
    except Exception:
        return now_datetime()


def _campaign_for(request_id):
    """A delivery report belongs to whatever campaign made the original send."""
    if not request_id:
        return None
    return frappe.db.get_value(
        "WhatsApp Funnel Event",
        {"request_id": request_id, "event_type": "Outbound Sent"},
        "campaign",
    )


def _detail(event_type, report):
    if event_type == "Failed":
        return report.get("reason")
    if event_type == "Clicked":
        return report.get("url") or report.get("text")
    return None


def _account_for(integrated_number):
    if not integrated_number:
        return None
    return frappe.db.get_value(
        "WhatsApp Account", {"msg91_integrated_number": str(integrated_number)}, "name"
    )


def _find_message(request_id, uuid):
    """We stamp the request id on the message at send time; the wamid arrives later."""
    for field, value in (("msg91_request_id", request_id), ("message_id", uuid)):
        if not value:
            continue
        name = frappe.db.exists("WhatsApp Message", {field: value})
        if name:
            return frappe.get_doc("WhatsApp Message", name)
    return None


def _update_message(message, event_type, uuid):
    """Keep frappe_whatsapp's own status column honest, and backfill Meta's id."""
    updates = {}

    status_value = MESSAGE_STATUS.get(event_type)
    if status_value and message.status != status_value:
        updates["status"] = status_value

    # Until MSG91 tells us the wamid, message_id holds MSG91's request id.
    if uuid and message.message_id != uuid:
        updates["message_id"] = uuid

    if updates:
        message.db_set(updates, update_modified=False)
