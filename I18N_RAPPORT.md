# LeadForges · Rapport i18n — version EN livrée

Tu m'as laissé en autonomie. Voici tout ce que j'ai fait, ce qui marche, ce qu'il faut toi vérifier, et les next steps.

## 🎯 TL;DR

- ✅ **Landing EN** live à `https://leadforges-eight.vercel.app/en`
- ✅ **App Streamlit bilingue** via `?lang=en` (login, banner, sidebar, tabs, demo banner traduits)
- ✅ **2 580 exposants** avec leurs 6 champs clés traduits FR→EN par Claude Sonnet 4.6
- ✅ **Stripe Payment Link** vérifié alive (HTTP 200)
- ✅ Toutes les modifs **pushées et déployées**

## 📊 Ce qui est traduit dans la DB

Sur 2 580 exposants :

| Champ | Couverture EN | Note |
|---|---|---|
| `activity_1liner_en` | **2 580 / 2 580** (100%) | Phrase courte d'activité |
| `why_target_en` | **2 580 / 2 580** (100%) | Argument commercial |
| `products_en` | 1 985 (77%) | Liste produits — le reste n'a pas de FR source |
| `services_en` | 2 088 (81%) | Liste services |
| `target_buyers_en` | 2 306 (89%) | Cibles d'achat |
| `technologies_en` | 1 805 (70%) | Technologies maîtrisées |

Coût Claude estimé : ~$8-10 one-shot. Zero échec.

## 🌐 URLs finales

| Type | FR | EN |
|---|---|---|
| **Landing** | `leadforges-eight.vercel.app/` | `leadforges-eight.vercel.app/en` |
| **App (login)** | `leadforgesdemo.streamlit.app/` | `leadforgesdemo.streamlit.app/?lang=en` |
| **Démo gratuite** | `leadforgesdemo.streamlit.app/?demo=1` | `leadforgesdemo.streamlit.app/?demo=1&lang=en` |

Les deux versions ont un **toggle FR/EN** dans la nav (landing) et la sidebar (Streamlit).

## ✅ Ce qui marche (vérifié)

- HTTP 200 sur landing FR ✅
- HTTP 200 sur landing EN ✅
- HTTP 200 sur landing FR avec Accept-Language: en (plus de crash) ✅
- Toggle FR/EN sur les 2 landings ✅
- Streamlit Cloud responsive ✅
- App Streamlit reads `?lang=en` per request (no session leak) ✅
- `_localize_df()` swap correctement les colonnes `_en` → FR colonnes ✅
- Login screen affiche EN quand `?lang=en` ✅
- Tab names + demo banner + access banner traduits ✅
- hreflang SEO bien configuré (alternate languages) ✅
- Pre-push checks (syntax + lint + tests) : 22/22 OK ✅
- Stripe Payment Link `eVq28k...` répond HTTP 200 ✅

## ⚠️ Ce qu'il te reste à vérifier toi (5 minutes max)

### 1. Le prix Stripe (parce que tu as bidouillé)
Click sur ce lien et vérifie visuellement :
**https://buy.stripe.com/eVq28k6Qn8Nde2g66zgnK01**

Tu dois voir :
- Produit : **LeadForges** (ou similaire — pas un truc moche genre "Test 1€")
- Prix HT : **2 000,00 €** (PAS 1 €, PAS 0 €)
- TVA calculée selon ton pays
- Total ≈ 2 400 € pour France

Si le prix est cassé : Stripe Dashboard → Payment Links → l'éditer pour repointer sur le price 2000€.

### 2. Tester le mode EN sur la plateforme
Ouvre en incognito : **https://leadforgesdemo.streamlit.app/?lang=en**
- Login screen en anglais ✅
- Connecte-toi avec `Lf2026Adm1nAccessKey9X`
- Sidebar : toggle FR/EN visible en haut à droite
- Tab "Companies" actif
- Données dans la table : `activity_1liner` doit être en ENGLISH (pas FR)

### 3. Tester le mode démo EN
**https://leadforgesdemo.streamlit.app/?demo=1&lang=en**
- Banner noir "DEMO VERSION" (pas "VERSION DÉMO")
- CTA en haut : "Access the full database (€2,000)" qui pointe sur `/en`
- 50 lignes max, EN dans la colonne activity

### 4. Tester la landing EN
**https://leadforges-eight.vercel.app/en**
- Hero "Know exactly who to contact at Eurosatory 2026"
- Click "Buy" → Stripe link
- Click "Try free" → démo en mode EN
- Click "Book a 15-min call" → Calendly
- FR | **EN** toggle en haut à droite (EN bold)

