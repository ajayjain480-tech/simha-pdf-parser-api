"""
Core PDF parsing logic. Uses pdfplumber (great for tables/layout) with
PyMuPDF (fitz) as a fast fallback for plain text and metadata.
"""
import io
from typing import Optional

import pdfplumber
import fitz  # PyMuPDF


def parse_pdf(file_bytes: bytes, extract_tables: bool = True, extract_images: bool = False) -> dict:
    result = {
        "page_count": 0,
        "metadata": {},
        "pages": [],
    }

    # Metadata via PyMuPDF (fast, reliable)
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        result["page_count"] = doc.page_count
        meta = doc.metadata or {}
        result["metadata"] = {
            "title": meta.get("title") or None,
            "author": meta.get("author") or None,
            "subject": meta.get("subject") or None,
            "creator": meta.get("creator") or None,
            "producer": meta.get("producer") or None,
            "creation_date": meta.get("creationDate") or None,
        }

    # Per-page text + tables via pdfplumber
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for i, page in enumerate(pdf.pages):
            page_data = {
                "page_number": i + 1,
                "text": page.extract_text() or "",
                "width": page.width,
                "height": page.height,
            }
            if extract_tables:
                tables = page.extract_tables()
                page_data["tables"] = tables if tables else []
            result["pages"].append(page_data)

    result["full_text"] = "\n\n".join(p["text"] for p in result["pages"])
    return result


def parse_pdf_text_only(file_bytes: bytes) -> dict:
    """Faster path: plain text + metadata only, no table extraction."""
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        meta = doc.metadata or {}
        pages = []
        for i, page in enumerate(doc):
            pages.append({"page_number": i + 1, "text": page.get_text()})
        return {
            "page_count": doc.page_count,
            "metadata": {
                "title": meta.get("title") or None,
                "author": meta.get("author") or None,
            },
            "pages": pages,
            "full_text": "\n\n".join(p["text"] for p in pages),
        }
