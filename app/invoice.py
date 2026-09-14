"""
Invoice/receipt structured-field extraction.
Built on top of the generic text/table extraction in parser.py.
Uses table-structure extraction where available, with a general
text-line heuristic fallback for invoices without ruled table borders
(the common case for most real-world invoices).
"""
import re
from typing import Optional

from app.parser import parse_pdf

DATE_PATTERNS = [
    r"\b(\d{1,2}[-\/\.](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-\/\.]\d{2,4})\b",
    r"\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4})\b",
    r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{2,4})\b",
    r"\b(\d{1,2}\.\d{1,2}\.\d{2,4})\b",
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
    r"\b(\d{4}[/-]\d{1,2}[/-]\d{1,2})\b",
]

INVOICE_NUMBER_PATTERNS = [
    r"(?:invoice|inv|bill|receipt)\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-\/]{2,20})",
]

TOTAL_LINE_KEYWORDS = [
    r"grand\s*total", r"total\s*amount", r"amount\s*due", r"total\s*due",
    r"balance\s*due", r"net\s*payable", r"total\s*payable", r"amount\s*payable",
    r"^total\b",
]
TAX_LINE_KEYWORDS = [r"\b(cgst|sgst|igst|gst|vat|tax)\b"]

TAX_COMPONENT_KEYWORDS = {
    "cgst_amount": [r"\bcgst\b"],
    "sgst_amount": [r"\bsgst\b", r"\butgst\b"],
    "igst_amount": [r"\bigst\b"],
    "vat_amount": [r"\bvat\b"],
    "sales_tax_amount": [r"\bsales\s*tax\b"],
}

VENDOR_LINE_HINTS = ["ltd", "llp", "pvt", "inc", "corp", "technologies", "solutions", "enterprises", "timber", "traders", "industries", "exports", "consulting", "chemicals", "textiles", "agro", "retail", "group"]
INVOICE_TITLE_STOPWORDS = ["tax invoice", "invoice", "bill", "receipt", "proforma invoice", "credit note", "debit note", "original", "duplicate", "tax invoice / bill of supply"]
LABEL_STOPWORDS = ["dated", "gstin", "state", "name", "code", "buyer", "bill", "to", "invoice", "no"]

TABLE_HEADER_HINTS = {
    "description": ["description", "item", "product", "particulars", "goods"],
    "quantity": ["qty", "quantity"],
    "unit_price": ["rate", "price", "unit price", "unit rate"],
    "tax": ["tax", "gst", "vat", "cgst", "sgst", "igst"],
    "amount": ["amount", "total", "value"],
}

LINE_ITEM_SKIP_KEYWORDS = ['total', 'subtotal', 'tax', 'cgst', 'sgst', 'igst', 'vat', 'amount in words',
                            'declaration', 'gstin', 'invoice', 'bill to', 'buyer', 'ship to',
                            'place of supply', 'authorised', 'signatory', 'e. & o.e', 'sold by',
                            'consignee', 'terms', 'note', 'company', 'pan', 'round off',
                            'rounding', 'less :', 'less:', 'payable']
LINE_ITEM_HEADER_KEYWORDS = ['description', 'particulars', 'qty', 'quantity', 'rate', 'amount', 'hsn', 'sac', 'goods']


def _has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s)


def _search_first_valid(patterns, text, flags=re.IGNORECASE, validator=None):
    for pat in patterns:
        for m in re.finditer(pat, text, flags):
            val = m.group(1).strip()
            if validator is None or validator(val):
                return val
    return None


def _find_invoice_number(text: str) -> Optional[str]:
    for m in re.finditer(r"(?:invoice|inv|bill|receipt)\s*(?:no\.?|number|#)", text, re.IGNORECASE):
        window = text[m.end():m.end() + 150]
        for token in re.split(r"[\s:|]+", window):
            token = token.strip(".,")
            if not token:
                continue
            if token.lower() in LABEL_STOPWORDS:
                continue
            if _has_digit(token) and 3 <= len(token) <= 20:
                return token
    return None


def _guess_vendor(lines: list[str]) -> Optional[str]:
    candidates = [l.strip() for l in lines if l.strip()]
    for line in candidates[:8]:
        low = line.lower()
        if any(hint in low for hint in VENDOR_LINE_HINTS):
            cut = re.split(r"\b(invoice|dated|gstin|state name|buyer|bill to)\b", line, flags=re.IGNORECASE)[0]
            return cut.strip()
    for line in candidates:
        low = line.lower()
        if low not in INVOICE_TITLE_STOPWORDS and not any(low.startswith(sw) for sw in INVOICE_TITLE_STOPWORDS):
            cut = re.split(r"\b(invoice|dated|gstin|state name|buyer|bill to)\b", line, flags=re.IGNORECASE)[0]
            return cut.strip()
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
            numbers = re.findall(r"[\d,]+\.\d{2}", line)
            if numbers:
                total_tax += float(numbers[-1].replace(",", ""))
                found = True
    return f"{total_tax:.2f}" if found else None


def _extract_tax_components(text: str) -> dict:
    result = {}
    for field, keywords in TAX_COMPONENT_KEYWORDS.items():
        total = 0.0
        found = False
        for line in text.splitlines():
            low = line.lower()
            if any(re.search(kw, low) for kw in keywords):
                numbers = re.findall(r"[\d,]+\.\d{2}", line)
                if numbers:
                    total += float(numbers[-1].replace(",", ""))
                    found = True
        result[field] = f"{total:.2f}" if found else None
    return result


