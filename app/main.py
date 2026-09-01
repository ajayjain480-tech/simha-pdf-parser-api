from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app import database as db
from app.auth import verify_request
from app.parser import parse_pdf, parse_pdf_text_only
from app.invoice import extract_invoice_fields
from app.payments import router as payments_router

app = FastAPI(
    title="Simha Techlabs PDF Parser API",
    description="Extract text, tables and metadata from PDF documents.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(payments_router)

MAX_FILE_SIZE_MB = 15


@app.on_event("startup")
def startup():
    db.init_db()


@app.get("/health")
def health():
    return {"status": "ok", "service": "pdf-parser-api"}


@app.post("/v1/parse")
async def parse(
    file: UploadFile = File(...),
    extract_tables: bool = Query(default=True),
    fast_mode: bool = Query(default=False, description="Skip table extraction for speed"),
    auth=Depends(verify_request),
):
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(400, "Only PDF files are accepted")

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE_MB}MB limit")

    try:
        if fast_mode:
            result = parse_pdf_text_only(file_bytes)
        else:
            result = parse_pdf(file_bytes, extract_tables=extract_tables)
    except Exception as e:
        raise HTTPException(422, f"Could not parse PDF: {str(e)}")

    result["_meta"] = {"auth_source": auth.get("source")}
    return result


@app.post("/v1/parse/invoice")
async def parse_invoice(
    file: UploadFile = File(...),
    auth=Depends(verify_request),
):
    """
    Structured invoice/receipt extraction — the paid, higher-margin tier.
    Returns vendor, invoice number, date, total, tax, and line items.
    """
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(400, "Only PDF files are accepted")

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE_MB}MB limit")

    try:
        result = extract_invoice_fields(file_bytes)
    except Exception as e:
        raise HTTPException(422, f"Could not extract invoice fields: {str(e)}")

    result["_meta"] = {"auth_source": auth.get("source")}
    return result


@app.post("/v1/keys/free")
def issue_free_key(email: str):
    """Self-serve free-tier key issuance (rate-limited by IP in production —
    add e.g. slowapi if you expose this publicly without a signup gate)."""
    existing = None
    with db.get_conn() as conn:
        cur = conn.execute("SELECT api_key FROM api_keys WHERE email = ? AND plan = 'free'", (email,))
        row = cur.fetchone()
        if row:
            existing = row[0]
    if existing:
        return {"api_key": existing, "plan": "free", "note": "existing key returned"}
    key = db.create_api_key(email=email, plan="free")
    return {"api_key": key, "plan": "free"}


@app.get("/v1/plans")
def list_plans():
    return db.PLANS
