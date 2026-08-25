"""A funnel state, defined by the user rather than hardcoded by us."""

import frappe
from frappe.model.document import Document


class WhatsAppLeadState(Document):
    def validate(self):
        if self.is_terminal:
            # A terminal state is entered by an explicit rule, so a score band
            # would only ever fight with that rule.
            self.min_score = 0
            self.allow_regression = 0


def by_rank(enabled_only=True):
    filters = {"enabled": 1} if enabled_only else {}
    return frappe.get_all(
        "WhatsApp Lead State",
        filters=filters,
        fields=[
            "name",
            "rank",
            "min_score",
            "is_terminal",
            "allow_regression",
            "maps_to_status",
            "maps_to_lost_reason",
            "notify_on_entry",
        ],
        order_by="rank asc",
    )
