"""Re-map fiches that ended up with products_categories=['Autre — à qualifier']
to the closest canonical PRODUCT_CATEGORIES entry, by scanning their
activity_1liner + products list for keywords.

Why this matters
----------------
The LLM re-enrichment pass used examples (not the full closed taxonomy) for
products_categories. When the LLM's free-form output didn't match the
canonical list exactly, ``filter_cats()`` rejected it and substituted
``"Autre — à qualifier"`` — which collapses the supply-chain tier to
``N/A``. This restores the ~345 affected fiches to a sensible tier.

Run :
    python -m scripts.remap_autre_categories
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT_PATH = ROOT / "data" / "exports" / "targeting_profiles_final.json"
CAT_PATH = ROOT / "data" / "llm_test" / "llm_categories_overrides.json"


# Keyword → canonical PRODUCT_CATEGORIES mapping. Order matters : the first
# match wins. Patterns are case-insensitive ; \b boundaries used.
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    # --- Plateformes terrestres ---
    (r"v[ée]hicule[s]? blind[ée]|blind[ée][s]? l[ée]gers?|MRAP|APC\b|VBCI|Griffon|Jaguar", ["Véhicules blindés"]),
    (r"v[ée]hicule[s]? tactique|véhicule 4x4|VLTT|Acmat|HMV|Bastion", ["Véhicules tactiques (non blindés)"]),
    (r"camion[s]? militaire|logistique route", ["Camions militaires & logistique"]),
    (r"engin[s]? du génie|déminage|d[ée]mineur|franchissement|pose de pont|génie militaire|EOD\b", ["Engins du génie militaire"]),
    (r"ambulance[s]? militaire|véhicule[s]? sanitaire", ["Ambulances & véhicules sanitaires"]),
    (r"\bUGV\b|robot[s]? terrestre[s]?", ["Robots terrestres (UGV / EOD)"]),
    # --- Plateformes aériennes & espace ---
    (r"avion[s]? militaire|h[ée]licopt[èe]re militaire|chasseur|appareil de combat|Rafale|Mirage", ["Avions & hélicoptères militaires"]),
    (r"munition[s]? r[ôo]deuse|munition rôdeuse|loitering|munition kamikaze|FPV", ["Drones FPV & munitions rôdeuses"]),
    (r"\bdrone[s]? a[ée]rien|UAV|micro[- ]drone|drone tactique|drone ISR", ["Drones aériens (UAV)"]),
    (r"anti[- ]drone|C[- ]?UAS|contre[- ]drone|brouillage drone", ["Systèmes anti-drone (C-UAS)"]),
    (r"satellite[s]?|cubesat|orbital|spatial[e]?", ["Satellites & CubeSats"]),
    # --- Naval ---
    (r"navir[es]?|sous[- ]marin|fr[ée]gate|patrouilleur|naval[e]?", ["Navires & sous-systèmes navals"]),
    (r"sonar|ASM\b|lutte anti[- ]sous[- ]marine|anti[- ]submarine", ["Sonars & systèmes ASM"]),
    # --- Armement ---
    (r"missile[s]?|armement guid[ée]", ["Missiles & armements guidés"]),
    (r"roquette[s]?|lance[- ]roquette", ["Roquettes & lance-roquettes"]),
    (r"munition.{0,15}(petit|moyen) calibre|cartouche", ["Munitions petit/moyen calibre"]),
    (r"obus|munition.{0,15}gros calibre|munition gros calibre", ["Munitions gros calibre & obus"]),
    (r"explosif[s]?|materi(au|el)x? énergétique[s]?|propergol|poudre noire", ["Explosifs & matériaux énergétiques"]),
    (r"tourelle[s]? téléopérée|RWS\b|remote weapon station", ["Tourelles téléopérées (RWS)"]),
    (r"arme[s]? non[- ]l[ée]tale|taser|stunlock|disabling weapon", ["Armes non-létales"]),
    (r"pyrotechnie|artifices?", ["Pyrotechnie & artifices"]),
    (r"arme[s]? lourde|systeme de tir|canon de\b", ["Armes lourdes & systèmes de tir"]),
    (r"arme[s]? l[ée]g[èe]re|fusil[s]?|pistolet[s]?|carabin|mitrailleuse|couteau[x]? tactique[s]?|baionnette", ["Armes légères & accessoires"]),
    # --- Capteurs & électronique défense ---
    (r"optroniqu|viseur[s]?|EO[/ ]IR|electro[- ]optic", ["Optronique & viseurs (EO/IR)"]),
    (r"\bradar[s]?|AESA|monopulse", ["Radars & traitement signal"]),
    (r"d[ée]tecteur[s]? IR|détecteur infrarouge|cooled detector|uncooled detector", ["Détecteurs IR refroidis & non-refroidis"]),
    (r"capteur[s]? sismique|capteur[s]? acoustique|geophone|hydrophone", ["Capteurs sismiques & acoustiques"]),
    (r"capteur[s]? embarqu", ["Capteurs embarqués (généraux)"]),
    (r"guerre [ée]lectronique|EW\b|electronic warfare|brouillage", ["Systèmes de guerre électronique (EW)"]),
    (r"\bIFF\b|identification ami|combat identification|identification friend or foe", ["Systèmes IFF & identification"]),
    # --- Communications & C4ISR ---
    (r"radio[s]? tactique|SDR\b|software defined radio|liaison tactique|MANET", ["Radios tactiques & SDR"]),
    (r"antenne[s]? RF|infrastructure RF|antenne micro[- ]?onde", ["Antennes & infrastructures RF"]),
    (r"5G\b|réseau radio mobile|P[- ]LTE\b|réseau militaire", ["Réseaux militaires (5G/P-LTE/MANET)"]),
    (r"\bC4ISR\b|système C2|command and control|système commandement", ["Systèmes C2 & C4ISR"]),
    (r"streaming vid[ée]o|data tactique|liaison de données|Link 16|Link 22", ["Streaming vidéo & data tactiques"]),
    # --- Cyber & logiciels ---
    (r"intelligence artificielle|machine learning|\bIA\b vision|computer vision défense", ["Plateformes IA / vision défense"]),
    (r"cybers[ée]curit[ée]|firewall|SIEM|EDR\b|XDR\b|SOC\b", ["Cybersécurité (logiciels & appliances)"]),
    (r"simulation tactique|cyber range|simulation défense|wargame|simulation entraînement", ["Logiciels de simulation & cyber range"]),
    (r"plateforme logiciel|logiciel m[ée]tier|test.{0,15}mesure|LabVIEW|software platform", ["Logiciels métier défense (autres)"]),
    # --- Soldat connecté & protection ---
    (r"casque[s]? de combat|casque tactique|casque balistique|Ops[- ]Core", ["Casques de combat"]),
    (r"gilet[s]? pare[- ]balles?|plaque[s]? balistique|plate carrier|porte[- ]plaque", ["Gilets & plaques pare-balles"]),
    (r"vitrage[s]? blind[ée]", ["Vitrages blindés"]),
    (r"vision nocturne|jumelle[s]? infrarouge|\bNVG\b|night vision", ["Vision nocturne & jumelles"]),
    (r"viseur[s]? d[\'’]?arme|optique[s]? d[\'’]?arme|red dot|lunette de tir", ["Optiques d'armes (viseurs)"]),
    (r"NRBC|CBRN|RCBN|chimique[\s-]?biologique|d[ée]contamination|détection toxique", ["NRBC (masques, tenues, détecteurs)"]),
    (r"textile[s]? technique|textile[s]? tactique|ballistique tissue|tissus balistique", ["Textiles techniques tactiques"]),
    (r"[ée]quipement[s]? du fantassin|[ée]quipement[s]? individuel|combat equipment|tenue de combat|sac à dos militaire", ["Équipement du fantassin (général)"]),
    # --- Énergie & propulsion ---
    (r"batterie[s]? li[- ]?ion|pack[s]? [ée]nerg[ée]tique|cellule lithium|stockage [ée]nergie", ["Batteries & packs énergétiques"]),
    (r"pile[s]? à combustible|hydrog[èe]ne|fuel cell", ["Piles à combustible & H2"]),
    (r"moteur[s]? diesel|propulsion lourde|gen[ée]ration thermique", ["Moteurs diesel / propulsion lourde"]),
    (r"moteur[s]? a[ée]ronautique|turbine[s]? a[ée]ro|turbomoteur", ["Moteurs aéronautiques"]),
    (r"groupe[s]? [ée]lectrog[èe]ne|[ée]nergie tactique|génératrice", ["Groupes électrogènes & énergie tactique"]),
    # --- Simulation & formation ---
    (r"simulateur[s]? d[\'’]?entra[îi]nement|simulateur[s]? de tir|VR\s?[/ ]\s?AR|réalité augmentée|réalité virtuelle", ["Simulateurs d'entraînement & VR/AR"]),
    (r"cam[ée]ra[s]? tactique|cam[ée]ra POV|action cam tactical|GoPro tactical", ["Caméras tactiques & POV"]),
    # --- Sous-systèmes ---
    (r"avionique|instrument[s]? embarqu[ée]s?|cockpit display", ["Avionique & instruments embarqués"]),
    (r"circuit[s]? imprim[ée]|carte[s]? [ée]lectronique|PCB\b|composant[s]? [ée]lectronique", ["Composants électroniques & cartes"]),
    (r"connectique durcie|c[âa]blage durcie|harness|connecteur[s]? mil[- ]spec", ["Connectique & câblage durcis"]),
    (r"alimentation[s]? durcie|convertisseur[s]? durcie|DC[- ]DC militaire", ["Alimentations & convertisseurs durcis"]),
    (r"bo[îi]tier[s]? durcie|ch[âa]ssis durcie|enclosure militaire", ["Boîtiers & châssis durcis"]),
    (r"servomoteur[s]?|motion control précision|actionneur précision", ["Servomoteurs & motion control précision"]),
    (r"transmission[s]? m[ée]canique|bo[îi]te de vitesses|engrenage[s]? lourd", ["Mécanique de transmission"]),
    (r"hydraulique|motion control hydrauliqu|servo[- ]hydraulique", ["Hydraulique & motion control"]),
    (r"usinage|pi[èe]ce[s]? m[ée]canique|tournage[- ]fraisage|forge|forg[ée]|usine|m[ée]canosoudage", ["Pièces mécaniques & usinage"]),
    (r"composite[s]?|fibre carbone|matériaux composites|polym[èe]re renforc[ée]|laminé|prepreg|moulage composite", ["Matériaux composites & blindage"]),
    (r"acier[s]? sp[ée]cial|m[ée]tallurgie|acier blind[ée]|special steel|UHS\b|steel mill", ["Aciers & métallurgie spéciale"]),
    (r"c[ée]ramique[s]? technique|céramique balistique|alumine|carbure de silicium|SiC", ["Céramiques techniques & balistiques"]),
    (r"fixation[s]? a[ée]ronautique|visserie a[ée]ronautique|riveterie", ["Fixations & visserie aéronautique"]),
    # --- Logistique & infrastructure ---
    (r"conteneur[s]?|malle[s]?|shelter[s]?|abri d[ée]ployable", ["Conteneurs, malles, shelters"]),
    (r"logistique militaire|kitting|sourcing supply chain", ["Logistique militaire & transport"]),
    (r"m[âa]t[s]?|signalisation|balisage", ["Mâts, signalisation & balisage"]),
    (r"purification d[\'’]?eau|station d[\'’]?eau|traitement de l[\'’]?eau|water purification", ["Stations de purification d'eau & infra"]),
    (r"si[èe]ge[s]? cabine|ergonomie cabine|si[èe]ge pilote", ["Sièges & ergonomie cabine"]),
    (r"lubrifiant[s]?|fluide[s]? techniques?|huile moteur militaire", ["Lubrifiants & fluides"]),
    # --- Institutionnel ---
    (r"cluster\b|f[ée]d[ée]ration\b|chambre de commerce|business association|syndicat professionnel|trade association", ["Représentation institutionnelle (cluster, fédération, chambre)"]),
    (r"ministère de la défense|achat public défense|politique industrielle|DGA\b|MoD procurement", ["Achat public défense & politique industrielle"]),
    (r"m[ée]dia[s]? d[ée]fense|publication d[ée]fense|magazine d[ée]fense|press[e]? défense", ["Médias & publications défense"]),
    (r"organisateur de salon|salon professionnel|congr[èe]s défense|trade show organizer", ["Organisation de salons & conférences défense"]),
    (r"banque|financement|capital risque|fonds d[\'’]?investissement|assurance|garanties export", ["Financement, banque & assurance défense"]),
    (r"recherche académique|laboratoire universitaire|institut de recherche|R&D académique|university research", ["R&D académique & laboratoires"]),
    (r"conseil stratégique|due[- ]diligence|M&A\b|conseil M&A", ["Conseil stratégique & due-diligence M&A"]),
    (r"transit export|logistique export défense|fret international", ["Logistique export défense & transit"]),
    (r"distributeur|distribution composants|repr[ée]sentation de marque|reseller", ["Distribution composants & représentation de marques"]),
]

COMPILED = [(re.compile(pattern, re.I | re.U), cats) for pattern, cats in KEYWORD_RULES]


def map_text_to_cat(text: str) -> list[str]:
    """Return up to 3 canonical categories matching the text."""
    found: list[str] = []
    for rx, cats in COMPILED:
        if rx.search(text):
            for c in cats:
                if c not in found:
                    found.append(c)
        if len(found) >= 3:
            break
    return found[:3]


def main() -> int:
    recs = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    cat_ov = json.loads(CAT_PATH.read_text(encoding="utf-8")) if CAT_PATH.exists() else {}

    n_remapped = 0
    n_examined = 0
    for r in recs:
        eid = str(r["exhibitor_id"])
        cats = r.get("products_categories") or []
        # Only fix fiches whose ONLY product cat is "Autre"
        if cats != ["Autre — à qualifier"]:
            continue
        n_examined += 1
        # Build the search corpus from activity + products + services
        text_parts = [r.get("activity_1liner") or ""]
        text_parts += list(r.get("products") or [])
        text_parts += list(r.get("services") or [])
        text_parts += list(r.get("technologies") or [])
        text = " · ".join(text_parts)
        new_cats = map_text_to_cat(text)
        if new_cats:
            existing = cat_ov.get(eid, {})
            existing.setdefault("services_categories", [])
            existing.setdefault("technologies_categories", [])
            existing["products_categories"] = new_cats
            cat_ov[eid] = existing
            n_remapped += 1

    CAT_PATH.write_text(
        json.dumps(cat_ov, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Examined  : {n_examined} fiches with products_categories=[Autre]")
    print(f"Re-mapped : {n_remapped} ({n_remapped*100/max(n_examined,1):.1f}%)")
    print(f"Saved     : {CAT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
