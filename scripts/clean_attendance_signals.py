"""Clean known data-quality issues found during the audit.

Fixes :
1. ``person_role`` values that look like email addresses → blank role,
   stash the email as ``[email] …`` line in notes (if not already there).
2. ``person_name`` values that look like email addresses → blank
   person_name and stash the email in notes.
3. Junk emails in notes (logo paths, '@2x' DPI hints, sentry/example domains,
   single-word "@" tokens) → drop the email line.
4. ``person_role`` longer than 100 chars → truncate at 100 with ellipsis.

Run :
    python -m scripts.clean_attendance_signals               # dry-run
    python -m scripts.clean_attendance_signals --execute     # apply
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "eurosatory.db"

EMAIL_LIKE_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
EMAIL_FULL_RX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
EMAIL_LINE_RX = re.compile(r"\[email\]\s*([^\s\n]+)")


def replace_or_add(notes: str, prefix: str, value: str) -> str:
    line = f"{prefix} {value}"
    lines = (notes or "").splitlines()
    out = []
    replaced = False
    for ln in lines:
        if ln.startswith(prefix):
            out.append(line)
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(line)
    return "\n".join(out).strip()


def is_junk_email(em: str) -> bool:
    em = em.lower().strip()
    if "@" not in em or "." not in em.split("@")[-1]:
        return True
    if any(em.endswith(s) for s in (".png", ".jpg", ".gif", ".svg", ".webp")):
        return True
    if any(s in em for s in ("@2x.", "@3x.", "wixpress.com", "sentry.io",
                              "@sentry", "@example.", "@test.")):
        return True
    if len(em) > 80 or len(em) < 6:
        return True
    if em.endswith("@"):
        return True
    return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    n_role_cleaned = 0
    n_name_cleaned = 0
    n_email_dropped = 0
    n_role_truncated = 0
    fixes = []

    # Pass 1: scan all rows
    cur.execute(
        "SELECT id, person_name, person_role, notes "
        "FROM attendance_signals"
    )
    rows = cur.fetchall()
    for r in rows:
        sid = r["id"]
        name = r["person_name"]
        role = r["person_role"]
        notes = r["notes"] or ""

        new_name = name
        new_role = role
        new_notes = notes
        changed = False

        # Fix 1: role that looks like an email
        if role and EMAIL_FULL_RX.match(role.strip()):
            email_in_role = role.strip().lower()
            new_role = ""
            if "[email]" not in new_notes:
                new_notes = replace_or_add(new_notes, "[email]", email_in_role)
            n_role_cleaned += 1
            changed = True
            if len(fixes) < 8:
                fixes.append(f"id={sid}: role looked like email → blanked, email stashed: {email_in_role}")

        # Fix 2: name that looks like an email
        if name and EMAIL_FULL_RX.match(name.strip()):
            email_in_name = name.strip().lower()
            new_name = ""
            if "[email]" not in new_notes:
                new_notes = replace_or_add(new_notes, "[email]", email_in_name)
            n_name_cleaned += 1
            changed = True
            if len(fixes) < 8:
                fixes.append(f"id={sid}: name was email → blanked, stashed: {email_in_name}")

        # Fix 3: junk email lines
        if "[email]" in new_notes:
            new_lines = []
            dropped_here = False
            for ln in new_notes.split("\n"):
                m = EMAIL_LINE_RX.match(ln.strip())
                if m and is_junk_email(m.group(1)):
                    dropped_here = True
                    continue
                new_lines.append(ln)
            if dropped_here:
                new_notes = "\n".join(new_lines).strip()
                n_email_dropped += 1
                changed = True

        # Fix 4: very long person_role
        if new_role and len(new_role) > 100:
            new_role = new_role[:97].rstrip() + "..."
            n_role_truncated += 1
            changed = True

        if changed and args.execute:
            cur.execute(
                "UPDATE attendance_signals "
                "SET person_name = ?, person_role = ?, notes = ? "
                "WHERE id = ?",
                (new_name or None, new_role or None, new_notes[:8000], sid),
            )

    if args.execute:
        con.commit()

    con.close()

    print(f"Roles that looked like an email   : {n_role_cleaned}")
    print(f"Names that looked like an email   : {n_name_cleaned}")
    print(f"Junk-email lines dropped from notes: {n_email_dropped}")
    print(f"Roles truncated at 100 chars      : {n_role_truncated}")
    if fixes:
        print("\nFix samples:")
        for f in fixes:
            print(f"  {f}")

    if not args.execute:
        print("\n[dry-run] re-run with --execute to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
