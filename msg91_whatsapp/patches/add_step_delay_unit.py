"""Give campaign steps a unit, so a wait can be authored in minutes or hours.

`delay_hours` always meant hours, which is right for a real sequence and useless
for testing one: nobody wants to wait four hours to find out whether nudge two
fires. The value moves to `delay` and carries its unit alongside.
"""

import frappe


def execute():
    if not frappe.db.has_column("WhatsApp Campaign Step", "delay_hours"):
        return

    # Everything authored before this patch was in hours by definition.
    frappe.db.sql(
        """
        update `tabWhatsApp Campaign Step`
        set delay = ifnull(delay_hours, 0),
            delay_unit = 'Hours'
        where ifnull(delay, 0) = 0
        """
    )
