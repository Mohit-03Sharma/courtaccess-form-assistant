# CourtAccess Form Assistant

An AI-powered guided form-filling assistant for Massachusetts Trial Court forms. Users answer questions via text or voice (in English, Spanish, or Portuguese) and the assistant fills the PDF automatically.

Built as a standalone feature for the [CourtAccess AI](https://github.com/SunnyYadav16/court-access-ai) MLOps project.

---

## Features

- **Guided fill** — Gemini 2.5 Flash asks one field at a time, confirms answers, and advances automatically
- **Voice input** — speak your answer, get a spoken reply back
- **Input validation** — dates, phone numbers, emails, state abbreviations formatted automatically
- **RAG grounding** — field instructions retrieved from official form corpus (not Gemini training knowledge)
- **Multipage AcroForm export** — fills and downloads the real PDF with correct field values
- **Field jump-back** — click any filled field in the sidebar to go back and edit it
- **Dynamic form catalog** — add a new PDF and run one script; it appears automatically

---

## Project Structure

```
courtaccess-form-assistant/
├── backend/
│   ├── main.py                        ← FastAPI entry point
│   ├── .env.example                   ← Copy to .env and add GEMINI_API_KEY
│   ├── requirements.txt
│   ├── routers/
│   │   ├── assistant.py               ← Session, message, export endpoints
│   │   ├── voice_rest.py              ← STT + TTS endpoints
│   │   └── voice_ws.py                ← Gemini Live WebSocket (reference, not active)
│   ├── services/
│   │   ├── gemini_client.py           ← Prompt builder + Gemini API calls
│   │   ├── field_extractor.py         ← Reads field schema from corpus JSON
│   │   ├── pdf_filler.py              ← AcroForm + overlay PDF filling
│   │   ├── reply_parser.py            ← Parses FIELD_UPDATE from Gemini replies
│   │   ├── retriever.py               ← ChromaDB RAG retrieval (3-strategy)
│   │   └── form_catalog.py            ← Dynamic form catalog from corpus/
│   ├── scripts/
│   │   ├── introspect_form.py         ← Auto-generates corpus JSON from a PDF
│   │   └── build_index.py             ← Builds ChromaDB vector index
│   ├── corpus/
│   │   └── instructions/              ← JSON schema files (one per form)
│   └── forms/                         ← PDF files (local dev only)
└── frontend/
    └── src/
        ├── App.tsx                    ← Main app, routing, state
        ├── api.ts                     ← All API calls (frozen after Day 1)
        └── components/
            ├── ChatInterface.tsx      ← Chat UI + greeting + jump-back
            ├── PdfViewer.tsx          ← Multipage PDF canvas + field overlays
            ├── VoiceButton.tsx        ← Voice input/output (REST-based)
            └── FieldProgress.tsx      ← Sidebar progress + clickable fields
```

---

## Quick Start

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

pip install -r requirements.txt
# OR if that fails on your platform:
# pip install -r requirements-core.txt

# Add your Gemini API key at the PROJECT ROOT (not inside backend/)
cd ..
cp .env.example .env
# Edit .env and set GEMINI_API_KEY
cd backend

# Build the RAG index (optional — auto-builds on first session if skipped)
# python -m scripts.build_index

# Start the server
uvicorn main:app --reload

Server runs at `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
pnpm install
pnpm dev
```

App runs at `http://localhost:5173`.

---

## Adding a New Form

```bash
# 1. Copy the PDF into backend/forms/

# 2. Run the introspection script — generates the field schema JSON automatically
#    (uses PyMuPDF + Gemini Vision to extract field names, labels, instructions)
python -m scripts.introspect_form forms/your_form.pdf your_form_id

# 3. Restart the backend — form appears automatically in GET /api/forms
#    The RAG index builds automatically on the first session for any new form
```

> **In production** (main repo integration), steps 2 and 3 are fully automated —
> the Airflow DAG runs `introspect_form` and indexes the form automatically
> whenever the scraper detects a new or updated form on mass.gov.
> See `docs/INTEGRATION_HANDOFF.md` for details.

---

## Integration with Main Repo

This standalone prototype is designed to be integrated into [SunnyYadav16/court-access-ai](https://github.com/SunnyYadav16/court-access-ai). See the [Integration Handoff Document](docs/INTEGRATION_HANDOFF.md) for the complete integration guide covering:

- Which files to copy and where
- The three integration seams (Airflow DAG task, GCS-aware form catalog, frontend routing)
- Environment variables required
- How to add new forms post-integration

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | From [Google AI Studio](https://aistudio.google.com/apikey) |
| `FORM_TEXT_MAX_CHARS` | No | Cap on form text in Gemini context (default: 48000) |

---

## Tech Stack

**Backend:** FastAPI, Python, `google-genai` SDK, ChromaDB, pdfplumber, PyMuPDF, pdfrw, reportlab

**Frontend:** React 19, TypeScript, Vite, Tailwind CSS v4, PDF.js

**AI:** Gemini 2.5 Flash (chat), `gemini-embedding-001` (RAG), `gemini-2.5-flash-preview-tts` (TTS)
