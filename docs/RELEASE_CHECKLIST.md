# LeadForges · Release checklist

Three-layer defense to keep prod bug-free.

## Layer 1 — Pre-commit / pre-push gates (automated)

The git hook installed at `.git/hooks/pre-push` blocks any push that fails :

| Check | Catches |
|---|---|
| `py_compile` on every `app/`, `scripts/`, `tests/` file | Syntax errors, import-time errors |
| `scripts/lint_streamlit_patterns.py` | `session_state[K] = …` after widget instantiation, invalid `st.toast` emoji icons, NaN-unsafe `or ""` patterns |
| `tests/test_auth.py` | Auth regressions (login, expiry, NaN safety, zone mapping, company_type derivation) |

**Install once** :
```bash
ln -sf ../../scripts/pre_push_check.sh .git/hooks/pre-push
```

**Manual run** (debugging) :
```bash
bash scripts/pre_push_check.sh
```

**Override** (use sparingly, only when you KNOW the failure is a false positive) :
```bash
git push --no-verify
```

## Layer 2 — Smoke test post-push

After `git push`, before announcing the deploy to buyers :

```bash
python -m scripts.smoke_test_prod
```

Polls the prod URL until `_stcore/health` returns `200`, then probes the homepage. Catches **boot failures** (the Cloud rebuild crashed at module import). Does NOT catch interactive bugs.

## Layer 3 — Manual click-test (60 seconds)

After the smoke test passes :

1. Open the prod URL in a fresh incognito window.
2. Type your admin password — confirm the banner shows your name + expiry.
3. Click one **company-type chip** (e.g. *OEM*) — confirm the table reloads.
4. Open the **sidebar filter** *Type d'entreprise* — confirm it's already pre-checked.
5. Switch to **📡 Attendance Signals** — confirm the page loads.
6. Switch to **Listes ciblées** — click *Charger ces filtres* on one list, confirm you bounce back to Companies with the filters applied.
7. **Sign out** — confirm you return to the login screen.

If any step fails : open `Manage app → Logs` in Streamlit Cloud, copy-paste the traceback, fix, re-push.

## Adding new bug patterns

When a new prod bug surfaces, immediately :

1. Fix the bug.
2. Add a new check to `scripts/lint_streamlit_patterns.py` (static detection).
3. Add a regression test to `tests/test_auth.py` or a new test file.
4. Commit both the fix and the new check in the same push — guarantees the bug class never comes back.

This is how the codebase gets safer over time : every prod bug becomes a guard rail.

## Common bug patterns already guarded against

### `session_state[K] = value` after widget instantiation
**Symptom** : Click on a chip / button → `StreamlitAPIException: cannot be modified after the widget with key=… is instantiated`.

**Fix** : Use `on_click=callback` instead of mutating `st.session_state[K]` inside an `if st.button(...):` block. Callbacks run BEFORE the next script run, so widgets re-instantiate cleanly.

```python
# ❌ Wrong
if st.button("Click"):
    st.session_state["filter_X"] = new_value

# ✅ Right
def _cb():
    st.session_state["filter_X"] = new_value
st.button("Click", on_click=_cb)
```

### Invalid emoji icon in `st.toast`
**Symptom** : `StreamlitAPIException: The value "…" is not a valid emoji`.

**Fix** : Use proper Unicode emoji code points only (⭐ U+2B50, ✅, ⚠️, ⏳). Avoid geometric symbols like ★ (U+2605), ↺.

### Pandas NaN floats crashing `.strip()` / `.upper()`
**Symptom** : `AttributeError: 'float' object has no attribute 'strip'` somewhere in `apply()` callbacks.

**Fix** : Replace `(row.get("col") or "").strip()` with an `isinstance` guard :
```python
v = row.get("col")
if isinstance(v, str):
    v = v.strip()
else:
    v = ""
```
