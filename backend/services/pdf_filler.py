import io
import json
import os
import re
import pdfrw
from pdfrw import PdfReader as PdfReaderRw, PdfWriter as PdfWriterRw
from pdfrw.objects.pdfstring import PdfString
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from PyPDF2 import PdfReader, PdfWriter

# Massachusetts Trial Court — Notice of Appearance DeptRB[0] export values (PDF /Opt order).
_DEPT_RADIO_BY_KEYWORD: list[tuple[str, list[str]]] = [
    ("1", ["boston municipal", "bmc", "boston municipal court"]),
    ("2", ["district court", "district"]),
    ("3", ["housing court", "housing"]),
    ("4", ["juvenile court", "juvenile"]),
    ("5", ["land court", "land"]),
    ("6", ["probate", "probate and family", "family court", "pfc"]),
    ("7", ["superior court", "superior"]),
]


def _dept_radio_value(user_text: str) -> str | None:
    t = user_text.lower().strip()
    if not t:
        return None
    for val, keywords in _DEPT_RADIO_BY_KEYWORD:
        if any(kw in t for kw in keywords):
            return val
    return None


def _appearance_radio_value(user_text: str) -> str:
    t = user_text.lower()
    if any(
        w in t
        for w in ("attorney", "lawyer", "counsel", "represent", "on behalf", "firm")
    ):
        return "2"
    return "1"


def _split_case_caption(text: str) -> tuple[str, str]:
    parts = re.split(r"\s+v\.?\s+", text.strip(), maxsplit=1, flags=re.I)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return text.strip(), ""


def _load_form_def(form_id: str) -> dict:
    json_path = os.path.join("corpus", "instructions", f"{form_id}.json")
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _answers_to_notice_acroform(answers: dict[str, str], form_def: dict) -> dict[str, str]:
    """Map semantic field names to PyPDF2 / XFA-style field keys."""
    m = form_def.get("acroform_field_map") or {}
    out: dict[str, str] = {}

    if "docket_number" in answers and answers["docket_number"] != "":
        k = m.get("docket_number", "case[0]")
        out[k] = answers["docket_number"]

    if "case_name" in answers and answers["case_name"] != "":
        targets = form_def.get("case_name_targets") or ["PlffField[0]", "DfdtField[0]"]
        plff_k, dfdt_k = targets[0], targets[1]
        a, b = _split_case_caption(answers["case_name"])
        out[plff_k] = a
        if b:
            out[dfdt_k] = b

    if "court_department" in answers and answers["court_department"] != "":
        rv = _dept_radio_value(answers["court_department"])
        if rv:
            out[m.get("court_department", "DeptRB[0]")] = rv

    for semantic, default_pdf in (
        ("court_division", "DivisionDrop[0]"),
        ("attorney_for", "PartyNameField1[0]"),
        ("full_name", "NameField[0]"),
        ("bbo_number", "BBOField[0]"),
        ("firm_name", "FirmField[0]"),
        ("phone_office", "PhoneField[0]"),
        ("street_address", "AddressField[0]"),
        ("apt_unit", "AptField[0]"),
        ("phone_mobile", "CellField[0]"),
        ("city_town", "CityField[0]"),
        ("state", "StateField[0]"),
        ("zip_code", "ZipcodeField[0]"),
        ("email", "EmailField[0]"),
        ("date", "DateTextbox[0]"),
    ):
        if semantic not in answers or answers[semantic] == "":
            continue
        pdf_key = m.get(semantic, default_pdf)
        out[pdf_key] = answers[semantic]

    if "appearance_type" in answers and answers["appearance_type"] != "":
        rk = m.get("appearance_type", "RadioButtonList[0]")
        out[rk] = _appearance_radio_value(answers["appearance_type"])

    return out


