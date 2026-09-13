"""
Invoice/receipt structured-field extraction.
Built on top of the generic text/table extraction in parser.py.
"""
import re
from typing import Optional

from app.parser import parse_pdf

DATE_PATTERNS = [
    r"\b(\d{1,2}[-\/](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-\/]\d{2,4})\b",
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4})\b",
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
    r"\b(\d{4}[/-]\d{1,2}[/-]\d{1,2})\b",
]

INVOICE_NUMBER_PATTERNS = [
    r"(?:invoice|inv|bill|receipt)\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-\/]{2,20})",
]

TOTAL_LINE_KEYWORDS = [r"grand\s*total", r"total\s*amount", r"amount\s*due", r"total\s*due", r"balance\s*due", r"^total\b"]
TAX_LINE_KEYWORDS = [r"\b(cgst|sgst|igst|gst|vat|tax)\b"]

VENDOR_LINE_HINTS = ["ltd", "llp", "pvt", "inc", "corp", "technologies", "solutions", "enterprises", "timber", "traders", "industries"]
INVOICE_TITLE_STOPWORDS = ["tax invoice", "invoice", "bill", "receipt", "proforma invoice", "credit note", "debit note", "original", "duplicate"]


def _has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s)


def _search_first_valid(patterns, text, flags=re.IGNORECASE, validator=None):
    for pat in patterns:
        for m in re.finditer(pat, text, flags):
            val = m.group(1).strip()
            if validator is None or validator(val):
                return val
    return None


def _guess_vendor(lines: list[str]) -> Optional[str]:
    candidates = [l.strip() for l in lines if l.strip()]
    for line in candidates[:8]:
        low = line.lower()
        if any(hint in low for hint in VENDOR_LINE_HINTS):
            return line
    for line in candidates:
        low = line.lower()
        if low not in INVOICE_TITLE_STOPWORDS and not any(low.startswith(sw) for sw in INVOICE_TITLE_STOPWORDS):
            return line
    return candidates[0] if candidates else None


def _last_number_on_matching_line(keyword_patterns, text, require_decimal=False):
    for line in text.splitlines():
        low = line.lower()
        if any(re.search(kw, low) for kw in keyword_patterns):
            if require_decimal:
                numbers = re.findall(r"[\d,]+\.\d{2}", line)
            else:
                numbers = re.findall(r"[\d,]+\.\d{2}|\d{2,}(?:,\d{3})*", line)
                decimals = [n for n in numbers if "." in n]
                if decimals:
                    numbers = decimals
            if numbers:
                return numbers[-1].replace(",", "")
    return None


def _extract_tax_amount(text: str) -> Optional[str]:
    total_tax = 0.0
    found = False
    for line in text.splitlines():
        low = line.lower()
        if any(re.search(kw, low) for kw in TAX_LINE_KEYWORDS):
            numbers = re.findall(r"[\d,]+\.\d{2}", line)  # require decimals so bare "9%" isn't grabbed
            if numbers:
                total_tax += float(numbers[-1].replace(",", ""))
                found = True
    return f"{total_tax:.2f}" if found else None


def _extract_line_items(text: str) -> list[dict]:
    """
    Heuristic line-item extraction, tolerant of extra unit tokens (e.g. "pcs")
    and HSN/SAC codes interleaved with numbers. Still an MVP heuristic —
    accuracy should be validated against more real invoices over time.
    """
    items = []
    pattern = re.compile(
        r"^\s*\d+\s+(.{3,60}?)\s+(?:\d{4,8}\s+)?"          # sl no, description, optional HSN code
        r"([\d,]+\.?\d*)\s*(?:pcs|kg|nos|units?)?\s+"        # quantity
        r"(?:[\d,]+\.?\d*\s*(?:pcs|kg|nos|units?)?\s+)?"    # optional second qty column
        r"([\d,]+\.?\d*)\s*(?:pcs|kg|nos|units?)?\s+"        # rate
        r"([\d,]+\.?\d*)\s*$",                                # amount (last number on line)
        re.IGNORECASE,
    )
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if m:
            items.append({
                "description": m.group(1).strip(),
                "quantity": m.group(2).replace(",", ""),
                "unit_price": m.group(3).replace(",", ""),
                "amount": m.group(4).replace(",", ""),
            })
    return items


def extract_invoice_fields(file_bytes: bytes) -> dict:
    parsed = parse_pdf(file_bytes, extract_tables=True)
    full_text = parsed["full_text"]
    lines = full_text.splitlines()

    vendor = _guess_vendor(lines)
    invoice_number = _search_first_valid(INVOICE_NUMBER_PATTERNS, full_text, validator=_has_digit)
    date = _search_first_valid(DATE_PATTERNS, full_text)
    total = _last_number_on_matching_line(TOTAL_LINE_KEYWORDS, full_text)
    tax = _extract_tax_amount(full_text)
    line_items = _extract_line_items(full_text)

    fields_found = sum(1 for v in [vendor, invoice_number, date, total] if v)
    confidence = round(fields_found / 4, 2)

    return {
        "vendor": vendor,
        "invoice_number": invoice_number,
        "date": date,
        "total_amount": total,
        "tax_amount": tax,
        "line_items": line_items,
        "confidence": confidence,
        "page_count": parsed["page_count"],
        "raw_text_available": True,
    }
