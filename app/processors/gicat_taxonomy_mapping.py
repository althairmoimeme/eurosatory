"""Map GICAT 3-level taxonomy (DOMAINES D'ACTIVITÉ) onto our 75 canonical
product categories + 5 supply-chain tiers.

GICAT publishes a structured French taxonomy of 12 top-level domains and
206 sub-domains. This file compiles regex rules — most are exact-match
sub-domain labels — to map them onto our buckets.
"""
from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern, list[str]]] = []


def _add(pat: str, cats: list[str]) -> None:
    _RULES.append((re.compile(pat, re.I | re.U), cats))


# === Plateformes terrestres ===
_add(r"v[eé]hicules?\s+blind[eé]s?|combat vehicle|chars?\s+(?:de combat)?",
     ["Véhicules blindés"])
_add(r"v[eé]hicules?\s+tactiques?|mobilit[eé]\s+et\s+a[eé]romobilit[eé]|"
     r"mobilit[eé]\s+terrestre",
     ["Véhicules tactiques (non blindés)"])
_add(r"camions?\s+militaires?|logistique op[eé]rationnelle.*?infrastructures?|"
     r"v[eé]hicules?\s+logistiques?",
     ["Camions militaires & logistique"])
_add(r"engins?\s+du\s+g[eé]nie|construction\s+et\s+infrastructures?|"
     r"engineering militaire",
     ["Engins du génie militaire"])
_add(r"ambulances?|sant[eé]\s+op[eé]rationnelle|m[eé]dical\s+militaire|"
     r"medevac",
     ["Ambulances & véhicules sanitaires"])
_add(r"robots?\s+terrestres?|ugv|eod|déminage",
     ["Robots terrestres (UGV / EOD)"])

# === Plateformes aériennes ===
_add(r"a[eé]ronef|h[eé]licopt[eè]res?|avions?\s+militaires?|voilure",
     ["Avions & hélicoptères militaires"])
_add(r"\bdrones?\b(?!.*anti)|\buav\b|\baeronef.*non\s+habit[eé]|drones?\s+a[eé]riens?",
     ["Drones aériens (UAV)"])
_add(r"munitions?\s+r[oô]deuses?|loitering munition|drones?\s+kamikaze",
     ["Drones FPV & munitions rôdeuses"])
_add(r"anti.{0,3}drones?|c.{0,2}uas|lutte anti drones?",
     ["Systèmes anti-drone (C-UAS)"])
_add(r"satellites?|spatial militaire|orbital",
     ["Satellites & CubeSats"])

# === Naval ===
_add(r"navires?|marine\s+(?:de|nationale|militaire)|sous.{0,3}marin",
     ["Navires & sous-systèmes navals"])
_add(r"sonars?|asm",
     ["Sonars & systèmes ASM"])

# === Armement ===
_add(r"armes\s+et\s+munitions|armes\s+(?:l[eé]g[eè]res|individuelles)|"
     r"fusils?|pistolets?|munitions?\s+l[eé]g[eè]res?|"
     r"armes\s+\(.*\)",
     ["Armes légères & accessoires"])
_add(r"armes\s+lourdes|puissance\s+de\s+feu|artillerie",
     ["Armes lourdes & systèmes de tir"])
_add(r"munitions?\s+(?:moyen|petit)\s+calibre|munitions?\s+petit\b",
     ["Munitions petit/moyen calibre"])
_add(r"munitions?\s+(?:gros|grand)\s+calibre|obus",
     ["Munitions gros calibre & obus"])
_add(r"missiles?\s+|lenkflugk|armements?\s+guid[eé]s?",
     ["Missiles & armements guidés"])
_add(r"explosifs?|énerg[eé]tiques?|propulseurs?\s+pyro",
     ["Explosifs & matériaux énergétiques"])
_add(r"tourelles?|rws|t[eé]l[eé]op[eé]r[eé]e",
     ["Tourelles téléopérées (RWS)"])
_add(r"non.{0,3}l[eé]tales?|moins\s+l[eé]tales?",
     ["Armes non-létales"])
_add(r"pyrotechnies?|signalisation pyro",
     ["Pyrotechnie & artifices"])

# === Capteurs / électronique ===
_add(r"optroniques?|viseurs?|lunettes?\s+de\s+vis[eé]e|jumelles?",
     ["Optronique & viseurs (EO/IR)"])
_add(r"\bradars?\b|détection radar",
     ["Radars & traitement signal"])
_add(r"capteurs?\s+embarqu[eé]s?|capteurs?\s+(?:multi|généraux)|"
     r"surveillance.\s*détection.\s*alerte",
     ["Capteurs embarqués (généraux)"])
_add(r"capteurs?\s+sismiques?|détection acoustique|sensors? acoustic",
     ["Capteurs sismiques & acoustiques"])
