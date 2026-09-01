import os
import hmac
import hashlib
import json
import time

import razorpay
from fastapi import APIRouter, Request, HTTPException, Header
from pydantic import BaseModel, EmailStr

from app import database as db

router = APIRouter()

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) if RAZORPAY_KEY_ID else None


class CreateOrderRequest(BaseModel):
    email: EmailStr
    plan: str  # "starter" | "pro" | "scale"
    currency: str = "INR"  # "INR" or "USD" -- Razorpay International handles USD capture,
    # PayPal path (see /pay/paypal-order) is the alternative for non-Razorpay-supported geos


@router.post("/pay/razorpay-order")
def create_razorpay_order(body: CreateOrderRequest):
    if body.plan not in db.PLANS or body.plan == "free":
        raise HTTPException(400, "Invalid plan")
    if not client:
        raise HTTPException(500, "Razorpay not configured on server")

    plan_info = db.PLANS[body.plan]
    amount = plan_info["price_inr"] if body.currency.upper() == "INR" else plan_info["price_usd"]
    if amount <= 0:
        raise HTTPException(400, "Selected plan/currency has no price configured")

    order = client.order.create({
        "amount": amount,
        "currency": body.currency.upper(),
        "notes": {"email": body.email, "plan": body.plan},
        "payment_capture": 1,
    })

    db.record_payment(
        order_id=order["id"], payment_id=None, api_key=None, email=body.email,
        plan=body.plan, amount=amount, currency=body.currency.upper(),
        gateway="razorpay", status="created",
    )

    return {
        "order_id": order["id"],
        "amount": amount,
        "currency": body.currency.upper(),
        "razorpay_key_id": RAZORPAY_KEY_ID,  # safe to expose; used by Razorpay Checkout.js on the frontend
    }


@router.post("/pay/razorpay-webhook")
async def razorpay_webhook(request: Request, x_razorpay_signature: str = Header(default=None)):
    body = await request.body()

    if RAZORPAY_WEBHOOK_SECRET:
        expected = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, x_razorpay_signature or ""):
            raise HTTPException(400, "Invalid webhook signature")

    payload = json.loads(body)
    event = payload.get("event", "")

    if event == "payment.captured":
        payment_entity = payload["payload"]["payment"]["entity"]
        order_id = payment_entity["order_id"]
        payment_id = payment_entity["id"]
        notes = payment_entity.get("notes", {})
        email = notes.get("email")
        plan = notes.get("plan")

        if not email or not plan:
            # fall back to what we stored when the order was created
            raise HTTPException(400, "Missing email/plan in payment notes")

        new_key = db.create_api_key(email=email, plan=plan)
        db.record_payment(
            order_id=order_id, payment_id=payment_id, api_key=new_key, email=email,
            plan=plan, amount=payment_entity["amount"], currency=payment_entity["currency"],
            gateway="razorpay", status="captured",
        )
        # TODO: send `new_key` to the customer by email (e.g. via SendGrid/SES)
        # rather than relying on them polling an endpoint.

    return {"status": "ok"}


@router.get("/pay/status/{order_id}")
def payment_status(order_id: str):
    with db.get_conn() as conn:
        cur = conn.execute(
            "SELECT status, api_key FROM payments WHERE order_id = ?", (order_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Order not found")
        status_, api_key = row
        return {"status": status_, "api_key": api_key if status_ == "captured" else None}
