"""Turn on the human handoff for Hot Lead on sites seeded before it existed.

A hot lead that nobody is told about is just a row in a list, and the whole
point of scoring is that a person picks up the phone. Only touches the state if
it still looks like the one we seeded.
"""

import frappe


def execute():
    if not frappe.db.exists("WhatsApp Lead State", "Hot Lead"):
        return

    state = frappe.get_doc("WhatsApp Lead State", "Hot Lead")
    if state.notify_on_entry:
        return

    state.notify_on_entry = 1
    if not state.handoff_note:
        state.handoff_note = "Hot on WhatsApp. Call them."
    state.save(ignore_permissions=True)
