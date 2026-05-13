# LeadForges — Setup du tunnel de vente

Tout ce qui a été codé + ce qu'il te reste à faire (services externes).

---

## 1 · Landing page · `landing/index.html`

**Ce qui est fait :**
- HTML autonome (Tailwind via CDN, fonts Inter + JetBrains Mono)
- Charte noir / blanc / gris avec accent navy `#0B2E4A` (cohérent avec l'app)
- Structure pro : hero + démo vidéo + problème/agitation + solution (3 couches) + chiffres + pricing + démo CTA + FAQ + final CTA + footer
- Copy AIDA + PAS, urgence (J-33), risque retourné via démo gratuite, FAQ pour objections

**Ce qu'il faut compléter :**
| Élément | Où | Quoi mettre |
|---|---|---|
| Vidéo démo | `<div class="aspect-video">` ~ligne 130 | `<iframe src="https://www.loom.com/embed/TON_ID" allow="..." style="..."></iframe>` |
| Stripe Payment Link | `<a id="cta-buy" href="...">` ~ligne 220 | Le `https://buy.stripe.com/…` créé à l'étape 2 |
| Calendly | Tous les `cal.com/leadforges/15min` (2 occurrences) | Ton vrai lien Calendly / Cal.com / Savvycal |
| Email contact | `<a href="mailto:contact@leadforges.io">` | Ton vrai email |
| URL de l'app démo | `<a href="https://leadforges.streamlit.app/?demo=1">` | À garder telle quelle (déjà branché) |

**Déploiement :** drag & drop `landing/index.html` sur Vercel / Netlify / Cloudflare Pages → URL en 30 sec. Pour un vrai domaine `leadforges.io`, achète-le sur Cloudflare Registrar (~9€/an) et pointe-le sur Vercel.

---

## 2 · Stripe · paiement à 2 000 €

### 2.1 · Créer le produit
1. Stripe Dashboard → **Products** → **+ Add product**
2. Nom : `LeadForges — Base d'intelligence Eurosatory 2026`
3. Prix : `2000.00 EUR` · One-time
4. ✅ **Note l'ID du prix** (`price_xxxxx`) — ça sert pour l'anti-spoof.

### 2.2 · Créer le Payment Link
1. Stripe Dashboard → **Payment links** → **+ New**
2. Sélectionne le produit créé
3. **Collect customer info** : ✅ Name, ✅ Email (obligatoire pour la livraison)
4. **After payment** : Show confirmation page → message :
   > "Merci ! Tu reçois tes accès LeadForges par email dans les 2 minutes. Vérifie aussi tes spams."
5. Copie le lien `https://buy.stripe.com/...` → colle-le dans `landing/index.html` à la place de `REMPLACE_PAR_TON_PAYMENT_LINK`.

### 2.3 · Configurer le webhook (livraison auto des identifiants)

**Option A — Manuel (recommandé pour démarrer, < 10 ventes / semaine) :**

Tu ne setup PAS le webhook. À chaque vente, Stripe t'envoie l'email de notification. Tu lances :
```bash
cd /Users/bertantoine/eurosatory-scraper
python -m scripts.grant_access --name "Nom du buyer" --email "client@x.com" --days 78
git add .streamlit/secrets.toml && git commit -m "grant access" && git push
```
Le script `grant_access.py` génère le mot de passe + imprime l'email à copier-coller au buyer. Streamlit Cloud rebuild en ~30s. Total : **5 min par vente.**

**Option B — Auto (pour scaler) :**

1. Déploie `scripts/stripe_webhook.py` sur Fly.io / Render / Vercel :
   ```bash
   pip install fastapi uvicorn stripe
   uvicorn scripts.stripe_webhook:app --port 4242
   ```
2. Configure les env vars (voir docstring du fichier) :
   - `STRIPE_WEBHOOK_SECRET` (depuis Stripe → Developers → Webhooks)
   - `STRIPE_PRICE_ID` (depuis l'étape 2.1)
   - `SMTP_*` (Gmail App Password ou SendGrid / Postmark / Resend)
   - `BUYERS_REPO_DIR` (chemin d'un clone du repo leadforges sur le serveur)
   - `GITHUB_TOKEN` (PAT avec write access au repo)
3. Stripe Dashboard → **Developers** → **Webhooks** → **+ Add endpoint** :
   - URL : `https://<ton-deploy>/stripe/webhook`
   - Event : `checkout.session.completed`
   - Copy le **signing secret** → variable `STRIPE_WEBHOOK_SECRET`

Le worker :
- Vérifie la signature Stripe (anti-fake)
- Génère un mot de passe 20-char
- Append un `[[buyers]]` à `.streamlit/secrets.toml`
- Git commit + push → Streamlit Cloud rebuild
- Envoie l'email avec les identifiants au buyer
- T'envoie un email de notification

---

## 3 · Version démo · 50 lignes (déjà codée)

**Status : prête à l'emploi.**

- DB démo : `data/eurosatory_demo.db` (50 exposants + 50 signaux, 0.9 MB)
- Construite par : `python -m scripts.build_demo_db`
- Activée par : URL `?demo=1` ou env var `LEADFORGES_DEMO=1`

**URL démo :** `https://leadforges.streamlit.app/?demo=1`

En mode démo :
- ❌ Auth bypassée (accès libre sans mot de passe)
- ✅ Banner persistant noir "VERSION DÉMO · 50 lignes" en haut avec CTA "Accéder à la base complète"
- ✅ Toutes les fonctionnalités fonctionnent (filtres, listes, groupements, exports)
- ❌ Les fonctions admin (curation, watchlist…) restent invisibles (déjà gatées par `is_admin()`)

**À tester localement :** `streamlit run app/ui/streamlit_app.py` puis ouvre `http://localhost:8501/?demo=1`.

---

## 4 · Calendly / Cal.com — démo 15 min

1. Crée un compte sur [Cal.com](https://cal.com) (gratuit) ou Calendly (gratuit jusqu'à 1 event type)
2. Crée un event type "LeadForges Demo · 15 min" :
   - Durée : 15 min
   - Buffer : 5 min avant / après
   - Lieu : Google Meet (auto-généré)
3. Copie ton lien (ex: `https://cal.com/leadforges/demo`) → remplace `https://cal.com/leadforges/15min` dans `landing/index.html` (2 occurrences) ET dans le template email de `scripts/stripe_webhook.py`.

---

## 5 · Email · SMTP

Pour l'envoi des identifiants automatique (Option B du §2.3) :

**Option simple — Gmail App Password (limité 500 emails/jour, OK pour le moonshot) :**
1. Active la 2FA sur ton compte Gmail
2. Va sur https://myaccount.google.com/apppasswords
3. Génère un "App password" pour "LeadForges"
4. Set :
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=ton@gmail.com
   SMTP_PASSWORD=<le-16-char-app-password>
   DELIVERY_FROM_EMAIL="LeadForges <ton@gmail.com>"
   ```

**Option pro — Resend / Postmark / SendGrid (meilleure délivrabilité) :**
- [Resend](https://resend.com) : 3 000 emails/mois gratuit, SDK ultra-simple
- Configure un domaine custom (`noreply@leadforges.io`) avec DKIM/SPF
- Récupère ta clé API → adapte `_send_email()` dans `scripts/stripe_webhook.py`

---

## 6 · Checklist déploiement final

```
[ ] Tester localement : streamlit run app/ui/streamlit_app.py
    → http://localhost:8501              (mode normal, login)
    → http://localhost:8501/?demo=1      (mode démo, sans login)

[ ] Pusher tout au git :
    git add -A
    git commit -m "Sales funnel : landing page + demo mode + Stripe webhook"
    git push

[ ] Configurer Stripe :
    [ ] Produit + Payment Link → 2 000 €
    [ ] Coller le Payment Link dans landing/index.html

[ ] Déployer la landing :
    [ ] Drag & drop landing/index.html sur Vercel
    [ ] (Optionnel) Connecter un domaine custom

[ ] Setup Calendly / Cal.com → remplacer les 2 occurrences dans landing/

[ ] Enregistrer la vidéo de démo (2 min) → upload Loom unlisted
    → coller l'iframe dans landing/index.html

[ ] (Option A) Démarrer en livraison manuelle via grant_access.py
    OU
[ ] (Option B) Déployer scripts/stripe_webhook.py + configurer le webhook Stripe

[ ] Tester le tunnel end-to-end :
    [ ] Ouvrir la landing
    [ ] Cliquer "Tester gratuitement" → vérifier la démo charge
    [ ] Cliquer "Acheter" → simuler un paiement Stripe (mode test)
    [ ] Vérifier que tu reçois l'email + que les identifiants sont créés
    [ ] Se connecter à leadforges.streamlit.app avec ces identifiants

[ ] Annoncer aux ~3 000 contacts défense que tu as.
```

---

## 7 · Tarifs de production estimés

| Service | Coût mensuel | Note |
|---|---|---|
| Streamlit Cloud (app) | 0 € | Gratuit jusqu'à 1 GB RAM |
| Vercel (landing) | 0 € | Hobby tier suffit |
| Stripe | 1,4 % + 0,25 € / transaction | ~28 € par vente à 2k |
| Resend (emails) | 0 € | 3 000 emails/mois gratuit |
| Cal.com | 0 € | Free tier |
| Fly.io (webhook worker) | 0 € | Free tier (256 MB) |
| Domaine `.io` | ~3 €/mois | Optionnel |
| **Total fixe** | **~3 €/mois** | + 28 € par vente |

**Break-even : 1 vente** (1 972 € net par vente).

---

## 8 · Que faire si quelqu'un veut une démo guidée en direct ?

Tous les CTA secondaires pointent vers ton Calendly 15 min. Pendant l'appel :
1. Ouvre `https://leadforges.streamlit.app/?demo=1` en partage d'écran
2. Filtre sur le segment du prospect (zone géo, type d'entreprise, secteur)
3. Montre un signal en détail (l'angle d'approche suggéré, la source URL)
4. Pose la question : *"Sur ton ICP réel, tu vois combien de leads tu sortirais en 30 min ?"*
5. Fini par : *"Tu peux acheter directement, ou je t'envoie le Stripe Link par mail — comme tu préfères."*
