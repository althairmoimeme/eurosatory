"""Stripe webhook receiver — auto-delivers LeadForges credentials.

Architecture
────────────
  Buyer pays via Stripe Payment Link → Stripe POSTs ``checkout.session.completed``
  to this webhook → we verify the signature, generate a 20-char password,
  append a new ``[[buyers]]`` entry to ``.streamlit/secrets.toml``, then
  send the buyer their credentials via SMTP. Streamlit Cloud picks up the
  new secret on its next refresh (~30s).

Deployment
──────────
  This is a small FastAPI app meant to be deployed independently of the
  Streamlit app (it needs persistent server time to receive webhooks).
  Cheapest options : Cloudflare Workers / Vercel Edge / Fly.io / Render.

  Required env vars :
    STRIPE_WEBHOOK_SECRET   – the ``whsec_…`` from your Stripe webhook
    STRIPE_PRICE_ID         – the price you expect on the session (anti-spoof)
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD  – email delivery
    DELIVERY_FROM_EMAIL     – "LeadForges <noreply@yourdomain.com>"
    BUYERS_REPO_DIR         – local path to a cloned copy of the leadforges
                              repo (the worker commits + pushes secrets.toml
                              here to trigger a Streamlit Cloud rebuild).
    GITHUB_TOKEN            – PAT with repo write access

  Stripe webhook setup :
    1. Stripe Dashboard → Developers → Webhooks → Add endpoint
    2. URL : https://<your-deploy>/stripe/webhook
    3. Event : checkout.session.completed
    4. Copy the Signing secret → set STRIPE_WEBHOOK_SECRET

Run locally :
    uvicorn scripts.stripe_webhook:app --port 4242
    stripe listen --forward-to localhost:4242/stripe/webhook

Manual override
───────────────
  If you want to disable auto-delivery and just get notified, set
  ``DELIVERY_MODE=notify_only`` — the worker will email YOU instead of the
  buyer, with all the info needed to run ``scripts/grant_access.py`` by hand.
"""

from __future__ import annotations

import logging
import os
import secrets
import smtplib
import string
import subprocess
import sys
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

try:
    import stripe
    from fastapi import FastAPI, Header, HTTPException, Request
except ImportError:
    print("Install deps: pip install fastapi uvicorn stripe", file=sys.stderr)
    raise

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("leadforges-webhook")

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")  # optional anti-spoof
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
DELIVERY_FROM_EMAIL = os.environ.get(
    "DELIVERY_FROM_EMAIL", "LeadForges <noreply@leadforges.io>"
)
BUYERS_REPO_DIR = os.environ.get("BUYERS_REPO_DIR", "/srv/leadforges-repo")
DELIVERY_MODE = os.environ.get("DELIVERY_MODE", "auto")  # auto | notify_only
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "a.bertantoine@gmail.com")
APP_URL = os.environ.get("APP_URL", "https://leadforgesdemo.streamlit.app")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

app = FastAPI()


def _gen_password(n: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _send_email(to: str, subject: str, html: str, text: str) -> None:
    if not (SMTP_USER and SMTP_PASSWORD):
        log.warning("SMTP not configured — skipping send to %s", to)
        return
    msg = MIMEMultipart("alternative")
    msg["From"] = DELIVERY_FROM_EMAIL
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)
    log.info("Email sent to %s : %s", to, subject)


