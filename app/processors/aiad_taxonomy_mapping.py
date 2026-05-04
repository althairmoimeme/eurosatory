"""Map AIAD member IT/EN free-text descriptions onto our 75 canonical
product categories + 5 supply-chain tiers.

AIAD member pages publish free-form English descriptions under
"Products and activities". We compile ~80 EN+IT regex patterns.
"""
from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern, list[str]]] = []


def _add(pat: str, cats: list[str]) -> None:
    _RULES.append((re.compile(pat, re.I | re.U), cats))


# === Plateformes terrestres ===
_add(r"\barmou?red vehicles?\b|\bcombat vehicle|\bblindato|\bcarro armato|"
     r"\bmain battle tank|\bmbt\b|\bapc\b",
     ["Véhicules blindés"])
_add(r"\btactical vehicle|\bunarmou?red vehicle|\bpatrol vehicle|"
     r"\bveicolo tattico",
     ["Véhicules tactiques (non blindés)"])
_add(r"\btruck\b|\bautocarro|\blogistic vehicle",
     ["Camions militaires & logistique"])
_add(r"\bmilitary engineering|\bgenio militare|\bbridging system",
     ["Engins du génie militaire"])
_add(r"\bambulance|\bambulanza|\bmedical vehicle",
     ["Ambulances & véhicules sanitaires"])
_add(r"\bugv\b|\bunmanned ground vehicle|\beod\b|\brobot terrestre",
     ["Robots terrestres (UGV / EOD)"])

# === Plateformes aériennes ===
_add(r"\bhelicopters?\b|\belicottero|\brotorcraft|\bgunship",
     ["Avions & hélicoptères militaires"])
_add(r"\baircraft\b|\baeromobile|\bvelivolo|\bfighter aircraft|"
     r"\btransport aircraft",
     ["Avions & hélicoptères militaires"])
_add(r"\buav\b|\bunmanned aerial|\bdroni\b|\bdrone\b|\baeromobile a pilotaggio remoto",
     ["Drones aériens (UAV)"])
_add(r"\bloitering munition|\bmunizione vagante|\bkamikaze drone",
     ["Drones FPV & munitions rôdeuses"])
_add(r"\bcounter[- ]drone|\banti[- ]drone|\bc-?uas\b|\banti drone",
     ["Systèmes anti-drone (C-UAS)"])
_add(r"\bsatellites?\b|\bsatellite\b|\bspazio\b",
     ["Satellites & CubeSats"])

# === Naval ===
_add(r"\bnaval ship|\bsubmarine|\bnavy vessel|\bnave militare|\bsommergibile|"
     r"\bship building|\bcantieri navali",
     ["Navires & sous-systèmes navals"])
_add(r"\bsonar|\basw\b|\banti[- ]submarine",
     ["Sonars & systèmes ASM"])

# === Armement ===
_add(r"\bsmall arms|\barmi leggere|\brifle\b|\bpistol\b|\bmachine gun",
     ["Armes légères & accessoires"])
_add(r"\bartillery|\bartiglieria|\bhowitzer|\bmortar\b|\bmortaio",
     ["Armes lourdes & systèmes de tir"])
_add(r"\bsmall calibre ammunition|\bmunizione|\bmunitions?\s+cal",
     ["Munitions petit/moyen calibre"])
_add(r"\blarge calibre ammunition|\bshell\b|\b155\s?mm\b",
     ["Munitions gros calibre & obus"])
_add(r"\bmissiles?\b|\bmissili|\bguided weapons?\b|\barmamenti guidati",
     ["Missiles & armements guidés"])
_add(r"\bexplosives?\b|\besplosivi|\benergetic materials?\b",
     ["Explosifs & matériaux énergétiques"])
_add(r"\brws\b|\bremote weapon station|\bturret\b|\btorretta",
     ["Tourelles téléopérées (RWS)"])
_add(r"\bnon[- ]lethal|\bnon letale|\bcrowd control",
     ["Armes non-létales"])

# === Capteurs / électronique ===
_add(r"\boptronics?|\bsight\b|\boptronica|\bvision system|\bvisore",
     ["Optronique & viseurs (EO/IR)"])
_add(r"\bradar|\bsignal processing",
     ["Radars & traitement signal"])
_add(r"\bsensors?\b|\bsensore\b|\bsurveillance system",
     ["Capteurs embarqués (généraux)"])
_add(r"\binfrared\b|\binfrarosso|\bthermal imaging",
     ["Détecteurs IR refroidis & non-refroidis"])
_add(r"\belectronic warfare|\bguerra elettronica|\bjamming|\bsigint",
     ["Systèmes de guerre électronique (EW)"])
_add(r"\biff\b|\bidentification friend",
     ["Systèmes IFF & identification"])
_add(r"\bnight vision|\bvisione notturna|\bbinocular",
     ["Vision nocturne & jumelles"])

# === Communications / C4ISR ===
_add(r"\btactical radio|\bsdr\b|\bradio tattica",
     ["Radios tactiques & SDR"])
_add(r"\bantennas?\b|\bantenna|\brf infrastructure",
     ["Antennes & infrastructures RF"])
