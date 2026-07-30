"""Translate frappe_whatsapp's Meta Graph API payloads into MSG91's schema.

frappe_whatsapp builds Meta-shaped payloads (``messaging_product``/``to``/
``type`` + a ``template.components`` ARRAY). MSG91 wants:

- templates  -> nested ``payload.template.to_and_components`` where components is
                a DICT keyed ``header_1`` / ``body_N`` / ``button_N``
- free-form  -> a FLAT body with ``recipient_number`` + ``text``

Keeping this translation in one place means the rest of frappe_whatsapp (its UI,
template store and conversation view) works untouched.
"""


def is_template(data):
    return (data or {}).get("type") == "template"


def to_msg91(data, integrated_number):
    """Route a Meta-shaped payload to the right MSG91 shape."""
    if is_template(data):
        return template_payload(data, integrated_number)
    return session_payload(data, integrated_number)


def template_payload(data, integrated_number):
    template = data.get("template") or {}
    language = (template.get("language") or {}).get("code") or "en_US"
    components = flatten_components(template.get("components") or [])

    return {
        "integrated_number": integrated_number,
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "template",
            "template": {
                "name": template.get("name"),
                "language": {"code": language, "policy": "deterministic"},
                "to_and_components": [
                    {"to": [str(data.get("to"))], "components": components}
                ],
            },
        },
    }


def session_payload(data, integrated_number):
    """Free-form (24h window) message: text or media."""
    msg_type = data.get("type") or "text"
    payload = {
        "integrated_number": integrated_number,
        "recipient_number": str(data.get("to")),
        "content_type": msg_type,
    }

    if msg_type == "text":
        payload["text"] = (data.get("text") or {}).get("body", "")
        return payload

    # Media: image / document / video / audio -> {link, caption, filename}
    media = data.get(msg_type) or {}
    payload[msg_type] = {
        k: v for k, v in {
            "link": media.get("link"),
            "caption": media.get("caption"),
            "filename": media.get("filename"),
        }.items() if v
    }
    return payload


def flatten_components(components):
    """Meta's components ARRAY -> MSG91's components DICT.

    header  -> header_1
    body    -> body_1, body_2, ... (positional, matching {{1}}, {{2}}, ...)
    button  -> button_1, button_2, ... (by button index)
    """
    flat = {}
    for component in components:
        ctype = (component.get("type") or "").lower()
        params = component.get("parameters") or []

        if ctype == "header":
            for param in params:
                flat["header_1"] = _param_value(param)

        elif ctype == "body":
            for index, param in enumerate(params, start=1):
                flat[f"body_{index}"] = _param_value(param)

        elif ctype == "button":
            index = int(component.get("index", 0)) + 1
            for param in params:
                flat[f"button_{index}"] = _param_value(param)

    return flat


def _param_value(param):
    """Meta parameter -> MSG91 {type, value}."""
    ptype = (param.get("type") or "text").lower()

    if ptype == "text":
        return {"type": "text", "value": param.get("text", "")}

    if ptype in ("image", "document", "video", "audio"):
        media = param.get(ptype) or {}
        value = {"type": ptype, "value": media.get("link", "")}
        if media.get("filename"):
            value["filename"] = media["filename"]
        return value

    if ptype == "payload":
        return {"type": "text", "value": param.get("payload", "")}

    if ptype == "currency":
        currency = param.get("currency") or {}
        return {"type": "text", "value": currency.get("fallback_value", "")}

    if ptype == "date_time":
        date_time = param.get("date_time") or {}
        return {"type": "text", "value": date_time.get("fallback_value", "")}

    # Unknown parameter type: pass the primitive through as text.
    return {"type": "text", "value": str(param.get(ptype, ""))}
