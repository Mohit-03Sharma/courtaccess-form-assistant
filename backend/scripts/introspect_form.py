"""
Auto-generate corpus/instructions/{form_id}.json from a PDF.

Usage:
    python -m scripts.introspect_form <pdf_path> <form_id>

Example:
    python -m scripts.introspect_form forms/notice_of_appearance.pdf notice_of_appearance
    python -m scripts.introspect_form forms/complaint_for_partition.pdf complaint_for_partition

For AcroForm PDFs: reads fields directly via PyMuPDF (labels, types, rects, page numbers).
For flat PDFs:     prints instructions for manual JSON creation.
"""

import sys
import os
import json
import re
import fitz
import pdfplumber
from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Field name suffixes to strip when building snake_case semantic names
_STRIP_SUFFIXES = re.compile(
    r'(Field|Textbox|Sub|Drop|List|RB|Button|Box)$', flags=re.I
)

# Labels that are not useful as display labels
_SKIP_LABEL_PREFIXES = ("Click to", "Check ", "Select ")
_SKIP_LABELS = {"Enter Date", "Enter SIGNATURE", "Enter Signature"}


def _short_name(full_xfa_name: str) -> str:
    """
    Extract the leaf field name from XFA path and convert to snake_case.
    'form1[0].BodyPage1[0].PartyInformationSub[0].NameField[0]' -> 'name'
    'County' -> 'county'
    'NameRow1' -> 'name_row_1'
    """
    leaf = full_xfa_name.split(".")[-1]
    leaf = re.sub(r'\[\d+\]$', '', leaf)
    leaf = _STRIP_SUFFIXES.sub('', leaf)
    # camelCase to snake_case
    leaf = re.sub(r'([a-z])([A-Z])', r'\1_\2', leaf)
    leaf = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', leaf)
    # spaces and special chars to underscore
    leaf = re.sub(r'[\s/\-()#]+', '_', leaf)
    leaf = re.sub(r'_+', '_', leaf).lower().strip('_')
    return leaf or full_xfa_name.lower()


def _make_display_label(label: str, sname: str) -> str:
    """
    Convert a raw PDF label to a clean display label.
    'Enter Street Address' -> 'Street Address'
    'Check Family' -> 'Family' (fallback to sname if too short)
    '' -> derived from sname
    """
    if not label:
        return sname.replace('_', ' ').title()

    # strip common prefixes
    for prefix in ("Enter ", "Select "):
        if label.startswith(prefix):
            cleaned = label[len(prefix):].strip()
            if cleaned:
                return cleaned.title()

    # skip unhelpful labels
    if label in _SKIP_LABELS or any(label.startswith(p) for p in _SKIP_LABEL_PREFIXES):
        return sname.replace('_', ' ').title()

    return label.title()


def _extract_acroform_fields(pdf_path: str, page_height: float) -> list[dict]:
    """
    Extract all fillable fields from an AcroForm PDF using PyMuPDF.
    - Filters out Button widgets (PRINT, CLEAR etc.)
    - Deduplicates fields that repeat on every page header (County, Docket Year etc.)
    - Stores page number (0-indexed) and rect in pdfplumber coordinate space
    """
    doc = fitz.open(pdf_path)
    seen_names: set[str] = set()
    fields: list[dict] = []

    for page in doc:
        page_num = page.number
        for widget in page.widgets():
            full_name = widget.field_name or ""
            label = (widget.field_label or "").strip()
            ftype = widget.field_type_string or "Text"

            if not full_name:
                continue

            # filter action buttons
            if ftype == "Button":
                continue

            # deduplicate — header fields repeat on every page, keep first only
            if full_name in seen_names:
                continue
            seen_names.add(full_name)

            if ftype not in ("Text", "RadioButton", "CheckBox", "ListBox", "ComboBox"):
                continue

            sname = _short_name(full_name)
            display_label = _make_display_label(label, sname)

            # convert PyMuPDF rect [x0, y0, x1, y1] (y from bottom of page)
            # to pdfplumber rect [x0, y0, x1, y1] (y from top of page)
            r = widget.rect
            rect = [
                round(r.x0, 2),
                round(r.y0, 2),
                round(r.x1, 2),
                round(r.y1, 2),
            ]

            field_type = "text"
            if ftype == "RadioButton":
                field_type = "radio"
            elif ftype == "CheckBox":
                field_type = "checkbox"
            elif ftype in ("ListBox", "ComboBox"):
                field_type = "dropdown"

            fields.append({
                "name": sname,
                "label": display_label,
                "type": field_type,
                "required": False,
                "instructions": "",
                "page": page_num,
                "rect": rect,
                "_pdf_name": full_name,
            })

    return fields


