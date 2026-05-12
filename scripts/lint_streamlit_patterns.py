"""Static analysis for Streamlit anti-patterns we've already been bitten by.

Run :
    python -m scripts.lint_streamlit_patterns

Exits non-zero if any pattern is detected — wire it into a pre-push
hook (see ``.git/hooks/pre-push``) so the prod is never burned twice
by the same class of bug.

Patterns checked
----------------
1.  ``st.session_state["filter_X"] = …`` (or ``del``) INSIDE
    ``if st.button(...):`` body, when ``filter_X`` is ALSO a widget
    key declared elsewhere in the file. Streamlit raises
    ``StreamlitAPIException: cannot be modified after the widget…
    is instantiated`` at click time. Fix : use ``on_click=callback``.

2.  ``st.toast(..., icon="X")`` where X is not in the Unicode emoji
    set Streamlit accepts (eg. ``"★"`` U+2605). Streamlit raises
    ``StreamlitAPIException: not a valid emoji``.

3.  ``or "".strip()`` on a column read from a pandas DataFrame
    without isinstance check — pandas hands NaN floats for missing
    cells, ``nan or ""`` returns nan, ``.strip()`` then explodes.
    Heuristic only — pattern ``(r.get("...") or "").strip()`` /
    ``(df[...].iloc[...] or "").strip()``.

Add new patterns as we encounter new prod bugs.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_UI = ROOT / "app" / "ui"


# ──────────────────────────────────────────────────────────────────────────


def _gather_widget_keys(tree: ast.AST) -> set[str]:
    """Collect every ``key="something"`` argument passed to an ``st.<widget>``
    call in the AST. These are the keys that, once instantiated, cannot
    be reassigned later in the same script run.
    """
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match calls of the form st.<anything>(...)
        if not (isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "st"):
            continue
        for kw in node.keywords:
            if kw.arg == "key" and isinstance(kw.value, ast.Constant):
                v = kw.value.value
                if isinstance(v, str):
                    keys.add(v)
    return keys


def _check_session_state_in_button_block(
    tree: ast.AST, widget_keys: set[str], rel_path: str,
) -> list[tuple[int, str]]:
    """Detect ``st.session_state[K] = …`` or ``del st.session_state[K]``
    inside the body of ``if st.button(...):`` (or st.checkbox, st.toggle…)
    when ``K`` is also a widget key.
    """
    issues: list[tuple[int, str]] = []

    class Visitor(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:  # noqa: N802
            # Is the test a call to st.button(...) ?
            test = node.test
            is_button = (
                isinstance(test, ast.Call)
                and isinstance(test.func, ast.Attribute)
                and isinstance(test.func.value, ast.Name)
                and test.func.value.id == "st"
                and test.func.attr in {
                    "button", "checkbox", "toggle", "form_submit_button",
                }
            )
            if is_button:
                # Look for forbidden writes in the body
                for sub in ast.walk(node):
                    issue = self._check_assign(sub)
                    if issue:
                        issues.append(issue)
            self.generic_visit(node)

        def _check_assign(self, sub):
            """Return (lineno, msg) if `sub` is a session_state[K] = ...
            with K in widget_keys."""
            target = None
            if isinstance(sub, ast.Assign):
                if len(sub.targets) == 1:
                    target = sub.targets[0]
            elif isinstance(sub, ast.AugAssign):
                target = sub.target
            elif isinstance(sub, ast.Delete):
                for t in sub.targets:
                    issue = _ss_subscript_match(t, widget_keys, sub.lineno,
                                                 "del st.session_state[…]")
                    if issue:
                        return issue
                return None

            if target is None:
                return None
            return _ss_subscript_match(target, widget_keys, sub.lineno,
                                        "st.session_state[…] = …")

    Visitor().visit(tree)
    return issues


def _ss_subscript_match(node, widget_keys, lineno, label):
    """Match ``st.session_state["K"]`` where K is a widget key."""
    if not isinstance(node, ast.Subscript):
        return None
    val = node.value
    # st.session_state[...]
    if not (isinstance(val, ast.Attribute)
            and val.attr == "session_state"
            and isinstance(val.value, ast.Name)
            and val.value.id == "st"):
        return None
    # Extract the subscript key value (Constant or Name)
    key_node = node.slice
    if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
        k = key_node.value
        if k in widget_keys:
            return (lineno, f"{label} where key={k!r} is a widget key — "
                    f"use ``on_click=callback`` instead.")
    return None


# ──────────────────────────────────────────────────────────────────────────


_TOAST_RX = re.compile(
    r"st\.toast\([^)]*icon\s*=\s*['\"]([^'\"]+)['\"]",
)
# Streamlit accepts characters that Python's ``emoji`` library — or
# the underlying Unicode tables — recognise as emoji. We can't pull the
# emoji library in just for the linter, so we approximate :
#  - Main pictographic blocks : 0x1F300-0x1FAFF (Emoticons, Symbols &
#    Pictographs, Transport, Supplemental, Extended-A, etc.)
#  - Misc-Symbols/Dingbats : 0x2600-0x27BF — Streamlit-friendly EXCEPT
#    a known list of non-emoji symbols (geometric stars, etc.) that
#    Streamlit rejects.
#  - Misc symbols and arrows : individual approved code points.

# Known Misc-Symbols/Dingbats that look like emoji but Streamlit rejects.
_NON_EMOJI_BLOCKLIST: set[int] = {
    0x2605,  # ★ BLACK STAR
    0x2606,  # ☆ WHITE STAR
    0x2734,  # ✴ EIGHT POINTED BLACK STAR
    0x2733,  # ✳ EIGHT SPOKED ASTERISK
    0x21BA,  # ↺ ANTICLOCKWISE OPEN CIRCLE ARROW
    0x21BB,  # ↻ CLOCKWISE OPEN CIRCLE ARROW
}

# Specific outside-the-range code points that ARE accepted (because
# they're Unicode-classified as emoji even though they're in the lower
# Symbols block).
_EXTRA_OK: set[int] = {
    0x2B50, 0x2B55,         # ⭐ MEDIUM STAR ; ⭕ HEAVY LARGE CIRCLE
    0x231A, 0x231B,         # ⌚ WATCH ; ⌛ HOURGLASS
    0x23E9, 0x23EA, 0x23EB, 0x23EC,  # ⏩ ⏪ ⏫ ⏬
    0x23F0, 0x23F1, 0x23F2, 0x23F3,  # ⏰ ⏱ ⏲ ⏳
    0x2705, 0x274C, 0x2728,  # ✅ ❌ ✨
    0x26A0, 0x26A1,         # ⚠ ⚡
    0x2615, 0x2603, 0x2618, 0x2693, 0x26F2,  # ☕ ☃ ☘ ⚓ ⛲
    0x2196, 0x2197, 0x2198, 0x2199, 0x2194, 0x2195,  # arrows
    0x25B6, 0x25C0,         # ▶ ◀
}


def _is_streamlit_emoji(c: str) -> bool:
    if not c:
        return False
    cp = ord(c[0])
    if cp in _NON_EMOJI_BLOCKLIST:
        return False
    if cp in _EXTRA_OK:
        return True
    # Main emoji block (Pictographic & Supplemental)
    if 0x1F300 <= cp <= 0x1FAFF:
        return True
    # Misc-Symbols/Dingbats — accept unless explicitly blocklisted above
    if 0x2600 <= cp <= 0x27BF:
        return True
    return False


def _check_toast_emojis(text: str, rel_path: str) -> list[tuple[int, str]]:
    issues = []
    for i, line in enumerate(text.splitlines(), 1):
        m = _TOAST_RX.search(line)
        if m:
            icon = m.group(1)
            if not _is_streamlit_emoji(icon):
                issues.append(
                    (i, f"st.toast(icon={icon!r}) — not a Streamlit-valid "
                     f"emoji. Use ⭐, ⚠️, ✅, etc.")
                )
    return issues


# ──────────────────────────────────────────────────────────────────────────


_NAN_PATTERNS = [
    re.compile(
        r"\(r\.get\(['\"][^'\"]+['\"]\)\s+or\s+['\"]['\"]\)\.(strip|lower|upper|split)"
    ),
    re.compile(
        r"\(.*?get\(['\"][^'\"]+['\"]\)\s+or\s+\"\s*\"\)\.upper\(\)"
    ),
]


def _check_nan_unsafe_or(text: str, rel_path: str) -> list[tuple[int, str]]:
    issues = []
    for i, line in enumerate(text.splitlines(), 1):
        if "isinstance" in line:
            continue  # likely already guarded
        for rx in _NAN_PATTERNS:
            if rx.search(line):
                issues.append(
                    (i, "``(row.get(X) or \"\").strip()`` will crash on NaN "
                     "(pandas missing cells are nan floats, not None). "
                     "Use ``isinstance(v, str)`` guard.")
                )
                break
    return issues


# ──────────────────────────────────────────────────────────────────────────


def lint_file(path: Path) -> list[tuple[str, int, str]]:
    """Run every check on a single file. Returns ``[(path, lineno, msg)]``."""
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [(str(path), e.lineno or 0, f"SyntaxError: {e.msg}")]

    rel = str(path.relative_to(ROOT))
    keys = _gather_widget_keys(tree)
    out: list[tuple[str, int, str]] = []
    for ln, msg in _check_session_state_in_button_block(tree, keys, rel):
        out.append((rel, ln, msg))
    for ln, msg in _check_toast_emojis(text, rel):
        out.append((rel, ln, msg))
    for ln, msg in _check_nan_unsafe_or(text, rel):
        out.append((rel, ln, msg))
    return out


def main() -> int:
    targets = sorted(APP_UI.rglob("*.py"))
    if not targets:
        print("(no UI files to lint)")
        return 0

    n_issues = 0
    for path in targets:
        if "__pycache__" in path.parts:
            continue
        for rel, ln, msg in lint_file(path):
            n_issues += 1
            print(f"  {rel}:{ln}  {msg}")

    if n_issues:
        print()
        print(f"❌ {n_issues} Streamlit anti-pattern(s) detected.")
        print("   Fix before pushing — these bugs only surface at user click time.")
        return 1
    print("✅ No Streamlit anti-patterns detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
