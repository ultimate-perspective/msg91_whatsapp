"""Turn the old hardcoded funnel into editable rows, and migrate existing contacts.

The five stages that used to live in a Python list become `WhatsApp Lead State`
records, and the one transition we actually implemented (inbound reply means
Interacted) becomes a `WhatsApp Lead Rule`. Everything here is a starting point
the user is expected to rewrite; nothing re-seeds once it exists.
"""

import frappe

STATES = [
    {"state_name": "Outreach Sent", "rank": 10, "min_score": 0},
    {"state_name": "Outreach Seen", "rank": 20, "min_score": 10},
    {"state_name": "Interacted", "rank": 30, "min_score": 30},
    {"state_name": "Warm Lead", "rank": 40, "min_score": 60, "allow_regression": 1},
    {
        "state_name": "Hot Lead",
        "rank": 50,
        "min_score": 100,
        "allow_regression": 1,
        "notify_on_entry": 1,
        "handoff_note": "Hot on WhatsApp. Call them.",
    },
    {"state_name": "Opted Out", "rank": 90, "is_terminal": 1},
    {"state_name": "Converted", "rank": 100, "is_terminal": 1},
]

RULES = [
    {
        "rule_name": "Message read",
        "trigger": "On Event",
        "event_type": "Read",
        "score_delta": 10,
        "max_times": 3,
        "priority": 50,
        "notes": "They opened it. Weak signal, so it is capped.",
    },
    {
        "rule_name": "Link clicked",
        "trigger": "On Event",
        "event_type": "Clicked",
        "score_delta": 25,
        "priority": 50,
        "notes": "Clicking a button or link in the template.",
    },
    {
        "rule_name": "Replied",
        "trigger": "On Event",
        "event_type": "Inbound Received",
        "score_delta": 30,
        "priority": 50,
        "notes": "Any reply at all. This is what used to set Interacted.",
    },
    {
        "rule_name": "Asked about price",
        "trigger": "On Event",
        "event_type": "Inbound Received",
        "score_delta": 40,
        "priority": 40,
        "conditions": [
            {"fact": "message_text", "operator": "in", "value": "price,cost,how much,rate,quote,charges"}
        ],
        "notes": "Buying intent. Edit the keyword list to match how your customers actually write.",
    },
    {
        "rule_name": "Said stop",
        "trigger": "On Event",
        "event_type": "Inbound Received",
        "score_delta": 0,
        "priority": 1,
        "set_state": "Opted Out",
        "set_opted_out": 1,
        "conditions": [
            {"fact": "message_text", "operator": "in", "value": "stop,unsubscribe,remove me,do not message"}
        ],
        "notes": "Terminal. Blocks every automated send from here on.",
    },
    {
        "rule_name": "Gone quiet",
        "trigger": "On Schedule",
        "score_delta": -30,
        "priority": 60,
        "conditions": [
            {"fact": "days_since_inbound", "operator": "greater than", "value": "14"}
        ],
        "notes": "Two weeks of silence cools them off. Needs Allow Regression on the state they are in.",
    },
]

# old hardcoded stage -> new state record (same names, so this is mostly identity)
LEGACY_STAGES = {
    "Outreach Sent": "Outreach Sent",
    "Outreach Seen": "Outreach Seen",
    "Interacted": "Interacted",
    "Warm Lead": "Warm Lead",
    "Hot Lead": "Hot Lead",
}


def execute():
    _seed_states()
    _seed_rules()
    _migrate_contacts()


def _seed_states():
    for state in STATES:
        if frappe.db.exists("WhatsApp Lead State", state["state_name"]):
            continue
        doc = frappe.new_doc("WhatsApp Lead State")
        doc.update(state)
        doc.insert(ignore_permissions=True)


def _seed_rules():
    for rule in RULES:
        if frappe.db.exists("WhatsApp Lead Rule", rule["rule_name"]):
            continue
        doc = frappe.new_doc("WhatsApp Lead Rule")
        doc.update(rule)
        doc.insert(ignore_permissions=True)


def _migrate_contacts():
    """Carry the old `stage` string over to the new `state` link, then drop it."""
    if not frappe.db.has_column("WhatsApp Funnel Contact", "stage"):
        return

    for name, stage in frappe.db.sql(
        "select name, stage from `tabWhatsApp Funnel Contact` where ifnull(stage, '') != ''"
    ):
        state = LEGACY_STAGES.get(stage)
        if state and frappe.db.exists("WhatsApp Lead State", state):
            frappe.db.set_value("WhatsApp Funnel Contact", name, "state", state, update_modified=False)
