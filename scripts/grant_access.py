"""Admin tool : grant a paying buyer access to LeadForges.

Workflow
--------
After a successful Stripe payment, run :

    python -m scripts.grant_access \\
        --name "Thales Group" \\
        --email "contact@thales.com" \\
        --days 200

The script :
  1. Generates a 16-char alphanumeric password (cryptographically random).
  2. Appends a new ``[[buyers]]`` block to ``.streamlit/secrets.toml``.
  3. Prints :
       • The password (copy-paste into your delivery email).
       • A ready-to-send delivery email (subject + body).
       • Next-step instructions (git push to trigger Cloud rebuild).

It does NOT auto-send the email, does NOT auto-push to git. You stay
in control of every step.

Re-running with the same ``--name`` is safe — by default the existing
entry is left alone and the script exits with a warning. Use
``--rotate`` to regenerate the password for an existing buyer (e.g.
if the old one leaked).

Options
-------
  --name      buyer display name (required)
  --email     buyer contact email (recommended for follow-up)
  --days      access duration in days from today (default : 200)
  --expires   explicit expiry YYYY-MM-DD (overrides --days)
  --secrets   path to secrets.toml (default : .streamlit/secrets.toml)
  --rotate    regenerate password for existing buyer
  --dry-run   print what would be added, don't touch the file
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional


_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SECRETS = _ROOT / ".streamlit" / "secrets.toml"

# Alphanumeric only (no symbols) — safer to type, copy/paste, email.
_PASSWORD_ALPHABET = string.ascii_letters + string.digits
_PASSWORD_LEN = 20


def generate_password(length: int = _PASSWORD_LEN) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def _ensure_secrets_exists(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# LeadForges secrets — DO NOT COMMIT TO GIT\n\n"
        "APP_PASSWORD = \"\"\n\n",
        encoding="utf-8",
    )
    print(f"📁 Created empty {path}")


def _existing_buyer_block(secrets_text: str, name: str) -> bool:
    """Return True if a ``[[buyers]]`` block with this name already exists."""
    needle = f'name     = "{name}"'
    alt = f'name = "{name}"'
    return needle in secrets_text or alt in secrets_text


def _append_buyer_block(
    secrets_path: Path, password: str, name: str, expires: date,
    email: Optional[str] = None,
) -> None:
    block = (
        "\n[[buyers]]\n"
        f'password = "{password}"\n'
        f'name     = "{name}"\n'
        f'expires  = "{expires.isoformat()}"\n'
    )
    if email:
        block += f'email    = "{email}"\n'
    with secrets_path.open("a", encoding="utf-8") as f:
        f.write(block)


def _rotate_password(
    secrets_path: Path, name: str, new_password: str,
) -> bool:
    """Find the buyer block by name and replace its password line.

    Returns True on success, False if the buyer block wasn't found.
    """
    text = secrets_path.read_text(encoding="utf-8")
    # Match the block boundaries simply : split on [[buyers]] occurrences
    parts = text.split("[[buyers]]")
    out_parts = [parts[0]]
    found = False
    for part in parts[1:]:
        if not found and (
            f'name     = "{name}"' in part or f'name = "{name}"' in part
        ):
            # Replace the password line
            new_lines = []
            for line in part.splitlines():
                stripped = line.strip()
                if stripped.startswith("password"):
                    new_lines.append(f'password = "{new_password}"')
                else:
                    new_lines.append(line)
            out_parts.append("[[buyers]]" + "\n".join(new_lines))
            found = True
        else:
            out_parts.append("[[buyers]]" + part)
    if found:
        secrets_path.write_text("".join(out_parts), encoding="utf-8")
    return found


def _delivery_email(
    name: str, password: str, expires: date,
    app_url: str = "https://leadforges.streamlit.app",
) -> tuple[str, str]:
    """Generate the subject + body of the delivery email."""
    subject = "🎯 LeadForges — votre accès Eurosatory 2026"
    body = f"""\
Bonjour,

Bienvenue chez LeadForges. Voici votre accès personnel à la base de
prospection Eurosatory 2026 :

  🔗 URL          : {app_url}
  👤 Buyer        : {name}
  🔐 Mot de passe : {password}
  📅 Valide jusqu'au : {expires.strftime('%d %B %Y')}

