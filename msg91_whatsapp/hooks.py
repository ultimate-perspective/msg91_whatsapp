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

# Inbound messages open the 24h session window and advance the funnel.
doc_events = {
    "WhatsApp Message": {
        "after_insert": "msg91_whatsapp.funnel.signals.on_whatsapp_message",
    }
}

# Scheduled tasks (nudge engine - parked on the funnel-campaigns branch)
# scheduler_events = {
#     "cron": {
#         "*/15 * * * *": [
#             "msg91_whatsapp.funnel.scheduler.run_nudges",
#         ]
#     }
# }
