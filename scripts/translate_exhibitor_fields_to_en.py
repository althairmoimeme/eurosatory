"""Translate exhibitor key FR fields → EN via Claude.

Translates the JSON ``data/exports/targeting_profiles_final.json`` IN
PLACE — for every entry, adds parallel ``*_en`` keys for the French
text fields. Other fields (numeric scores, categorical taxonomy values,
URLs, company names) are left untouched.

Fields translated
─────────────────
  string fields :
    - activity_1liner       → activity_1liner_en
    - why_target            → why_target_en

  list-of-strings fields (each item translated) :
    - products              → products_en
    - services              → services_en
    - target_buyers         → target_buyers_en
    - technologies          → technologies_en

Strategy
────────
  Batched : 10 exhibitors per Claude call (each exhibitor has up to 6
  fields, so batches are bounded in token size). The model receives a
  JSON array and returns a parallel JSON array with the ``*_en`` keys
  added.

Idempotency
───────────
  Entries that already have ``activity_1liner_en`` set are skipped
  (unless ``--force``). The script can be safely re-run after a crash.

Cost estimate
─────────────
  ~2 580 entries × ~6 fields × ~30 tokens avg ≈ 460K input + 460K output
  tokens. With Claude Sonnet 4.6 ($3/$15 per M) → ~$8-10 total.

Usage
─────
    python -m scripts.translate_exhibitor_fields_to_en --dry-run --limit 10
    python -m scripts.translate_exhibitor_fields_to_en --limit 100
    python -m scripts.translate_exhibitor_fields_to_en
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

try:
    import anthropic
except ImportError:
    print("Install : pip install anthropic", file=sys.stderr)
    raise


JSON_PATH = ROOT / "data" / "exports" / "targeting_profiles_final.json"

STRING_FIELDS = ["activity_1liner", "why_target"]
LIST_FIELDS = ["products", "services", "target_buyers", "technologies"]
ALL_FIELDS = STRING_FIELDS + LIST_FIELDS

BATCH_SIZE = 10
PARALLEL_BATCHES = 5  # concurrent Claude API calls (well under tier-1 50 RPM)


SYSTEM_PROMPT = """\
You are a professional French-to-English translator specialized in the
defense / aerospace / security industry.

You will receive a JSON array of objects. Each object describes one
exhibitor and contains short French-language fields. For every
non-empty field provided, return its English translation. Use clear,
professional, factual English. Preserve domain-specific terminology
(e.g. ITAR, EOD, C4ISR, RPAS, CBRN, MCO, MRO, EW).

