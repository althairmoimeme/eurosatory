"""Vercel serverless function : Stripe webhook → Turso → Resend.

Flow
────
  Stripe POSTs ``checkout.session.completed`` to /api/stripe-webhook
    → verify signature (HMAC-SHA256, no SDK dep)
    → generate 20-char password
    → INSERT into Turso ``buyers`` table (idempotent on stripe_session_id)
    → Resend email to buyer with credentials
    → Resend email to admin with sale notification

Env vars (set in Vercel project settings)
─────────────────────────────────────────
  STRIPE_WEBHOOK_SECRET   — whsec_… from Stripe webhook config
  TURSO_DATABASE_URL      — libsql://<db>.turso.io
  TURSO_AUTH_TOKEN        — Turso DB token
  RESEND_API_KEY          — re_… from Resend
  RESEND_FROM             — e.g. "LeadForges <onboarding@resend.dev>"
  ADMIN_EMAIL             — your address for sale notifications
  APP_URL                 — https://leadforgesdemo.streamlit.app
  ACCESS_DURATION_DAYS    — default 78 (until late July)

Deployment
──────────
  Drop this file in landing/api/ → Vercel auto-detects as a Python function
  exposed at https://<your-app>.vercel.app/api/stripe-webhook
"""

from __future__ import annotations

import hmac
import hashlib
import json
import os
import secrets
import string
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler

import httpx


# ─── ENV ──────────────────────────────────────────────────────────────

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM",
                             "LeadForges <onboarding@resend.dev>")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "a.bertantoine@gmail.com")
APP_URL = os.environ.get("APP_URL", "https://leadforgesdemo.streamlit.app")
ACCESS_DAYS = int(os.environ.get("ACCESS_DURATION_DAYS", "78"))


# ─── HELPERS ──────────────────────────────────────────────────────────