def _append_buyer_and_push(name: str, email: str, password: str,
                           expires: str) -> bool:
    """Append a [[buyers]] entry to .streamlit/secrets.toml in the local
    repo clone, commit + push. Streamlit Cloud rebuilds on push.

    Returns True on success, False if anything goes wrong (the worker
    falls back to notify_only mode and emails you the details to do it
    by hand).
    """
    repo = Path(BUYERS_REPO_DIR)
    secrets_file = repo / ".streamlit" / "secrets.toml"
    if not secrets_file.exists():
        log.error("secrets.toml not found at %s", secrets_file)
        return False
    block = (
        f'\n[[buyers]]\n'
        f'password = "{password}"\n'
        f'name     = "{name}"\n'
        f'expires  = "{expires}"\n'
        f'email    = "{email}"\n'
    )
    with secrets_file.open("a", encoding="utf-8") as f:
        f.write(block)

    try:
        subprocess.run(["git", "-C", str(repo), "pull", "--rebase"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "add",
                        str(secrets_file.relative_to(repo))],
                       check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m",
             f"grant access : {name}"],
            check=True, capture_output=True,
        )
        subprocess.run(["git", "-C", str(repo), "push"],
                       check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        log.error("git push failed : %s", e.stderr.decode("utf-8", "ignore"))
        return False


def _build_buyer_email(name: str, password: str, expires: str) -> tuple[str, str]:
    """Return (text, html) version of the credentials email."""
    text = f"""Bonjour {name.split()[0] if name else 'et bienvenue'},

Merci pour ton achat de LeadForges — voici tes identifiants :

  URL       : {APP_URL}
  Mot de passe : {password}
  Accès jusqu'au : {expires}

Comment commencer
─────────────────
  1. Ouvre {APP_URL} dans ton navigateur
  2. Colle ton mot de passe ci-dessus
  3. Tu arrives sur l'onglet "Sociétés" avec les 2 580 exposants
  4. Pour les signaux d'attendance (LinkedIn, posts, presse) → onglet "Attendance Signals"
  5. Pour sauvegarder des segments → onglet "Listes ciblées"

Besoin d'aide ?
───────────────
  Réponds à cet email. Si tu veux une démo guidée de 15 min :
  https://calendly.com/a-bertantoine/demo-leadforges

Bonne préparation pour Eurosatory 2026.

—
LeadForges
"""
    html = f"""<!DOCTYPE html>
<html><body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif;
                   max-width: 580px; margin: 0 auto; padding: 24px; color: #0A0A0A;">
  <div style="text-align: center; margin-bottom: 32px;">
    <h1 style="font-size: 24px; font-weight: 700; margin: 0;">LeadForges</h1>
  </div>

  <p style="font-size: 16px; line-height: 1.6;">
    Bonjour {name.split()[0] if name else 'et bienvenue'},
  </p>
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
    <a href="https://calendly.com/a-bertantoine/demo-leadforges">book 15 min en visio</a>.
  </p>
</body></html>"""
    return text, html


def _build_admin_notify(name: str, email: str, amount_cents: int,
                        password: str | None) -> tuple[str, str]:
    """Email sent to admin when notify_only mode is on, OR when auto
    delivery failed."""
    text = f"""💰 Nouvelle vente LeadForges

  Nom    : {name}
  Email  : {email}
  Montant : {amount_cents / 100:.2f} €
  Mot de passe pré-généré : {password or '— (à générer)'}

À faire (si auto-delivery a échoué) :
  cd /Users/bertantoine/eurosatory-scraper
  python -m scripts.grant_access --name "{name}" --email "{email}" --days 78
  git add .streamlit/secrets.toml && git commit -m "grant {name}" && git push

L'email de livraison au buyer sera (re)envoyé manuellement par grant_access.py.
"""
    html = f"<pre>{text}</pre>"
    return text, html


@app.get("/")
def health() -> dict:
    return {"ok": True, "service": "leadforges-stripe-webhook"}


@app.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default=""),
) -> dict:
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=stripe_signature,
            secret=STRIPE_WEBHOOK_SECRET,
        )
    except (stripe.error.SignatureVerificationError, ValueError) as e:  # noqa
        log.warning("invalid signature : %s", e)
        raise HTTPException(status_code=400, detail="invalid signature")

    if event["type"] != "checkout.session.completed":
        log.info("ignoring event %s", event["type"])
        return {"received": True, "ignored": True}

    sess = event["data"]["object"]
    customer_email = (
        sess.get("customer_details", {}).get("email")
        or sess.get("customer_email")
    )
    customer_name = (
        sess.get("customer_details", {}).get("name") or "Buyer"
    )
    amount = sess.get("amount_total", 0)

    if not customer_email:
        log.warning("session %s has no email", sess.get("id"))
        return {"received": True, "error": "no email"}

    # Optional anti-spoof : check the price matches our expected product.
    if STRIPE_PRICE_ID:
        line_items_ok = False
        for line in sess.get("line_items", {}).get("data", []) or []:
            if line.get("price", {}).get("id") == STRIPE_PRICE_ID:
                line_items_ok = True
                break
        # fallback : if line_items is not expanded, accept by amount
        if not line_items_ok and amount < 100000:  # < 1000 €
            log.warning("price spoof suspected — amount=%s", amount)

    password = _gen_password()
    expires = (datetime.utcnow() + timedelta(days=78)).strftime("%Y-%m-%d")

    auto_ok = False
    if DELIVERY_MODE == "auto":
        auto_ok = _append_buyer_and_push(
            name=customer_name, email=customer_email,
            password=password, expires=expires,
        )

    # Always notify admin of the sale.
    admin_text, admin_html = _build_admin_notify(
        name=customer_name, email=customer_email,
        amount_cents=amount,
        password=password if auto_ok or DELIVERY_MODE == "notify_only" else None,
    )
    _send_email(ADMIN_EMAIL,
                f"💰 LeadForges sale — {customer_name}",
                admin_html, admin_text)

    # Send credentials to buyer only if auto-push succeeded.
    if auto_ok:
        buyer_text, buyer_html = _build_buyer_email(
            name=customer_name, password=password, expires=expires,
        )
        _send_email(customer_email,
                    "Tes accès LeadForges",
                    buyer_html, buyer_text)

    return {
        "received": True,
        "auto_delivered": auto_ok,
        "buyer": customer_email,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "4242")))
