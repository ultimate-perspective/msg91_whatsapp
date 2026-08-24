"""One row per customer number: identity, cached signals, current state.

The state itself is no longer ours to decide. It is resolved by
`msg91_whatsapp.funnel.engine` from the states and rules the user defines.
"""

from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


class WhatsAppFunnelContact(Document):
    @property
    def session_open(self):
        if not self.session_expires_at:
            return False
        return get_datetime(self.session_expires_at) > now_datetime()