def _extract_line_items_from_tables(all_tables: list) -> list[dict]:
    """Uses the PDF's real table structure when pdfplumber can detect one
    (requires ruled grid lines) -- most reliable when it applies."""
    for table in all_tables:
        if not table or len(table) < 2:
            continue
        header = [(c or "").strip().lower() for c in table[0]]
        col_map = {}
        for key, hints in TABLE_HEADER_HINTS.items():
            for i, h in enumerate(header):
                if any(hint == h or hint in h for hint in hints):
                    if key not in col_map:
                        col_map[key] = i
        if "description" not in col_map or "amount" not in col_map:
            continue

        items = []
        for row in table[1:]:
            if not row or all(not (c or "").strip() for c in row):
                continue
            desc_idx = col_map["description"]
            desc = (row[desc_idx] or "").strip() if desc_idx < len(row) else ""
            if not desc or desc.lower() in ("total", "grand total", "subtotal"):
                continue
            item = {"description": desc}
            for key in ["quantity", "unit_price", "tax", "amount"]:
                idx = col_map.get(key)
                if idx is None or idx >= len(row):
                    continue
                val = (row[idx] or "").strip()
                m = re.search(r"[\d,]+\.?\d*", val)
                if m:
                    item[key] = m.group(0).replace(",", "")
            items.append(item)
        if items:
            return items
    return []


def _extract_line_items_generic(text: str) -> list[dict]:
    """General-purpose fallback for invoices with no detectable table
    structure (the common case). Matches any line with a description
    followed by a proper decimal amount, tolerant of an optional leading
    serial number, HSN/SAC codes, and unit labels mixed in."""
    items = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        if any(kw in low for kw in LINE_ITEM_SKIP_KEYWORDS):
            continue
        decimals = re.findall(r"[\d,]+\.\d{2}", stripped)
        if not decimals:
            continue
        m_prefix = re.match(r"^\s*\d{1,3}[\.\)]?\s+", stripped)
        rest = stripped[m_prefix.end():] if m_prefix else stripped
        if not re.match(r"^[A-Za-z]", rest):
            continue
        header_word_count = sum(1 for kw in LINE_ITEM_HEADER_KEYWORDS if kw in low)
        if header_word_count >= 2:
            continue
        amount = decimals[-1].replace(",", "")
        m_desc = re.match(r"^(.*?)(?=\s+[\d(])", rest)
        desc = m_desc.group(1).strip() if m_desc else rest.strip()
        if not desc or len(desc) < 3:
            continue
        if desc.rstrip().endswith(":"):
            continue
        tail = rest[len(desc):]
        numeric_tokens = re.findall(r"[\d,]+\.?\d*", tail)
        item = {"description": desc, "amount": amount}
        if numeric_tokens and numeric_tokens[0].replace(",", "") != amount:
            item["quantity"] = numeric_tokens[0].replace(",", "")
        items.append(item)
    return items


def _sum_table_tax_column(all_tables: list) -> Optional[str]:
    for table in all_tables:
        if not table or len(table) < 2:
            continue
        header = [(c or "").strip().lower() for c in table[0]]
        tax_idx = None
        for i, h in enumerate(header):
            if any(hint == h or hint in h for hint in TABLE_HEADER_HINTS["tax"]):
                tax_idx = i
                break
        if tax_idx is None:
            continue
        total = 0.0
        found = False
        for row in table[1:]:
            if not row or tax_idx >= len(row):
                continue
            val = (row[tax_idx] or "").strip()
            m = re.search(r"[\d,]+\.?\d*", val)
            if m:
                total += float(m.group(0).replace(",", ""))
                found = True
        if found:
            return f"{total:.2f}"
    return None


def extract_invoice_fields(file_bytes: bytes) -> dict:
    parsed = parse_pdf(file_bytes, extract_tables=True)
    full_text = parsed["full_text"]
    lines = full_text.splitlines()
    all_tables = [t for p in parsed["pages"] for t in p.get("tables", [])]

    vendor = _guess_vendor(lines)
    invoice_number = _find_invoice_number(full_text)
    date = _search_first_valid(DATE_PATTERNS, full_text)
    total = _last_number_on_matching_line(TOTAL_LINE_KEYWORDS, full_text)

    line_items = _extract_line_items_from_tables(all_tables)
    if not line_items:
        line_items = _extract_line_items_generic(full_text)

    tax = _extract_tax_amount(full_text)
    if tax is None:
        tax = _sum_table_tax_column(all_tables)

    tax_components = _extract_tax_components(full_text)

    fields_found = sum(1 for v in [vendor, invoice_number, date, total] if v)
    confidence = round(fields_found / 4, 2)

    return {
        "vendor": vendor,
        "invoice_number": invoice_number,
        "date": date,
        "total_amount": total,
        "tax_amount": tax,
        "cgst_amount": tax_components["cgst_amount"],
        "sgst_amount": tax_components["sgst_amount"],
        "igst_amount": tax_components["igst_amount"],
        "vat_amount": tax_components["vat_amount"],
        "sales_tax_amount": tax_components["sales_tax_amount"],
        "line_items": line_items,
        "confidence": confidence,
        "page_count": parsed["page_count"],
        "raw_text_available": True,
    }
