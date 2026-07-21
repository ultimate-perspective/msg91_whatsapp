app_name = "msg91_whatsapp"
app_title = "MSG91 WhatsApp"
app_publisher = "Design Instantly"
app_description = "Send WhatsApp messages and run a lead-nudge funnel via MSG91"
app_email = "designinstantly@gmail.com"
app_license = "MIT"

# ------------------------------------------------------------------------------
# The sections below are intentionally left as commented placeholders. They get
# filled in over the next phases of the build:
#
#   P3 - ingest MSG91 delivery/read webhook -> outreach_seen
#   P4 - nudge scheduler
#
# ------------------------------------------------------------------------------

# Scheduled tasks (P4 - nudge engine)
# scheduler_events = {
#     "cron": {
#         "*/15 * * * *": [
#             "msg91_whatsapp.funnel.scheduler.run_nudges",
#         ]
#     }
# }

# Document events (P2 - map inbound WhatsApp Message -> interacted)
# doc_events = {
#     "WhatsApp Message": {
#         "after_insert": "msg91_whatsapp.funnel.signals.on_whatsapp_message",
#     }
# }
