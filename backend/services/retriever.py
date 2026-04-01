"""
Retrieves relevant field instruction chunks from ChromaDB.

Three retrieval strategies run in parallel for every message turn:

  1. Exact lookup  — current field's chunk by ID (O(1), no embedding needed)
  2. Form overview — the form-level chunk by ID (O(1), always injected)
  3. Semantic search — user's message embedded and searched against all field
                       chunks for this form, to catch cross-field references
                       (e.g. "how does this relate to the docket number?")

The combined result is injected into the Gemini system prompt as
RETRIEVED CONTEXT, replacing any instruction Gemini might hallucinate
from training data.
"""

import os
from functools import lru_cache
import chromadb
from google import genai
from dotenv import load_dotenv

load_dotenv()

CHROMA_PATH     = os.path.join(os.path.dirname(__file__), "..", "chroma_db")
COLLECTION_NAME = "form_fields"
EMBED_MODEL     = "gemini-embedding-001"

_genai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


@lru_cache(maxsize=1)
def _get_collection():
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _embed_query(text: str) -> list[float]:
    result = _genai_client.models.embed_content(model=EMBED_MODEL, contents=[text])
    return result.embeddings[0].values


def retrieve_field_context(
    form_id: str,
    field_name: str,
    field_label: str,
    user_message: str = "",
    k: int = 2,
) -> str:
    """
    Returns a formatted context string ready to inject into the system prompt.

    Combines:
      - Form overview chunk (always)
      - Current field chunk (exact lookup)
      - Cross-field chunks relevant to the user's message (semantic, optional)
    """
    collection = _get_collection()
    seen_ids:  set[str] = set()
    sections:  list[str] = []

    # ── 1. Form overview ──────────────────────────────────────────────
    form_chunk_id = f"form:{form_id}"
    try:
        res = collection.get(ids=[form_chunk_id], include=["documents"])
        if res["documents"] and res["documents"][0]:
            sections.append("FORM OVERVIEW:\n" + res["documents"][0])
            seen_ids.add(form_chunk_id)
    except Exception:
        pass

    # ── 2. Current field (exact lookup) ──────────────────────────────
    field_chunk_id = f"field:{form_id}::{field_name}"
    try:
        res = collection.get(ids=[field_chunk_id], include=["documents"])
        if res["documents"] and res["documents"][0]:
            sections.append("CURRENT FIELD INSTRUCTIONS:\n" + res["documents"][0])
            seen_ids.add(field_chunk_id)
    except Exception:
        pass

    # ── 3. Cross-field semantic search ────────────────────────────────
    # Only fire if the user's message contains a question or references
    # something other than just answering the current field.
    if user_message and _looks_like_question(user_message):
        query = f"{user_message} {field_label} {form_id}"
        try:
            embedding = _embed_query(query)
            results   = collection.query(
                query_embeddings=[embedding],
                n_results=min(k + 2, collection.count()),
                where={"form_id": form_id},
                include=["documents", "distances", "metadatas"],
            )
            docs       = results.get("documents", [[]])[0]
            distances  = results.get("distances",  [[]])[0]
            metadatas  = results.get("metadatas",  [[]])[0]

            cross_chunks = []
            for doc, dist, meta in zip(docs, distances, metadatas):
                chunk_id = (
                    f"field:{form_id}::{meta.get('field_name', '')}"
                    if meta.get("chunk_type") == "field"
                    else f"form:{form_id}"
                )
                if chunk_id in seen_ids:
                    continue
                if dist > 0.45:  # only include genuinely similar chunks
                    continue
                cross_chunks.append(doc)
                seen_ids.add(chunk_id)
                if len(cross_chunks) >= k:
                    break

            if cross_chunks:
                sections.append(
                    "RELATED FIELD CONTEXT (may be relevant to the user's question):\n"
                    + "\n---\n".join(cross_chunks)
                )
        except Exception:
            pass

    if not sections:
        return ""

    return "\n\n".join(sections)


def _looks_like_question(message: str) -> bool:
    """
    Heuristic: does this message look like a clarification question
    rather than just an answer to the current field?
    """
    msg = message.lower().strip()
    question_words = ("what", "why", "how", "when", "where", "which",
                      "who", "does", "do", "is", "are", "can", "could",
                      "should", "explain", "tell me", "what's", "what is")
    if msg.endswith("?"):
        return True
    if any(msg.startswith(w) for w in question_words):
        return True
    # References another field by mentioning a different concept
    # (crude but avoids embedding overhead for simple answers like "Suffolk")
    return len(msg.split()) > 6


def is_index_ready() -> bool:
    try:
        return _get_collection().count() > 0
    except Exception:
        return False