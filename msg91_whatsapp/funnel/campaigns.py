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
from frappe.utils import add_to_date, cint, flt, get_datetime, get_time, now_datetime
from frappe.utils.file_lock import LockTimeoutError
from frappe.utils.synchronization import filelock

from msg91_whatsapp.funnel import contacts
from msg91_whatsapp.msg91_whatsapp.doctype.whatsapp_lead_state.whatsapp_lead_state import (
    by_rank,
)
from msg91_whatsapp.msg91_whatsapp.doctype.whatsapp_session.whatsapp_session import (
    get_active_account,
    is_window_open,
)
from msg91_whatsapp.utils import normalize_phone

ENROLLMENT = "WhatsApp Campaign Enrollment"
OPEN_STATUSES = ("Queued", "Active", "Waiting")
BATCH_SIZE = 200

# What a delivery attempt did, which decides whether the journey moves on.
SENT = "sent"
SKIPPED = "skipped"
EXITED = "exited"


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
            "entry_state": contact.get("state"),
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


def enroll_on_state(contact):
    """Enrol into every live campaign that is waiting for this state.

    Called from the rule engine the moment a contact changes state, so the wait
    starts from the customer's own action rather than from the next sweep. This
    is what makes a campaign continuous: `Saved Filter` collects its audience
    once, at Start, and never looks again.
    """
    if not contact.state or contact.opted_out:
        return []

    rows = frappe.get_all(
        "WhatsApp Campaign",
        filters={
            "status": "Active",
            "enroll_mode": "On Entering State",
            "enroll_on_state": contact.state,
        },
        fields=["name", "activated_at"],
        order_by="priority asc",
    )

    enrolled = []
    for row in rows:
        if not acted_since(contact, row.activated_at):
            continue
        try:
            if enroll(row.name, phone=contact.phone):
                enrolled.append(row.name)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(), f"MSG91: auto-enrol failed for {row.name}"
            )
    return enrolled


def acted_since(contact, activated_at):
    """Did this contact act after the campaign was switched on?

    Score is replayed from the whole event log on every evaluation, and the
    hourly sweep re-evaluates everybody. So correcting a rule moves contacts who
    have been sitting quietly for months, and without this a campaign's first
    minute would enrol its entire back catalogue on the strength of something
    somebody tapped in March.

    The test is their last inbound message, because that is what a state change
    means here and it is also what opens the window a free-form nudge needs.
    """
    if not activated_at:
        return True

    last_inbound = contact.get("last_inbound_at")
    if not last_inbound:
        # They have never written to us, so there is no window and nothing that
        # could have just happened.
        return False

    return get_datetime(last_inbound) >= get_datetime(activated_at)


def enroll_current_state(campaign):
    """Back-fill: enrol everyone sitting in the campaign's trigger state today.

    Auto-enrolment fires on entering a state, so people who were already there
    when the campaign started are not swept up. Usually that is what you want —
    their 24h window shut long ago and a free-form nudge would be skipped — so
    this is a button rather than something Start does on its own. It is also the
    one path that ignores the Started At cutoff, which is the whole point of it.
    """
    campaign_doc = frappe.get_cached_doc("WhatsApp Campaign", campaign)
    if not campaign_doc.enroll_on_state:
        return 0

    enrolled = 0
    for phone in frappe.get_all(
        contacts.DOCTYPE,
        filters={"state": campaign_doc.enroll_on_state, "opted_out": 0},
        pluck="phone",
    ):
        if enroll(campaign, phone=phone):
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


