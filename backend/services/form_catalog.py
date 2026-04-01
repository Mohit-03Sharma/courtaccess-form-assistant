"""
Discover forms from corpus/instructions/*.json.
Each instruction file must include form_id, form_name, and pdf_file (under backend/forms/).
"""
from __future__ import annotations

import glob
import json
import os

_INSTRUCTIONS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "corpus", "instructions")
)


def _pdf_exists(rel_path: str) -> bool:
    return os.path.isfile(os.path.normpath(os.path.join(os.path.dirname(__file__), "..", rel_path)))


def load_form_catalog() -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    pattern = os.path.join(_INSTRUCTIONS_DIR, "*.json")
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        fid = data.get("form_id")
        if not fid or not isinstance(fid, str):
            continue
        pdf_file = data.get("pdf_file")
        if not pdf_file:
            pdf_file = f"{fid}.pdf"
        pdf_path = os.path.join("forms", pdf_file).replace("\\", "/")
        if not _pdf_exists(pdf_path):
            continue
        catalog[fid] = {
            "name": data.get("form_name", fid),
            "pdf_path": pdf_path,
            "fill_mode": data.get("pdf_fill_mode", "overlay"),
        }
    return catalog


_CATALOG_CACHE: dict[str, dict] | None = None


def get_form_catalog() -> dict[str, dict]:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        _CATALOG_CACHE = load_form_catalog()
    return _CATALOG_CACHE


def get_form_entry(form_id: str) -> dict | None:
    return get_form_catalog().get(form_id)


def refresh_form_catalog() -> dict[str, dict]:
    global _CATALOG_CACHE
    _CATALOG_CACHE = load_form_catalog()
    return _CATALOG_CACHE