_add(r"infrarouges?\s+|imagerie\s+ir|d[eé]tecteurs?\s+ir",
     ["Détecteurs IR refroidis & non-refroidis"])
_add(r"guerre\s+[eé]lectronique|brouilleurs?\s+|electronic warfare|sigint",
     ["Systèmes de guerre électronique (EW)"])
_add(r"iff|identification\s+(?:ami|friend)",
     ["Systèmes IFF & identification"])
_add(r"vision\s+nocturne|nvg|intensificateur",
     ["Vision nocturne & jumelles"])
_add(r"surveillance.{0,3}acquisition.{0,3}désignation",
     ["Optronique & viseurs (EO/IR)"])

# === Communications / C4ISR ===
_add(r"radios?\s+tactiques?|software\s+defined\s+radio|sdr",
     ["Radios tactiques & SDR"])
_add(r"antennes?|hf|infrastructures?\s+rf",
     ["Antennes & infrastructures RF"])
_add(r"\b5g\b|p.{0,2}lte|manet|réseaux\s+(?:tactiques?|militaires?)",
     ["Réseaux militaires (5G/P-LTE/MANET)"])
_add(r"systèmes?\s+d['’]?information\s+(?:et|de)\s+communication|c4isr|"
     r"\bc2\b|\bc4i\b|systèmes?\s+de\s+commandement|mission management",
     ["Systèmes C2 & C4ISR"])
_add(r"streaming\s+vid[eé]o|video tactique|live broadcast",
     ["Streaming vidéo & data tactiques"])

# === Cyber ===
_add(r"cyber\s*s[eé]curit[eé]|cyber.{0,3}d[eé]fense|s[eé]curit[eé]\s+informatique|"
     r"infosec",
     ["Cybersécurité (logiciels & appliances)"])
_add(r"cyber\s+range|simulation\s+cyber",
     ["Logiciels de simulation & cyber range"])
_add(r"d[eé]veloppement\s+logiciel|logiciels?\s+(?:m[eé]tier|sp[eé]cialis[eé])|"
     r"digital et cyberespace|logiciels?\s+et\s+solutions",
     ["Logiciels métier défense (autres)"])
_add(r"intelligence\s+artificielle|machine\s+learning|"
     r"aide\s+(?:à|a)\s+la\s+prise\s+de\s+d[eé]cision|computer vision",
     ["Plateformes IA / vision défense"])

# === Soldat / protection ===
_add(r"casques?\s+de\s+combat|casques?\s+balistiques?",
     ["Casques de combat"])
_add(r"gilets?\s+pare.?balles?|plaques?\s+balistiques?|protection corporelle",
     ["Gilets & plaques pare-balles"])
_add(r"vitrages?\s+blind[eé]s?|verre\s+balistique",
     ["Vitrages blindés"])
_add(r"[eé]quipement(?:s)?\s+(?:du\s+fantassin|des forces|combattant|"
     r"individuel)|soldat\s+connect[eé]",
     ["Équipement du fantassin (général)"])
_add(r"\bnrbc\b|\bcbrn\b|d[eé]contamination|risque chimique",
     ["NRBC (masques, tenues, détecteurs)"])
_add(r"textiles?\s+techniques?|tarn|camouflage|tenue\s+(?:de combat|technique)",
     ["Textiles techniques tactiques"])
_add(r"protection\s+balistique",
     ["Gilets & plaques pare-balles"])

# === Énergie ===
_add(r"sources?\s+d['’]?[eé]nergie|énergie tactique|stockage électrique",
     ["Batteries & packs énergétiques"])
_add(r"batteries?|piles?\s+rechargeables?",
     ["Batteries & packs énergétiques"])
_add(r"piles?\s+(?:à|a)\s+combustible|hydrogène|h2",
     ["Piles à combustible & H2"])
_add(r"moteurs?\s+diesel|propulsion lourde",
     ["Moteurs diesel / propulsion lourde"])
_add(r"moteurs?\s+a[eé]ronautiques?|turbines?\s+aéro",
     ["Moteurs aéronautiques"])
_add(r"groupes?\s+électrog[eè]nes?|générateurs?\s+",
     ["Groupes électrogènes & énergie tactique"])

# === Simulation ===
_add(r"simulateurs?\s+|simulation\s+(?:d['’]?entrainement|"
     r"mission|tactique)|formation\s+des\s+forces|"
     r"vr\b|réalité\s+(?:virtuelle|augmentée)",
     ["Simulateurs d'entraînement & VR/AR"])

# === Sous-systèmes & matériaux ===
_add(r"composants?\s+[eé]lectroniques?|cartes?\s+[eé]lectroniques?|pcb",
     ["Composants électroniques & cartes"])
