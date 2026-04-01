import os
from google import genai
from dotenv import load_dotenv
from services.field_extractor import FieldSchema
from services.reply_parser import parse_field_update_from_text, normalize_field_updates
from services.retriever import retrieve_field_context, is_index_ready

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = "gemini-2.5-flash"
_FORM_TEXT_CAP = int(os.getenv("FORM_TEXT_MAX_CHARS", "48000"))


def build_system_prompt(
    form_text: str,
    fields: list[FieldSchema],
    current_field: FieldSchema,
    language: str,
    answers: dict,
    form_id: str = "",
    user_message: str = "",
) -> str:
    filled = [f"{f.label}: {answers[f.name]}" for f in fields if f.name in answers]

    field_list = "\n".join(
        f"- {f.name}: {f.label} ({'required' if f.required else 'optional'}) [{f.type}]"
        for f in fields
    )
    filled_summary = "\n".join(filled) if filled else "None yet"

    lang_instruction = {
        "es": "You must respond entirely in Spanish.",
        "pt": "You must respond entirely in Portuguese.",
        "en": "You must respond in English.",
    }.get(language, "You must respond in English.")

    # Retrieve RAG context — form overview + current field + cross-field if question
    rag_context = ""
    if is_index_ready():
        rag_context = retrieve_field_context(
            form_id=form_id,
            field_name=current_field.name,
            field_label=current_field.label,
            user_message=user_message,
        )

    return f"""You are a court form assistant helping a user fill out a Massachusetts court form.
{f'''
RETRIEVED CONTEXT — use this as the authoritative source.
Prefer this over your own training knowledge about Massachusetts forms.

{rag_context}
''' if rag_context else ""}
CRITICAL RULES:
1. You ONLY help the user fill out this specific form. Nothing else.
2. Do NOT give legal advice, opinions, or predictions about their case.
3. Ask about exactly ONE field per response — the CURRENT FIELD shown below.
4. When the user provides an answer, respond in this EXACT order:
   a. On the very FIRST line, output FIELD_UPDATE in this EXACT format:
      FIELD_UPDATE: {{"{current_field.name}": "value"}}
   b. Confirm their answer in one short sentence.
   c. Immediately ask about the next unfilled field using its official instructions.
5. NEVER ask the user to confirm or say "yes/si" before saving. Accept their first clear answer immediately.
6. NEVER repeat a question for a field that already appears in FIELDS ALREADY FILLED.
7. NEVER say "all fields have been filled" unless every single field in ALL FIELDS IN THIS FORM
   is present in FIELDS ALREADY FILLED.
8. SIGNATURE FIELDS: If the current field name contains "signature" or "sign", do NOT ask the user.
   Immediately output on the first line:
   FIELD_UPDATE: {{"{current_field.name}": null}}
   Then say: "Signature fields are provided physically — skipping this one." Then ask the next field.
9. SKIPPING OPTIONAL FIELDS: If the field is optional and the user says "skip", "leave blank",
   "not applicable", "n/a", or similar, output on the first line:
   FIELD_UPDATE: {{"{current_field.name}": null}}
   Then confirm you are skipping it and ask the next field.
10. DATE FORMATTING: Always save dates as MM/DD/YYYY regardless of how the user enters them.
    Examples: "January 5th 2024" → "01/05/2024", "1/5/24" → "01/05/2024".
11. PHONE FORMATTING: Strip all formatting from the phone number and count the digits.
    - If it has exactly 10 digits, save it as (XXX) XXX-XXXX.
    - If it does not have 10 digits, do NOT save it — ask them to re-enter the correct number.
    Note: if the user already provided formatting like (617) 555-0123, normalize and accept it.
12. EMAIL VALIDATION: The email must contain "@" and ".".
    - If it does, save it as-is.
    - If it does not, do NOT save it — ask them to re-enter a valid email address.
13. STATE FORMATTING: Convert any US state name to its 2-letter abbreviation.
    Examples: "Massachusetts" → "MA", "California" → "CA", "New York" → "NY".
14. CHECKBOX FIELDS: Only save "true" or "false". Convert "yes/checked/x" → "true", "no/unchecked" → "false".
15. DROPDOWN FIELDS: Only save one of the exact valid options listed in the official instructions.
    If the user's answer does not match a valid option, list the options and ask them to choose.
16. {lang_instruction}

FORM CONTENT:
{form_text[:_FORM_TEXT_CAP]}

ALL FIELDS IN THIS FORM:
{field_list}

FIELDS ALREADY FILLED:
{filled_summary}

CURRENT FIELD TO ASK ABOUT:
Name: {current_field.name}
Label: {current_field.label}
Type: {current_field.type}
Required: {"Yes — this field cannot be left blank." if current_field.required else "Optional — user may skip this."}
Official instructions: {current_field.instructions}

Your job: If the user has not yet answered, ask one clear question about the current field.
If the user just answered, output FIELD_UPDATE on line 1, confirm briefly, then ask the next field.
"""


def chat_turn(
    form_text: str,
    fields: list[FieldSchema],
    current_field: FieldSchema,
    history: list[dict],
    user_message: str,
    language: str,
    answers: dict,
    form_id: str = "",
) -> tuple[str, dict | None]:

    system_prompt = build_system_prompt(
        form_text, fields, current_field, language, answers, form_id,
        user_message=user_message,
    )

    contents = []
    for turn in history:
        contents.append({
            "role": turn["role"],
            "parts": [{"text": turn["content"]}]
        })
    contents.append({
        "role": "user",
        "parts": [{"text": user_message}]
    })

    response = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config={
            "system_instruction": system_prompt,
            "max_output_tokens": 600,
            "temperature": 0.3,
        }
    )

    reply = response.text.strip()
    reply, raw = parse_field_update_from_text(reply)
    field_updates = normalize_field_updates(raw)

    return reply, field_updates