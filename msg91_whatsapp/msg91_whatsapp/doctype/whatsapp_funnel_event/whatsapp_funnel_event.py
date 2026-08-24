"""Append-only log of everything that happens to a contact.

Every funnel decision derives from these rows rather than from mutable fields on
the contact, which buys three things:

- rules can be re-evaluated (replayed) after the user edits them
- "why is this lead Hot?" has a literal answer
- time-based questions ("nothing for 7 days") are queryable
"""

from frappe.model.document import Document


class WhatsAppFunnelEvent(Document):
    pass
