# MSG91 WhatsApp

A Frappe app that sends WhatsApp through the **MSG91** API and runs a
signal-driven lead funnel on top of it, for Design Instantly's CRM.

Unlike `frappe_whatsapp` (which talks to Meta's Graph API directly), this app
routes sends through MSG91, because the number is onboarded on MSG91 as BSP and
Meta rejects sends from our own app with `(#200) permissions`.

## How it fits together

```
frappe_whatsapp WhatsApp Message ──(override: notify)──> MSG91
        │                                                  │
        │ after_insert                        delivery reports (webhook)
        v                                                  v
              msg91_whatsapp.funnel.events.record()
                            │
                            v
                  WhatsApp Funnel Event  (append-only log)
                            │
                            v
              funnel.engine  ──score──> WhatsApp Funnel Contact.state
                            │
                            └──optional mirror──> CRM Lead.status
```

**The funnel states are yours, not ours.** Nothing about Hot, Warm or Interacted
is hardcoded. You define the states, what each signal is worth, and where the
thresholds sit.

## Doctypes

| Doctype | Purpose |
|---|---|
| `MSG91 Settings` | Auth key, integrated number, webhook token and URL, CRM mirror switch |
| `WhatsApp Session` | The 24h free-form window, per (customer number x business number) |
| `WhatsApp Funnel Event` | Append-only log of everything that happened. The source of truth |
| `WhatsApp Funnel Contact` | One row per customer number: cached signals, score, current state |
| `WhatsApp Lead State` | **You define these.** Name, rank, entry score, terminal, CRM mapping |
| `WhatsApp Lead Rule` | **You define these.** When X happens and Y holds, add points or force a state |
| `WhatsApp Campaign` | **You define these.** Audience, first touch, nudge sequence, schedule, exit rules |
| `WhatsApp Campaign Enrollment` | One lead's trip through one campaign. Mechanical, not editable |

## How scoring works

Rules add or subtract points. States are score bands. A contact sits in the
highest-ranked state whose minimum score they have reached.

- **On Event** rules fire once per matching event, optionally capped by
  *Max Times Per Contact* so a weak signal like "read it" cannot accumulate
  forever.
- **On Schedule** rules are re-checked on every sweep, which is how time-based
  rules work. Nothing fires an event when a lead goes quiet.
- **Terminal states** (Opted Out, Converted, Lost) are set by a rule's *Set
  State*, never by score, and nothing moves out of them automatically.
- **Assign On Entry** hands the lead to a person the moment they reach a state.
  Hot Lead ships with this on: scoring is pointless if nobody picks up the
  phone.
- **Regression** is off by default. A contact only falls back to a lower state if
  the state they are in has *Allow Regression* ticked.

The score is **replayed from the event log** on every evaluation rather than
accumulated. Editing a rule therefore corrects history instead of leaving stale
points behind, and `Score Breakdown` on the contact shows exactly which rules
produced the number.

Condition facts available: `message_text`, `template_name`, `current_state`,
`lead_status`, `score`, `inbound_count`, `outbound_count`, `nudge_count`,
`days_since_inbound`, `days_since_outbound`, `days_since_read`,
`days_since_clicked`, `replied`, `opted_out`, `session_open`.

`nudge_count` counts campaign messages sent to that contact, across campaigns.

For keyword matching use the `in` operator with a comma-separated list; it
matches if any entry appears anywhere in the text, case-insensitively.

## Campaigns

Campaigns are the **only** thing that sends. Nothing goes out before a campaign
is started, and there is no ad-hoc automated message, so every outbound message
traces back to something a human switched on.

The division of labour:

- The **campaign** decides who gets messaged and when.
- The **state engine** decides how interested they are.

A campaign has an optional first-touch template, sent on enrolment, then a list
of steps. Each step waits a set time after the previous one — in hours for a real
sequence, or minutes to rehearse one end to end — then sends unless its
condition says otherwise (`If Not Replied`, `If Not Read`, `If Not Clicked`, or
`Always`), optionally gated on the contact's global score.

### Template or free form

Each step chooses how it sends:

- **Template** — an approved template. Lands whether or not the 24h window is
  open, and is billed. Personalised from the CRM Lead, so it needs one.
- **Free Form** — plain text. Free, no template approval, and it reaches people
  who have no CRM Lead at all, because there is nothing to personalise from.
  Only legal while the window is open.

The window opens when the **customer** messages you, not when you message them,
and runs 24h from their last message. So a free-form step only works on someone
who replied, and only for as long as that reply is fresh. Steps in the first few
hours after a reply are safe; past 24h they cannot land, and the campaign form
says so on save.

`If The Window Has Closed` decides what a free-form step does when it comes due
and the window has shut: skip it, send a template instead, or drop them from the
campaign. A fallback template is best-effort — if the contact has no CRM Lead it
is skipped rather than ending the journey, because a later step may still land
if they write back.

`{name}` in free-form text is replaced with their WhatsApp profile name, or the
CRM Lead's first name, or "there".

### Who gets enrolled

Three ways, set by `Enroll Mode`:

- **Manual** — you add leads by hand.
- **Saved Filter** — everyone matching a CRM Lead filter, collected **once**, at
  Start. It never looks again.
- **On Entering State** — anyone who reaches a chosen state, automatically and
  continuously, the moment a rule puts them there. This is what makes a campaign
  react to behaviour instead of to a list.

Auto-enrolment fires on *entering* the state, so people already sitting in it
when the campaign starts are not swept up. That is usually right — their window
shut long ago — but `Enrol Everyone Already At This Level` under Audience will
back-fill them.

Leaving **Outreach Template** empty enrols people silently: no opening message,
the first wait just begins. Use that when the outreach went out somewhere else
(an MSG91 dashboard blast) and this campaign only runs the follow-up.

`Exit When They Reach A Deeper Level` drops anyone whose state now ranks above
the one that enrolled them, so a person only ever hears the nudge written for
where they actually are.

### States are global, journeys are not

A lead can run through many campaigns over time. Their **journey** is per
campaign: step 3 of the Diwali Sale, waiting 48 hours. Their **state** is not:
asking about pricing makes the person Hot, full stop, not "Hot in Diwali".

The two layers talk in one direction each. Campaign events are tagged with the
campaign for reporting, and score globally. Global state gates the campaign, via
**Exit When State Is**.

Opted-out contacts always exit, whether or not you list Opted Out there.

### Before you point one at a real audience

Save the campaign and it tells you, in plain words, anything that would make it
misbehave: a template whose Field Names do not resolve against CRM Lead, a
placeholder count that does not match, a sending number with no MSG91
integrated number. Start Campaign refuses outright while any of those stand.
This matters because a bad Field Names setting does not error at send time, it
quietly ships a message with blanks in it.

**Send Test Message** puts the first touch through the whole chain (template,
personalisation, MSG91, event log) for one lead, without enrolling anyone or
moving any counter. Do this once per campaign.

The **Nudges** section shows when each step actually lands, counted from
enrolment, so you are not adding up delays in your head.

### What is deliberately not here

There is no global send cap. Campaigns are the only sender, so a contact
enrolled in two live campaigns will hear from both, and neither knows about the
other. With one campaign running this is a non-issue. Use campaign **Priority**
if it ever becomes one.

Nothing off WhatsApp reaches the funnel. A click on your own website, a checkout
started and abandoned, a purchase — none of it is a signal here. Only what
happens in the conversation.

The runner sends up to 200 messages per campaign per 15-minute tick. Enrolment
is immediate regardless, so a burst is absorbed as a queue rather than lost.

Because the runner is a 15-minute poll, a wait is a floor and not an alarm: a
2-hour step lands at the first tick at or after the 2-hour mark. That is
invisible on an hours-long sequence and very visible on a minutes-long test of
one, which is what **Run Due Nudges Now** on an active campaign is for.

## Setup

1. **MSG91 Settings**: auth key, integrated number (digits with country code,
   e.g. `919924859743`), template namespace.
2. **WhatsApp Account**: set *MSG91 Integrated Number* on each account.
3. **Webhook token**: put any long random string in *Webhook Token* and save.
   The *Delivery Report Webhook URL* field then shows the callback URL.
4. **MSG91 Dashboard** > WhatsApp > Webhook (New) > Create Webhook, event
   **On Outbound Report Received**, paste that URL. Without this, delivered,
   read, failed and clicked never reach us and any rule about them is dead.
5. Review the seeded **WhatsApp Lead State** and **WhatsApp Lead Rule** records
   and rewrite them for your funnel. They are a starting point, not a default.
6. Turn on *Auto Write Lead Status* once the states look right, if you want the
   CRM Lead status to follow.
7. Build a **WhatsApp Campaign**, add steps, *Send Test Message* to yourself,
   then press *Start Campaign*. Until a campaign is started, the app records
   signals and scores contacts but sends nothing on its own.

To test a free-form step, message the business number from the test handset
first — otherwise its window is shut and there is nothing to send into.

## MSG91 endpoints

Two paths, split by content type and not by recipient count:

- `/whatsapp/whatsapp-outbound-message/bulk/` — templates only. Despite the
  name this is MSG91's single-template send; `to` is a list and we pass one
  number. Works outside the 24h window.
- `/whatsapp/whatsapp-outbound-message/` — everything free-form: text, media,
  interactive. Only legal inside the 24h window.

Send one request per contact even though the bulk path would accept several, or
the single `request_id` in the response can no longer be tied to one recipient.

## Roadmap

- Signals from outside WhatsApp, so a website visit can move someone a level.
- Intent detection beyond keywords.
- A panel inside the CRM frontend, so sales users are not in the desk UI.

## License

MIT
