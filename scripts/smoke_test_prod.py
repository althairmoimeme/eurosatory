"""Post-deploy smoke test : poll the prod URL until rebuild done,
then probe the key user flows.

Usage :
    python -m scripts.smoke_test_prod                       # default URL
    python -m scripts.smoke_test_prod https://my-app.streamlit.app
    python -m scripts.smoke_test_prod --timeout 180         # wait longer

What it does
────────────
  1. Polls ``GET /_stcore/health`` until HTTP 200 (Streamlit ready).
  2. Polls ``GET /`` to ensure the homepage shell loads.
  3. Reports the build time + final status.

Limitations
───────────
  We can't simulate a real user click via HTTP — Streamlit's
  ``websocket`` protocol is required for the interactive flow. So this
  catches **boot failures** (ImportError, SyntaxError surfaced at
  module import, etc.) but NOT widget-after-instantiation bugs that
  only surface at user click. For those, rely on :
    - ``scripts/lint_streamlit_patterns.py`` (caught at code time)
    - ``tests/test_auth.py``                 (caught at lint time)
    - Manual click-test before announcing the deploy to buyers.
"""

from __future__ import annotations

import argparse
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


DEFAULT_URL = "https://leadforges.streamlit.app"


def _probe(url: str, timeout_sec: float = 10) -> tuple[int, str]:
    """Return ``(status_code, body_excerpt)`` or ``(0, error_msg)`` on
    connection failure."""
    try:
        req = Request(url, headers={"User-Agent": "leadforges-smoke/1.0"})
        with urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            return resp.status, resp.read(1024).decode("utf-8", "ignore")
    except HTTPError as e:
        return e.code, e.reason
    except URLError as e:
        return 0, str(e.reason)
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default=DEFAULT_URL,
                    help=f"Base URL of the deployed app (default : {DEFAULT_URL})")
    ap.add_argument("--timeout", type=int, default=180,
                    help="Max seconds to wait for the rebuild (default : 180)")
    ap.add_argument("--interval", type=int, default=5,
                    help="Poll interval in seconds (default : 5)")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    health_url = f"{base}/_stcore/health"
    home_url = f"{base}/"

    print(f"Smoke testing : {base}")
    print(f"  Health endpoint : {health_url}")
    print(f"  Homepage        : {home_url}")
    print()

    t0 = time.time()
    deadline = t0 + args.timeout
    health_ok = False
    last_msg = ""
    while time.time() < deadline:
        elapsed = int(time.time() - t0)
        code, body = _probe(health_url, timeout_sec=10)
        new_msg = f"  [{elapsed:>3}s]  health → {code}  {body[:60]}"
        if new_msg != last_msg:
            print(new_msg, flush=True)
            last_msg = new_msg
        if code == 200 and "ok" in body.lower():
            health_ok = True
            break
        time.sleep(args.interval)

    if not health_ok:
        print()
        print(f"❌ Health endpoint never returned 200 after {args.timeout}s.")
        return 1

    print()
    print(f"✅ Health endpoint OK after {int(time.time() - t0)}s")

    # Probe the homepage too — catches "module loaded but page render
    # raised" cases (eg. the load_crm ImportError we hit last week).
    code, body = _probe(home_url, timeout_sec=15)
    if code == 200:
        print(f"✅ Homepage returns 200 ({len(body)} byte excerpt)")
    else:
        print(f"⚠️  Homepage returned {code}  {body[:200]}")
        print("    → Open the URL in a browser and check ``Manage app › Logs``.")
        return 2

    print()
    print("Smoke test passed. Continue with a manual click-test (login + 1 chip "
          "+ 1 filter + 1 export) before announcing the deploy to buyers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
