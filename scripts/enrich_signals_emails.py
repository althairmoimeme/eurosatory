"""Find professional emails for ``attendance_signals`` rows via enrich.so.

Strategy
--------
1. Pick rows where ``person_name`` is full (≥2 words), ``company_name`` is
   set, and ``notes`` does not yet contain an email.
2. For every distinct *canonical* company in that subset, derive a domain :
   - first try the local ``exhibitors`` table for an exact / startswith
     match,
   - else ask Claude (claude-haiku-4-5) for the most likely domain.
   Domains are cached on disk (``data/llm_test/company_domains.json``)
   so repeated runs cost nothing.
3. For every signal, call enrich.so's ``find_email`` MCP tool
   (``firstName + lastName + domain``).  10 credits per call.
4. Persist found emails into ``attendance_signals.notes`` (existing
   convention :  ``[email] foo@bar.com``).

Usage
-----
    python -m scripts.enrich_signals_emails --limit 5      # smoke test
    python -m scripts.enrich_signals_emails                # full run
    python -m scripts.enrich_signals_emails --dry-run      # no API calls
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
DOMAIN_CACHE = ROOT / "data" / "llm_test" / "company_domains.json"
ENRICH_BASE = "https://mcp.enrich.so/mcp"
ENRICH_KEY_ENV = "ENRICH_SO_API_KEY"

# Companies we never bother trying to enrich emails for — institutional,
# military, ministry, press, conference organisers, embassies, etc.
# These are the entities our targets *meet*, not the ones our sales team
# wants to email-prospect (the people there have generic / redacted contact
# channels).
INSTITUTIONAL_RX = re.compile(
    r"\b("
    r"minist[èeé]r[eé]|ministry|secr[eé]tariat d['\"]?[eé]tat|"
    r"arm[eé]e|army|navy|air force|gendarmerie|police|garde nationale|"
    r"d[eé]l[eé]gation|delegation|"
    r"press|journal|media|magazine|breaking defense|defense news|"
    r"eurosatory|salon|expo|exhibition|conference|speakers?|"
    r"ambassade|embassy|consulat|consulate|"
    r"parlement|parliament|s[eé]nat|senate|assembl[eé]e|"
    r"commission|directorate|agency|agence|"
    r"r[eé]gion|prefecture|mairie|chambre de commerce"
    r")\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def load_domain_cache() -> dict[str, Optional[str]]:
    if DOMAIN_CACHE.exists():
        return json.loads(DOMAIN_CACHE.read_text(encoding="utf-8"))
    return {}


def save_domain_cache(c: dict[str, Optional[str]]) -> None:
    DOMAIN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    DOMAIN_CACHE.write_text(
        json.dumps(c, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Domain inference
# ---------------------------------------------------------------------------


def url_to_domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        p = urlparse(url if "://" in url else "http://" + url)
        d = (p.netloc or p.path).lower().strip()
        d = re.sub(r"^www\.", "", d).split("/")[0].split(":")[0]
        return d if "." in d else None
    except Exception:
        return None


def domain_from_exhibitor_table(
    canon: str,
    cur: sqlite3.Cursor,
) -> Optional[str]:
    """Lookup the exhibitor catalog ; return domain if a match is found."""
    cur.execute(
        "SELECT website_url FROM exhibitors "
        "WHERE LOWER(TRIM(company_name)) = ? "
        "  AND website_url IS NOT NULL AND website_url != ''",
        (canon,),
    )
    row = cur.fetchone()
    if row:
        d = url_to_domain(row[0])
        if d:
            return d

    # startswith heuristic — handle subsidiaries (Airbus Defence and Space → AIRBUS)
    parts = canon.split()
    if len(parts) >= 2:
        prefix = parts[0]
        cur.execute(
            "SELECT website_url FROM exhibitors "
            "WHERE LOWER(TRIM(company_name)) = ? "
            "  AND website_url IS NOT NULL AND website_url != ''",
            (prefix,),
        )
        row = cur.fetchone()
        if row:
            d = url_to_domain(row[0])
            if d:
                return d
    return None


def domain_from_llm(canon: str, client) -> Optional[str]:
    """Last-resort : ask Claude haiku for the most likely domain.

    Returns None if Claude refuses (unknown company)."""
    if not client:
        return None
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=120,
            system=(
                "Tu es un assistant de recherche de domaines d'entreprise. "
                "L'utilisateur te donne un nom de société ; tu réponds "
                "STRICTEMENT par le domaine le plus probable de leur site web "
                "officiel, format 'domain.tld' (sans http, sans www). Si tu "
                "ne connais pas la société avec une certitude raisonnable, "
                "réponds 'unknown'. Aucun autre commentaire."
            ),
            messages=[{"role": "user", "content": f"Société: {canon}"}],
        )
        text = ""
        for blk in resp.content:
            if hasattr(blk, "text"):
                text += blk.text
        text = text.strip().lower()
        if text and text != "unknown" and "." in text and " " not in text:
            # Strip protocol if Claude added one
            text = re.sub(r"^https?://", "", text)
            text = re.sub(r"^www\.", "", text).split("/")[0]
            return text
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠ LLM domain lookup failed for {canon!r}: {e!r}")
    return None


# ---------------------------------------------------------------------------
# Enrich.so MCP client
# ---------------------------------------------------------------------------


class EnrichMCP:
    """Minimal MCP-over-HTTP client for enrich.so."""

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
        if not self.session_id:
            raise RuntimeError("enrich.so did not return mcp-session-id")
        # send initialized notification
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

    async def call(self, name: str, arguments: dict) -> dict:
        rid = int(time.time() * 1000) % 1_000_000_000
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
        # Parse SSE-formatted response: "event: message\ndata: {...}\n\n"
        text = r.text
        for line in text.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                return payload
        return json.loads(text)

    async def find_email(
        self,
        first: str,
        last: str,
        domain: str,
    ) -> Optional[dict]:
        try:
            payload = await self.call(
                "find_email",
                {"firstName": first, "lastName": last, "domain": domain},
            )
        except Exception as e:  # noqa: BLE001
            print(f"   ✗ enrich.so call error: {e!r}")
            return None
        if "error" in payload:
            print(f"   ✗ enrich.so error: {payload['error']}")
            return None
        result = payload.get("result", {})
        content = result.get("content", [])
        if not content:
            return None
        try:
            text = content[0].get("text") or ""
            return json.loads(text)
        except Exception as e:  # noqa: BLE001
            print(f"   ✗ enrich.so parse error: {e!r}  raw={content!r}")
            return None

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------


def fetch_eligibles(limit: Optional[int] = None) -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT id, person_name, company_name, canonical_company_name, notes
        FROM attendance_signals
        WHERE is_duplicate = 0
          AND person_name IS NOT NULL AND person_name != ''
          AND company_name IS NOT NULL AND company_name != ''
          AND (notes IS NULL OR notes NOT LIKE '%@%')
        ORDER BY id
    """)
    rows = []
    for r in cur.fetchall():
        parts = r["person_name"].strip().split()
        if len(parts) < 2:
            continue
        canon = (
            r["canonical_company_name"]
            or r["company_name"].lower().strip()
        )
        # B2B-only filter — drop institutional / military / press / events.
        if INSTITUTIONAL_RX.search(canon):
            continue
        rows.append({
            "id": r["id"],
            "first": parts[0],
            "last": parts[-1],
            "person_name": r["person_name"],
            "company_name": r["company_name"],
            "canonical": canon,
            "notes": r["notes"] or "",
        })
    con.close()
    if limit:
        rows = rows[:limit]
    return rows