def _enrich_with_gemini(fields: list[dict], form_text: str) -> list[dict]:
    """
    Ask Gemini to:
    1. Mark which fields are required
    2. Write plain-language instructions for each field
    """
    field_summary = "\n".join(
        f"- name: {f['name']}, label: {f['label']}, type: {f['type']}, page: {f['page']}"
        for f in fields
    )

    prompt = f"""You are documenting a Massachusetts court form for a legal aid application \
that helps non-English speakers fill out court forms.

Form text (first 2500 chars):
{form_text[:2500]}

Fields extracted from the PDF:
{field_summary}

For each field by its exact `name`, return a JSON array with:
- name: unchanged
- required: true if the form cannot be filed without this field, false otherwise
- instructions: one plain-language sentence (simple English, no jargon) explaining \
what to enter or select

Return ONLY a valid JSON array, no markdown fences, no explanation.
[{{"name": "...", "required": true, "instructions": "..."}}]"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    text = response.text.strip()
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'\s*```\s*$', '', text)

    try:
        enriched = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"Warning: Gemini enrichment JSON parse failed ({e}). Using defaults.")
        return fields

    enrich_map = {e["name"]: e for e in enriched}

    for f in fields:
        meta = enrich_map.get(f["name"], {})
        f["required"] = bool(meta.get("required", False))
        if meta.get("instructions"):
            f["instructions"] = meta["instructions"]
        elif not f["instructions"]:
            # fallback: generate from label
            lbl = f["label"].lower()
            if "date" in lbl:
                f["instructions"] = "Enter the date in month/day/year format."
            elif "signature" in lbl:
                f["instructions"] = "Sign your name here."
            elif "phone" in lbl:
                f["instructions"] = "Enter the phone number including area code, e.g. 617-555-0123."
            elif "zip" in lbl or "postal" in lbl:
                f["instructions"] = "Enter your 5-digit ZIP code."
            elif "email" in lbl:
                f["instructions"] = "Enter your email address."
            elif "state" in lbl and "street" not in lbl:
                f["instructions"] = "Enter the two-letter state abbreviation, e.g. MA."
            elif f["type"] == "checkbox":
                f["instructions"] = f"Check this box if applicable."
            elif f["type"] == "radio":
                f["instructions"] = f"Select the appropriate option."
            elif f["type"] == "dropdown":
                f["instructions"] = f"Select the appropriate {f['label'].lower()} from the list."
            else:
                f["instructions"] = f"Enter the {f['label'].lower()} as it appears on your court documents."

    return fields


def introspect(pdf_path: str, form_id: str) -> None:
    print(f"\nInspecting: {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        page_height = float(pdf.pages[0].height)
        page_width = float(pdf.pages[0].width)
        num_pages = len(pdf.pages)
        form_text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    print(f"Pages: {num_pages}  |  Size: {page_width} x {page_height}")
    print("Extracting AcroForm fields via PyMuPDF...")

    fields = _extract_acroform_fields(pdf_path, page_height)

    if not fields:
        print("\nNo AcroForm fields found — this is a flat/scanned PDF.")
        print("Flat PDF introspection is not yet automated.")
        print(f"Create corpus/instructions/{form_id}.json manually.")
        return

    print(f"Found {len(fields)} unique fields across {num_pages} pages.")
    print("Enriching labels and instructions via Gemini...")

    fields = _enrich_with_gemini(fields, form_text)

    # build acroform_field_map: semantic_name → full XFA/PDF field name
    acroform_map = {f["name"]: f.pop("_pdf_name") for f in fields}

    output = {
        "form_id": form_id,
        "form_name": form_id.replace("_", " ").title(),
        "pdf_file": os.path.basename(pdf_path),
        "pdf_fill_mode": "acroform",
        "page_width": page_width,
        "page_height": page_height,
        "acroform_field_map": acroform_map,
        "fields": fields,
    }

    out_path = os.path.join("corpus", "instructions", f"{form_id}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)

    print(f"\nWrote: {out_path}")
    print(f"Total fields: {len(fields)}\n")
    print(f"{'Name':<40} {'Type':<12} {'Page':<6} {'Required'}")
    print("-" * 72)
    for f in fields:
        req = "YES" if f["required"] else "no"
        pg = str(f["page"] + 1)
        print(f"  {f['name']:<38} {f['type']:<12} {pg:<6} {req}")
    print("\nDone. Review the JSON and adjust form_name if needed.")
    print(f"Restart the backend — the form will appear automatically in GET /api/forms.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m scripts.introspect_form <pdf_path> <form_id>")
        print("Example: python -m scripts.introspect_form forms/my_form.pdf my_form")
        sys.exit(1)
    introspect(sys.argv[1], sys.argv[2])