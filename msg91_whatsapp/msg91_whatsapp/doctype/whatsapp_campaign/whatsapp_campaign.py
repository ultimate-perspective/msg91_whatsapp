"""A campaign: who to message, what to open with, and the nudges that follow.

Starting one is an explicit act. Nothing is ever sent from a Draft, which is the
only reason it is safe to keep the runner on a schedule.
"""

import frappe
from frappe.model.document import Document

from msg91_whatsapp.funnel import campaigns


class WhatsAppCampaign(Document):
    def validate(self):
        self._validate_steps()
        self._validate_audience()

    def _validate_steps(self):
        for index, step in enumerate(self.steps, start=1):
            if not step.step_name:
                step.step_name = f"Step {index}"
            if step.delay_hours is not None and step.delay_hours < 0:
                frappe.throw(f"{step.step_name}: wait cannot be negative.")
            if (
                step.min_score
                and step.max_score
                and step.min_score > step.max_score
            ):
                frappe.throw(
                    f"{step.step_name}: minimum score is above the maximum, so it can never send."
                )

    def _validate_audience(self):
        if self.enroll_mode != "Saved Filter":
            return
        try:
            frappe.parse_json(self.audience_filter or "{}")
        except Exception:
            frappe.throw("Audience Filter is not valid JSON.")

    @frappe.whitelist()
    def start(self):
        """Enrol the audience and let the runner take over."""
        if self.status == "Active":
            frappe.throw("This campaign is already running.")

        if not self.steps:
            frappe.msgprint(
                "This campaign has no nudges, so it will send the outreach template and stop.",
                indicator="orange",
                title="No steps",
            )

        self.db_set("status", "Active", update_modified=False)

        enrolled = campaigns.enroll_audience(self.name) if self.enroll_mode == "Saved Filter" else 0
        queued = frappe.db.count(
            "WhatsApp Campaign Enrollment", {"campaign": self.name, "status": "Queued"}
        )

        return {"enrolled": enrolled, "queued": queued}

    @frappe.whitelist()
    def pause(self):
        """Stop sending. Enrollments keep their place and resume where they left off."""
        self.db_set("status", "Paused", update_modified=False)

    @frappe.whitelist()
    def resume(self):
        self.db_set("status", "Active", update_modified=False)

    @frappe.whitelist()
    def enroll_lead(self, lead):
        """Add one lead by hand. Works whether or not the campaign is running."""
        enrollment = campaigns.enroll(self.name, lead=lead)
        if not enrollment:
            frappe.throw(
                "Not enrolled: either they are already in this campaign, they have "
                "opted out, or the lead has no phone number."
            )
        return enrollment.name

    @frappe.whitelist()
    def preview_audience(self):
        """How many leads the filter currently matches, without enrolling anyone."""
        if self.enroll_mode != "Saved Filter":
            return {"count": 0, "mode": self.enroll_mode}

        filters = frappe.parse_json(self.audience_filter or "{}")
        return {"count": frappe.db.count("CRM Lead", filters), "mode": self.enroll_mode}
