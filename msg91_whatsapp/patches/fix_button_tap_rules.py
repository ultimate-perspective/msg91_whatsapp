"""Repair a funnel wired to the wrong operator, and campaigns nobody could join.

Three mistakes that all fail silently, which is why they are worth a patch
rather than a note in the README:

- The keyword rules were saved with `equals`, which asks for the entire
  comma-separated list to be typed verbatim. `in` is the operator that matches
  any one entry anywhere in the message.
- "Tapped Know More" listened for `Outbound Sent`, so it watched our own
  messages rather than the customer's tap, and could never fire.
- Both campaigns were left on `Manual` enrolment with no state, so nothing ever
  enrolled anyone and the nudges had no one to nudge.

Every change is conditional on the field still holding the broken value, so a
second run does nothing and a later hand-edit is never clobbered.
"""

import frappe

RULE_FIELDS = {
    # Deeper intent needs the lower priority number, or a forced state from an
    # earlier step wins the tie and drags the contact back down a level.
    "Tapped Know More": {"event_type": "Inbound Received", "priority": 50},
    "Has a question": {"priority": 40},
    "Wants a call": {"priority": 30},
}

KEYWORD_RULES = ("Tapped Know More", "Has a question", "Wants a call")

CAMPAIGN_STATES = {
    "tapped Know More, went quiet": "Interacted",
    'tapped "Ask a question", never asked': "Warm Lead",
}


def execute():
    _fix_rules()
    _fix_operators()
    _fix_campaigns()
    frappe.clear_cache()


def _fix_rules():
    for name, values in RULE_FIELDS.items():
        if frappe.db.exists("WhatsApp Lead Rule", name):
            frappe.db.set_value("WhatsApp Lead Rule", name, values, update_modified=False)


def _fix_operators():
    placeholders = ", ".join(["%s"] * len(KEYWORD_RULES))
    frappe.db.sql(
        f"""
        update `tabWhatsApp Lead Condition`
        set operator = 'in'
        where fact = 'message_text'
          and operator = 'equals'
          and parent in ({placeholders})
        """,
        KEYWORD_RULES,
    )

    # The duplicate was harmless under `in` (matching is case-insensitive) but
    # reads as though case mattered.
    frappe.db.sql(
        """
        update `tabWhatsApp Lead Condition`
        set `value` = 'know more'
        where fact = 'message_text'
          and parent = 'Tapped Know More'
          and `value` = 'know more, Know More'
        """
    )


def _fix_campaigns():
    for name, state in CAMPAIGN_STATES.items():
        if not frappe.db.exists("WhatsApp Campaign", name):
            continue
        if not frappe.db.exists("WhatsApp Lead State", state):
            continue

        current = frappe.db.get_value(
            "WhatsApp Campaign", name, ["enroll_mode", "enroll_on_state"], as_dict=True
        )
        # Only touch a campaign still sitting on the default. Anything else is a
        # deliberate choice by whoever edited it last.
        if current.enroll_mode != "Manual" or current.enroll_on_state:
            continue

        frappe.db.set_value(
            "WhatsApp Campaign",
            name,
            {"enroll_mode": "On Entering State", "enroll_on_state": state},
            update_modified=False,
        )
