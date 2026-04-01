"""
Build the ChromaDB vector index from all form schema JSON files.

Two types of chunks are stored:
  1. form:{form_id}          — one per form, describes overall purpose and structure
  2. field:{form_id}::{name} — one per field, includes section context

Usage:
    python -m scripts.build_index              # index all forms
    python -m scripts.build_index <form_id>    # index one form only
    python -m scripts.build_index <form_id> --force  # re-index even if already exists
"""

import os, sys, json, glob, time
import chromadb
from google import genai
from dotenv import load_dotenv

load_dotenv()

CHROMA_PATH      = os.path.join(os.path.dirname(__file__), "..", "chroma_db")
INSTRUCTIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "corpus", "instructions")
COLLECTION_NAME  = "form_fields"
EMBED_MODEL      = "gemini-embedding-001"

genai_client  = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)

# ── Page → section label map ──────────────────────────────────────────────────
# Heuristic: assign a human-readable section name based on page number.
# Override per form_id if needed.
_DEFAULT_PAGE_SECTIONS = {
    0: "Header / Case Information",
    1: "Property Details",
    2: "Ownership and Title",
    3: "Property Use and Encumbrances",
    4: "Co-owner Information",
    5: "Attorney / Party Signature",
}

_FORM_PAGE_SECTIONS: dict[str, dict[int, str]] = {
    "notice_of_appearance": {
        0: "Case and Court Information",
        1: "Attorney / Party Details",
    },
    "petition_209a": {
        0: "Petitioner and Respondent Information",
        1: "Animal Details and Relief Requested",
    },
}

def _section_for(form_id: str, page: int) -> str:
    sections = _FORM_PAGE_SECTIONS.get(form_id, _DEFAULT_PAGE_SECTIONS)
    return sections.get(page, f"Page {page + 1}")


# ── Embedding ─────────────────────────────────────────────────────────────────
def _embed(texts: list[str]) -> list[list[float]]:
    result = genai_client.models.embed_content(model=EMBED_MODEL, contents=texts)
    return [e.values for e in result.embeddings]


# ── Form-level chunk ──────────────────────────────────────────────────────────
def _form_chunk(form_id: str, schema: dict) -> tuple[str, str, dict]:
    form_name   = schema.get("form_name", form_id)
    fields      = schema.get("fields", [])
    pages       = sorted({f.get("page", 0) for f in fields})
    req_count   = sum(1 for f in fields if f.get("required"))
    total_count = len(fields)

    sections = _FORM_PAGE_SECTIONS.get(form_id, _DEFAULT_PAGE_SECTIONS)
    section_lines = "\n".join(
        f"  Page {p+1}: {sections.get(p, f'Page {p+1}')}" for p in pages
    )

    chunk_id   = f"form:{form_id}"
    chunk_text = f"""Form overview: {form_name}
Form ID: {form_id}
Total fields: {total_count} ({req_count} required, {total_count - req_count} optional)
Pages: {len(pages)}
Structure:
{section_lines}

This form is a Massachusetts Trial Court document. It must be filled completely
and accurately before filing. Required fields cannot be left blank."""

    metadata = {
        "chunk_type": "form",
        "form_id":    form_id,
        "form_name":  form_name,
    }
    return chunk_id, chunk_text, metadata


# ── Field-level chunk ─────────────────────────────────────────────────────────
def _field_chunk(form_id: str, form_name: str, field: dict) -> tuple[str, str, dict]:
    chunk_id = f"field:{form_id}::{field['name']}"
    page     = field.get("page", 0)
    section  = _section_for(form_id, page)

    lines = [
        f"Form: {form_name}",
        f"Section: {section} (page {page + 1})",
        f"Field name: {field['name']}",
        f"Field label: {field['label']}",
        f"Field type: {field['type']}",
        f"Required: {'Yes' if field.get('required') else 'No'}",
    ]
    if field.get("instructions"):
        lines.append(f"Official instructions: {field['instructions']}")

    chunk_text = "\n".join(lines)
    metadata   = {
        "chunk_type":  "field",
        "form_id":     form_id,
        "form_name":   form_name,
        "field_name":  field["name"],
        "field_label": field["label"],
        "field_type":  field["type"],
        "required":    bool(field.get("required", False)),
        "page":        int(page),
        "section":     section,
    }
    return chunk_id, chunk_text, metadata


# ── Index one form ────────────────────────────────────────────────────────────
def index_form(collection, form_id: str, schema: dict, force: bool = False):
    form_name = schema.get("form_name", form_id)
    fields    = schema.get("fields", [])

    if not fields:
        print(f"  [{form_id}] No fields — skipping.")
        return

    # Build all chunk IDs for this form
    all_ids = [f"form:{form_id}"] + [f"field:{form_id}::{f['name']}" for f in fields]

    # Find which are already indexed
    existing_ids: set[str] = set()
    if not force:
        try:
            res = collection.get(ids=all_ids, include=[])
            existing_ids = set(res["ids"])
        except Exception:
            pass

    to_build: list[tuple[str, str, dict]] = []

    # Form-level chunk
    fid, ftxt, fmeta = _form_chunk(form_id, schema)
    if fid not in existing_ids:
        to_build.append((fid, ftxt, fmeta))

    # Field-level chunks
    for field in fields:
        cid, ctxt, cmeta = _field_chunk(form_id, form_name, field)
        if cid not in existing_ids:
            to_build.append((cid, ctxt, cmeta))

    if not to_build:
        print(f"  [{form_id}] All {len(all_ids)} chunks already indexed — skipping.")
        return

    print(f"  [{form_id}] Indexing {len(to_build)} chunks "
          f"(skipping {len(existing_ids)} already indexed)...")

    # Embed in batches of 50
    batch_size = 50
    for i in range(0, len(to_build), batch_size):
        batch = to_build[i:i + batch_size]
        ids        = [c[0] for c in batch]
        texts      = [c[1] for c in batch]
        metadatas  = [c[2] for c in batch]
        embeddings = _embed(texts)
        collection.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
        if i + batch_size < len(to_build):
            time.sleep(0.5)

    print(f"  [{form_id}] Done. {len(to_build)} chunks indexed.")


# ── Entry point ───────────────────────────────────────────────────────────────
def build_index(target_form_id: str | None = None, force: bool = False):
    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    paths = sorted(glob.glob(os.path.join(INSTRUCTIONS_DIR, "*.json")))
    if not paths:
        print("No JSON schema files found in corpus/instructions/")
        return

    print(f"Found {len(paths)} schema file(s). Building index...\n")

    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                schema = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  Skipping {path}: {e}")
            continue

        form_id = schema.get("form_id", os.path.splitext(os.path.basename(path))[0])
        if target_form_id and form_id != target_form_id:
            continue

        index_form(collection, form_id, schema, force=force)

    print(f"\nIndex complete. Total chunks: {collection.count()}")


if __name__ == "__main__":
    target = None
    force  = "--force" in sys.argv
    args   = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        target = args[0]
    build_index(target_form_id=target, force=force)