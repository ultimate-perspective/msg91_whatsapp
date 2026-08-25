"""Campaign enrollment and the nudge runner.

Campaigns are the only thing that sends. There is no ad-hoc automated message
and nothing goes out before a campaign is started, which keeps every outbound
message attributable to something a human switched on.

The split that matters: this module owns *journey* (which step, when next), and
nothing else. How interested a contact is stays global, on the funnel contact,
decided by the rule engine. A lead can run through many campaigns over time
without those campaigns ever disagreeing about who they are.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import add_to_date, cint, get_datetime, get_time, now_datetime
from frappe.utils.file_lock import LockTimeoutError
from frappe.utils.synchronization import filelock

from msg91_whatsapp.funnel import contacts
from msg91_whatsapp.utils import normalize_phone

ENROLLMENT = "WhatsApp Campaign Enrollment"
OPEN_STATUSES = ("Queued", "Active", "Waiting")
BATCH_SIZE = 200


# --------------------------------------------------------------------------
# enrollment
# --------------------------------------------------------------------------

def enroll(campaign, lead=None, phone=None):
    """Put one lead into a campaign. Idempotent per (campaign, contact)."""
    campaign_doc = frappe.get_cached_doc("WhatsApp Campaign", campaign)

    phone = normalize_phone(phone or _phone_for_lead(lead))
    if not phone:
        return None

    contact = contacts.get_or_create(phone)
    if not contact.name or not frappe.db.exists(contacts.DOCTYPE, contact.name):
        contact.insert(ignore_permissions=True)

    if contact.opted_out:
        return None

    if frappe.db.exists(ENROLLMENT, {"campaign": campaign, "contact": contact.name}):
        return None

    enrollment = frappe.new_doc(ENROLLMENT)
    enrollment.update(
        {
            "campaign": campaign,
            "contact": contact.name,
            "phone": phone,
            "lead": lead or contact.get("lead"),
            "status": "Queued",
            "entered_at": now_datetime(),
            "next_action_at": _start_time(campaign_doc),
        }
    )
    enrollment.insert(ignore_permissions=True)

    frappe.db.set_value(
        "WhatsApp Campaign",
        campaign,
        "enrolled_count",
        cint(frappe.db.get_value("WhatsApp Campaign", campaign, "enrolled_count")) + 1,
        update_modified=False,
    )
    return enrollment


def enroll_audience(campaign):
    """Enrol every CRM Lead matching the campaign's filter. Runs once, at start."""
    campaign_doc = frappe.get_cached_doc("WhatsApp Campaign", campaign)
    if campaign_doc.enroll_mode != "Saved Filter":
        return 0

    filters = frappe.parse_json(campaign_doc.audience_filter or "{}")
    enrolled = 0
    for lead in frappe.get_all("CRM Lead", filters=filters, pluck="name"):
        if enroll(campaign, lead=lead):
            enrolled += 1
    return enrolled


def _phone_for_lead(lead):
    if not lead:
        return None
    row = frappe.db.get_value("CRM Lead", lead, ["mobile_no", "phone"], as_dict=True)
    return row and (row.mobile_no or row.phone)


def _start_time(campaign_doc):
    start = get_datetime(campaign_doc.start_on) if campaign_doc.start_on else now_datetime()
    return max(start, now_datetime())


# --------------------------------------------------------------------------
# the runner
# --------------------------------------------------------------------------

def run():
    """Scheduled entry point. Walks every active campaign's due enrollments.

    Single-flight. A slow run must never overlap the next tick, because two
    workers holding the same enrollment would each send its step, and the
    customer would get the same nudge twice.
    """
    try:
        with filelock("msg91_campaign_runner", timeout=1):
            _run()
    except LockTimeoutError:
        # The previous tick is still going. Nothing to do; it will catch up.
        return


