# MSG91 WhatsApp

A Frappe (v15) app that sends WhatsApp messages through the **MSG91** API and,
on top of that, runs a signal-driven **lead-nudge funnel**
(`outreach_seen → interacted → warm → hot`) for Design Instantly's CRM.

Unlike `frappe_whatsapp` (which talks to Meta's Graph API directly), this app
routes sends through MSG91, so the WhatsApp number can stay onboarded on MSG91.

## Status

Phase 1 — Settings + sender. Scaffolding only:

- `MSG91 Settings` (single doctype): auth key (password), integrated number,
  namespace, base URL, default language.
- `msg91_whatsapp.api.send`:
  - `send_template(to, template_name, components, language)` — approved template
    (works outside the 24h window).
  - `send_session_text(to, body)` — free-form text (only valid inside the 24h
    session window).

## Roadmap

- P2 — contact funnel state + map inbound reply → `interacted`.
- P3 — ingest MSG91 delivery/read webhook → `outreach_seen`.
- P4 — nudge scheduler with per-stage nudges + caps/quiet-hours/opt-out.
- P5 — warm/hot intent detection (keywords, then LLM).

## Configuration

After install, open **MSG91 Settings** and set the auth key, integrated number
(digits only, e.g. `919924859743`), and template namespace. The auth key is
stored encrypted in a password field and never committed to code.

## License

MIT