def _pdfrw_apply_field_values(reader, field_values: dict[str, str]) -> None:

    def decode_pdf_name(raw) -> str:
        s = str(raw).strip("()")
        raw_bytes = s.encode("latin-1")
        if raw_bytes[:2] in (b'\xfe\xff', b'\xff\xfe'):
            try:
                return raw_bytes.decode("utf-16")
            except Exception:
                pass
        try:
            return raw_bytes.decode("utf-8")
        except Exception:
            return s

    def walk(fields) -> None:
        if not fields:
            return
        for f in fields:
            if isinstance(f, pdfrw.objects.pdfdict.PdfDict):
                kids = f.get("/Kids")
                if kids:
                    walk(kids)
                t, ft = f.get("/T"), f.get("/FT")
                if not t or not ft:
                    continue
                name = decode_pdf_name(t)
                if name not in field_values:
                    continue
                val = field_values[name]
                if str(ft) == "/Btn":
                    vs = str(val)
                    f.update(pdfrw.PdfDict(V=vs, AS=vs))
                else:
                    f.update(pdfrw.PdfDict(V=PdfString.encode(val)))

    acro = reader.Root.AcroForm
    if acro and acro.Fields:
        walk(acro.Fields)
        acro.update(pdfrw.PdfDict(NeedAppearances=pdfrw.PdfObject("true")))


def fill_pdf_acroform(pdf_path: str, form_id: str, answers: dict[str, str]) -> bytes:
    form_def = _load_form_def(form_id)
    if form_id == "notice_of_appearance":
        field_values = _answers_to_notice_acroform(answers, form_def)
    else:
        field_values = {}
        amap = form_def.get("acroform_field_map") or {}
        for sem, pdf_key in amap.items():
            if sem in answers and answers[sem] != "":
                field_values[pdf_key] = answers[sem]

    reader = PdfReaderRw(pdf_path)
    _pdfrw_apply_field_values(reader, field_values)
    buf = io.BytesIO()
    PdfWriterRw().write(buf, reader)
    return buf.getvalue()


def _fill_overlay_pages(pdf_path: str, form_def: dict, answers: dict[str, str]) -> bytes:
    page_height = form_def.get("page_height", 792)
    fields = {f["name"]: f for f in form_def["fields"]}

    original = PdfReader(pdf_path)
    num_pages = len(original.pages)

    by_page: dict[int, list[tuple[list, str]]] = {}
    for field_name, value in answers.items():
        if field_name not in fields or value is None or value == "":
            continue
        meta = fields[field_name]
        rect = meta.get("rect")
        if not rect:
            continue
        p = int(meta.get("page", 0))
        if p < 0 or p >= num_pages:
            continue
        by_page.setdefault(p, []).append((rect, str(value)))

    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=letter)

    for page_idx in range(num_pages):
        if page_idx > 0:
            c.showPage()
        c.setFont("Helvetica", 9)
        for rect, value in by_page.get(page_idx, []):
            x0, y0, x1, y1 = rect
            rl_y = page_height - y1
            field_width = x1 - x0
            field_height = y1 - y0
            text_y = rl_y + (field_height * 0.3)

            c.setFillColorRGB(0.92, 0.97, 0.92)
            c.rect(x0, rl_y, field_width, field_height, fill=1, stroke=0)

            c.setFillColorRGB(0, 0.35, 0)
            max_chars = max(8, int(field_width / 5.5))
            display = value[:max_chars] + ("…" if len(value) > max_chars else "")
            c.drawString(x0 + 3, text_y, display)

    c.save()
    packet.seek(0)

    overlay = PdfReader(packet)
    writer = PdfWriter()

    for i in range(num_pages):
        page = original.pages[i]
        if i < len(overlay.pages):
            page.merge_page(overlay.pages[i])
        writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def fill_pdf(pdf_path: str, form_id: str, answers: dict[str, str], fill_mode: str) -> bytes:
    if fill_mode == "acroform":
        return fill_pdf_acroform(pdf_path, form_id, answers)
    form_def = _load_form_def(form_id)
    return _fill_overlay_pages(pdf_path, form_def, answers)


# Backwards-compatible name used by tests / imports
def fill_pdf_flat(pdf_path: str, form_id: str, answers: dict[str, str]) -> bytes:
    return fill_pdf(pdf_path, form_id, answers, "overlay")