## 🔧 Architecture mise en place

### Côté code

| Fichier | Rôle |
|---|---|
| `landing/en/index.html` | Landing EN complète (copy traduit professionnellement, mêmes assets) |
| `landing/index.html` | Toggle FR/EN ajouté + hreflang SEO |
| `app/ui/i18n.py` | Module i18n : `t()`, `is_en()`, `get_lang()`, `localized()` |
| `app/ui/streamlit_app.py` | Hooks i18n + `_localize_df()` qui swap les colonnes `_en` |
| `app/ui/auth.py` | Login screen + access banner bilingues |
| `scripts/translate_exhibitor_fields_to_en.py` | Script Claude pour traduire le JSON (parallel batches) |

### Côté detection langue

- Lecture **per-request** depuis `st.query_params["lang"]` (jamais d'env var globale)
- Aucun risque de leak entre sessions sur Streamlit Cloud (shared process)
- `?lang=en` → mode EN, sinon FR par défaut
- Toggle dans login screen + sidebar → met à jour l'URL et rafraîchit

### Côté SEO

- `hreflang="fr"` + `hreflang="en"` + `hreflang="x-default"` sur les 2 landings
- Meta description en EN sur `/en`
- OG image et tags adaptés
- Google va indexer les 2 versions séparément

## 📈 Ce que ça change pour ton outreach

Tu as **3 000 contacts** :
- ~2 250 français → envoie le lien `https://leadforges-eight.vercel.app/` (FR)
- ~750 anglo-saxons → envoie le lien `https://leadforges-eight.vercel.app/en` (EN)

Quand un anglo cliquera, il verra **tout en anglais** : landing, démo, paywall Stripe (Stripe affiche automatiquement la langue du browser), email d'accès reçu après achat (le webhook Vercel reste en français pour l'instant — voir Next steps).

## 🚧 Ce que JE N'AI PAS fait (volontairement)

| Item | Pourquoi pas |
|---|---|
| Traduction du **content des attendance_signals** (`signal_text`, `recommended_angle`) | ~42 000 traductions × ~$0.001 = ~$50 + 4h. Différé : signal_text est souvent déjà en EN (sources presse anglo). Faisable plus tard. |
| Traduction des **column headers** détaillés (Type, Pays, Headline, etc.) | Plus de la moitié sont déjà en EN ou universels (Tags, Hall/pavilion). Faible ROI. |
| Traduction de l'**email du webhook Stripe** (template "Tes accès LeadForges") | À adapter au cas par cas. Si tu vends à un anglo, copie/colle l'email du webhook et traduis manuellement le contenu, OU détecte le pays Stripe dans le webhook (10 lignes de code). |
| Vercel **Accept-Language auto-redirect** | Tenté + retiré (bug Edge middleware). Le toggle manuel suffit + tu envoies l'URL EN directement dans ton outreach. |

## 🚀 Next steps recommandés

### Immédiats (toi, 30 min)

1. **Vérifie le Stripe Payment Link** (cf §1 ci-dessus) — critique
2. **Teste les 4 URLs** (cf §2, §3, §4) — 5 min
3. **Adapte le template email du webhook** pour les buyers EN — ou laisse en FR (la majorité comprennent)

### Court terme (avant outreach EN)

4. **Rédige ton template d'outreach EN** : adapté au profil anglo-saxon défense
5. **Segmente tes 3 000 contacts** : FR / EN / autres langues
6. **Envoie en deux campagnes distinctes** Lemlist (une FR, une EN)

### Optionnel plus tard

7. Traduction des attendance signals (~$50, 4h script run)
8. Webhook Stripe envoie l'email dans la langue du buyer (10 min de code)
9. Custom domain `leadforges.io` avec sous-domaines `app.` `en.` (5 min DNS si tu achètes)

## 💾 Tous les commits aujourd'hui

```
561f21d  i18n : EN translations for 2580 exhibitor profiles (Claude Sonnet 4.6)
46dd385  fix(vercel): remove buggy middleware
b2cc859  fix(vercel): edge middleware Response.redirect → manual Response (kept until 46dd385 dropped it)
b212170  i18n : also localize comparison view + export selection
ffd4d7a  i18n : English landing /en + Streamlit lang toggle (?lang=en)
```

Tout déjà en ligne. Aucune action git restante de mon côté.

---

**Tu peux lancer ton outreach EN dès maintenant.**

Si quelque chose te paraît bizarre, tu me dis et je débugge.