def _run():
    for name in frappe.get_all(
        "WhatsApp Campaign",
        filters={"status": "Active"},
        order_by="priority asc",
        pluck="name",
    ):
        try:
            run_campaign(name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"MSG91: campaign run failed for {name}")
        frappe.db.commit()


def run_campaign(campaign):
    campaign_doc = frappe.get_doc("WhatsApp Campaign", campaign)

    if campaign_doc.end_on and get_datetime(campaign_doc.end_on) < now_datetime():
        _close(campaign_doc)
        return

    due = frappe.get_all(
        ENROLLMENT,
        filters={
            "campaign": campaign,
            "status": ["in", OPEN_STATUSES],
            "next_action_at": ["<=", now_datetime()],
        },
        order_by="next_action_at asc",
        limit=BATCH_SIZE,
        pluck="name",
    )

    for name in due:
        try:
            _process(frappe.get_doc(ENROLLMENT, name), campaign_doc)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"MSG91: enrollment {name} failed")


def _process(enrollment, campaign_doc):
    contact = frappe.get_doc("WhatsApp Funnel Contact", enrollment.contact)

    reason = _exit_reason(contact, campaign_doc)
    if reason:
        _exit(enrollment, reason)
        return

    steps = [step for step in campaign_doc.steps if step.enabled]

    if enrollment.status == "Queued":
        if not _send(campaign_doc, enrollment, campaign_doc.outreach_template):
            return
        _schedule_next(enrollment, campaign_doc, steps, step_index=0)
        return

    index = cint(enrollment.current_step)
    if index >= len(steps):
        _complete(enrollment)
        return

    step = steps[index]
    if _should_send(step, enrollment, contact):
        if not _send(campaign_doc, enrollment, step.template):
            return

    # A skipped step still advances. Otherwise the journey stalls forever on a
    # condition that will never become true.
    _schedule_next(enrollment, campaign_doc, steps, step_index=index + 1)


def _exit_reason(contact, campaign_doc):
    """Opt-out is absolute. Everything else is the campaign's own choice."""
    if contact.opted_out:
        return "Opted out"

    exit_states = {row.lead_state for row in campaign_doc.exit_states}
    if contact.state and contact.state in exit_states:
        return f"Reached state {contact.state}"

    return None


def _should_send(step, enrollment, contact):
    condition = step.send_if or "Always"

    if condition == "If Not Replied" and enrollment.replied_in_campaign:
        return False
    if condition == "If Not Read" and enrollment.read_in_campaign:
        return False
    if condition == "If Not Clicked" and enrollment.clicked_in_campaign:
        return False

    score = cint(contact.score)
    if cint(step.min_score) and score < cint(step.min_score):
        return False
    if cint(step.max_score) and score > cint(step.max_score):
        return False

    return True


def _schedule_next(enrollment, campaign_doc, steps, step_index):
    if step_index >= len(steps):
        enrollment.current_step = step_index
        _complete(enrollment)
        return

    due = add_to_date(now_datetime(), hours=steps[step_index].delay_hours or 0)
    enrollment.current_step = step_index
    enrollment.next_action_at = clamp_to_window(due, campaign_doc)
    enrollment.status = "Waiting"
    enrollment.save(ignore_permissions=True)


def _complete(enrollment):
    enrollment.status = "Completed"
    enrollment.next_action_at = None
    enrollment.save(ignore_permissions=True)


def _exit(enrollment, reason):
    enrollment.status = "Exited"
    enrollment.exit_reason = reason
    enrollment.next_action_at = None
    enrollment.save(ignore_permissions=True)
    _bump(enrollment.campaign, "exited_count")


def _close(campaign_doc):
    """Past the end date: stop, and close anything still open."""
    for name in frappe.get_all(
        ENROLLMENT,
        filters={"campaign": campaign_doc.name, "status": ["in", OPEN_STATUSES]},
        pluck="name",
    ):
        _exit(frappe.get_doc(ENROLLMENT, name), "Campaign ended")

    campaign_doc.db_set("status", "Completed", update_modified=False)


