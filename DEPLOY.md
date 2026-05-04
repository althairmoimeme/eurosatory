# Deploy on Streamlit Community Cloud

Step-by-step guide to deploy this Streamlit app at a public, password-protected
URL — accessible 24/7 without your laptop running.

---

## 1. Push the code to GitHub (private repo)

```bash
cd /Users/bertantoine/eurosatory-scraper

# Already initialised by the prep script — just verify
git status
git log --oneline | head -5
```

Then on GitHub :

1. Go to <https://github.com/new>
2. Repository name : `eurosatory-scraper` (or anything you like)
3. **Visibility : Private** (the data contains commercial defense intel)
4. *Do NOT* tick "Add a README / .gitignore / license" — we already have ours
5. Click **Create repository**

Push your local repo :

```bash
git remote add origin git@github.com:YOUR_USERNAME/eurosatory-scraper.git
# OR using HTTPS if you don't have an SSH key:
# git remote add origin https://github.com/YOUR_USERNAME/eurosatory-scraper.git

git branch -M main
git push -u origin main
```

---

## 2. Deploy on Streamlit Community Cloud

1. Go to <https://share.streamlit.io/>
2. Sign in with GitHub (the same account where you just pushed)
3. Click **New app** → **From existing repo**
4. Fill in :
   - **Repository** : `YOUR_USERNAME/eurosatory-scraper`
   - **Branch** : `main`
   - **Main file path** : `app/ui/streamlit_app.py`
   - **App URL** (subdomain) : pick something like `eurosatory-targeting`
5. Click **Advanced settings** :
   - Python version : `3.11` or `3.12`
6. Click **Deploy**

The first build takes ~3-5 minutes (downloads + installs deps).

---

## 3. Add the password (and any optional API keys)

While the app is building (or right after first load) :

1. In the Streamlit Cloud dashboard, click your app → **Settings** (top right)
2. Scroll to **Secrets**
3. Paste this (replace the password!) :

```toml
APP_PASSWORD = "votre-mot-de-passe-fort-ici"

# Optional — only needed if you re-run scraping/LLM scripts from the cloud
ANTHROPIC_API_KEY = ""
ENRICH_SO_API_KEY = ""
FULLENRICH_API_KEY = ""
ROCKETREACH_API_KEY = ""
```

4. **Save** → the app reboots and now requires the password.

---

## 4. Restrict access to specific Google accounts (optional, recommended)

Streamlit Cloud lets you restrict access by Google email :

1. App → Settings → **Sharing**
2. Toggle **Make this app private** ON
3. Add your collaborator's email + your own
4. Save

Anyone outside the list can't even see the password screen.

---

## 5. Share

Send your collaborator :

- The URL : `https://eurosatory-targeting.streamlit.app/` (or whatever subdomain you picked)
- The password (out-of-band — Slack DM, signal, etc.)

---

## What happens when you push new commits

Streamlit Cloud auto-deploys on every push to `main`. To update :

```bash
# After making changes locally
git add .
git commit -m "Update: <what changed>"
git push
```

The app rebuilds in ~30s-2min and your collaborator sees the updated version.

---

## Updating the data

The slim ``data/eurosatory_deploy.db`` is the read-only DB shipped to the
cloud. To refresh it locally :

```bash
# 1. Re-run any scraping / enrichment locally as usual (writes to
#    ``data/eurosatory.db`` — the full dev DB).
# 2. Rebuild the slim deploy DB :
python -c "
import sqlite3, shutil
shutil.copy('data/eurosatory.db', 'data/eurosatory_deploy.db')
con = sqlite3.connect('data/eurosatory_deploy.db')
for t in ('crawled_pages', 'scraping_runs', 'activity_log',
          'exhibitor_enrichment_sources'):
    con.execute(f'DROP TABLE IF EXISTS {t}')
con.commit(); con.execute('VACUUM'); con.close()
"
# 3. Refresh JSON exports (used by the targeting profile UI):
python -m scripts.merge_manual_overrides
python -m scripts.normalize_categories
python -m scripts.export_xlsx

# 4. Push:
git add data/eurosatory_deploy.db data/exports/
git commit -m "Refresh data $(date +%Y-%m-%d)"
git push
```

---

## Limits / caveats

- Streamlit Cloud free tier : 1 GB RAM, sleeps after 7 days of inactivity
  (wakes up automatically on next visit, takes 10-30s to spin up).
- The slim DB is **read-only** — custom-list changes made on the cloud
  instance are ephemeral (reset on each rebuild). If your collaborator
  needs persistent custom lists, we'd need to add a hosted Postgres
  (Supabase / Neon free tiers work).
- Cloud is hosted on AWS US-East. If juridiction matters (defense data),
  consider Hetzner Falkenstein (DE) instead — same setup but on a
  Hetzner VPS.