_add(r"\b5g\b|\btactical network|\brete militare",
     ["Réseaux militaires (5G/P-LTE/MANET)"])
_add(r"\bc4isr\b|\bc2\b|\bcommand and control|\bcomando e controllo|"
     r"\bcombat management",
     ["Systèmes C2 & C4ISR"])

# === Cyber & IA ===
_add(r"\bcyber\s*security|\bcybersecurity|\bsicurezza informatica",
     ["Cybersécurité (logiciels & appliances)"])
_add(r"\bcyber\s*range|\bsimulazione cyber",
     ["Logiciels de simulation & cyber range"])
_add(r"\bsoftware development|\bdefense software|\bsoftware difesa",
     ["Logiciels métier défense (autres)"])
_add(r"\bartificial intelligence|\bintelligenza artificiale|\bia\b|\bai\b|\bmachine learning",
     ["Plateformes IA / vision défense"])

# === Soldat / protection ===
_add(r"\bcombat helmet|\belmetto",
     ["Casques de combat"])
_add(r"\bbody armou?r|\bballistic vest|\bvest|\bcorpetto antiproiettile",
     ["Gilets & plaques pare-balles"])
_add(r"\barmou?red glass|\bvetro balistico",
     ["Vitrages blindés"])
_add(r"\bsoldier system|\bequipaggiamento del fante|\bindividual equipment",
     ["Équipement du fantassin (général)"])
_add(r"\bcbrn\b|\bnbc\b|\bnrbc\b|\bdecontamination",
     ["NRBC (masques, tenues, détecteurs)"])
_add(r"\btextile\b|\btactical clothing|\btessuto tecnico",
     ["Textiles techniques tactiques"])

# === Énergie ===
_add(r"\bbatter(?:y|ies)\b|\bbatteria|\benergy storage",
     ["Batteries & packs énergétiques"])
_add(r"\bfuel cell|\bidrogeno",
     ["Piles à combustible & H2"])
_add(r"\bdiesel engine|\bmotore diesel",
     ["Moteurs diesel / propulsion lourde"])
_add(r"\baircraft engine|\bmotore aeronautico|\bturbine|\baero engine|"
     r"\baircraft propulsion",
     ["Moteurs aéronautiques"])
_add(r"\bgenerator|\bpower generator|\bgruppo elettrogeno",
     ["Groupes électrogènes & énergie tactique"])

# === Simulation ===
_add(r"\bsimulator|\bsimulazione|\btraining system|\bvr\b|\bvirtual reality|"
     r"\baugmented reality",
     ["Simulateurs d'entraînement & VR/AR"])

# === Composants & matériaux ===
_add(r"\belectronic components|\bcircuit boards?|\bpcb\b",
     ["Composants électroniques & cartes"])
_add(r"\bcabling\b|\bharness|\bconnector\b|\bcavi\b",
     ["Connectique & câblage durcis"])
_add(r"\bmechanical (?:components|parts)|\bmachining|\bcnc\b|"
     r"\blavorazioni meccaniche",
     ["Pièces mécaniques & usinage"])
_add(r"\btransmission\b|\bgearbox",
     ["Mécanique de transmission"])
_add(r"\bhydraulics?\b|\bpneumatics?\b|\bidraulica",
     ["Hydraulique & motion control"])
_add(r"\bcomposites?\b|\bcompositi|\bcarbon fib(?:er|re)\b",
     ["Matériaux composites & blindage"])
_add(r"\bsteel\b|\bacciaio|\balloy|\btitanium|\bmetallurg",
     ["Aciers & métallurgie spéciale"])
_add(r"\bceramics?\b|\bceramica balistica",
     ["Céramiques techniques & balistiques"])

# === Logistique & infra ===
_add(r"\bcontainer\b|\bshelter\b|\bcontenitore",
     ["Conteneurs, malles, shelters"])
_add(r"\bmilitary logistics?\b|\blogistica militare",
     ["Logistique militaire & transport"])
_add(r"\bavionics?\b",
     ["Avionique & instruments embarqués"])

# === Cyberspace + Surveillance ===
_add(r"\bsurveillance\b|\bsorveglianza",
     ["Optronique & viseurs (EO/IR)"])

# === Service-only ===
_add(r"\bdefense consultancy|\bconsulenza difesa",
     ["Conseil stratégique & due-diligence M&A"])
_add(r"\bmro\b|\bmaintenance|\boverhaul|\bmanutenzione",
     ["Logistique militaire & transport"])


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
        "SELECT id, source_snippet FROM attendance_signals "
        "WHERE source_platform='aiad-it'"
    )
    n = 0
    n_ok = 0
    cat_dist: Counter[str] = Counter()
    for sid, snippet in cur.fetchall():
        n += 1
        text = snippet or ""
        cats = map_text_to_categories(text)
        if cats:
            n_ok += 1
            for c in cats:
                cat_dist[c] += 1
    con.close()
    print(f"AIAD fiches: {n}, with cat: {n_ok} ({n_ok*100/max(1,n):.1f}%)")
    for c, k in cat_dist.most_common(20):
        print(f"  {k:>4}  {c}")


if __name__ == "__main__":
    coverage_report()
