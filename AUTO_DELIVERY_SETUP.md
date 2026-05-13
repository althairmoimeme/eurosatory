# LeadForges — Auto-delivery des identifiants (Stripe → Vercel → Turso + Resend)

**Total : ~20 minutes de click-ops, ensuite zero touch par vente.**

Architecture :
```
Buyer paie sur Stripe
   ↓
Stripe POST → Vercel /api/stripe-webhook (Python)
   ↓
[Webhook]  vérifie signature → génère password 20-char
         → INSERT INTO turso buyers
         → Resend → email buyer (mot de passe + URL)
         → Resend → email admin (notif vente)
   ↓
Buyer se connecte sur leadforgesdemo.streamlit.app
   ↓
app/ui/auth.py lit Turso (cache 30s) + st.secrets → valide → access OK
```

---

## ☑️ Étape 1 — Turso (5 min) — la BDD des buyers

1. Crée un compte sur https://turso.tech (sign up with GitHub)
2. Dashboard → **+ Create Database**
   - Name : `leadforges`
   - Group : `default` (région la plus proche, ex `fra` pour Paris)
3. Une fois la DB créée, click dessus → onglet **General**
   - Copie **Database URL** : `libsql://leadforges-<your-org>.turso.io`
   - Copie le **token** : section **Generate Token** → Read & Write → Never expires → Generate → copie le `eyJxxxxx…` (long token JWT)

Tu n'as PAS besoin de créer la table — le webhook le fait automatiquement au premier hit (`CREATE TABLE IF NOT EXISTS buyers`).

---

## ☑️ Étape 2 — Resend (5 min) — l'envoi d'emails

1. Crée un compte sur https://resend.com (sign up with GitHub)
2. Dashboard → **API Keys** → **+ Create API Key**
   - Name : `leadforges-prod`
   - Permission : `Sending access` (read+write on Send)
   - Copie le `re_xxxxx…`
3. Pour démarrer : utilise le sender par défaut `onboarding@resend.dev`
   - Limite : 100 emails/jour, super pour valider le funnel
4. Quand t'as un domaine (`leadforges.io`), ajoute-le dans **Domains** → vérifie DKIM (5 min) → utilise `noreply@leadforges.io` comme sender

---

## ☑️ Étape 3 — Env vars dans Vercel (3 min)

Vercel dashboard → ton projet `leadforges` → **Settings → Environment Variables** → ajoute (Production + Preview + Development = tout) :

| Variable | Valeur |
|---|---|
| `STRIPE_WEBHOOK_SECRET` | (vide pour l'instant, on remplit à l'étape 5) |
| `TURSO_DATABASE_URL` | `libsql://leadforges-…turso.io` (étape 1) |
| `TURSO_AUTH_TOKEN` | `eyJxxxxx…` (étape 1) |
| `RESEND_API_KEY` | `re_xxxxx…` (étape 2) |
| `RESEND_FROM` | `LeadForges <onboarding@resend.dev>` |
| `ADMIN_EMAIL` | `a.bertantoine@gmail.com` |
| `APP_URL` | `https://leadforgesdemo.streamlit.app` |
| `ACCESS_DURATION_DAYS` | `78` |

→ Click **Save**.

---

## ☑️ Étape 4 — Env vars dans Streamlit Cloud (3 min)

Streamlit Cloud dashboard → `leadforges` → **⋮ Manage app → Settings → Secrets** → ajoute À LA FIN du fichier (en plus des `[[buyers]]` existants) :

```toml
# --- Turso (live buyers DB, auto-populated by Stripe webhook) ---
TURSO_DATABASE_URL = "libsql://leadforges-…turso.io"
TURSO_AUTH_TOKEN   = "eyJxxxxx…"
```

→ Click **Save**.  
→ Streamlit Cloud redémarre l'app automatiquement (~30s).

---

## ☑️ Étape 5 — Webhook Stripe (3 min)

1. Stripe Dashboard → **Developers → Webhooks** → **+ Add endpoint**
2. **Endpoint URL** : `https://leadforges.vercel.app/api/stripe-webhook`
3. **Listen to** : "Events on your account"
4. **Select events** : UNIQUEMENT `checkout.session.completed`
5. **Description** (optionnel) : "LeadForges auto-delivery"
6. Click **Add endpoint**
7. Sur la page de l'endpoint → section **Signing secret** → bouton **Reveal** → copie `whsec_xxxxx…`
8. Retourne dans **Vercel → Settings → Environment Variables** → édite `STRIPE_WEBHOOK_SECRET` → colle le `whsec_…` → **Save**
9. Vercel → **Deployments** → click le bouton **⋮** sur le dernier deploy → **Redeploy** (pour que la nouvelle env var soit prise en compte par la fonction)

