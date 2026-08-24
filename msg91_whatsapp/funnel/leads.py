"""Resolve a WhatsApp number to the CRM Lead it belongs to.

Numbers reach us in whatever shape the customer's handset sends them, and the
CRM stores whatever the sales rep typed, so exact matching is hopeless. We match
on the last 10 digits, which is stable across "+91 99248 59743", "919924859743"
and "9924859743".
"""

import frappe

from msg91_whatsapp.utils import phone_key

PHONE_FIELDS = ("mobile_no", "phone")


def find_lead(phone):
    """Return the name of the best-matching CRM Lead, or None.

    Prefers an open lead over a converted one, then the most recently touched,
    so an active conversation wins over a stale duplicate.
    """
    key = phone_key(phone)
    if len(key) < 10:
        return None

    conditions = " or ".join(f"`{field}` like %(pattern)s" for field in PHONE_FIELDS)
    rows = frappe.db.sql(
        f"""
        select name
        from `tabCRM Lead`
        where {conditions}
        order by converted asc, modified desc
        limit 1
        """,
        {"pattern": f"%{key}"},
    )
    return rows[0][0] if rows else None


def lead_for_contact(contact):
    """Resolve once and remember it on the contact.

    Cheap on the hot path (every inbound message and status callback), and it
    also gives a human somewhere to correct a bad match by hand.
    """
    if contact.get("lead"):
        return contact.lead

    lead = find_lead(contact.phone)
    if lead:
        contact.lead = lead
    return lead
