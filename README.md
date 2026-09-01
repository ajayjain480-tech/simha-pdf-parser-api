# Simha Techlabs — PDF Parser API

FastAPI service that extracts text, tables, and metadata from PDFs, with
API-key auth, plan-based quotas, and Razorpay payment → auto key issuance.

## 1. Run locally
```bash
pip install -r requirements.txt
export DB_PATH=./pdfparser.db
uvicorn app.main:app --reload
```
Test:
```bash
curl -X POST http://localhost:8000/v1/keys/free -d "email=you@example.com"
curl -X POST "http://localhost:8000/v1/parse" \
  -H "X-API-Key: <key from above>" \
  -F "file=@sample.pdf"
```

## 2. Deploy to Render (Singapore)
1. Push this folder to a GitHub repo.
2. In Render: **New → Blueprint**, point at the repo — `render.yaml` sets
   region `singapore`, disk, and health check automatically.
3. In the service's **Environment** tab, set the secrets Render left blank:
   `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`,
   `RAPIDAPI_PROXY_SECRET` (generate any random string for the last one —
   you'll reuse it in RapidAPI's config in step 4).
4. Deploy. Your base URL will be `https://simha-pdf-parser-api.onrender.com`.

**Note on `/data`:** Render's disk is persistent but tied to a single
instance — fine to start, but for real scale move `payments`/`api_keys`
to Render's managed Postgres (a one-line swap: point `DB_PATH`-style logic
at `psycopg2` instead of `sqlite3`). Ask me when you're ready and I'll do
that migration.

## 3. Razorpay payments
- `POST /pay/razorpay-order` — frontend calls this with `{email, plan, currency}`,
  gets back an `order_id` to hand to Razorpay Checkout.js.
- Razorpay Dashboard → Webhooks → add `https://<your-app>/pay/razorpay-webhook`,
  subscribe to `payment.captured`, and set the same secret as
  `RAZORPAY_WEBHOOK_SECRET`. On a captured payment, the webhook auto-creates
  an API key and stores it against the order — poll `/pay/status/{order_id}`
  from your frontend to show the customer their key.
- **On Razorpay + PayPal:** these are two separate, competing payment
  gateways — you can't "enroll" PayPal inside Razorpay. For international
  cards, Razorpay itself supports 100+ currencies once your international
  payments feature is activated (needs IEC + AD Code, which you have via
  your export registration). If you still want PayPal specifically as a
  second, independent checkout option, I can add a parallel
  `/pay/paypal-order` route using PayPal's Orders API — same pattern as the
  Razorpay one. Let me know and I'll wire it in.

## 4. List on RapidAPI (manual, no API for this step)
1. Create a RapidAPI provider account → **Add New API**.
2. Import via OpenAPI: once deployed, your spec is auto-generated at
   `https://<your-app>/openapi.json` — paste that URL into RapidAPI's import.
3. Set the base URL to your Render URL.
4. Under **Security**, RapidAPI will forward `X-RapidAPI-Proxy-Secret` —
   paste the same value you set as `RAPIDAPI_PROXY_SECRET` on Render. The
   API already checks this header (see `app/auth.py`) so RapidAPI
   subscribers skip your own key/quota system entirely — RapidAPI bills
   and doles out access on your behalf.
5. Define your pricing tiers in RapidAPI's monetization tab (separate from
   the direct-sale plans in `database.py` — you can run both channels).
6. Submit for review.

## 5. Endpoints
| Endpoint | Purpose |
|---|---|
| `POST /v1/parse` | Upload a PDF, get text/tables/metadata JSON |
| `POST /v1/keys/free` | Issue a free-tier key by email |
| `GET /v1/plans` | List plans and quotas |
| `POST /pay/razorpay-order` | Create a Razorpay order for a paid plan |
| `POST /pay/razorpay-webhook` | Razorpay calls this on payment events |
| `GET /pay/status/{order_id}` | Poll payment/key issuance status |
| `GET /health` | Health check (used by Render) |
