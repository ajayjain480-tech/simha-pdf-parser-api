"""
Invoice/receipt structured-field extraction.
Built on top of the generic text/table extraction in parser.py.
Uses regex + heuristics on the extracted text (MVP approach — no ML model
needed to launch). Accuracy improves over time as we tune patterns against
real customer documents; that's the natural roadmap item, not a blocker to launch.
"""
import re
from datetime import datetime
from typing import Optional

from app.parser import parse_pdf

# --- Regex patterns (English-language invoices; extend per-locale as needed) ---

DATE_PATTERNS = [
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
    r"\b(\d{4}[/-]\d{1,2}[/-]\d{1,2})\b",
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4})\b",
]

INVOICE_NUMBER_PATTERNS = [
    r"(?:invoice|inv|bill)\s*(?:no\.?|number|#)?\s*[:\-]?\s*([A-Z0-9\-\/]{3,20})",
    r"(?:receipt)\s*(?:no\.?|number|#)?\s*[:\-]?\s*([A-Z0-9\-\/]{3,20})",
]

TOTAL_PATTERNS = [
    r"(?:grand\s*total|total\s*amount|amount\s*due|total\s*due|balance\s*due)\s*[:\-]?\s*(?:₹|rs\.?|inr|\$|usd)?\s*([\d,]+\.?\d*)",
    r"(?:^|\n)\s*total\s*[:\-]?\s*(?:₹|rs\.?|inr|\$|usd)?\s*([\d,]+\.?\d*)",
]

TAX_PATTERNS = [
    r"(?:gst|igst|cgst|sgst|vat|tax)\s*(?:@\s*\d+%)?\s*[:\-]?\s*(?:₹|rs\.?|inr|\$)?\s*([\d,]+\.?\d*)",
]

VENDOR_LINE_HINTS = ["ltd", "llp", "pvt", "inc", "corp", "technologies", "solutions", "enterprises"]


def _search_first(patterns, text, flags=re.IGNORECASE):
    for pat in patterns:
        m = re.search(pat, text, flags)
        if m:
            return m.group(1).strip()
    return None


def _guess_vendor(lines: list[str]) -> Optional[str]:
    # Heuristic: vendor name is usually in the first few non-empty lines,
    # often containing a company-suffix keyword.
    for line in lines[:8]:
        low = line.lower()
        if any(hint in low for hint in VENDOR_LINE_HINTS):
            return line.strip()
    # fallback: first non-empty line
    for line in lines:
        if line.strip():
            return line.strip()
    return None


def _extract_line_items(text: str) -> list[dict]:
    """
    Heuristic line-item extraction: lines matching
    "<description> ... <qty> ... <unit price> ... <amount>"
    This is intentionally simple for the MVP; a follow-up iteration can
    use the table-extraction path (parser.py) when the PDF has real table
    structure, which is common for invoices.
    """
    items = []
    pattern = re.compile(
        r"^(.{3,60}?)\s+(\d+)\s+(?:x\s*)?(?:₹|rs\.?|\$)?\s*([\d,]+\.?\d*)\s+(?:₹|rs\.?|\$)?\s*([\d,]+\.?\d*)\s*$",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if m:
            items.append({
                "description": m.group(1).strip(),
                "quantity": m.group(2),
                "unit_price": m.group(3).replace(",", ""),
                "amount": m.group(4).replace(",", ""),
            })
    return items


def extract_invoice_fields(file_bytes: bytes) -> dict:
    parsed = parse_pdf(file_bytes, extract_tables=True)
    full_text = parsed["full_text"]
    lines = full_text.splitlines()

    vendor = _guess_vendor(lines)
    invoice_number = _search_first(INVOICE_NUMBER_PATTERNS, full_text)
    date = _search_first(DATE_PATTERNS, full_text)
    total = _search_first(TOTAL_PATTERNS, full_text)
    tax = _search_first(TAX_PATTERNS, full_text)
    line_items = _extract_line_items(full_text)

    # Confidence score: simple heuristic based on how many key fields resolved.
    fields_found = sum(1 for v in [vendor, invoice_number, date, total] if v)
    confidence = round(fields_found / 4, 2)

    return {
        "vendor": vendor,
        "invoice_number": invoice_number,
        "date": date,
        "total_amount": total.replace(",", "") if total else None,
        "tax_amount": tax.replace(",", "") if tax else None,
        "line_items": line_items,
        "confidence": confidence,
        "page_count": parsed["page_count"],
        "raw_text_available": True,
    }
