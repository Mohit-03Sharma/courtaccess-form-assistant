import pdfplumber
import json
import os
from dataclasses import dataclass

@dataclass
class FieldSchema:
    name: str
    label: str
    type: str
    required: bool
    instructions: str = ""
    rect: list = None

def extract_fields(pdf_path: str, form_id: str = None) -> list[FieldSchema]:
    fields = _extract_acroform_fields(pdf_path)
    if fields:
        return fields
    if form_id:
        return _load_fields_from_json(form_id)
    return []

def _extract_acroform_fields(pdf_path: str) -> list[FieldSchema]:
    fields = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            if not page.annots:
                continue
            for annot in page.annots:
                if annot.get("subtype") != "Widget":
                    continue
                name = annot.get("T") or annot.get("TU") or ""
                label = annot.get("TU") or annot.get("T") or ""
                ft = annot.get("FT", "/Tx")
                field_type = "checkbox" if ft == "/Btn" else "dropdown" if ft == "/Ch" else "text"
                if not name:
                    continue
                fields.append(FieldSchema(
                    name=name.strip(),
                    label=label.strip(),
                    type=field_type,
                    required=False
                ))
    return fields

def _load_fields_from_json(form_id: str) -> list[FieldSchema]:
    json_path = os.path.join("corpus", "instructions", f"{form_id}.json")
    if not os.path.exists(json_path):
        return []
    with open(json_path, "r") as f:
        data = json.load(f)
    return [
        FieldSchema(
            name=field["name"],
            label=field["label"],
            type=field["type"],
            required=field["required"],
            instructions=field.get("instructions", ""),
            rect=field.get("rect", None)
        )
        for field in data["fields"]
    ]

def extract_text(pdf_path: str) -> str:
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
            text += "\n"
    return text.strip()