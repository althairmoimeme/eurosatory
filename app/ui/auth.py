"""Multi-buyer authentication for LeadForges.

Design
------
LeadForges is sold as a one-shot product (3 k€ per buyer, valid until
the end of the Eurosatory edition window + post-event follow-up). Each
buyer gets their own password. They all hit the same Streamlit
deployment ; access is gated by the password they were assigned at the
time of purchase.

Configuration
-------------
The list of buyers lives in ``st.secrets["buyers"]`` — a TOML array of
tables (one entry per buyer). Example ``.streamlit/secrets.toml`` :

    APP_PASSWORD = "fallback-dev-password"   # legacy single password

    [[buyers]]
    password = "Hgs7K2pXq9mNwR3v"            # 16+ chars, random
    name     = "Thales Group"
    expires  = "2026-11-30"
    email    = "contact@thales.com"           # optional, audit-only

    [[buyers]]
    password = "Tq5pV8sZ4xWf2eHk"
    name     = "Safran Defense"
    expires  = "2026-11-30"

Security
--------
- Passwords are compared in constant time (``hmac.compare_digest``).
- A buyer whose ``expires`` date has passed is locked out automatically.
- Falls back to the legacy single ``APP_PASSWORD`` if the new ``buyers``
  array is missing or empty — supports local dev and existing deploys.

Session state
-------------
On successful login we cache ``buyer`` on ``st.session_state`` :

    {
        "name"    : "Thales Group",
        "expires" : datetime.date(2026, 11, 30),
        "days_left" : 198,
        "is_admin": False,
    }

UI helpers (``render_login_screen``, ``render_access_banner``) live
here too to keep all authentication concerns in one place.
"""

from __future__ import annotations

import hmac
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import streamlit as st


# Lock window after too many failed attempts (seconds).
_RATELIMIT_LOCK_SECONDS = 60
_RATELIMIT_THRESHOLD = 5


# ── Data model ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Buyer:
    """A single buyer's access metadata. Frozen → safely cached."""
    name: str
    expires: date
    email: Optional[str] = None

    @property
    def days_left(self) -> int:
        return (self.expires - date.today()).days

    @property
    def is_expired(self) -> bool:
        return self.days_left < 0


# ── Secrets reading ───────────────────────────────────────────────────────


def _read_secrets() -> dict:
    """Best-effort read of ``st.secrets``. Returns ``{}`` if no secrets file
    is configured (typical for local dev).
    """
    try:
        # st.secrets is a Mapping ; cast to dict for easier introspection
        return dict(st.secrets)  # type: ignore[attr-defined]
    except (FileNotFoundError, OSError, KeyError, Exception):  # noqa: BLE001
        return {}