_add(r"v[eé]troniques?|électronique embarquée|systèmes?\s+embarqu[eé]s?",
     ["Composants électroniques & cartes"])
_add(r"connectique|c[aâ]blage|harnais|connecteurs?",
     ["Connectique & câblage durcis"])
_add(r"pi[eè]ces?\s+m[eé]caniques?|usinage|fraisage|découpage|"
     r"sous.{0,3}ensembles?",
     ["Pièces mécaniques & usinage"])
_add(r"transmission|boîte\s+de\s+vitesse|gearbox",
     ["Mécanique de transmission"])
_add(r"hydraulique|pneumatique",
     ["Hydraulique & motion control"])
_add(r"composites?|fibres?\s+(?:de\s+)?(?:carbone|verre)|cfk",
     ["Matériaux composites & blindage"])
_add(r"\bm[eé]taux?\b|aciers?|métallurgie|alliages?|titane|aluminium|"
     r"blindages?\s+|surblindage",
     ["Aciers & métallurgie spéciale"])
_add(r"céramiques?\s+|céramiques\s+balistiques?",
     ["Céramiques techniques & balistiques"])
_add(r"fixations?|visserie|boulonnerie",
     ["Fixations & visserie aéronautique"])
_add(r"alimentations?\s+(?:durcies?|électriques?)|convertisseurs?",
     ["Alimentations & convertisseurs durcis"])
_add(r"servomoteurs?|motion\s+control|précision\s+ant",
     ["Servomoteurs & motion control précision"])
_add(r"avioniques?|instruments?\s+embarqu[eé]s?",
     ["Avionique & instruments embarqués"])

# === Logistique & infra ===
_add(r"conteneurs?|shelters?|caisses?\s+de\s+transport|abris?\s+modulaires?",
     ["Conteneurs, malles, shelters"])
_add(r"logistique\s+militaire|transport\s+militaire",
     ["Logistique militaire & transport"])
_add(r"purification\s+(?:de\s+l['’]?)?eau|station\s+d['’]?eau",
     ["Stations de purification d'eau & infra"])
_add(r"sièges?\s+|cabines?\s+(?:de\s+pilotage|cockpit)",
     ["Sièges & ergonomie cabine"])
_add(r"lubrifiants?|fluides?\s+spéciaux|huiles?\s+industrielles?",
     ["Lubrifiants & fluides"])

# === Renseignement / Surveillance / OSINT ===
_add(r"renseignement|osint|intelligence\s+gathering|anticipation",
     ["Capteurs embarqués (généraux)"])

# === Sécurité civile / urgences ===
_add(r"incendie|lutte\s+contre\s+l['’]?incendie|sapeurs?.pompiers?",
     ["Engins du génie militaire"])
_add(r"sauvetage|search\s+and\s+rescue|secours",
     ["Engins du génie militaire"])

# === Industrial subcontracting / N/A by tier ===
_add(r"moyens?\s+de\s+fabrication|outillages?|robotique\s+industrielle",
     ["Pièces mécaniques & usinage"])

# === Institutionnel (services, no product) ===
# These map intentionally to N/A on tier ladder (institutional categories
# in PRODUCT_CATEGORIES live in the closed list).
_add(r"conseil\s+strat[eé]gique|consulting\s+d[eé]fense|advisory",
     ["Conseil stratégique & due-diligence M&A"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def map_text_to_categories(text: str, *, max_cats: int = 5) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for pat, cats in _RULES:
        if pat.search(text):
            for c in cats:
                if c not in found:
                    found.append(c)
                if len(found) >= max_cats:
                    return found
    return found


def coverage_report():
    import sqlite3
    import sys
    from collections import Counter
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(ROOT))
    db = ROOT / "data" / "eurosatory.db"
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute(
        "SELECT id, source_snippet, notes FROM attendance_signals "
        "WHERE source_platform='gicat-fr'"
    )
    n = 0
    n_with = 0
    cat_dist: Counter[str] = Counter()
    for sid, snippet, notes in cur.fetchall():
        text = snippet or ""
        for marker in ("[domaines]", "[secteurs]", "[produits]"):
            mm = re.search(rf"\[{marker[1:-1]}\]\s*(.+?)(?=\n\[|\Z)",
                           notes or "", re.DOTALL)
            if mm:
                text += " " + mm.group(1)
        cats = map_text_to_categories(text)
        n += 1
        if cats:
            n_with += 1
            for c in cats:
                cat_dist[c] += 1
    con.close()
    print(f"\nGICAT fiches: {n}, with cat: {n_with} ({n_with*100/max(1,n):.1f}%)")
    print("Top 20 categories:")
    for c, k in cat_dist.most_common(20):
        print(f"  {k:>4}  {c}")


if __name__ == "__main__":
    coverage_report()
