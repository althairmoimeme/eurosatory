# Eurosatory 2026 — defense commercial intelligence

Solution end-to-end pour analyser les **2 337 exposants** d'Eurosatory 2026 et produire une base commerciale **premium** exploitable par des équipes de vente défense / sécurité.

Pour chaque exposant, l'outil identifie :

1. **Ce que la société fabrique** (built_products + summary)
2. **Ce qu'elle vend** (sold_offerings + business_model)
3. **Ce qu'elle achète probablement** (probable_buying_needs avec confiance et justification)
4. **À qui la revendre comme cible commerciale** (commercial_target_type, interest_level, sales angle, pitch, objections, prospecting keywords)
5. **Sa pertinence défense** (defense_categories multi-label, defense_commercial_score sur 100, priority_level A+/A/B/C/D)

Le tout avec **source URL et niveau de confiance par champ**, et une **fiche commerciale 5 lignes** prête à imprimer.

## Aperçu d'une fiche commerciale réelle

```
SOCIÉTÉ : NT SERVICE UAB (LT)
ACTIVITÉ : "NT Service" UAB produces and delivers RF disruption systems...
CE QU'ILS FABRIQUENT : Counter-UAV systems, UAV / drones, Communications networks, Energy / batteries, Robotics / UGV
CE QU'ILS VENDENT : Maintenance / MRO, Engineering / consulting
CE QU'ILS ACHÈTENT PROBABLEMENT : Energy / batteries, Engines / propulsion, Sensors,
                                  Optronics / Optics, Communications networks,
                                  Composite materials, Embedded systems, Electronic components
POURQUOI C'EST UNE CIBLE : defense fit: Counter-UAV, UAV / drones, Homeland security ...
ANGLE D'APPROCHE : Position NT SERVICE UAB as a possible supplier / sub-contractor.
                   Target the procurement / supply chain function, not the BU.
PITCH CONSEILLÉ : We help defense engineering services like NT SERVICE UAB
                  accelerate component sourcing, qualify alternative suppliers ...
PRIORITÉ : A
DONNÉES À VÉRIFIER : (none)
SOURCES :
  - built::Counter-UAV systems: https://eurosatory.finderr.cloud/...
  - built::UAV / drones: http://ntservice.eu/
  - sold::Maintenance / MRO: http://ntservice.eu/
  - tech::5G / wireless: http://ntservice.eu/services/
```

## Architecture

```
Eurosatory catalogue (Finderr Cloud public API)
        │
        ▼  scrape  →  2 337 exposants
        │  enrich (Finderr v3 detail)  →  adresses, présentation, LinkedIn
        ▼
deep_crawler  →  visite priorisée des sites officiels
   /products /solutions /services /capabilities /industries
   /defense /aerospace /technology /about /news /downloads /pdf
   + extraction PDF (PyMuPDF) sur brochures, datasheets, catalogues
        ▼
Rule-based intelligence extractor (Python, deterministic)
   → built_products, sold_offerings, services, technologies,
     target_clients, markets_served, business_model, company_type,
     probable_buying_needs (BUYING_NEEDS_BY_BUILT mapping)
        ▼
Defense taxonomy (27 labels EN, multi-label)
        ▼
[Optional]  LLM refine (Anthropic SDK, claude-haiku-4-5, prompt caching)
   → tighter classifications + 5-line summary + pitch + objections + keywords
        ▼
Defense scorer (7 components, 0-100, A+/A/B/C/D)
        ▼
Sales-card generator
        ▼
SQLite + FastAPI + Streamlit + exports (CSV, XLSX, Airtable, CRM)
```

## Stack