def _coerce_date(value) -> Optional[date]:
    """Parse a TOML / ISO date or a Python ``date``/``datetime`` value."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def _load_buyers() -> dict[str, Buyer]:
    """Read ``st.secrets["buyers"]`` → mapping ``{password: Buyer}``.

    Skips malformed entries instead of crashing — operator might mistype
    one entry, we don't want to take down the whole gate.
    """
    secrets = _read_secrets()
    raw = secrets.get("buyers") or []
    out: dict[str, Buyer] = {}
    for i, entry in enumerate(raw):
        try:
            pw = str(entry.get("password") or "").strip()
            name = str(entry.get("name") or "").strip()
            expires = _coerce_date(entry.get("expires"))
            email = entry.get("email")
            if not pw or not name or expires is None:
                continue
            if len(pw) < 8:
                # Force a minimum password strength
                continue
            out[pw] = Buyer(name=name, expires=expires, email=email)
        except Exception:  # noqa: BLE001
            # Malformed entry — skip silently so the rest stays usable
            continue
    return out


def _legacy_password() -> str:
    """Fallback single-password (env var or secret). Empty string = no gate.

    Kept for local dev (no secrets file) and grandfathered deploys that
    were on the single-password model.
    """
    pw = os.getenv("APP_PASSWORD", "").strip()
    if pw:
        return pw
    secrets = _read_secrets()
    return str(secrets.get("APP_PASSWORD") or "").strip()


# ── Authentication API ──────────────────────────────────────────────────


_SESSION_KEY = "_lf_buyer"          # st.session_state key
_FAILED_KEY = "_lf_failed"          # rate-limit counter
_LOCKED_UNTIL_KEY = "_lf_locked_until"  # epoch seconds when lock expires


def current_buyer() -> Optional[Buyer]:
    """Return the authenticated buyer, or ``None`` if not logged in.

    A buyer whose access just expired (between page loads) is logged out
    automatically.
    """
    b: Optional[Buyer] = st.session_state.get(_SESSION_KEY)
    if b and b.is_expired:
        st.session_state.pop(_SESSION_KEY, None)
        return None
    return b


def is_authenticated() -> bool:
    """Convenience : True if a non-expired buyer is logged in OR if there's
    no gate configured at all (local dev fall-through)."""
    if current_buyer():
        return True
    # Local dev (no secrets at all) : let through
    buyers = _load_buyers()
    legacy = _legacy_password()
    return (not buyers) and (not legacy)


def _attempt_login(password: str) -> tuple[bool, Optional[Buyer]]:
    """Constant-time compare against every configured password.

    Returns ``(success, buyer_or_None)``. Legacy single-password matches
    yield a synthetic buyer with a 1-year expiry (for back-compat).
    """
    if not password:
        return False, None
    pw = password.strip()

    # Multi-buyer table
    buyers = _load_buyers()
    for cand_pw, buyer in buyers.items():
        if hmac.compare_digest(cand_pw, pw):
            if buyer.is_expired:
                return False, buyer  # caller can surface a clearer msg
            return True, buyer

    # Legacy single-password fallback
    legacy = _legacy_password()
    if legacy and hmac.compare_digest(legacy, pw):
        fallback = Buyer(
            name="Demo / Admin",
            expires=date.today().replace(year=date.today().year + 1),
        )
        return True, fallback

    return False, None


# ── UI components ────────────────────────────────────────────────────────


def render_login_screen() -> None:
    """Show the login screen. Returns nothing — caller should
    ``st.stop()`` after if not authenticated."""

    st.markdown(
        """
        <div style='max-width: 420px; margin: 4rem auto; padding: 2.2rem 1.75rem;
                    border: 1px solid #E2E8F0; background: #fff;
                    font-family: -apple-system, BlinkMacSystemFont, sans-serif;'>
            <div style='font-size: 0.68rem; letter-spacing: 0.14em;
                        color: #C1121F; text-transform: uppercase; font-weight: 700;'>
                LeadForges · Defense Commercial Intelligence
            </div>
            <div style='font-size: 1.55rem; font-weight: 700; color: #0F172A;
                        letter-spacing: -0.02em; margin: 0.7rem 0 1.4rem 0;'>
                Accès restreint
            </div>
            <div style='font-size: 0.88rem; color: #475569; line-height: 1.55;
                        margin-bottom: 1.6rem;'>
                Cette application contient une base commerciale défense
                pré-qualifiée pour le salon Eurosatory 2026.
                Saisis ton mot de passe d'accès (fourni à l'achat).
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cols = st.columns([1, 2, 1])
    with cols[1]:
        # Lock-out check : if too many recent fails, BLOCK login attempts
        # for ``_RATELIMIT_LOCK_SECONDS`` (session-scoped, naive but
        # sufficient to slow scripted brute force).
        locked_until = float(st.session_state.get(_LOCKED_UNTIL_KEY, 0))
        now = time.time()
        is_locked = locked_until > now
        remaining = int(locked_until - now) if is_locked else 0

        pw = st.text_input(
            "Mot de passe", type="password",
            label_visibility="collapsed",
            placeholder="Mot de passe d'accès",
            key="_lf_password_input",
            disabled=is_locked,
        )
        if is_locked:
            st.error(
                f"🔒 Trop d'essais incorrects. Réessaie dans **{remaining} s** "
                "(ou recharge la page si tu connais le bon mot de passe)."
            )
        elif pw:
            ok, buyer = _attempt_login(pw)
            if ok and buyer:
                # Success → wipe rate-limit state
                st.session_state[_SESSION_KEY] = buyer
                st.session_state.pop(_FAILED_KEY, None)
                st.session_state.pop(_LOCKED_UNTIL_KEY, None)
                st.rerun()
            else:
                # Bump fail counter ; trip the lock at the threshold
                fails = int(st.session_state.get(_FAILED_KEY, 0)) + 1
                st.session_state[_FAILED_KEY] = fails
                if buyer and buyer.is_expired:
                    st.error(
                        f"Accès de **{buyer.name}** expiré le "
                        f"{buyer.expires:%d/%m/%Y}. "
                        "Contacte support@leadforges.com pour renouveler."
                    )
                elif fails >= _RATELIMIT_THRESHOLD:
                    # Set the lock and force a rerun so the input box
                    # disables itself immediately.
                    st.session_state[_LOCKED_UNTIL_KEY] = (
                        time.time() + _RATELIMIT_LOCK_SECONDS
                    )
                    st.session_state[_FAILED_KEY] = 0  # reset counter
                    st.rerun()
                else:
                    remaining_attempts = _RATELIMIT_THRESHOLD - fails
                    st.error(
                        f"Mot de passe incorrect. "
                        f"({remaining_attempts} essai{'s' if remaining_attempts > 1 else ''} "
                        f"restant{'s' if remaining_attempts > 1 else ''} avant verrouillage)"
                    )

    # Footer with contact
    st.markdown(
        """
        <div style='max-width: 420px; margin: 0.6rem auto 0 auto;
                    text-align: center; font-size: 0.78rem; color: #94A3B8;'>
            Besoin d'aide ? <a href='mailto:support@leadforges.com'
            style='color:#475569; text-decoration:underline;'>
            support@leadforges.com</a>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_access_banner() -> None:
    """Render the buyer-identity strip shown above the main header.

    Color-coded by ``days_left`` :  ≥30j green · 7-29j orange · <7j red.
    Includes a sign-out button.
    """
    buyer = current_buyer()
    if not buyer:
        return

    days = buyer.days_left
    if days >= 30:
        bg = "#1F7A4D"  # green
    elif days >= 7:
        bg = "#D97706"  # orange
    else:
        bg = "#7B2D2D"  # red

    plural = "s" if abs(days) > 1 else ""
    msg = (
        f"👤 <strong>{buyer.name}</strong> "
        f"· accès valable jusqu'au {buyer.expires:%d/%m/%Y} "
        f"({days} jour{plural} restant{plural})"
    )

    cols = st.columns([8, 1])
    with cols[0]:
        st.markdown(
            f"<div style='background:{bg};color:white;padding:0.4rem 0.9rem;"
            f"border-radius:4px;font-size:0.82rem;'>{msg}</div>",
            unsafe_allow_html=True,
        )
    with cols[1]:
        if st.button("Sign out", key="_lf_signout", use_container_width=True):
            sign_out()


def sign_out() -> None:
    """Clear the auth session + any per-session state (favorites etc.)."""
    for k in list(st.session_state.keys()):
        if k.startswith("_lf_") or k in {"fav_ids"}:
            st.session_state.pop(k, None)
    st.rerun()


# ── Favorites — session-scoped because the Cloud DB is read-only ─────────


def get_favorite_ids() -> set[int]:
    """Return the session-scoped favorite exhibitor IDs.

    We don't persist favorites to disk on Cloud (filesystem is read-only).
    Buyers export their selection to XLSX/CSV at any time to keep a
    durable record outside LeadForges.
    """
    s = st.session_state.get("fav_ids")
    if s is None:
        s = set()
        st.session_state["fav_ids"] = s
    return s


def set_favorite(exhibitor_id: int, favorite: bool) -> None:
    """Add or remove an exhibitor id from the session's favorites."""
    fav = get_favorite_ids()
    if favorite:
        fav.add(int(exhibitor_id))
    else:
        fav.discard(int(exhibitor_id))


def clear_favorites() -> None:
    st.session_state["fav_ids"] = set()