Ce que vous y trouvez :
  • 2 580 sociétés exposantes Eurosatory pré-qualifiées
  • 13 540 contacts (acheteurs, sales, R&D, direction)
  • 19 listes ciblées prêtes à exporter
  • Filtres avancés par zone, type d'entreprise, certifications, etc.
  • Exports XLSX / CSV illimités vers votre CRM

Onboarding en 30 min :
  1. Connectez-vous avec le mot de passe ci-dessus
  2. Sidebar gauche → définissez vos filtres (Type d'entreprise, Pays, ...)
  3. Tableau Companies → cochez ⭐ sur vos cibles, exportez en XLSX
  4. Onglet 📡 Attendance Signals → contacts nommés avec emails / LinkedIn

Besoin d'aide ? Répondez à ce message ou écrivez à support@leadforges.com.

Bonne préparation,
L'équipe LeadForges
"""
    return subject, body


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Grant a paying buyer access to LeadForges.",
    )
    ap.add_argument("--name", required=True, help="Buyer display name")
    ap.add_argument("--email", help="Buyer contact email (optional)")
    ap.add_argument("--days", type=int, default=200,
                    help="Access duration in days (default : 200)")
    ap.add_argument("--expires", help="Explicit expiry YYYY-MM-DD")
    ap.add_argument("--secrets", default=str(_DEFAULT_SECRETS),
                    help="Path to secrets.toml")
    ap.add_argument("--rotate", action="store_true",
                    help="Regenerate password for an existing buyer")
    ap.add_argument("--dry-run", action="store_true",
                    help="Don't touch the file, just print what would happen")
    ap.add_argument("--app-url", default="https://leadforges.streamlit.app",
                    help="Public LeadForges URL (in the email template)")
    args = ap.parse_args()

    secrets_path = Path(args.secrets)
    _ensure_secrets_exists(secrets_path)
    text = secrets_path.read_text(encoding="utf-8")
    exists = _existing_buyer_block(text, args.name)

    if exists and not args.rotate:
        print(
            f"⚠️  Buyer '{args.name}' already has access. "
            f"Use --rotate to regenerate password, or --name with a "
            f"different value."
        )
        return 1

    # Compute expiry
    if args.expires:
        try:
            expires = date.fromisoformat(args.expires)
        except ValueError:
            print(f"❌ Invalid --expires : {args.expires} (expected YYYY-MM-DD)")
            return 2
    else:
        expires = date.today() + timedelta(days=args.days)

    pw = generate_password()
    action = "ROTATE" if exists else "GRANT"

    print()
    print("═" * 70)
    print(f"  {action} access for : {args.name}")
    print("═" * 70)
    print(f"  Password : {pw}")
    print(f"  Expires  : {expires} ({(expires - date.today()).days} days)")
    if args.email:
        print(f"  Email    : {args.email}")
    print("═" * 70)
    print()

    if args.dry_run:
        print("(dry-run — secrets.toml not modified)")
    else:
        if exists and args.rotate:
            if not _rotate_password(secrets_path, args.name, pw):
                print(f"❌ Could not find buyer block for {args.name!r}")
                return 3
            print(f"✓ Password rotated in {secrets_path}")
        else:
            _append_buyer_block(secrets_path, pw, args.name, expires, args.email)
            print(f"✓ New buyer block appended to {secrets_path}")

    # Email template
    subject, body = _delivery_email(args.name, pw, expires, args.app_url)
    print()
    print("─" * 70)
    print("  📧 Email to send to the buyer :")
    print("─" * 70)
    print(f"To      : {args.email or '(specify with --email)'}")
    print(f"Subject : {subject}")
    print()
    print(body)
    print("─" * 70)
    print()
    print("Next steps :")
    print(f"  1. Send the email above to {args.email or 'the buyer'}.")
    if not args.dry_run:
        print(f"  2. git add {secrets_path} && git commit && git push")
        print("     → Streamlit Cloud rebuilds in 30-90 s.")
        print("     ⚠️  Make sure .streamlit/secrets.toml is in .gitignore")
        print("        AND that you've also pasted the secrets into the")
        print("        Streamlit Cloud Secrets UI (it doesn't read the file).")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