| Couche | Choix |
|---|---|
| Scraping API | `httpx` async + `tenacity` retry + per-host throttle |
| Deep crawl | BFS prioritisé, depth-limited, robots-aware, even bytes-cap |
| PDF | PyMuPDF (`fitz`) — texte des brochures publiques |
| Intelligence | Règles déterministes (regex + dictionnaires) → optionnellement raffinées par Claude (Haiku 4.5) avec **prompt caching** sur la taxonomie (>4 KB pour clearer le minimum cache de Haiku) |
| Validation | Pydantic + `messages.parse()` → JSON garanti valide |
| Stockage | SQLite + SQLAlchemy 2.0 (Postgres-ready) |
| API | FastAPI |
| UI | Streamlit (2 onglets : Catalogue rapide + Intelligence premium) |
| Exports | CSV, XLSX, Airtable, CRM |

## Installation

```bash
cd /Users/bertantoine/eurosatory-scraper
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.cli init
```

Pour activer la couche LLM (optionnelle) :

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
```

Sans clé, le pipeline tourne en mode **rule-based déterministe** — toutes les fiches sont produites, juste avec moins de finesse sur les champs free-text (pitch, sales angle, summary).

## Pipeline complet

```bash
# 1. Catalogue Eurosatory  (~ 5 s)
python -m app.cli scrape

# 2. Enrichissement Finderr (adresses, descriptions, LinkedIn)  (~ 10 min)
python -m app.cli enrich

# 3. Intelligence commerciale premium  (~ 30 min pour 200 exposants prioritaires)
#    Sans clé : extraction règles seules.
#    Avec clé : Claude raffine, sales card LLM-grade.
python -m app.cli intelligence --limit 200

# 4. Voir une fiche commerciale en CLI
python -m app.cli sales-card 1

# 5. UI commerciale
python -m app.cli ui  →  http://localhost:8501

