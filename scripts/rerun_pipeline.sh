#!/bin/bash
# Re-run the full pipeline after LLM enrichment + rebuild deploy artifacts.
# To run: bash scripts/rerun_pipeline.sh
set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate

echo "═════════════════════════════════════════════════════════════"
echo "1/5  merge_manual_overrides"
echo "═════════════════════════════════════════════════════════════"
python -m scripts.merge_manual_overrides

echo ""
echo "═════════════════════════════════════════════════════════════"
echo "2/5  normalize_categories (apply LLM cat overrides + tier)"
echo "═════════════════════════════════════════════════════════════"
python -m scripts.normalize_categories

echo ""
echo "═════════════════════════════════════════════════════════════"
echo "3/5  export_xlsx"
echo "═════════════════════════════════════════════════════════════"
python -m scripts.export_xlsx

echo ""
echo "═════════════════════════════════════════════════════════════"
echo "4/5  rebuild slim deploy DB"
echo "═════════════════════════════════════════════════════════════"
python -c "
import sqlite3, shutil, os
shutil.copy('data/eurosatory.db', 'data/eurosatory_deploy.db')
con = sqlite3.connect('data/eurosatory_deploy.db')
for t in ('crawled_pages', 'scraping_runs', 'activity_log',
          'exhibitor_enrichment_sources'):
    con.execute(f'DROP TABLE IF EXISTS {t}')
con.commit()
con.execute('VACUUM')
con.close()
sz = os.path.getsize('data/eurosatory_deploy.db') / 1024 / 1024
print(f'Slim deploy DB rebuilt: {sz:.1f} MB')
"

echo ""
echo "═════════════════════════════════════════════════════════════"
echo "5/5  audit final"
echo "═════════════════════════════════════════════════════════════"
python -c "
import json, re
recs = json.load(open('data/exports/targeting_profiles_final.json'))
GOOD_VERB = re.compile(r'^(Con[çc]oit|Fabrique|[ÉE]dite|Distribue|Int[èe]gre|Forme|Maintient|Conseille|Loue|Exploite|Op[èe]re|Repr[ée]sente|Fournit|D[ée]veloppe|R[ée]alise|Audite|Pilote|Conduit|Sous[- ]traite|Forge|Usine|Anime|Approvisionne|Organise|Mutualise|Coordonne|Exporte|Vend|Assure|Produit|G[èe]re|H[ée]berge|Imprime|Assemble|Installe|Certifie|Test[e]?|Calibre|Construit|Soude|Met|Extr[au]it|Recycle|Transforme|D[ée]ploie|Promeut|F[ée]d[èe]re|Effectue|Propose|Soutient|Accompagne|Pr[ée]pare|Programme|Surveille|Investit|Finance|Modernise|D[ée]mant[èe]le|Refurbit|Pr[ée]te|Encadre|Anticipe|R[ée]volutionne|Refurbit|Identifie|Restructure)', re.I | re.U)
n = len(recs)
n_ok = sum(1 for r in recs if GOOD_VERB.match((r.get('activity_1liner') or '').strip()))
n_dp = sum(1 for r in recs if 'données publiques trop pauvres' in (r.get('activity_1liner') or '').lower())
print(f'Total fiches : {n}')
print(f'Activity OK  : {n_ok} ({n_ok*100/n:.1f}%)')
print(f'Données pauvres : {n_dp}')
print(f'Reste à fix : {n - n_ok - n_dp}')
print()
from collections import Counter
tiers = Counter(r.get('supply_chain_tier', 'N/A') for r in recs)
print('Tier distribution:')
for t in ('OEM','MRO','Tier 1','Tier 2','Tier 3','Tier 4','N/A'):
    print(f'  {t:<10} {tiers.get(t, 0)}')
"

echo ""
echo "✅ Pipeline rerun complete. Files updated:"
echo "   - data/exports/targeting_profiles_final.json"
echo "   - data/exports/targeting_profiles_final.csv"
echo "   - data/exports/Eurosatory_2026_targeting.xlsx"
echo "   - data/eurosatory_deploy.db (slim, for cloud deploy)"
echo ""
echo "Next: git add . && git commit -m 'data refresh' && push"
