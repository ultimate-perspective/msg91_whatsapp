"""One lead's trip through one campaign.

Deliberately mechanical. This doctype answers "where is the machine", never
"how interested is this person" -- that lives once, globally, on
`WhatsApp Funnel Contact`. A lead can hold many enrollments over time and they
do not argue with each other, because none of them owns an opinion.
"""

from frappe.model.document import Document


class WhatsAppCampaignEnrollment(Document):
    pass