def persist_email(sid: int, email: str, existing_notes: str) -> None:
    new_notes = (
        f"{existing_notes}\n[email] {email}".strip()
        if existing_notes
        else f"[email] {email}"
    )
    new_notes = new_notes[:2000]
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "UPDATE attendance_signals SET notes = ? WHERE id = ?",
        (new_notes, sid),
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main_async(args) -> int:
    rows = fetch_eligibles(limit=args.limit)
    if not rows:
        print("Aucun signal éligible.")
        return 0
    print(f"Eligibles : {len(rows)} signaux")

    # ---- Domain resolution (cached) ----
    domain_cache = load_domain_cache()
    canons = sorted({r["canonical"] for r in rows})
    print(f"Sociétés canoniques uniques : {len(canons)}")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Optional Claude client for fallback domain inference
    llm_client = None
    if not args.no_llm:
        from anthropic import Anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if api_key:
            llm_client = Anthropic(api_key=api_key, max_retries=3)

    n_resolved = 0
    n_unknown = 0
    n_via_db = 0
    n_via_llm = 0
    for c in canons:
        if c in domain_cache:
            d = domain_cache[c]
            if d:
                n_resolved += 1
            else:
                n_unknown += 1
            continue
        d = domain_from_exhibitor_table(c, cur)
        if d:
            domain_cache[c] = d
            n_resolved += 1
            n_via_db += 1
            continue
        d = domain_from_llm(c, llm_client) if llm_client else None
        domain_cache[c] = d  # may be None — cache the failure too
        if d:
            n_resolved += 1
            n_via_llm += 1
        else:
            n_unknown += 1

    con.close()
    save_domain_cache(domain_cache)
    print(
        f"Domaines : {n_resolved} résolus "
        f"({n_via_db} via DB, {n_via_llm} via Claude), "
        f"{n_unknown} inconnus"
    )

    # ---- Build the actionable list ----
    actionable = [r for r in rows if domain_cache.get(r["canonical"])]
    print(f"Signaux actionnables : {len(actionable)} "
          f"(coût attendu : {len(actionable)*10} crédits enrich.so)")

    if args.dry_run:
        print("\n[--dry-run] arrêt avant les appels enrich.so. Quelques exemples :")
        for r in actionable[:10]:
            d = domain_cache[r["canonical"]]
            print(f"  id={r['id']:>4}  {r['first']} {r['last']:<20} @ {d}")
        return 0

    # ---- Enrich.so calls ----
    api_key = os.getenv(ENRICH_KEY_ENV, "").strip()
    if not api_key:
        raise SystemExit(
            f"{ENRICH_KEY_ENV} not set in .env (ou exporté en shell)"
        )
    mcp = EnrichMCP(api_key)
    await mcp.initialize()

    sem = asyncio.Semaphore(args.concurrency)

    async def process(r):
        async with sem:
            d = domain_cache[r["canonical"]]
            data = await mcp.find_email(r["first"], r["last"], d)
            if not data:
                return r["id"], None
            # Response shape per enrich.so docs
            email = None
            success = data.get("success")
            if success:
                d2 = data.get("data") or {}
                email = d2.get("email")
            return r["id"], (email, r["notes"])

    n_done = 0
    n_ok = 0
    n_found = 0
    t0 = time.time()
    tasks = [process(r) for r in actionable]
    for coro in asyncio.as_completed(tasks):
        sid, payload = await coro
        n_done += 1
        if payload is None:
            continue
        n_ok += 1
        email, existing_notes = payload
        if email and "@" in email:
            persist_email(sid, email, existing_notes)
            n_found += 1
        if n_done % 10 == 0 or n_done == len(actionable):
            dt = time.time() - t0
            rate = n_done / dt if dt > 0 else 0
            print(
                f"  [{n_done:>4}/{len(actionable)}] "
                f"ok={n_ok}  found={n_found}  ({rate:.2f}/s)"
            )

    await mcp.close()
    print(
        f"\nDone in {time.time()-t0:.1f}s : "
        f"appels OK = {n_ok}, emails trouvés = {n_found} "
        f"(coût ≈ {n_ok*10} crédits)"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N signals (smoke test).")
    p.add_argument("--dry-run", action="store_true",
                   help="Resolve domains but skip enrich.so calls.")
    p.add_argument("--no-llm", action="store_true",
                   help="Skip Claude domain inference (use only DB lookups).")
    p.add_argument("--concurrency", type=int, default=4,
                   help="Concurrent enrich.so calls (default 4).")
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
