"""
Invoice/receipt structured-field extraction.
Built on top of the generic text/table extraction in parser.py.
Uses table-structure extraction where available, with a general
text-line heuristic fallback for invoices without ruled table borders.
Infers CGST/SGST vs IGST split from seller/buyer GSTIN state codes
when the source document states only a combined tax figure.
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
    r"(?:tax\s*invoice|invoice|inv|bill|receipt|order)\s*(?:no\.?|number|num|#|id)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-\/]{2,20})",
]

TOTAL_LINE_KEYWORDS = [
    r"grand\s*total", r"total\s*amount", r"amount\s*due", r"total\s*due",
    r"balance\s*due", r"net\s*payable", r"total\s*payable", r"amount\s*payable",
    r"total\s*incl", r"total\s*including", r"amount\s*in\s*inr", r"^total\b",
]
TAX_LINE_KEYWORDS = [r"\b(cgst|sgst|igst|utgst|gst|vat|hst|pst|qst|consumption\s*tax|sales\s*tax|tax)\b"]

TAX_COMPONENT_KEYWORDS = {
    "cgst_amount": [r"\bcgst\b"],
    "sgst_amount": [r"\bsgst\b", r"\butgst\b"],
    "igst_amount": [r"\bigst\b"],
    "vat_amount": [r"\bvat\b"],
    "sales_tax_amount": [r"\bsales\s*tax\b"],
    "gst_amount": [r"(?<!c)(?<!s)(?<!i)(?<!ut)\bgst\b"],
    "hst_amount": [r"\bhst\b"],
    "pst_amount": [r"\bpst\b"],
    "qst_amount": [r"\bqst\b"],
    "consumption_tax_amount": [r"\bconsumption\s*tax\b"],
}

VENDOR_LINE_HINTS = [
    "ltd", "llp", "pvt", "inc", "corp", "corporation", "technologies", "solutions",
    "enterprises", "timber", "traders", "industries", "exports", "consulting",
    "chemicals", "textiles", "agro", "retail", "group", "co.", "co,", "company",
    "gmbh", "ag", "sarl", "sas", "s.a.", "s.a", "s.l.", "s.r.l.", "srl", "bv", "b.v.",
    "nv", "n.v.", "oy", "ab", "as", "a/s", "plc", "kk", "k.k.", "co., ltd", "co ltd",
    "sdn bhd", "pte", "pte. ltd", "pty", "pty ltd", "oyj", "spa", "s.p.a.",
]
INVOICE_TITLE_STOPWORDS = [
    "tax invoice", "invoice", "bill", "receipt", "proforma invoice", "credit note",
    "debit note", "original", "duplicate", "tax invoice / bill of supply",
    "commercial invoice", "sales receipt", "order confirmation", "statement",
]
LABEL_STOPWORDS = ["dated", "gstin", "state", "name", "code", "buyer", "bill", "to", "invoice", "no", "date"]

TABLE_HEADER_HINTS = {
    "description": ["description", "item", "product", "particulars", "goods", "details"],
    "quantity": ["qty", "quantity", "units"],
    "unit_price": ["rate", "price", "unit price", "unit rate", "unit cost"],
    "tax": ["tax", "gst", "vat", "cgst", "sgst", "igst", "hst", "pst"],
    "amount": ["amount", "total", "value", "subtotal"],
}

LINE_ITEM_SKIP_KEYWORDS = ['total', 'subtotal', 'tax', 'cgst', 'sgst', 'igst', 'utgst', 'gst', 'hst', 'pst', 'qst',
                            'vat', 'consumption tax', 'amount in words', 'declaration', 'gstin', 'invoice',
                            'bill to', 'buyer', 'ship to', 'place of supply', 'authorised', 'authorized',
                            'signatory', 'e. & o.e', 'sold by', 'consignee', 'terms', 'note', 'company', 'pan',
                            'round off', 'rounding', 'less :', 'less:', 'payable', 'iban', 'swift', 'bank details',
                            'routing number', 'account number', 'payment terms']
LINE_ITEM_HEADER_KEYWORDS = ['description', 'particulars', 'qty', 'quantity', 'rate', 'amount', 'hsn', 'sac', 'goods']

GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
    "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman and Diu", "26": "Dadra and Nagar Haveli", "27": "Maharashtra",
    "28": "Andhra Pradesh (old)", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands",
    "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}
GSTIN_PATTERN = r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b"
BUYER_SECTION_MARKERS = [r"\bbill\s*to\b", r"\bbuyer\b", r"\bconsignee\b", r"\bship\s*to\b", r"\bsold\s*to\b"]

TAX_ID_PATTERNS = [
    ("GSTIN", GSTIN_PATTERN),
    ("VAT", r"\bVAT\s*(?:Reg(?:istration)?\.?\s*(?:No\.?|Number)?|No\.?)\s*[:\-]?\s*([A-Z]{2}\s?[\d\s]{7,12})"),
    ("EIN", r"\bEIN[:\s]*(\d{2}-\d{7})\b"),
    ("ABN", r"\bABN[:\s]*([\d\s]{9,14})\b"),
    ("IEC", r"\bIEC[:\s]*([A-Z0-9]{8,10})\b"),
]


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
    for m in re.finditer(r"(?:tax\s*invoice|invoice|inv|bill|receipt|order)\s*(?:no\.?|number|num|#|id)", text, re.IGNORECASE):
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


LINE_ITEM_UNIT_WORDS = {'kg', 'kgs', 'ltr', 'ltrs', 'litre', 'litres', 'liter', 'liters', 'ml', 'gm', 'gms',
                         'gram', 'grams', 'mm', 'cm', 'in', 'inch', 'inches', 'w', 'watt', 'watts', 'gsm',
                         'oz', 'lb', 'lbs', 'pcs', 'nos', 'unit', 'units', 'mtr', 'mtrs', 'meter', 'meters',
                         'ft', 'feet', 'yd', 'yard', 'yards', 'box', 'boxes', 'set', 'sets', 'pair', 'pairs'}


def _find_desc_and_tail(rest: str):
    """Splits 'rest' into (description, tail) at the first standalone
    numeric token -- treating numbers glued to letters (55in, 65W) and
    numbers followed by a unit word (40 Kg) as part of the description,
    not the start of the numeric fields."""
    tokens = rest.split(' ')
    desc_tokens = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        clean = tok.strip('(),')
        is_plain_number = bool(re.fullmatch(r'[\d,]+\.?\d*', clean))
        is_glued_number = bool(re.fullmatch(r'[\d,]+\.?\d*[A-Za-z]+', clean)) and not is_plain_number
        if is_glued_number:
            desc_tokens.append(tok)
            i += 1
            continue
        if is_plain_number:
            next_tok = tokens[i + 1].strip('.,').lower() if i + 1 < len(tokens) else ''
            if next_tok in LINE_ITEM_UNIT_WORDS:
                desc_tokens.append(tok)
                i += 1
                continue
            break
        desc_tokens.append(tok)
        i += 1
    desc = ' '.join(desc_tokens).strip()
    tail = ' ' + ' '.join(tokens[i:]) if i < len(tokens) else ''
    return desc, tail


def _extract_line_items_generic(text: str) -> list[dict]:
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
        desc, tail = _find_desc_and_tail(rest)
        if not desc or len(desc) < 3:
            continue
        if desc.rstrip().endswith(":"):
            continue
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


def _extract_seller_buyer_gstins(text: str):
    matches = list(re.finditer(GSTIN_PATTERN, text))
    if not matches:
        return None, None
    buyer_marker = None
    for kw in BUYER_SECTION_MARKERS:
        m = re.search(kw, text, re.IGNORECASE)
        if m and (buyer_marker is None or m.start() < buyer_marker):
            buyer_marker = m.start()
    seller, buyer = None, None
    for m in matches:
        val = m.group(0)
        if buyer_marker is not None and m.start() >= buyer_marker:
            if buyer is None:
                buyer = val
        elif seller is None:
            seller = val
    if seller is None:
        seller = matches[0].group(0)
    if buyer is None:
        for m in matches:
            if m.group(0) != seller:
                buyer = m.group(0)
                break
    return seller, buyer


def _infer_tax_split(tax_amount, seller_gstin, buyer_gstin, full_text):
    if tax_amount is None:
        return None, None, None, None
    seller_state = GST_STATE_CODES.get(seller_gstin[:2]) if seller_gstin else None
    buyer_state = GST_STATE_CODES.get(buyer_gstin[:2]) if buyer_gstin else None
    if not buyer_state:
        m = re.search(r"place\s*of\s*supply\s*[:\-]?\s*([A-Za-z &]+)", full_text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().lower()
            for name in GST_STATE_CODES.values():
                if name.lower() in candidate:
                    buyer_state = name
                    break
    if not seller_state or not buyer_state:
        return None, None, None, None
    amt = float(tax_amount)
    if seller_state == buyer_state:
        half = round(amt / 2, 2)
        return f"{half:.2f}", f"{half:.2f}", None, "inferred_same_state"
    return None, None, f"{amt:.2f}", "inferred_different_state"


def _find_tax_ids(text: str) -> list:
    matches = []
    for typ, pat in TAX_ID_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            val = (m.group(1) if m.groups() else m.group(0)).strip()
            val = re.sub(r"\s+", " ", val).upper()
            matches.append((m.start(), typ, val))
    matches.sort(key=lambda x: x[0])
    return matches


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
    seller_gstin, buyer_gstin = _extract_seller_buyer_gstins(full_text)

    tax_split_source = "stated" if any(tax_components.values()) else None
    if not any(tax_components.values()) and tax is not None:
        cgst, sgst, igst, basis = _infer_tax_split(tax, seller_gstin, buyer_gstin, full_text)
        if basis:
            tax_components["cgst_amount"] = cgst
            tax_components["sgst_amount"] = sgst
            tax_components["igst_amount"] = igst
            tax_split_source = basis

    fields_found = sum(1 for v in [vendor, invoice_number, date, total] if v)
    confidence = round(fields_found / 4, 2)

    return {
        "vendor": vendor,
        "seller_gstin": seller_gstin,
        "buyer_gstin": buyer_gstin,
        "invoice_number": invoice_number,
        "date": date,
        "total_amount": total,
        "tax_amount": tax,
        "cgst_amount": tax_components["cgst_amount"],
        "sgst_amount": tax_components["sgst_amount"],
        "igst_amount": tax_components["igst_amount"],
        "vat_amount": tax_components["vat_amount"],
        "sales_tax_amount": tax_components["sales_tax_amount"],
        "gst_amount": tax_components["gst_amount"],
        "hst_amount": tax_components["hst_amount"],
        "pst_amount": tax_components["pst_amount"],
        "qst_amount": tax_components["qst_amount"],
        "consumption_tax_amount": tax_components["consumption_tax_amount"],
        "tax_split_source": tax_split_source,
        "line_items": line_items,
        "confidence": confidence,
        "page_count": parsed["page_count"],
        "raw_text_available": True,
    }
