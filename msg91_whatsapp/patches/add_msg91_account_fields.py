"""Add per-number MSG91 configuration to frappe_whatsapp's WhatsApp Account.

Each business number decides its own transport: tick ``msg91_enabled`` to route
that number's sends through MSG91, otherwise it keeps going direct to Meta.
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
            "fieldname": "msg91_enabled",
            "fieldtype": "Check",
            "label": "Send via MSG91",
            "default": "0",
            "description": (
                "Route this number's outbound messages through MSG91 instead of "
                "Meta's Graph API."
            ),
            "insert_after": "msg91_section",
        },
        {
            "fieldname": "msg91_integrated_number",
            "fieldtype": "Data",
            "label": "MSG91 Integrated Number",
            "depends_on": "eval:doc.msg91_enabled",
            "description": "Digits only, with country code. e.g. 919924859743",
            "insert_after": "msg91_enabled",
        },
    ]
}


def execute():
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
