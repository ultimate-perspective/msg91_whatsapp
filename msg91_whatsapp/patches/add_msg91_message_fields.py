"""Stamp MSG91's request id onto frappe_whatsapp's WhatsApp Message.

MSG91 returns a `request_id` on send and echoes it back on every later status
callback. Without somewhere to keep it, delivered/read/failed reports have
nothing to attach to.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
    "WhatsApp Message": [
        {
            "fieldname": "msg91_request_id",
            "fieldtype": "Data",
            "label": "MSG91 Request ID",
            "insert_after": "message_id",
            "read_only": 1,
            "search_index": 1,
            "description": "Correlates this message with MSG91's delivery reports.",
        }
    ]
}


def execute():
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