---

## ☑️ Étape 6 — Test end-to-end (5 min)

### 6a. Test du webhook directement (sanity check)

```bash
curl -s https://leadforges.vercel.app/api/stripe-webhook
```
Tu dois voir : `{"ok": true, "service": "leadforges-stripe-webhook"}`. Si pas ça → la fonction n'est pas deployée correctement, check les logs Vercel.

### 6b. Test du flow complet en mode Stripe TEST

1. Stripe Dashboard → bouton **Test mode** en haut à droite (toggle)
2. **Products → ton produit LeadForges** → **+ Create payment link** (en mode test cette fois)
3. Webhooks → **+ Add endpoint** (idem qu'étape 5) → cette fois sur Stripe TEST avec **le même URL Vercel** → copie le `whsec_test_…` → set comme `STRIPE_WEBHOOK_SECRET_TEST` dans Vercel (ou remplace temporairement le secret prod)
4. Ouvre ton Payment Link de TEST → utilise la carte de test `4242 4242 4242 4242`, n'importe quelle date + CVC
5. Renseigne ton **propre email** comme buyer
6. Click Payer

### 6c. Vérifie ce qui s'est passé

- **Email reçu (~30s)** : "Tes accès LeadForges" → contient ton mot de passe
- **Email reçu admin (~30s)** : "💰 LeadForges sale — Ton Nom"
- **Vercel logs** : Dashboard → Deployments → click le dernier → **Functions** → tu vois la requête POST avec status 200
- **Turso** : Dashboard → ta DB → **Edit data** → tu vois ta ligne dans `buyers`
- **Streamlit** : ouvre `https://leadforgesdemo.streamlit.app` → colle le mot de passe reçu → accès OK ✅

Si l'un de ces 5 checks rate, lis les logs Vercel — l'erreur est dedans.

### 6d. Repasse en LIVE

Une fois le test OK, supprime/désactive le webhook de TEST dans Stripe et garde uniquement celui de LIVE configuré à l'étape 5.

---

## 🎯 Une fois fait, voilà ton flow par vente :

```
T+0      : Buyer click "Acheter" sur la landing
T+30s    : Buyer renseigne carte sur Stripe → paie 2 400 € TTC (2 000 HT + TVA)
T+45s    : Stripe envoie checkout.session.completed à Vercel
T+50s    : Vercel webhook → Turso INSERT + Resend send
T+1min   : Buyer reçoit son email avec password + URL
T+2min   : Buyer login sur leadforgesdemo.streamlit.app
T+3min   : Tu reçois ton email de notif "💰 sale"
```

**Toi : 0 click**. C'est entièrement auto.

---

## 🐛 Debug rapide

| Symptôme | Cause probable | Fix |
|---|---|---|
| Buyer paie mais ne reçoit rien | Webhook URL invalide ou env vars manquantes côté Vercel | Stripe Dashboard → Webhooks → ton endpoint → onglet "Events" → check le code HTTP de la dernière tentative |
| Buyer reçoit email mais login fail | Streamlit Cloud n'a pas les env vars Turso (étape 4) | Refait l'étape 4 → Save → patiente 30s |
| Email arrive en spam | Sender `onboarding@resend.dev` non whitelisté chez le buyer | Vérifier ton domaine custom sur Resend (étape 2.4) |
| Doublon de buyer (Stripe retry) | Le webhook est idempotent sur `stripe_session_id` | ✅ Pas de problème : la 2ème tentative renvoie `{"duplicate": true}` sans réenvoyer l'email |
| 400 "invalid signature" dans Vercel logs | Le `STRIPE_WEBHOOK_SECRET` ne match pas | Vérifie qu'il a bien commencé par `whsec_` et que tu as fait le Redeploy après changement de la var |

---

## 📊 Voir tes ventes en temps réel

Turso a un éditeur SQL en ligne :

1. Turso dashboard → ta DB → **Edit data** → table `buyers`
2. Tu vois toutes les ventes : nom, email, montant, date, password

Ou via la CLI :
```bash
turso db shell leadforges "SELECT created_at, name, email, amount_paid_cents/100 AS eur FROM buyers ORDER BY id DESC"
```

---

## 🔐 Note sécurité

Les mots de passe sont stockés **en clair** dans Turso. C'est volontaire :
- Volume faible (max quelques dizaines de buyers)
- Pas de donnée sensible derrière (juste un accès à une base de données défense déjà publique)
- Simplicité > rigueur cryptographique pour un moonshot 1-shot

Si tu veux du hashing bcrypt plus tard, c'est 20 lignes de code à ajouter dans le webhook et dans `auth.py`. Mais pas critique pour Eurosatory 2026.
