"""One user-authored rule: when X happens and Y holds, score it and/or state it."""

import frappe
from frappe.model.document import Document


class WhatsAppLeadRule(Document):
    def validate(self):
        if not self.score_delta and not self.set_state and not self.set_opted_out:
            frappe.throw("A rule must do something: set a score delta, a state, or opt-out.")


def active_rules(trigger=None, event_type=None):
    filters = {"enabled": 1}
    if trigger:
        filters["trigger"] = trigger
    if event_type:
        filters["event_type"] = event_type

    names = frappe.get_all(
        "WhatsApp Lead Rule", filters=filters, order_by="priority asc", pluck="name"
    )
    return [frappe.get_cached_doc("WhatsApp Lead Rule", name) for name in names]