def run_now(campaign):
    """Process one campaign's due enrollments immediately.

    Shares the runner's lock rather than going around it, so pressing this while
    the scheduled tick is mid-flight waits its turn instead of sending the same
    nudge twice.
    """
    try:
        with filelock("msg91_campaign_runner", timeout=30):
            run_campaign(campaign)
            frappe.db.commit()
    except LockTimeoutError:
        frappe.throw("The scheduled run is busy right now. Try again in a moment.")


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

    reason = _exit_reason(contact, campaign_doc, enrollment)
    if reason:
        _exit(enrollment, reason)
        return

    steps = [step for step in campaign_doc.steps if step.enabled]

    if enrollment.status == "Queued":
        # No first touch means this campaign only runs the follow-up, so the
        # clock starts without anything being said.
        if campaign_doc.outreach_template:
            if _send_template(campaign_doc, enrollment, campaign_doc.outreach_template) == EXITED:
                return
        _schedule_next(enrollment, campaign_doc, steps, step_index=0)
        return

    index = cint(enrollment.current_step)
    if index >= len(steps):
        _complete(enrollment)
        return

    step = steps[index]
    if _should_send(step, enrollment, contact):
        if _deliver(campaign_doc, enrollment, step, contact) == EXITED:
            return

    # A skipped step still advances. Otherwise the journey stalls forever on a
    # condition that will never become true.
    _schedule_next(enrollment, campaign_doc, steps, step_index=index + 1)


def _exit_reason(contact, campaign_doc, enrollment=None):
    """Opt-out is absolute. Everything else is the campaign's own choice."""
    if contact.opted_out:
        return "Opted out"

    exit_states = {row.lead_state for row in campaign_doc.exit_states}
    if contact.state and contact.state in exit_states:
        return f"Reached state {contact.state}"

    if enrollment is not None and campaign_doc.exit_on_advance:
        entry = enrollment.get("entry_state") or campaign_doc.enroll_on_state
        if _has_advanced(contact.state, entry):
            return f"Moved on to {contact.state}"

    return None


def _has_advanced(state, entry_state):
    """True once the contact ranks above the level that enrolled them.

    This is what keeps a person hearing only the nudge written for where they
    actually are. Someone who taps through to a deeper step should fall silent
    on the shallower sequence rather than receive both.
    """
    if not state or not entry_state or state == entry_state:
        return False

    ranks = {row.name: cint(row.rank) for row in by_rank(enabled_only=False)}
    if state not in ranks or entry_state not in ranks:
        return False
    return ranks[state] > ranks[entry_state]


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


def delay_in_hours(step):
    """A step's wait, whichever unit it was authored in.

    Minutes exist so a sequence can be rehearsed end to end in a few minutes
    instead of a few hours. Everything downstream works in hours.
    """
    value = flt(step.delay)
    if (step.delay_unit or "Hours") == "Minutes":
        return value / 60.0
    return value


def _schedule_next(enrollment, campaign_doc, steps, step_index):
    if step_index >= len(steps):
        enrollment.current_step = step_index
        _complete(enrollment)
        return

    due = add_to_date(now_datetime(), hours=delay_in_hours(steps[step_index]))
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


def step_problems(step):
    """A step's own way of being wrong, which differs by how it sends."""
    if (step.message_type or "Template") != "Free Form":
        return template_problems(step.template)

    problems = []
    if not (step.message_text or "").strip():
        problems.append("is free-form but has no message text.")

    if step.if_window_closed == "Send Template Instead":
        if not step.fallback_template:
            problems.append(
                "falls back to a template when the window is closed, but no fallback template is set."
            )
        else:
            problems += template_problems(step.fallback_template)

    return problems


