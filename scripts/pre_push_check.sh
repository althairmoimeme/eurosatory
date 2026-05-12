#!/usr/bin/env bash
# Pre-push gate for LeadForges. Refuses to push if any check fails.
#
# Install as a git hook :
#     ln -sf ../../scripts/pre_push_check.sh .git/hooks/pre-push
#
# Manual run :
#     bash scripts/pre_push_check.sh
#
# What it checks
# ──────────────
#   1. ``py_compile`` every file under app/ and scripts/ — catches
#      syntax errors at the cheapest possible level.
#   2. Streamlit anti-pattern lint
#      (scripts/lint_streamlit_patterns.py) — catches the bug classes
#      that only surface at user click time.
#   3. Auth regression tests (tests/test_auth.py) — fast, no IO,
#      tolerable in a pre-push hook.
#
# Exit codes
# ──────────
#   0 → all green, push proceeds
#   1 → at least one check failed, push aborted

set -u

# Robust repo-root resolution — works whether the script is invoked
# directly or via the symlinked git hook (.git/hooks/pre-push).
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [[ -z "$REPO_ROOT" ]]; then
    # Fallback : two levels up from the script file (resolving symlinks)
    SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
    REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
cd "$REPO_ROOT" || exit 2

# Use the venv python directly — sourcing ``activate`` in a hook
# context doesn't persist, and PATH might be minimal under git.
if [[ -x .venv/bin/python ]]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "❌ No Python interpreter found. Install Python or restore .venv."
    exit 2
fi
export PYTHON

FAIL=0
echo "─────────────────────────────────────────────────────────────────"
echo "  Pre-push checks (LeadForges)"
echo "─────────────────────────────────────────────────────────────────"

# 1. Syntax check on every file
echo
echo "[1/3] Python syntax check ..."
SYNTAX_FAIL=0
while IFS= read -r f; do
    if ! $PYTHON -m py_compile "$f" 2>/dev/null; then
        echo "      ❌ $f"
        SYNTAX_FAIL=1
    fi
done < <(find app scripts tests -name '*.py' -not -path '*/__pycache__/*')
if [[ $SYNTAX_FAIL -eq 0 ]]; then
    echo "      ✅ Every .py file compiles."
else
    FAIL=1
fi

# 2. Streamlit anti-pattern lint
echo
echo "[2/3] Streamlit anti-pattern lint ..."
if $PYTHON -m scripts.lint_streamlit_patterns; then
    :
else
    FAIL=1
fi

# 3. Auth + normalizers regression tests
echo
echo "[3/3] Regression tests (tests/test_auth.py) ..."
if $PYTHON -m pytest tests/test_auth.py -q --no-header 2>&1 | tail -5; then
    if $PYTHON -m pytest tests/test_auth.py -q --no-header > /dev/null 2>&1; then
        echo "      ✅ All regression tests passing."
    else
        FAIL=1
    fi
else
    FAIL=1
fi

echo
echo "─────────────────────────────────────────────────────────────────"
if [[ $FAIL -eq 0 ]]; then
    echo "  ✅ All pre-push checks passed."
    exit 0
else
    echo "  ❌ Pre-push checks FAILED. Push aborted."
    echo "     Fix the issues above before pushing, or override with"
    echo "     ``git push --no-verify`` (use sparingly !)."
    exit 1
fi
