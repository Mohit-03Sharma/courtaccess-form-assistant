import uuid
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel
from services.field_extractor import extract_fields, extract_text
from services.gemini_client import chat_turn
from services.pdf_filler import fill_pdf
from services.form_catalog import refresh_form_catalog, get_form_catalog

router = APIRouter()


def _validated_field_updates(
    raw: dict | None,
    field_map: dict,
    current_field_name: str,
) -> dict | None:
    """
    Keep only the current field's update. Coerce value to str unless it is
    explicitly None (skipped/signature fields must stay None for export filter).
    """
    if not raw:
        return None
    out = {}
    for key, value in raw.items():
        if key not in field_map:
            continue
        if key != current_field_name:
            continue
        # Preserve None for skipped/signature fields; coerce everything else to str
        if value is None:
            out[key] = None
        else:
            coerced = str(value).strip()
            # Treat empty string the same as None — no value to store
            out[key] = coerced if coerced else None
    return out or None


sessions: dict = {}


class SessionRequest(BaseModel):
    form_id: str


class MessageRequest(BaseModel):
    session_id: str
    current_field: str
    message: str
    language: str = "en"
    history: list[dict] = []


class ExportRequest(BaseModel):
    session_id: str


@router.post("/session")
def create_session(req: SessionRequest):
    catalog = refresh_form_catalog()
    entry = catalog.get(req.form_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Form not found")

    # Auto-index this form if it hasn't been indexed yet
    from services.retriever import _get_collection
    from scripts.build_index import build_index
    try:
        col = _get_collection()
        existing = col.get(ids=[f"{req.form_id}::"], include=[])
        # Check if any chunks exist for this form_id
        has_chunks = col.get(
            where={"form_id": req.form_id}, limit=1, include=[]
        )["ids"]
        if not has_chunks:
            print(f"[Index] No chunks found for {req.form_id} — building index...")
            build_index(target_form_id=req.form_id)
            print(f"[Index] Done.")
    except Exception as e:
        print(f"[Index] Auto-index skipped: {e}")

    pdf_path = entry["pdf_path"]
    fields = extract_fields(pdf_path, form_id=req.form_id)
    form_text = extract_text(pdf_path)

    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "form_id": req.form_id,
        "form_text": form_text,
        "fields": fields,
        "field_map": {f.name: f for f in fields},
        "answers": {},
        "pdf_path": pdf_path,
        "fill_mode": entry["fill_mode"],
    }

    return {
        "session_id": session_id,
        "form_name": entry["name"],
        "fields": [
            {
                "name": f.name,
                "label": f.label,
                "type": f.type,
                "required": f.required,
                "rect": getattr(f, "rect", None),
                "page": getattr(f, "page", 0),
            }
            for f in fields
        ],
    }


@router.post("/assistant/message")
def send_message(req: MessageRequest):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    field_map = session["field_map"]
    fields = session["fields"]

    if req.current_field not in field_map:
        raise HTTPException(status_code=400, detail=f"Unknown field: {req.current_field}")

    current_field = field_map[req.current_field]

    reply, field_updates = chat_turn(
        form_text=session["form_text"],
        fields=fields,
        current_field=current_field,
        history=req.history,
        user_message=req.message,
        language=req.language,
        answers=session["answers"],
        form_id=session["form_id"],
    )

    field_updates = _validated_field_updates(
        field_updates, field_map, req.current_field
    )

    if field_updates:
        for key, value in field_updates.items():
            session["answers"][key] = value  # None stored as-is; filtered at export

    # Determine next unanswered field
    next_field = None
    for f in fields:
        if f.name not in session["answers"]:
            next_field = f.name
            break

    all_required = [f for f in fields if f.required]
    filled_required = [f for f in all_required if session["answers"].get(f.name) is not None]

    return {
        "reply": reply,
        "field_updates": field_updates,
        "next_field": next_field,
        "progress": {
            "filled": sum(1 for v in session["answers"].values() if v is not None),
            "total": len(fields),
            "required_filled": len(filled_required),
            "required_total": len(all_required),
        },
    }


@router.post("/assistant/export")
def export_form(req: ExportRequest):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Use session answers as authoritative source; filter out nulls (skipped/signature fields)
    clean_answers = {
        k: v for k, v in session["answers"].items() if v is not None
    }

    pdf_bytes = fill_pdf(
        pdf_path=session["pdf_path"],
        form_id=session["form_id"],
        answers=clean_answers,
        fill_mode=session["fill_mode"],
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f"attachment; filename={session['form_id']}_filled.pdf"
            )
        },
    )


@router.get("/session/{session_id}/greeting")
def get_greeting(session_id: str):
    """
    Returns the assistant's opening message for a session.
    Called once by the frontend immediately after session creation.
    """
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    fields = session["fields"]
    first_field = fields[0] if fields else None
    form_name = next(
        (v["name"] for k, v in get_form_catalog().items()
         if k == session["form_id"]), session["form_id"]
    )

    if not first_field:
        return {"reply": f"Welcome! You have selected {form_name}. This form has no fields to fill."}

    required_count = sum(1 for f in fields if f.required)
    total_count = len(fields)

    reply = (
        f"Hello! You've selected the **{form_name}**. "
        f"This form has {total_count} fields ({required_count} required). "
        f"I'll guide you through each one.\n\n"
        f"Let's start with the first field: **{first_field.label}**"
        f"{' (required)' if first_field.required else ' (optional)'}.\n\n"
        f"{first_field.instructions}"
    )

    return {"reply": reply}


@router.get("/forms/{form_id}/pdf")
def get_pdf(form_id: str):
    entry = get_form_catalog().get(form_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Form not found")
    return FileResponse(entry["pdf_path"], media_type="application/pdf")


@router.get("/forms")
def list_forms():
    catalog = refresh_form_catalog()
    return [
        {"form_id": k, "name": v["name"]}
        for k, v in sorted(catalog.items(), key=lambda x: x[1]["name"].lower())
    ]