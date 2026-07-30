"""Add per-number MSG91 configuration to frappe_whatsapp's WhatsApp Account.

Every number is linked through MSG91, so the only per-account setting we need is
which MSG91 integrated number this account sends from.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
    "WhatsApp Account": [
        {
            "fieldname": "msg91_section",
            "fieldtype": "Section Break",
            "label": "MSG91",
            "insert_after": "allow_auto_read_receipt",
        },
        {
            "fieldname": "msg91_integrated_number",
            "fieldtype": "Data",
            "label": "MSG91 Integrated Number",
            "description": "Digits only, with country code. e.g. 919924859743",
            "insert_after": "msg91_section",
        },
    ]
}


def execute():
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
