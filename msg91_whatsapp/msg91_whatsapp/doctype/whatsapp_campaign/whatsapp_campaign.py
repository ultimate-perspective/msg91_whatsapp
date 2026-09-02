"""A campaign: who to message, what to open with, and the nudges that follow.

Starting one is an explicit act. Nothing is ever sent from a Draft, which is the
only reason it is safe to keep the runner on a schedule.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from msg91_whatsapp.funnel import campaigns


def _describe(hours):
    """Cumulative offsets read better as minutes when small and days when big."""
    if hours <= 0:
        return "immediately"
    if hours < 1:
        return f"{hours * 60:g} min after enrolment"
    if hours < 48:
        return f"{hours:g}h after enrolment"
    days = hours / 24
    return f"day {days:g} ({hours:g}h)"


class WhatsAppCampaign(Document):
    def validate(self):
        self._validate_steps()
        self._validate_audience()
        self._warn_about_problems()

    def _warn_about_problems(self):
        """Say it on save, so it is not a surprise at Start."""
        if self.status == "Active":
            return
        problems = campaigns.campaign_problems(self)
        if problems:
            frappe.msgprint(
                "<ul>" + "".join(f"<li>{p}</li>" for p in problems) + "</ul>",
                title="This campaign cannot start yet",
                indicator="orange",
            )

    def _validate_steps(self):
        for index, step in enumerate(self.steps, start=1):
            if not step.step_name:
                step.step_name = f"Step {index}"
            if step.delay is not None and step.delay < 0:
                frappe.throw(f"{step.step_name}: wait cannot be negative.")
            if (step.message_type or "Template") == "Free Form":
                self._warn_if_window_unlikely(step)
            if (
                step.min_score
                and step.max_score
                and step.min_score > step.max_score
            ):
                frappe.throw(
                    f"{step.step_name}: minimum score is above the maximum, so it can never send."
                )

    def _warn_if_window_unlikely(self, step):
        """A free-form step past the 24h mark can never send, so say it on save.

        The wait is cumulative, and the window runs from the customer's last
        message, so a step scheduled beyond 24 hours of silence is dead unless
        they reply again in the meantime.
        """
        elapsed = sum(
            campaigns.delay_in_hours(other)
            for other in self.steps
            if other.enabled and other.idx <= step.idx
        )
        if elapsed < 24:
            return

        fallback = step.if_window_closed or "Skip Step"
        if fallback == "Send Template Instead":
            return

        frappe.msgprint(
            f"{step.step_name} lands {elapsed:g}h after enrolment, but the 24-hour "
            "window will normally have closed by then, so a free-form message "
            "cannot be delivered. Either move it earlier or set it to fall back "
            "to a template.",
            title="This nudge may never send",
            indicator="orange",
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

        problems = campaigns.campaign_problems(self)
        if problems:
            frappe.throw(
                "<ul>" + "".join(f"<li>{p}</li>" for p in problems) + "</ul>",
                title="Fix these before starting",
            )

        if not self.steps:
            frappe.msgprint(
                "This campaign has no nudges, so it will send the outreach template and stop.",
                indicator="orange",
                title="No steps",
            )

        if self.enroll_mode == "On Entering State":
            frappe.msgprint(
                f"Anyone who reaches <b>{self.enroll_on_state}</b> from now on will be "
                "enrolled automatically, as long as they message you after this moment. "
                "People already sitting in that state, and anyone whose last message "
                "predates now, are left alone.",
                indicator="blue",
                title="Enrolment is live",
            )

        # The cutoff for auto-enrolment: anyone whose last message predates this
        # is history, not a new lead.
        self.db_set("activated_at", now_datetime(), update_modified=False)
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
    def run_now(self):
        """Process anything due on this campaign now, rather than at the next tick.

        The scheduler runs every 15 minutes, which is invisible on an hours-long
        sequence and painful on a minutes-long test of one.
        """
        if self.status != "Active":
            frappe.throw("Only an Active campaign has anything to run.")
        campaigns.run_now(self.name)

    @frappe.whitelist()
    def enroll_current_state(self):
        """Add everyone already sitting in the trigger state, not just new arrivals."""
        if self.enroll_mode != "On Entering State":
            frappe.throw("This campaign does not enrol by state.")
        return campaigns.enroll_current_state(self.name)

    @frappe.whitelist()
    def test_send(self, lead):
        """Send the first touch to one lead, changing nothing else."""
        return campaigns.send_test(self.name, lead)

    @frappe.whitelist()
    def timeline(self):
        """When each step actually lands, counted from enrolment."""
        rows, offset = [], 0.0
        rows.append(
            {
                "step": "First touch",
                "sends": self.outreach_template or "nothing (silent enrolment)",
                "after": "immediately" if self.outreach_template else "-",
                "billed": bool(self.outreach_template),
            }
        )
        for index, step in enumerate(self.steps, start=1):
            if not step.enabled:
                continue
            offset += campaigns.delay_in_hours(step)
            free_form = (step.message_type or "Template") == "Free Form"
            rows.append(
                {
                    "step": step.step_name or f"Step {index}",
                    "sends": step.message_text if free_form else step.template,
                    "after": _describe(offset),
                    "condition": step.send_if,
                    "billed": not free_form,
                }
            )
        return rows

    @frappe.whitelist()
    def preview_audience(self):
        """How many leads the filter currently matches, without enrolling anyone."""
        if self.enroll_mode != "Saved Filter":
            return {"count": 0, "mode": self.enroll_mode}

        filters = frappe.parse_json(self.audience_filter or "{}")
        return {"count": frappe.db.count("CRM Lead", filters), "mode": self.enroll_mode}
