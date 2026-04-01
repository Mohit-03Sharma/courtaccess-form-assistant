import json


def parse_field_update_from_text(text: str) -> tuple[str, dict | None]:
    """
    Strip the FIRST occurrence of FIELD_UPDATE: {...} and everything after it,
    returning (cleaned_reply, parsed_dict).

    With the updated system prompt, FIELD_UPDATE is emitted on the FIRST line,
    so text[:idx] would be empty if we naively split there. We instead:
      - Extract the JSON object immediately after FIELD_UPDATE:
      - Return the remainder of the text (after the JSON block) as the reply
    """
    key = "FIELD_UPDATE:"
    idx = text.find(key)          # first occurrence, not last
    if idx < 0:
        return text.strip(), None

    tail = text[idx + len(key):].strip()
    try:
        obj, end_idx = json.JSONDecoder().raw_decode(tail)
    except json.JSONDecodeError:
        return text.strip(), None

    if not isinstance(obj, dict):
        return text[:idx].strip(), None

    # Everything after the JSON block is the human-readable reply
    after = tail[end_idx:].strip()
    return after, obj


def normalize_field_updates(raw: dict | None) -> dict | None:
    """
    Normalise the parsed FIELD_UPDATE dict.
    Preserves None values — do NOT coerce None to the string "None".
    Handles both formats:
      {"field_name": "docket_year", "value": "2024"}  (old Gemini format)
      {"docket_year": "2024"}                          (current format)
      {"docket_year": null}                            (skipped/signature)
    """
    if not raw:
        return None

    # Legacy format: {field_name: "...", value: "..."}
    if "field_name" in raw and "value" in raw:
        key = str(raw["field_name"])
        val = raw["value"]
        return {key: None if val is None else str(val)}

    # Current format: {field_name: value_or_null}
    result = {}
    for k, v in raw.items():
        result[str(k)] = None if v is None else str(v)
    return result