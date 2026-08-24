app_name = "msg91_whatsapp"
app_title = "MSG91 WhatsApp"
app_publisher = "Design Instantly"
app_description = "Send WhatsApp messages and run a lead-nudge funnel via MSG91"
app_email = "designinstantly@gmail.com"
app_license = "MIT"

# Route frappe_whatsapp's outbound messages through MSG91 (per WhatsApp Account).
override_doctype_class = {
    "WhatsApp Message": "msg91_whatsapp.overrides.whatsapp_message.MSG91WhatsAppMessage"
}

# Make the CRM's WhatsApp tab account-aware (per-number threads + window state)
# without forking frappe/crm. The extra argument is optional, so the stock
# frontend keeps working unchanged.
override_whitelisted_methods = {
    "crm.api.whatsapp.get_whatsapp_messages": "msg91_whatsapp.api.crm.get_whatsapp_messages",
    "crm.api.whatsapp.create_whatsapp_message": "msg91_whatsapp.api.crm.create_whatsapp_message",
    "crm.api.whatsapp.send_whatsapp_template": "msg91_whatsapp.api.crm.send_whatsapp_template",
}

# Every message, either direction, becomes a funnel event. Inbound also
# reopens the 24h session window.
doc_events = {
    "WhatsApp Message": {
        "after_insert": "msg91_whatsapp.funnel.signals.on_whatsapp_message",
    }
}

# Time-based rules ("gone quiet for 14 days") have no event to ride in on, so
# the whole book gets re-scored on a sweep. Hourly is plenty for a funnel whose
# fastest rule is measured in days.
scheduler_events = {
    # Nudges are checked often enough that a "wait 2 hours" step means roughly
    # two hours. Nothing is sent unless a campaign is Active and a step is due.
    "cron": {
        "*/15 * * * *": [
            "msg91_whatsapp.funnel.campaigns.run",
        ]
    },
    "hourly_long": [
        "msg91_whatsapp.funnel.engine.sweep",
    ],
}