# 6. Exports
python -m app.cli export --fmt intelligence-xlsx       # XLSX premium avec onglets A+/A/B
python -m app.cli export --fmt airtable                # CSV import Airtable
python -m app.cli export --fmt crm                     # CSV pour HubSpot/Pipedrive/Salesforce
python -m app.cli export --fmt intelligence-xlsx --priority "A+,A" --defense-category "Counter-UAV,UAV / drones"
```

## Ce qui est dans la base pour chaque exposant

### Identité & catalogue (rempli pour 100% des 2337)
- `company_name`, `country_iso2`, `country_name`, `city`, `address1`
- `website_url`, `eurosatory_profile_url`
- `linkedin_url` (40,2 %), `contact_email` (5,6 %), `phone` (6,5 %)
- `short_presentation`, `presentation`
- `stands` (Hall + nom), `pavilion`
- `is_featured`, `is_new_exhibitor`, `is_lab`

### Couche intelligence premium (`ExhibitorIntelligence`)
- `activity_summary` — synthèse 2-3 phrases
- `built_products` (liste, depuis taxonomie de 28 catégories)
- `built_product_summary`
- `sold_offerings` (liste, depuis 12 catégories)
- `services` (sous-ensemble de sold_offerings)
- `technologies` (16 catégories)
- `target_clients` (11 types : Land/Air/Navy/Police/Border/MOD…)
- `markets_served` (régions explicites)
- `programs_use_cases`
- `business_model` (OEM, Integrator, Distributor, Sub-contractor, SaaS, ...)
- `company_type` (Manufacturer, Software, Service, ...)
- `company_size_estimate`
- `probable_buying_needs` (dérivé via mapping BUYING_NEEDS_BY_BUILT + universaux)
- `buying_need_confidence` (high/medium/low) + `buying_need_reasoning`
- `supplier_needs`, `partnership_opportunities`
- `defense_categories` (27 labels EN, multi-label)
- `commercial_target_type` (potential_buyer / industrial_partner / distributor / integrator / competitor / potential_supplier / prime_contractor / subcontractor / technology_integrator / to_qualify)
- `commercial_interest_level` (very_high / high / medium / low)
- `interest_reason`
- `recommended_sales_angle`, `recommended_pitch`, `probable_objections`, `prospecting_keywords`
- `summary_for_sales` (5 lignes : Activity, Key offering, Buying angle, Why a target, Approach)
- `defense_commercial_score` (0-100)
- `defense_score_breakdown` (7 composantes, transparent)
- `defense_priority_level` (A+ / A / B / C / D)
- `defense_maturity_score`
- `field_sources` (URL source par champ)
- `field_confidence` (high/medium/low par champ)
- `fields_to_verify` (liste des champs sans evidence claire)
- `extraction_method` (rules / llm / mixed)
- `last_crawled_at`, `last_analyzed_at`

### Audit
- `CrawledPage` : log de chaque URL fetchée par exposant (kind, status, bytes, error)
- `data/raw/` : snapshots JSON bruts du catalogue Finderr

## Visite intelligente des sites

Le `deep_crawler` :

- **Reste sur le domaine** de l'exposant (pas de cross-host)
- Respecte `robots.txt`
- BFS avec **deux files** : pages prioritaires d'abord (regex sur les chemins ci-dessous), pages génériques ensuite
- Cap dur : `max_pages=10` HTML + `max_pdfs=2` + `max_depth=2`
- Per-host throttle 2 s, concurrence 4
- Skip gros bundles (`max_page_bytes=2 MB`), images, CSS, JS

**Chemins prioritaires** :
`/products`, `/solutions`, `/services`, `/capabilities`, `/offerings`, `/systems`, `/technologies`, `/industries`, `/markets`, `/sectors`, `/defense`, `/military`, `/homeland`, `/aerospace`, `/naval`, `/about`, `/company`, `/news`, `/press`, `/downloads`, `/brochures`, `/datasheets`.

## Scoring défense (0-100)

| Composante | Max | Critères |
|---|---:|---|
| Defense fit | 20 | nb labels défense core (Cyber, ISR, UAV, C4ISR, …) + dual-use + homeland |
| Offering clarity | 15 | built_products présents + sold_offerings présents + technologies |
| Supplier-buying potential | 20 | nombre de probable_buying_needs × confiance |
| Partnership potential | 15 | business_model intégrateur/OEM/distributeur + services + nombre de marchés |
| Size & maturity | 10 | employee_range + featured |
| International | 10 | nombre de marchés ≥ 2 + termes "international/export/worldwide" |
| Data completeness | 10 | site + email + phone + LinkedIn + adresse + activity_summary + built + tech |

**Niveaux** : A+ ≥ 90, A ≥ 75, B ≥ 60, C ≥ 40, D < 40.

## Couche LLM optionnelle (Claude Haiku 4.5)

Quand `ANTHROPIC_API_KEY` est défini :

- Modèle : `claude-haiku-4-5` (configurable)
- Sortie : Pydantic schéma garanti via `client.messages.parse()` → zéro JSON parsing
- **Prompt caching** sur la taxonomie + few-shot (>4 K tokens, au-dessus du minimum Haiku)
- Coût estimé : ~$3 pour les 2 337 exposants (premier call écrit le cache, les suivants lisent à 0,1×)
- Retries automatiques sur 429 / 5xx
- Fallback gracieux : si l'appel échoue ou si la clé est absente, le pipeline garde la sortie rule-based intacte

## Interface commerciale

```bash
python -m app.cli ui    →  http://localhost:8501
```

Deux onglets :

### 📇 Catalogue (rapide)
Tableau des 2 337 exposants avec recherche, filtres pays / priorité / catégorie / statut, fiche détaillée, tags, statut commercial (new / qualified / to_contact / contacted / not_relevant), notes, export CSV/XLSX.

### 🛰️ Intelligence commerciale (premium)
Tableau des exposants analysés en profondeur, filtres :
- pays
- **priorité défense (A+/A/B/C/D)**
- **niveau d'intérêt (very_high / high / medium / low)**
- **catégorie défense (27 labels EN)**
- **type de cible commerciale**
- score défense minimum
- "avec besoins d'achat probables"

Fiche détaillée : résumé 5 lignes, ce qu'ils fabriquent / vendent / achètent, pitch conseillé, objections, mots-clés prospection, sources crawlées avec status, breakdown du score, fiche commerciale verbatim.

Boutons d'export : CSV intelligence, XLSX (avec onglets A+ / A / B), Airtable, CRM.

## Rapport qualité

```bash
python -m app.cli report
```

Sort un JSON avec :

- `total_exhibitors`, `completeness_pct` (website / email / phone / LinkedIn / address)
- `priority_distribution`, `top_countries`, `taxonomy_distribution` (FR)
- `duplicate_candidates` (clusters par site, nom+pays, fuzzy)
- **`intelligence`** :
  - `analyzed`, `coverage_pct`
  - `with_built_products_pct`, `with_sold_offerings_pct`, `with_buying_needs_pct`, `with_recommended_pitch_pct`
  - `a_or_aplus_pct`
  - `defense_priority_distribution`, `interest_level_distribution`, `extraction_method_distribution`
  - `defense_taxonomy_distribution` (EN, 27 labels)
  - `field_confidence_distribution`
  - `crawl` : pages fetched, PDFs fetched, fetch errors

## Légalité & éthique

- Données strictement publiques (catalogue Eurosatory exposé via iframe public, sites corporate publics)
- Aucun contournement : la clé `X-API-KEY` Finderr utilisée est celle exposée dans le bundle JavaScript public, identique à celle qu'utilise tout visiteur du site officiel
- User-Agent transparent (contact mail inclus)
- Throttling par hôte + concurrence limitée + retry exponentiel
- Respect `robots.txt` lors du deep crawl
- **Aucune donnée personnelle** : pas de profils LinkedIn d'individus, pas de carnets d'adresses
- Emails génériques privilégiés (`contact@`, `sales@`, `info@`, `export@`)
- Snapshots bruts conservés (`data/raw/`) pour audit

## Structure du projet

```
app/
  config.py
  cli.py                                # Typer dispatcher
  database/
    models.py                           # SQLAlchemy 2.0 — Exhibitor, ExhibitorIntelligence, CrawledPage, ...
    db.py
  scrapers/
    http_client.py                      # async httpx + tenacity + throttle
    eurosatory_scraper.py               # Finderr public API
    deep_crawler.py                     # priority-path BFS, robots-aware
    pdf_extractor.py                    # PyMuPDF
    website_enricher.py                 # contact / about (legacy email enrichment)
  processors/
    normalizer.py
    deduplicator.py
    classifier.py                       # taxonomie FR (catalogue rapide)
    scoring.py                          # score basique 0-100
    defense_taxonomy.py                 # 27 labels EN multi-label
    intelligence.py                     # rule-based built/sold/buying extractor
    defense_scorer.py                   # 7 composantes, A+/A/B/C/D
    sales_card.py                       # 5-line summary, target type, pitch
    llm_intelligence.py                 # Claude Haiku 4.5 refinement (prompt-cached)
  pipelines/
    scrape.py
    enrich_finderr.py
    enrich_websites.py
    classify.py
    intelligence.py                     # orchestre crawl + extract + LLM + score + persist
    report.py
  api/
    main.py                             # FastAPI
  ui/
    streamlit_app.py                    # 2 onglets
  exports/
    exporters.py                        # CSV / XLSX / Airtable / CRM
data/
  eurosatory.db                         # SQLite
  raw/                                  # snapshots Finderr bruts (audit)
  exports/
  logs/
tests/
```

## Tests

```bash
pytest    # 25 tests, dont taxonomie défense, scorer, sales card, intelligence extractor
```

## Roadmap

- LinkedIn premium : passer par un fournisseur officiel (Phantombuster, Apollo, Cognism) plutôt qu'un scrape direct
- Crawler Playwright pour les rares sites SPA défense
- Stream l'intelligence layer en SSE pour suivre l'avancement dans l'UI
- Job scheduler (cron) pour `last_checked_at` hebdomadaire
- Détection automatique de filiales (matching nom + cluster de domaines)