def _gen_password(n: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _verify_stripe_signature(payload: bytes, sig_header: str,
                             secret: str, tolerance_seconds: int = 300) -> bool:
    """Verify Stripe webhook signature without the SDK.
    Signature scheme : ``t=<timestamp>,v1=<hex>`` where
    v1 = HMAC-SHA256(timestamp + '.' + payload, secret).
    """
    if not sig_header or not secret:
        return False
    try:
        parts = dict(p.split("=", 1) for p in sig_header.split(","))
        ts = int(parts["t"])
        if abs(time.time() - ts) > tolerance_seconds:
            return False  # stale
        signed_payload = f"{ts}.".encode() + payload
        expected = hmac.new(
            secret.encode(), signed_payload, hashlib.sha256
        ).hexdigest()
        provided = parts.get("v1", "")
        return hmac.compare_digest(expected, provided)
    except Exception:  # noqa: BLE001
        return False


# ─── TURSO (libSQL HTTP API) ──────────────────────────────────────────

def _turso_http_url() -> str:
    """Convert libsql:// → https:// for the HTTP API."""
    u = TURSO_DATABASE_URL.replace("libsql://", "https://")
    return u.rstrip("/") + "/v2/pipeline"


def _turso_execute(sql: str, args: list | None = None) -> dict:
    """Run a single statement via the libSQL HTTP /v2/pipeline endpoint."""
    payload = {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": sql,
                    "args": [
                        {"type": "text", "value": str(a)} if a is not None
                        else {"type": "null"}
                        for a in (args or [])
                    ],
                },
            },
            {"type": "close"},
        ]
    }
    r = httpx.post(
        _turso_http_url(),
        headers={"Authorization": f"Bearer {TURSO_AUTH_TOKEN}"},
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _turso_ensure_schema() -> None:
    """Idempotent CREATE TABLE — called on first webhook hit."""
    _turso_execute(
        """
        CREATE TABLE IF NOT EXISTS buyers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            password TEXT NOT NULL UNIQUE,
            name TEXT,
            email TEXT,
            expires_at TEXT NOT NULL,
            stripe_session_id TEXT UNIQUE,
            amount_paid_cents INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT
        )
        """,
    )
    _turso_execute(
        "CREATE INDEX IF NOT EXISTS idx_buyers_password ON buyers(password)",
    )


def _turso_insert_buyer(*, password: str, name: str, email: str,
                        expires_at: str, stripe_session_id: str,
                        amount_cents: int) -> bool:
    """Returns True if inserted, False if conflict (already processed)."""
    try:
        _turso_execute(
            """
            INSERT INTO buyers
              (password, name, email, expires_at, stripe_session_id, amount_paid_cents)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [password, name, email, expires_at, stripe_session_id, amount_cents],
        )
        return True
    except httpx.HTTPStatusError as e:
        body = e.response.text.lower()
        if "unique" in body or "constraint" in body:
            return False
        raise


# ─── RESEND ───────────────────────────────────────────────────────────

def _resend_send(to: str, subject: str, html: str, text: str) -> None:
    if not RESEND_API_KEY:
        print(f"[resend] skipped (no key) → {to}: {subject}")
        return
    r = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                 "Content-Type": "application/json"},
        json={"from": RESEND_FROM, "to": [to], "subject": subject,
              "html": html, "text": text},
        timeout=10,
    )
    if r.status_code >= 300:
        print(f"[resend] ERROR {r.status_code}: {r.text[:200]}")
    else:
        print(f"[resend] OK → {to}")


def _buyer_email(name: str, password: str, expires: str) -> tuple[str, str]:
    first = (name or "et bienvenue").split()[0]
    text = f"""Bonjour {first},

Merci pour ton achat de LeadForges — voici tes identifiants :

  URL          : {APP_URL}
  Mot de passe : {password}
  Accès jusqu'au : {expires}

Comment commencer
  1. Ouvre {APP_URL}
  2. Colle ton mot de passe ci-dessus
  3. Tu arrives sur les 2 580 exposants Eurosatory 2026
  4. Onglet "Attendance Signals" pour les LinkedIn / presse
  5. Onglet "Listes ciblées" pour sauvegarder tes segments

Besoin d'aide ?
  Réponds à cet email, ou book 15 min :
  https://calendly.com/a-bertantoine/demo-leadforges

Bonne préparation pour Eurosatory 2026.

— LeadForges
"""
    html = f"""<!DOCTYPE html>
<html><body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif;
                   max-width: 580px; margin: 0 auto; padding: 24px; color: #0A0A0A;">
  <div style="text-align: center; margin-bottom: 32px;">
    <h1 style="font-size: 24px; font-weight: 700; margin: 0;">LeadForges</h1>
  </div>
  <p style="font-size: 16px; line-height: 1.6;">Bonjour {first},</p>
  <p style="font-size: 16px; line-height: 1.6;">
    Merci pour ton achat — voici tes identifiants :
  </p>
  <div style="background: #F4F4F5; border-left: 3px solid #0A0A0A;
              padding: 20px 24px; margin: 24px 0; border-radius: 6px;
              font-family: 'JetBrains Mono', ui-monospace, monospace;">
    <div style="margin-bottom: 12px;"><strong>URL</strong><br>
      <a href="{APP_URL}" style="color: #0B2E4A;">{APP_URL}</a></div>
    <div style="margin-bottom: 12px;"><strong>Mot de passe</strong><br>
      <code style="background:#fff;padding:6px 10px;border-radius:4px;
                   font-size:14px;border:1px solid #E4E4E7;">{password}</code></div>
    <div><strong>Accès jusqu'au</strong><br>{expires}</div>
  </div>
  <h3 style="font-size: 16px; margin-top: 32px;">Comment commencer</h3>
  <ol style="line-height: 1.7; font-size: 15px;">
    <li>Ouvre <a href="{APP_URL}">{APP_URL}</a></li>
    <li>Colle ton mot de passe</li>
    <li>Tu arrives sur les 2 580 exposants</li>
    <li>Onglet <strong>Attendance Signals</strong> pour les LinkedIn / presse</li>
    <li>Onglet <strong>Listes ciblées</strong> pour sauvegarder tes segments</li>
  </ol>
  <p style="font-size: 14px; color: #71717A; margin-top: 32px;
            border-top: 1px solid #E4E4E7; padding-top: 16px;">
    Besoin d'aide ? Réponds à cet email, ou
    <a href="https://calendly.com/a-bertantoine/demo-leadforges">book 15 min</a>.
  </p>
</body></html>"""
    return text, html


def _admin_email(name: str, email: str, amount_cents: int,
                 password: str, stripe_session: str) -> tuple[str, str]:
    text = f"""💰 NOUVELLE VENTE LeadForges

  Buyer : {name}
  Email : {email}
  Montant : {amount_cents / 100:.2f} €
  Stripe session : {stripe_session}
  Mot de passe créé : {password}

Le buyer a été inséré dans Turso et l'email de livraison a été envoyé.
"""
    return text, f"<pre>{text}</pre>"


# ─── VERCEL HANDLER ───────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self) -> None:
        self._send(200, {"ok": True, "service": "leadforges-stripe-webhook"})

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length)
            sig = self.headers.get("Stripe-Signature", "")

            if not _verify_stripe_signature(payload, sig, STRIPE_WEBHOOK_SECRET):
                self._send(400, {"error": "invalid signature"})
                return

            event = json.loads(payload.decode())
            if event.get("type") != "checkout.session.completed":
                self._send(200, {"received": True, "ignored": event.get("type")})
                return

            sess = event["data"]["object"]
            buyer_email = (sess.get("customer_details") or {}).get("email") \
                or sess.get("customer_email")
            buyer_name = (sess.get("customer_details") or {}).get("name") or "Buyer"
            amount = sess.get("amount_total") or 0
            stripe_session_id = sess.get("id", "")

            if not buyer_email:
                self._send(200, {"received": True, "error": "no_email"})
                return

            # Ensure schema (cheap when idempotent).
            _turso_ensure_schema()

            password = _gen_password()
            expires = (datetime.now(timezone.utc)
                       + timedelta(days=ACCESS_DAYS)).strftime("%Y-%m-%d")

            inserted = _turso_insert_buyer(
                password=password,
                name=buyer_name,
                email=buyer_email,
                expires_at=expires,
                stripe_session_id=stripe_session_id,
                amount_cents=amount,
            )

            if not inserted:
                # Already processed (Stripe retry). Don't re-send the email.
                self._send(200, {
                    "received": True, "duplicate": True,
                    "session": stripe_session_id,
                })
                return

            # Send buyer email
            text, html = _buyer_email(buyer_name, password, expires)
            _resend_send(buyer_email, "Tes accès LeadForges", html, text)

            # Notify admin
            atext, ahtml = _admin_email(
                buyer_name, buyer_email, amount, password, stripe_session_id,
            )
            _resend_send(ADMIN_EMAIL,
                         f"💰 LeadForges sale — {buyer_name}", ahtml, atext)

            self._send(200, {"received": True, "delivered": True})

        except Exception as e:  # noqa: BLE001
            print("WEBHOOK ERROR:", e)
            traceback.print_exc()
            self._send(500, {"error": "internal", "detail": str(e)})