def _bump(campaign, field, by=1):
    current = cint(frappe.db.get_value("WhatsApp Campaign", campaign, field))
    frappe.db.set_value(
        "WhatsApp Campaign", campaign, field, current + by, update_modified=False
    )


# --------------------------------------------------------------------------
# template checks
# --------------------------------------------------------------------------

def template_problems(template):
    """Why this template would send the wrong thing, in plain words.

    frappe_whatsapp fills a template's placeholders by reading `field_names`
    off the template and pulling those fields from the referenced CRM Lead. Get
    that wrong and nothing errors: the message goes out with blanks or with the
    wrong value in it, and you find out from a customer. So it is checked before
    a campaign is allowed to start rather than at send time.
    """
    if not template:
        return ["No template selected."]

    if not frappe.db.exists("WhatsApp Templates", template):
        return [f"Template {template} no longer exists."]

    doc = frappe.get_cached_doc("WhatsApp Templates", template)
    if not doc.sample_values:
        # No placeholders, so there is nothing to fill in and nothing to break.
        return []

    problems = []
    if not doc.field_names:
        return [
            f"{template}: has placeholders but no Field Names, so it would send "
            "its sample values instead of real lead data. Set Field Names on the "
            "template."
        ]

    meta = frappe.get_meta("CRM Lead")
    unknown = [
        field.strip()
        for field in doc.field_names.split(",")
        if field.strip() and not meta.has_field(field.strip())
    ]
    if unknown:
        problems.append(
            f"{template}: Field Names refer to fields that do not exist on CRM Lead: "
            + ", ".join(unknown)
        )

    expected = len([v for v in doc.sample_values.split(",") if v.strip()])
    actual = len([f for f in doc.field_names.split(",") if f.strip()])
    if expected != actual:
        problems.append(
            f"{template}: has {expected} placeholder(s) but {actual} field name(s), "
            "so the values would land in the wrong slots."
        )

    return problems


def campaign_problems(campaign_doc):
    """Everything that would make this campaign misbehave once started."""
    problems = list(template_problems(campaign_doc.outreach_template))

    for index, step in enumerate(campaign_doc.steps, start=1):
        if not step.enabled:
            continue
        label = step.step_name or f"Step {index}"
        problems += [f"{label} - {problem}" for problem in template_problems(step.template)]

    if not campaign_doc.whatsapp_account:
        problems.append("No sending number chosen.")
    elif not frappe.db.get_value(
        "WhatsApp Account", campaign_doc.whatsapp_account, "msg91_integrated_number"
    ):
        problems.append(
            f"{campaign_doc.whatsapp_account} has no MSG91 Integrated Number, so nothing can send from it."
        )

    return problems


# --------------------------------------------------------------------------
# sending
# --------------------------------------------------------------------------

def send_test(campaign, lead):
    """Send the outreach template to one lead, without enrolling anyone.

    The whole chain end to end: template, personalisation, MSG91, the event log.
    Worth doing once before pointing a campaign at a real audience.
    """
    campaign_doc = frappe.get_doc("WhatsApp Campaign", campaign)

    problems = campaign_problems(campaign_doc)
    if problems:
        frappe.throw("<br>".join(problems), title="Fix these first")

    phone = normalize_phone(_phone_for_lead(lead))
    if not phone:
        frappe.throw(f"{lead} has no mobile or phone number.")

    probe = frappe._dict({"phone": phone, "lead": lead, "campaign": campaign})
    if not _send(campaign_doc, probe, campaign_doc.outreach_template, is_test=True):
        frappe.throw("The test message could not be sent. Check the error log.")

    return phone

