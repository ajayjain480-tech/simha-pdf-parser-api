import os
from fastapi import Header, HTTPException, status
from app import database as db

# When listed on RapidAPI, RapidAPI acts as the gateway and forwards a
# proxy secret header. Verifying it stops people from hitting your API
# directly and bypassing RapidAPI's own billing.
RAPIDAPI_PROXY_SECRET = os.environ.get("RAPIDAPI_PROXY_SECRET", "")


async def verify_request(
    x_api_key: str = Header(default=None),
    x_rapidapi_proxy_secret: str = Header(default=None),
    x_rapidapi_user: str = Header(default=None),
):
    """
    Two supported auth paths:
    1. Direct customers: X-API-Key header, checked + rate-limited against our DB.
    2. RapidAPI customers: RapidAPI validates their own subscribers and forwards
       X-RapidAPI-Proxy-Secret. We just confirm the request truly came from
       RapidAPI's gateway and skip our own quota (RapidAPI meters usage for you).
    """
    if RAPIDAPI_PROXY_SECRET and x_rapidapi_proxy_secret == RAPIDAPI_PROXY_SECRET:
        return {"source": "rapidapi", "user": x_rapidapi_user}

    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key header")

    record = db.get_key_record(x_api_key)
    if not record or not record["active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or inactive API key")

    quota = db.PLANS.get(record["plan"], db.PLANS["free"])["quota"]
    ok, count = db.increment_and_check_usage(x_api_key, quota)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Monthly quota exceeded ({quota} requests on '{record['plan']}' plan). Upgrade your plan.",
        )

    return {"source": "direct", "email": record["email"], "plan": record["plan"], "usage_this_month": count}
