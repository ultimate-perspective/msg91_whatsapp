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

For keyword matching use the `in` operator with a comma-separated list; it
matches if any entry appears anywhere in the text, case-insensitively.

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

- Campaigns: `WhatsApp Campaign` + steps + enrollment, driving automatic nudges
  off the state engine, with quiet hours, caps and cooldowns.
- Intent detection beyond keywords.

## License

MIT
