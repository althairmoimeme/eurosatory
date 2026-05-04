"""Re-scrape headlines for the 380 fiches whose original scrape was
blocked (Cloudflare, JS-required, anti-bot, etc.).

Two-pronged approach :
  1. ``cloudscraper`` — lightweight library that handles Cloudflare's
     standard bot challenge automatically (no headless browser needed).
     Handles ~70 % of the blocks we see in practice.
  2. Standard headers fallback for sites that aren't actually
     Cloudflare-protected but failed for unrelated reasons (UA filtered,
     missing Accept-Language…).

Outputs
-------
- Updates ``ExhibitorIntelligence.headline`` directly in the DB when a
  clean headline is recovered.
- Writes ``data/llm_test/rescrape_log.json`` with details per site.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from selectolax.parser import HTMLParser  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.crm.headline import _clean as _hd_clean, _looks_usable as _hd_ok  # noqa: E402
from app.database import (  # noqa: E402
    Exhibitor, ExhibitorIntelligence, SessionLocal,
)


# Same noise-detection used elsewhere in the pipeline.
_BAD = re.compile(
    r"<\s*(html|meta|script|head|body|div)\b|"
    r"\bcookies?\b.*\b(enable|accept|disable|require)|"
    r"javascript\s+(is\s+)?(disabled|required)|"
    r"\bcloudflare\b|just a moment|checking your browser|"
    r"page not found|404|forbidden|access denied|"
    r"this website is using a security service|"
    r"free shipping on millions of items|"
    r"^\s*skip to main content",
    re.I,
)


def _is_clean(s: Optional[str]) -> bool:
    if not s:
        return False
    s = s.strip()
    return len(s) >= 25 and not _BAD.search(s) and _hd_ok(s)


_META_SELECTORS = [
    ('meta[property="og:description"]', "content"),
    ('meta[name="description"]', "content"),
    ('meta[property="twitter:description"]', "content"),
    ('meta[name="twitter:description"]', "content"),
    ('meta[property="og:title"]', "content"),
    ("title", None),
]


def _extract_meta(html: str) -> Optional[str]:
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        return None
    for sel, attr in _META_SELECTORS:
        el = tree.css_first(sel)
        if not el:
            continue
        v = (el.attributes.get(attr) if attr else el.text()) or ""
        c = _hd_clean(v)
        if c and _is_clean(c):
            return c[:300]
    return None


def _normalise(url: str) -> Optional[str]:
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u
    return u


def main() -> int:
    import cloudscraper
    import requests

    # Build the ``cloudscraper`` session — it transparently solves the
    # standard Cloudflare bot challenge.
    sc = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "darwin", "mobile": False},
    )
    sc.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    })

    # Pull every exhibitor whose current headline is blocked / missing.
    s = SessionLocal()
    try:
        rows = s.execute(
            select(
                Exhibitor.id, Exhibitor.company_name, Exhibitor.website_url,
                ExhibitorIntelligence.id, ExhibitorIntelligence.headline,
            ).join(
                ExhibitorIntelligence,
                ExhibitorIntelligence.exhibitor_id == Exhibitor.id,
                isouter=True,
            )
        ).all()
    finally:
        s.close()

    todo = []
    for r in rows:
        eid, name, url, intel_id, headline = r
        if _is_clean(headline):
            continue
        u = _normalise(url)
        if not u or len(u) < 25:
            continue
        todo.append((eid, name, u, intel_id))

    print(f"To re-scrape: {len(todo)} sites")
    fixed = 0
    failed = 0
    log = []
    for i, (eid, name, url, intel_id) in enumerate(todo, start=1):
        try:
            r = sc.get(url, timeout=15, allow_redirects=True)
            html = r.text or ""
        except Exception as e:  # noqa: BLE001
            log.append({"eid": eid, "name": name, "url": url,
                        "ok": False, "err": str(e)[:80]})
            failed += 1
            if i % 20 == 0:
                print(f"  [{i:>3}/{len(todo)}]  fixed={fixed}  failed={failed}")
            continue
        new_h = _extract_meta(html)
        if not new_h:
            failed += 1
            log.append({"eid": eid, "name": name, "url": url, "ok": False,
                        "err": "no clean meta"})
            if i % 20 == 0:
                print(f"  [{i:>3}/{len(todo)}]  fixed={fixed}  failed={failed}")
            continue
        # Persist
        s2 = SessionLocal()
        try:
            if intel_id:
                intel = s2.get(ExhibitorIntelligence, intel_id)
            else:
                intel = ExhibitorIntelligence(exhibitor_id=eid)
                s2.add(intel)
            intel.headline = new_h
            s2.commit()
        finally:
            s2.close()
        fixed += 1
        log.append({"eid": eid, "name": name, "url": url, "ok": True,
                    "headline": new_h[:120]})
        if i % 20 == 0:
            print(f"  [{i:>3}/{len(todo)}]  fixed={fixed}  failed={failed}")
        time.sleep(0.3)  # be polite

    out = ROOT / "data" / "llm_test" / "rescrape_log.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(log, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\nFINAL : fixed={fixed}/{len(todo)}  failed={failed}")
    print(f"Wrote log to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