CRITICAL RULES :
  - NEVER translate proper nouns, brand names, product names, or
    company names. Keep them identical.
  - For list-of-strings fields (e.g. products, services), translate
    EACH item independently. Keep the list length and order.
  - When a field is empty, null, or missing in the input, simply omit
    it in the output (don't include a null *_en key).
  - Reply with ONLY a valid JSON array, no markdown fences, no
    surrounding commentary.

Output format : array same length as input. Each output object contains
ONLY the *_en keys (e.g. activity_1liner_en, why_target_en, products_en,
services_en, target_buyers_en, technologies_en), preserving the input
order.
"""


def _trim(s, n=120):
    if s is None:
        return None
    s = str(s)
    if len(s) <= n:
        return s
    return s[:n] + "…"


def _is_already_translated(entry: dict) -> bool:
    """Return True if at least the primary FR field has been translated."""
    return bool(entry.get("activity_1liner_en") or entry.get("why_target_en"))


def _has_any_translatable(entry: dict) -> bool:
    """Return True if any of the FR fields has content worth translating."""
    for f in STRING_FIELDS:
        v = entry.get(f)
        if v and isinstance(v, str) and v.strip():
            return True
    for f in LIST_FIELDS:
        v = entry.get(f)
        if v and isinstance(v, list) and any(
            isinstance(x, str) and x.strip() for x in v
        ):
            return True
    return False


def _build_batch_input(batch: list[dict]) -> list[dict]:
    """Pluck only the FR fields we want translated, into a clean payload."""
    out = []
    for entry in batch:
        item = {}
        for f in STRING_FIELDS:
            v = entry.get(f)
            if v and isinstance(v, str) and v.strip():
                item[f] = v.strip()
        for f in LIST_FIELDS:
            v = entry.get(f)
            if v and isinstance(v, list):
                cleaned = [x.strip() for x in v if isinstance(x, str) and x.strip()]
                if cleaned:
                    item[f] = cleaned
        out.append(item)
    return out


def _translate_batch(client, batch_input: list[dict]) -> list[dict] | None:
    user_msg = (
        "Translate the following exhibitor entries from French to English. "
        "Return a JSON array of the same length with *_en keys.\n\n"
        f"INPUT:\n{json.dumps(batch_input, ensure_ascii=False)}"
    )
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            text_out = ""
            for block in resp.content:
                if getattr(block, "type", "") == "text":
                    text_out += block.text
            text_out = text_out.strip()
            if text_out.startswith("```"):
                text_out = text_out.split("```", 2)[1]
                if text_out.startswith("json"):
                    text_out = text_out[4:]
                text_out = text_out.strip().rstrip("`").strip()
            data = json.loads(text_out)
            if isinstance(data, list) and len(data) == len(batch_input):
                return data
            print(f"   ! batch returned {len(data) if isinstance(data, list) else '?'} items, expected {len(batch_input)}")
        except json.JSONDecodeError as e:
            print(f"   ! JSON decode attempt {attempt + 1}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"   ! Claude attempt {attempt + 1}: {type(e).__name__}: {e}")
        time.sleep(2)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                    help="Max entries to translate (debug)")
    ap.add_argument("--force", action="store_true",
                    help="Re-translate even entries already translated")
    args = ap.parse_args()

    if not JSON_PATH.exists():
        print(f"❌ JSON not found : {JSON_PATH}", file=sys.stderr)
        return 1

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY missing in env", file=sys.stderr)
        return 1
    client = anthropic.Anthropic(api_key=api_key)

    print(f"Reading {JSON_PATH}...")
    with JSON_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    print(f"  total entries : {len(data)}")

    # Pick the pending list
    pending_idx = []
    for i, entry in enumerate(data):
        if not args.force and _is_already_translated(entry):
            continue
        if not _has_any_translatable(entry):
            continue
        pending_idx.append(i)
        if args.limit and len(pending_idx) >= args.limit:
            break

    print(f"  pending entries : {len(pending_idx)}")

    if args.dry_run:
        print("\n[dry-run] no API calls, no file writes")
        for i in pending_idx[:3]:
            e = data[i]
            print(f"  #{e.get('exhibitor_id')} {e.get('company_name')!r}")
            print(f"    activity_1liner: {_trim(e.get('activity_1liner'))}")
            print(f"    why_target     : {_trim(e.get('why_target'))}")
            print(f"    products       : {_trim(e.get('products'))}")
            print(f"    services       : {_trim(e.get('services'))}")
        return 0

    if not pending_idx:
        print("Nothing to translate.")
        return 0

    t0 = time.time()
    translated = 0
    failed = 0
    completed_batches = 0
    total_batches = (len(pending_idx) + BATCH_SIZE - 1) // BATCH_SIZE
    save_lock = threading.Lock()

    def _process_one_batch(batch_start_idx: int):
        nonlocal translated, failed, completed_batches
        chunk_idx = pending_idx[batch_start_idx:batch_start_idx + BATCH_SIZE]
        batch_entries = [data[i] for i in chunk_idx]
        batch_input = _build_batch_input(batch_entries)
        result = _translate_batch(client, batch_input)
        if result is None:
            with save_lock:
                failed += len(chunk_idx)
                completed_batches += 1
            return False
        # Merge translations back into the in-memory data structure
        with save_lock:
            for src_idx, tr in zip(chunk_idx, result):
                entry = data[src_idx]
                for f in STRING_FIELDS:
                    key = f"{f}_en"
                    v = tr.get(key)
                    if v and isinstance(v, str) and v.strip():
                        entry[key] = v.strip()
                for f in LIST_FIELDS:
                    key = f"{f}_en"
                    v = tr.get(key)
                    if v and isinstance(v, list):
                        cleaned = [x.strip() for x in v
                                   if isinstance(x, str) and x.strip()]
                        if cleaned:
                            entry[key] = cleaned
                translated += 1
            completed_batches += 1
            # Persist incrementally every batch (crash-safe)
            with JSON_PATH.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            elapsed = int(time.time() - t0)
            rate = translated / max(elapsed, 1)
            eta = int((len(pending_idx) - translated) / max(rate, 0.1))
            print(
                f"  [{elapsed:>4}s] batch {completed_batches}/{total_batches} ok · "
                f"translated={translated}/{len(pending_idx)} · "
                f"failed={failed} · rate={rate*60:.0f}/min · ETA={eta}s",
                flush=True,
            )
        return True

    # Schedule all batches with a ThreadPoolExecutor (Claude SDK is sync,
    # threads are the right primitive here — each thread blocks on the
    # HTTP call).
    batch_starts = list(range(0, len(pending_idx), BATCH_SIZE))
    with ThreadPoolExecutor(max_workers=PARALLEL_BATCHES) as pool:
        futures = [pool.submit(_process_one_batch, s) for s in batch_starts]
        for _ in as_completed(futures):
            pass  # progress is printed inside the worker

    print()
    print(f"✅ Translated {translated} entries in {int(time.time() - t0)}s")
    if failed:
        print(f"⚠️  {failed} entries failed — re-run the script to retry "
              f"(idempotent)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