def campaign_problems(campaign_doc):
    """Everything that would make this campaign misbehave once started."""
    problems = []

    # An empty first touch is a choice, not a mistake: it means the opening
    # message went out elsewhere and this campaign only runs the follow-up.
    if campaign_doc.outreach_template:
        problems += template_problems(campaign_doc.outreach_template)

    enabled_steps = [step for step in campaign_doc.steps if step.enabled]
    if not campaign_doc.outreach_template and not enabled_steps:
        problems.append(
            "Nothing to send: there is no first touch and no enabled steps."
        )

    for index, step in enumerate(campaign_doc.steps, start=1):
        if not step.enabled:
            continue
        label = step.step_name or f"Step {index}"
        problems += [f"{label} - {problem}" for problem in step_problems(step)]

    if campaign_doc.enroll_mode == "On Entering State" and not campaign_doc.enroll_on_state:
        problems.append(
            "Enrolment is set to On Entering State but no state is chosen, so nobody would ever join."
        )

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

    if campaign_doc.outreach_template:
        if _send_template(campaign_doc, probe, campaign_doc.outreach_template, is_test=True) != SENT:
            frappe.throw("The test message could not be sent. Check the error log.")
        return phone

    # No first touch, so the thing worth testing is the first nudge.
    step = next((s for s in campaign_doc.steps if s.enabled), None)
    if not step:
        frappe.throw("This campaign has nothing to send.")

    if (step.message_type or "Template") != "Free Form":
        if _send_template(campaign_doc, probe, step.template, is_test=True) != SENT:
            frappe.throw("The test message could not be sent. Check the error log.")
        return phone

    account = _open_window_account(phone, campaign_doc)
    if not account:
        frappe.throw(
            f"The 24-hour window for {phone} is closed, so this free-form step "
            "cannot be tested right now. Message the business number from that "
            "handset and try again."
        )

    contact = contacts.get_or_create(phone)
    if not contact.name or not frappe.db.exists(contacts.DOCTYPE, contact.name):
        contact.insert(ignore_permissions=True)

    if _send_freeform(campaign_doc, probe, step, contact, account, is_test=True) != SENT:
        frappe.throw("The test message could not be sent. Check the error log.")

    return phone

def _deliver(campaign_doc, enrollment, step, contact):
    """Send one step, whichever way it is configured to go out."""
    if (step.message_type or "Template") != "Free Form":
        return _send_template(campaign_doc, enrollment, step.template)

    account = _open_window_account(enrollment.phone, campaign_doc)
    if account:
        return _send_freeform(campaign_doc, enrollment, step, contact, account)

    # The window shut before the step came due. What that means is the user's
    # call, because "they went quiet" and "they are still listening" deserve
    # different answers.
    policy = step.if_window_closed or "Skip Step"
    if policy == "Send Template Instead" and step.fallback_template:
        return _send_template(
            campaign_doc, enrollment, step.fallback_template, is_fallback=True
        )
    if policy == "Exit Campaign":
        _exit(enrollment, "24h window closed")
        return EXITED
    return SKIPPED


def _open_window_account(phone, campaign_doc):
    """A business number whose 24h window with this customer is still open.

    The window belongs to a pair of numbers, not to a person, so the campaign's
    own number is tried first and the number they are actually talking to is
    the fallback.
    """
    chosen = campaign_doc.whatsapp_account
    if chosen and is_window_open(phone, chosen):
        return chosen

    active = get_active_account(phone)
    if active and active != chosen and is_window_open(phone, active):
        return active

    return None


def _send_template(campaign_doc, enrollment, template, is_test=False, is_fallback=False):
    """An approved template, which is legal whether or not the window is open.

    Returns EXITED when the enrollment was closed instead of messaged, in which
    case the caller must not go on to schedule anything.
    """
    if not template:
        return SKIPPED

    reference_doctype, reference_name = _reference_for(enrollment)
    if not reference_name:
        # A template is personalised from the lead record, so without one it
        # would ship with blanks where the name should be.
        if is_test or is_fallback:
            # A fallback is best-effort: it stands in for a free-form nudge that
            # could not be delivered. Ending the journey over it would also kill
            # the later steps, which may well land if the customer writes back
            # and reopens the window.
            return SKIPPED
        _exit(enrollment, "No CRM Lead to personalise from")
        return EXITED

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

    if not _insert_send(message, campaign_doc, campaign_doc.whatsapp_account, enrollment):
        return SKIPPED

    if is_test:
        # A test must not move the journey or inflate the campaign's numbers.
        return SENT

    _mark_sent(campaign_doc, enrollment)
    return SENT


