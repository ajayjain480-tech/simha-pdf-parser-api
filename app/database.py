"""
Lightweight SQLite persistence for API keys, plans, and usage.
On Render, mount a persistent disk and point DB_PATH at it, or swap this
for Render's managed Postgres (recommended for production revenue data).
"""
import sqlite3
import os
import secrets
import time
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "/data/pdfparser.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Plan definitions: name -> (monthly quota, price in paise INR, price in cents USD)
PLANS = {
    "free": {"quota": 50, "price_inr": 0, "price_usd": 0},
    "starter": {"quota": 2000, "price_inr": 99900, "price_usd": 1200},   # ₹999 / $12
    "pro": {"quota": 20000, "price_inr": 399900, "price_usd": 4900},    # ₹3999 / $49
    "scale": {"quota": 200000, "price_inr": 1499900, "price_usd": 17900}, # ₹14999 / $179
}


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                api_key TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                plan TEXT NOT NULL DEFAULT 'free',
                created_at INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage (
                api_key TEXT NOT NULL,
                period TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (api_key, period)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                order_id TEXT PRIMARY KEY,
                payment_id TEXT,
                api_key TEXT,
                email TEXT,
                plan TEXT,
                amount INTEGER,
                currency TEXT,
                gateway TEXT,
                status TEXT,
                created_at INTEGER
            )
        """)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def generate_api_key() -> str:
    return "pdfp_" + secrets.token_urlsafe(32)


def create_api_key(email: str, plan: str = "free") -> str:
    key = generate_api_key()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO api_keys (api_key, email, plan, created_at) VALUES (?, ?, ?, ?)",
            (key, email, plan, int(time.time())),
        )
        conn.commit()
    return key


def get_key_record(api_key: str):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT api_key, email, plan, active FROM api_keys WHERE api_key = ?",
            (api_key,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"api_key": row[0], "email": row[1], "plan": row[2], "active": bool(row[3])}


def set_plan(api_key: str, plan: str):
    with get_conn() as conn:
        conn.execute("UPDATE api_keys SET plan = ? WHERE api_key = ?", (plan, api_key))
        conn.commit()


def current_period() -> str:
    # Monthly quota window, e.g. "2026-08"
    return time.strftime("%Y-%m")


def increment_and_check_usage(api_key: str, quota: int):
    period = current_period()
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT count FROM usage WHERE api_key = ? AND period = ?",
            (api_key, period),
        )
        row = cur.fetchone()
        count = row[0] if row else 0
        if count >= quota:
            return False, count
        count += 1
        if row:
            conn.execute(
                "UPDATE usage SET count = ? WHERE api_key = ? AND period = ?",
                (count, api_key, period),
            )
        else:
            conn.execute(
                "INSERT INTO usage (api_key, period, count) VALUES (?, ?, ?)",
                (api_key, period, count),
            )
        conn.commit()
        return True, count


def record_payment(order_id, payment_id, api_key, email, plan, amount, currency, gateway, status):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO payments (order_id, payment_id, api_key, email, plan, amount, currency, gateway, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(order_id) DO UPDATE SET payment_id=excluded.payment_id, status=excluded.status""",
            (order_id, payment_id, api_key, email, plan, amount, currency, gateway, status, int(time.time())),
        )
        conn.commit()
