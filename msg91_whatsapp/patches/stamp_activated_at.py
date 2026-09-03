"""Give already-running campaigns a Started At, before enrolment starts reading it.

A campaign started before that field existed has no cutoff, and no cutoff means
auto-enrolment accepts anyone — including the whole back catalogue that the
hourly sweep is about to move as soon as a corrected rule replays their history.
Stamping the migration time is the honest reading of "from now on": nothing that
happened before this deploy can have happened after it.
"""

import frappe
from frappe.utils import now_datetime


def execute():
    if not frappe.db.has_column("WhatsApp Campaign", "activated_at"):
        return

    for name in frappe.get_all(
        "WhatsApp Campaign",
        filters={"status": "Active", "activated_at": ["is", "not set"]},
        pluck="name",
    ):
        frappe.db.set_value(
            "WhatsApp Campaign", name, "activated_at", now_datetime(), update_modified=False
        )