def _send_freeform(campaign_doc, enrollment, step, contact, account, is_test=False):
    """Plain text inside the 24h window: free to send, and no template approval.

    It still goes through `WhatsApp Message` rather than straight to MSG91, so
    the nudge appears in the CRM conversation and lands in the event log. A send
    that bypassed the record would be invisible to both.
    """
    body = render(step.message_text, contact)
    if not body:
        return SKIPPED

    reference_doctype, reference_name = _reference_for(enrollment)
    if not reference_name:
        # Free-form needs no lead to personalise from, which is the whole point:
        # it reaches people who only ever existed as a phone number. The message
        # still wants something to hang off, so point it at the funnel contact.
        reference_doctype, reference_name = contacts.DOCTYPE, contact.name

    message = frappe.new_doc("WhatsApp Message")
    message.update(
        {
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "message_type": "Manual",
            "content_type": "text",
            "message": body,
            "to": enrollment.phone,
        }
    )

    if not _insert_send(message, campaign_doc, account, enrollment):
        return SKIPPED

    if is_test:
        # A test must not move the journey or inflate the campaign's numbers.
        return SENT

    _mark_sent(campaign_doc, enrollment)
    return SENT


def _insert_send(message, campaign_doc, account, enrollment):
    """Inserting is what sends, so the number and campaign tag go on first.

    A refusal must not stall the journey behind it. The window can shut between
    the check and the send, and a template can be rejected long after it was
    approved; in both cases the right answer is to log it and let the next step
    have its turn.
    """
    previous_account = frappe.flags.get("msg91_whatsapp_account")
    previous_campaign = frappe.flags.get("msg91_campaign")
    frappe.flags.msg91_whatsapp_account = account
    frappe.flags.msg91_campaign = campaign_doc.name
    try:
        message.insert(ignore_permissions=True)
        return True
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"MSG91: send failed for {enrollment.get('name') or 'test send'}",
        )
        return False
    finally:
        frappe.flags.msg91_whatsapp_account = previous_account
        frappe.flags.msg91_campaign = previous_campaign


def _mark_sent(campaign_doc, enrollment):
    enrollment.status = "Active"
    enrollment.nudges_sent = cint(enrollment.nudges_sent) + 1
    enrollment.last_sent_at = now_datetime()
    _bump(campaign_doc.name, "sent_count")
    _note_nudge(enrollment)


def _note_nudge(enrollment):
    """Count the nudge on the contact, not just on the enrollment.

    `nudge_count` is offered as a rule fact but nothing populated it, because
    until campaigns could send there was nothing in the app that nudged. It is
    written last, after the send has already rippled through the event log and
    re-scored the contact, so it is not overwritten by that pass.
    """
    contact = enrollment.get("contact")
    if not contact:
        return

    current = cint(frappe.db.get_value(contacts.DOCTYPE, contact, "nudge_count"))
    frappe.db.set_value(
        contacts.DOCTYPE,
        contact,
        {"nudge_count": current + 1, "last_nudge_at": now_datetime()},
        update_modified=False,
    )


def render(text, contact):
    """Free-form personalisation, deliberately limited to one placeholder.

    Anything richer would need the CRM Lead, and the contacts this is for are
    exactly the ones who may not have a lead record.
    """
    if not text:
        return ""
    return text.replace("{name}", _display_name(contact))


def _display_name(contact):
    """What to call them.

    The CRM's first name is already a first name, so it is used whole. A WhatsApp
    profile name is not: these contacts are shops, and clipping "The Toy House"
    to its first word greets someone as "The". So it is used whole too.
    """
    if contact.get("lead"):
        first = (frappe.db.get_value("CRM Lead", contact.lead, "first_name") or "").strip()
        if first:
            return first

    return (contact.get("profile_name") or "").strip() or "there"


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
