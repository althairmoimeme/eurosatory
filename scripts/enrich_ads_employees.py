"""Phase 3 of ADS Group enrichment — find C-Level / Director employees
of each UK member company via enrich.so's MCP, then find their pro emails.

Pipeline
--------
For each ``attendance_signals`` row with ``source_platform='ads-group-uk'``:

    1. Compute a LinkedIn company URL by slug-ifying the company name.
       (Failed look-ups cost 0 credits, so we try one slug per company
       and accept the misses.)
    2. Call ``employee_finder`` → ask for up to 2 UK employees with
       job_level in {C-Level, VP, Director}. Cost: 1 credit per result.
    3. For each returned person, call ``find_email`` (10 credits each)
       using the company domain extracted from the JSON-LD blob already
       persisted in ``notes``.
    4. Insert a NEW ``attendance_signals`` row per person (entity_type
       'person'), with name + role + LinkedIn URL + email, linked back
       to the parent company by ``company_name`` / ``canonical_company_name``.

Budget control
--------------
    --limit N       only process the first N ADS companies (pilot)
    --max-employees stop after this many person rows inserted
    --no-email      skip find_email (cheaper, no email recovery)

Idempotent : a re-run skips ADS companies that already have a child
person row from this script.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

DB_PATH = ROOT / "data" / "eurosatory.db"
ENRICH_BASE = "https://mcp.enrich.so/mcp"
ENRICH_KEY_ENV = "ENRICH_SO_API_KEY"


# ---------------------------------------------------------------------------
# Slug helpers — turn "2iC Limited" → "2ic-limited"
# ---------------------------------------------------------------------------


def linkedin_slug_candidates(company_name: str) -> list[str]:
    """Return candidate LinkedIn slugs in priority order.

    Slug rules (LinkedIn uses):
    - lowercase, ASCII-fold, '-' separators
    - drop most punctuation
    """
    name = (company_name or "").strip()
    if not name:
        return []
    n = name.lower()
    # Drop legal suffixes (try variants WITH and WITHOUT)
    legal_suffix_rx = re.compile(
        r"\s+(ltd|limited|plc|llp|inc|corp|sa|gmbh|sas|sarl|bv)\.?$"
    )
    n_nosuffix = legal_suffix_rx.sub("", n).strip()

    def slugify(s: str) -> str:
        s = s.lower()
        # Replace common chars
        s = s.replace("&", "and").replace("'", "").replace("’", "")
        s = re.sub(r"[^a-z0-9]+", "-", s)
        s = re.sub(r"-+", "-", s).strip("-")
        return s

    seen: list[str] = []
    for src in (n, n_nosuffix, n + " ltd", n + " limited"):
        s = slugify(src)
        if s and s not in seen:
            seen.append(s)
    return seen[:3]  # cap to keep API calls bounded


def domain_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        p = urlparse(url if "://" in url else "http://" + url)
        d = (p.netloc or p.path).lower()
        d = re.sub(r"^www\.", "", d).split("/")[0].split(":")[0]
        return d if "." in d else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Enrich.so MCP client (HTTP + SSE response)
# ---------------------------------------------------------------------------


class EnrichMCP:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session_id: Optional[str] = None
        self._client = httpx.AsyncClient(timeout=60)

    async def initialize(self) -> None:
        r = await self._client.post(
            ENRICH_BASE,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "eurosatory-scraper", "version": "1.0"},
                },
            },
        )
        r.raise_for_status()
        self.session_id = r.headers.get("mcp-session-id")
        await self._client.post(
            ENRICH_BASE,
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized",
                  "params": {}},
        )

    def _headers(self) -> dict:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id:
            h["mcp-session-id"] = self.session_id
        return h

    async def call(self, name: str, arguments: dict) -> Optional[dict]:
        rid = int(time.time() * 1_000_000) % 1_000_000_000
        try:
            r = await self._client.post(
                ENRICH_BASE,
                headers=self._headers(),
                json={
                    "jsonrpc": "2.0", "id": rid,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            print(f"   ✗ MCP transport: {e!r}")
            return None
        text = r.text
        for line in text.splitlines():
            if line.startswith("data: "):
                try:
                    payload = json.loads(line[6:])
                except Exception:
                    return None
                if "error" in payload:
                    return None
                content = payload.get("result", {}).get("content", [])
                if not content:
                    return None
                try:
                    return json.loads(content[0].get("text", "{}"))
                except Exception:
                    return None
        return None

    async def employee_finder(
        self,
        linkedin_url: str,
        max_results: int = 2,
    ) -> list[dict]:
        out = await self.call(
            "employee_finder",
            {
                "company_linkedin_url": linkedin_url,
                "country": ["United Kingdom"],
                "job_level": ["C-Level", "VP", "Director"],
                "max_results": max_results,
            },
        )
        if not out or not out.get("success"):
            return []
        return out.get("data", {}).get("results", []) or []

    async def find_email(
        self, first: str, last: str, domain: str,
    ) -> Optional[str]:
        out = await self.call(
            "find_email",
            {"firstName": first, "lastName": last, "domain": domain},
        )
        if not out or not out.get("success"):
            return None
        return (out.get("data") or {}).get("email")

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------


_BLOB_RX = re.compile(r"\[ads-detail\]\s*(\{.*?\})\s*(?:\n|$)", re.DOTALL)


def load_targets(con: sqlite3.Connection,
                 limit: Optional[int]) -> list[dict]:
    cur = con.cursor()
    cur.execute(
        "SELECT id, company_name, canonical_company_name, source_url, notes "
        "FROM attendance_signals "
        "WHERE source_platform='ads-group-uk' "
        "ORDER BY id"
    )
    rows = cur.fetchall()
    # Skip ADS companies that already have a child enrich-so person
    # (idempotent re-runs)
    cur.execute(
        "SELECT DISTINCT canonical_company_name "
        "FROM attendance_signals "
        "WHERE source_platform='enrich-so-ads-employee'"
    )
    already = {r[0] for r in cur.fetchall() if r[0]}
    out = []
    for sid, name, canon, url, notes in rows:
        if canon in already:
            continue
        # Extract domain from JSON-LD blob in notes
        domain = None
        m = _BLOB_RX.search(notes or "")
        if m:
            try:
                blob = json.loads(m.group(1))
                domain = domain_from_url(blob.get("website"))
            except Exception:  # noqa: BLE001
                pass
        out.append({
            "id": sid,
            "company": name,
            "canonical": canon,
            "ads_url": url,
            "domain": domain,
        })
    if limit:
        out = out[:limit]
    return out


def insert_person(
    con: sqlite3.Connection,
    company_name: str,
    canonical: str,
    person: dict,
    email: Optional[str],
    parent_url: str,
) -> int:
    full_name = (
        f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
    )
    if not full_name:
        return 0
    role = (person.get("job_title") or "")[:255]
    role_level = person.get("job_level") or ""
    linkedin_url = person.get("linkedin_url") or ""
    notes_lines = []
    if linkedin_url:
        notes_lines.append(f"[linkedin] {linkedin_url}")
    if email:
        notes_lines.append(f"[email] {email}")
    if role_level:
        notes_lines.append(f"[level] {role_level}")
    notes = "\n".join(notes_lines)[:2000]
    cur = con.cursor()
    cur.execute(
        """INSERT INTO attendance_signals (
            edition_year, entity_type, person_name, person_role,
            company_name, canonical_company_name, country,
            source_platform, source_url, source_title, source_snippet,
            signal_type, signal_text, signal_strength_reason,
            is_company_post, is_personal_post, is_official_delegation,
            is_exhibitor_employee, is_duplicate,
            presence_confidence, manual_validation_status,
            notes
        ) VALUES (
            2026, 'person', ?, ?,
            ?, ?, 'United Kingdom',
            'enrich-so-ads-employee', ?, ?, ?,
            'leadership_lookup', ?,
            'C-Level / VP / Director identifié via enrich.so sur la fiche ADS Group UK.',
            0, 0, 0, 0, 0,
            'medium', 'pending',
            ?
        )""",
        (
            full_name[:255], role,
            company_name[:400], canonical[:80],
            (linkedin_url or parent_url)[:900],
            f"{full_name} — {role} @ {company_name}"[:400],
            (person.get("about") or person.get("about_me") or "")[:1500],
            f"{full_name} ({role}) chez {company_name}"[:500],
            notes,
        ),
    )
    return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main_async(args) -> int:
    api_key = os.getenv(ENRICH_KEY_ENV, "").strip()
    if not api_key:
        raise SystemExit(f"{ENRICH_KEY_ENV} not set")
    con = sqlite3.connect(DB_PATH)
    targets = load_targets(con, args.limit)
    if not targets:
        print("Aucune cible.")
        return 0
    print(f"Targets : {len(targets)} sociétés ADS UK à explorer")

    mcp = EnrichMCP(api_key)
    await mcp.initialize()

    n_done = 0
    n_with_emp = 0
    n_persons = 0
    n_emails = 0
    n_skipped_no_domain = 0
    t0 = time.time()
    sem = asyncio.Semaphore(args.concurrency)

    async def process(target):
        nonlocal n_with_emp, n_persons, n_emails, n_skipped_no_domain
        async with sem:
            slugs = linkedin_slug_candidates(target["company"])
            if not slugs:
                return
            employees: list[dict] = []
            tried_slugs: list[str] = []
            for slug in slugs:
                tried_slugs.append(slug)
                url = f"https://linkedin.com/company/{slug}"
                emps = await mcp.employee_finder(url, max_results=2)
                if emps:
                    employees = emps
                    break
            if not employees:
                return
            n_with_emp += 1
            # Persist found employees
            for i, emp in enumerate(employees):
                # Email lookup only on top employee (cost control)
                email = None
                if (
                    not args.no_email
                    and i == 0
                    and target["domain"]
                    and emp.get("first_name")
                    and emp.get("last_name")
                ):
                    email = await mcp.find_email(
                        emp["first_name"], emp["last_name"], target["domain"],
                    )
                    if email:
                        n_emails += 1
                inserted = insert_person(
                    con,
                    target["company"], target["canonical"],
                    emp, email, target["ads_url"],
                )
                n_persons += inserted
            if target["domain"] is None:
                n_skipped_no_domain += 1

    tasks = [process(t) for t in targets]
    BATCH = 20
    for i, coro in enumerate(asyncio.as_completed(tasks)):
        await coro
        n_done += 1
        if n_done % BATCH == 0 or n_done == len(targets):
            con.commit()
            dt = time.time() - t0
            rate = n_done / dt if dt > 0 else 0
            print(
                f"  [{n_done:>4}/{len(targets)}] companies_with_emp={n_with_emp} "
                f"persons={n_persons} emails={n_emails} ({rate:.2f}/s)"
            )
        if args.max_persons and n_persons >= args.max_persons:
            print(f"\nReached --max-persons={args.max_persons}, stopping.")
            break

    con.commit()
    con.close()
    await mcp.close()
    dt = time.time() - t0
    print(
        f"\nDone in {dt:.1f}s : "
        f"{n_with_emp} sociétés avec employés trouvés / {n_done} explorées · "
        f"{n_persons} personnes insérées · {n_emails} emails trouvés"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N ADS companies (pilot test).")
    p.add_argument("--max-persons", type=int, default=None,
                   help="Hard cap on inserted person rows (budget guard).")
    p.add_argument("--concurrency", type=int, default=4,
                   help="Concurrent enrich.so calls (default 4).")
    p.add_argument("--no-email", action="store_true",
                   help="Skip find_email — keeps cost ~1 credit per "
                        "person instead of ~10.")
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
