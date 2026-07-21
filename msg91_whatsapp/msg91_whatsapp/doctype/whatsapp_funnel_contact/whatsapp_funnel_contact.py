import frappe
from frappe.model.document import Document

# Funnel stages, top of funnel -> bottom. Contacts only ever move DOWN.
STAGE_ORDER = [
    "Outreach Sent",
    "Outreach Seen",
    "Interacted",
    "Warm Lead",
    "Hot Lead",
]


def stage_rank(stage):
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return 0


class WhatsAppFunnelContact(Document):
    def advance_to(self, target_stage):
        """Move the contact down-funnel only; never regress to an earlier stage."""
        if stage_rank(target_stage) > stage_rank(self.stage or STAGE_ORDER[0]):
            self.stage = target_stage

    @property
    def session_open(self):
        from frappe.utils import now_datetime, get_datetime

        if not self.session_expires_at:
            return False
        return get_datetime(self.session_expires_at) > now_datetime()