def _send(campaign_doc, enrollment, template, is_test=False):
    """Every campaign message is an approved template, so it is legal whether or
    not the 24h window happens to be open. A free-form nudge would fail on day
    three of a sequence, which is exactly when the sequence needs to work.

    Returns False when the enrollment was closed instead of messaged, in which
    case the caller must not go on to schedule anything.
    """
    if not template:
        return False

    reference_doctype, reference_name = _reference_for(enrollment)
    if not reference_name:
        if is_test:
            return False
        _exit(enrollment, "No CRM Lead to personalise from")
        return False

    message = frappe.new_doc("WhatsApp Message")
    message.update(
        {
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "message_type": "Template",
            "message": "Template message",
            "content_type": "text",
            "use_template": 1,
            "template": template,
            "to": enrollment.phone,
        }
    )

    # Inserting is what sends, so the account and the campaign tag have to be in
    # place beforehand. The override and the event recorder read them off flags.
    previous_account = frappe.flags.get("msg91_whatsapp_account")
    previous_campaign = frappe.flags.get("msg91_campaign")
    frappe.flags.msg91_whatsapp_account = campaign_doc.whatsapp_account
    frappe.flags.msg91_campaign = campaign_doc.name
    try:
        message.insert(ignore_permissions=True)
    finally:
        frappe.flags.msg91_whatsapp_account = previous_account
        frappe.flags.msg91_campaign = previous_campaign

    if is_test:
        # A test must not move the journey or inflate the campaign's numbers.
        return True

    enrollment.status = "Active"
    enrollment.nudges_sent = cint(enrollment.nudges_sent) + 1
    enrollment.last_sent_at = now_datetime()
    _bump(campaign_doc.name, "sent_count")
    return True


def _reference_for(enrollment):
    """frappe_whatsapp fills template parameters from the referenced document."""
    if enrollment.lead and frappe.db.exists("CRM Lead", enrollment.lead):
        return "CRM Lead", enrollment.lead
    return None, None


# --------------------------------------------------------------------------
# send window
# --------------------------------------------------------------------------

def _zone(name):
    """An unrecognised timezone must not take the whole runner down."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        frappe.log_error(f"MSG91: unknown timezone {name!r}, falling back", "MSG91 campaign")
        return None


def clamp_to_window(when, campaign_doc):
    """Push a due time into the campaign's send window, in the audience's zone."""
    if not campaign_doc.send_window_start or not campaign_doc.send_window_end:
        return when

    system_zone = _zone(frappe.utils.get_system_timezone()) or ZoneInfo("UTC")
    audience_zone = _zone(campaign_doc.timezone) or system_zone

    local = get_datetime(when).replace(tzinfo=system_zone).astimezone(audience_zone)
    start = get_time(campaign_doc.send_window_start)
    end = get_time(campaign_doc.send_window_end)

    if local.time() < start:
        local = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    elif local.time() >= end:
        local = (local + timedelta(days=1)).replace(
            hour=start.hour, minute=start.minute, second=0, microsecond=0
        )

    return local.astimezone(system_zone).replace(tzinfo=None)


# --------------------------------------------------------------------------
# engagement, fed from the event log
# --------------------------------------------------------------------------

ENGAGEMENT_FIELDS = {
    "Inbound Received": "replied_in_campaign",
    "Read": "read_in_campaign",
    "Clicked": "clicked_in_campaign",
}


def note_engagement(phone, event_type):
    """Mark engagement on every open enrollment for this number.

    A reply is not addressed to a campaign, it is addressed to your business, so
    every campaign currently talking to them counts it. Per-campaign reporting
    stays honest anyway, because the events themselves record which campaign
    sent what.
    """
    field = ENGAGEMENT_FIELDS.get(event_type)
    if not field:
        return

    rows = frappe.get_all(
        ENROLLMENT,
        filters={"phone": normalize_phone(phone), "status": ["in", OPEN_STATUSES]},
        fields=["name", "campaign", field],
    )

    for row in rows:
        if row.get(field):
            continue
        frappe.db.set_value(ENROLLMENT, row.name, field, 1, update_modified=False)
        if event_type == "Inbound Received":
            _bump(row.campaign, "replied_count")
