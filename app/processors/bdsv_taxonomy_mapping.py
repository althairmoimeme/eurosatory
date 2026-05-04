"""Map a BDSV member's German description + portfolio onto our 75
canonical product categories + 5 supply-chain tiers.

Unlike ADS Group UK which exposes a structured ``knowsAbout`` list,
BDSV publishes free-form German prose under ``Beschreibung`` and
``Portfolio``. This module compiles ~120 German regex patterns matching
the canonical product categories.

Usage
-----
    from app.processors.bdsv_taxonomy_mapping import map_text_to_categories
    cats = map_text_to_categories(beschreibung + " " + portfolio)
"""
from __future__ import annotations

import re

# Each entry : (regex_pattern, [canonical_categories])
# Patterns ARE case-insensitive and tested against the full text.

_RULES: list[tuple[re.Pattern, list[str]]] = []


def _add(pattern: str, categories: list[str]) -> None:
    _RULES.append((re.compile(pattern, re.I | re.U), categories))


# === Plateformes terrestres / OEM ===
_add(r"\bgepanzert(es?|en?|er)?\b|\bpanzer(fahrzeug|wagen)?\b|\bspz\b|\bschützenpanzer\b|\bkampfpanzer\b",
     ["Véhicules blindés"])
_add(r"\bgeschütztes? \w+(rad|kette)\b|\bradfahrzeuge?\b|\bkettenfahrzeuge?\b",
     ["Véhicules blindés"])
_add(r"\bmilitärische?n? fahrzeuge?\b|\btaktische fahrzeuge\b|\beinsatzfahrzeuge?\b",
     ["Véhicules tactiques (non blindés)"])
_add(r"\blkw\b|\blastkraftwagen\b|\bschwerlast\b|\bnutzfahrzeuge?\b",
     ["Camions militaires & logistique"])
_add(r"\bambulanz\b|\brettungsfahrzeuge?\b|\bsanitäts(fahrzeug|kraftwagen)?\b",
     ["Ambulances & véhicules sanitaires"])
_add(r"\bpionierfahrzeuge?\b|\bbergungs(panzer|fahrzeug)\b|\bbrücken(legepanzer|leger)?\b",
     ["Engins du génie militaire"])
_add(r"\bunbemannte?(s|n|r)? boden(fahrzeug|system)e?\b|\bugv\b|\bbodenroboter\b",
     ["Robots terrestres (UGV / EOD)"])

# === Plateformes aériennes ===
_add(r"\bhubschrauber\b|\bhelikopter\b|\brotor\w*\b",
     ["Avions & hélicoptères militaires"])
_add(r"\bflugzeuge?\b|\btransportflugzeug\b|\bjäger\b|\bkampfflugzeug\b|\beurofighter\b",
     ["Avions & hélicoptères militaires"])
_add(r"\bdrohnen?\b|\bunbemanntes? luft\w*\b|\buav\b|\bucav\b",
     ["Drones aériens (UAV)"])
_add(r"\bloitering munition\b|\bkamikaze[- ]?drohnen?\b|\bone[- ]?way[- ]?attack\b",
     ["Drones FPV & munitions rôdeuses"])
_add(r"\bgegen\s?drohnen?\b|\banti[- ]?drohne\b|\bc[- ]?uas\b|\bdrohnenabwehr\b",
     ["Systèmes anti-drone (C-UAS)"])
_add(r"\bsatellit\w*\b|\braumfahrt\b|\borbital\w*\b",
     ["Satellites & CubeSats"])

# === Naval ===
_add(r"\bschiffs?(bau|werft)?\b|\bwerft\b|\byacht\b|\bmarine\w*\b|\bfregatte\b|\bkorvette\b|\bu[- ]?boot\b",
     ["Navires & sous-systèmes navals"])
_add(r"\bsonar\b|\bunterwasser\w+\b|\basw\b",
     ["Sonars & systèmes ASM"])

# === Armement ===
_add(r"\bhandfeuerwaffen?\b|\bgewehr\b|\bpistolen?\b|\bsturmgewehr\b|\bscharfschützen\w+\b",
     ["Armes légères & accessoires"])
_add(r"\bgeschütze?\b|\bartillerie\w*\b|\bhaubitze\w*\b|\bkanon\w+\b|\bturm\b",
     ["Armes lourdes & systèmes de tir"])
_add(r"\bmunition\b|\bpatrone\w+\b|\bkleinkaliber\b|\bmittelkaliber\b",
     ["Munitions petit/moyen calibre"])
_add(r"\bgroßkaliber\b|\bartilleriemunition\b|\bgranat\w+\b",
     ["Munitions gros calibre & obus"])
_add(r"\blenkflugkörper\b|\brakete\w+\b|\bflugkörper\b|\bmissile?n?\b",
     ["Missiles & armements guidés"])
_add(r"\bsprengstoff\w*\b|\bzünder\b|\benerget\w+\b",
     ["Explosifs & matériaux énergétiques"])
_add(r"\bferngesteuerte? waffenstation\w*\b|\brws\b|\bwaffenstation\w*\b",
     ["Tourelles téléopérées (RWS)"])
_add(r"\bnicht[- ]?letale?\b|\bnichtletale?\b|\btaser\b|\bgummigeschoss\w+\b|\beinsatzgummi\w+\b",
     ["Armes non-létales"])
_add(r"\bpyrotech\w+\b|\bnebel\w+\b|\bsignalmunition\b",
     ["Pyrotechnie & artifices"])

# === Capteurs / électronique défense ===
_add(r"\boptronik\b|\bvisiere?\b|\bzielf(ern)?rohr\w*\b|\bkamera\b",
     ["Optronique & viseurs (EO/IR)"])
_add(r"\bradar(systeme|technik|sensor)?\b",
     ["Radars & traitement signal"])
_add(r"\bsensor(ik|en|systeme)?\b|\bdetektor\w*\b",
     ["Capteurs embarqués (généraux)"])
_add(r"\binfrarot\w*\b|\bwärmebildkamera\w*\b|\bir[- ]?detekt\w+\b",
     ["Détecteurs IR refroidis & non-refroidis"])
_add(r"\belektronische kampfführung\b|\bekf\b|\bjamm\w+\b|\belectronic warfare\b|\bsig[- ]?int\b",
     ["Systèmes de guerre électronique (EW)"])
_add(r"\biff\b|\bidentifizierung freund/?feind\b",
     ["Systèmes IFF & identification"])
_add(r"\bnachtsicht\w*\b|\bnvg\b|\bbildverstärker\w*\b",
     ["Vision nocturne & jumelles"])

# === Communications & C4ISR ===
_add(r"\btaktische?(s|n|r)? funk\b|\bsdr\b|\bsoftware[- ]?defined radio\b",
     ["Radios tactiques & SDR"])
_add(r"\bantenne\w*\b|\bhf[- ]?infrastruktur\w*\b",
     ["Antennes & infrastructures RF"])
_add(r"\b5g\b|\bp[- ]?lte\b|\bmesh[- ]?netz\w*\b|\btaktische[s]? netz\w*\b",
     ["Réseaux militaires (5G/P-LTE/MANET)"])
_add(r"\bführungssystem\w*\b|\bc2\b|\bc4isr\b|\bbattle management\b",
     ["Systèmes C2 & C4ISR"])
_add(r"\bvideo[- ]?streaming\b|\btaktische[s]? video\w*\b",
     ["Streaming vidéo & data tactiques"])

# === Cyber / logiciels ===
_add(r"\bcyber(sicherheit|security)\b|\bit[- ]?sicherheit\b|\binformations(sicherheit)?\b",
     ["Cybersécurité (logiciels & appliances)"])
_add(r"\bcyber[- ]?range\b|\bsim(ulation)?[- ]?übung\w*\b",
     ["Logiciels de simulation & cyber range"])
_add(r"\bsoftware\b|\banwendung\w*\b|\bsoftwareentwicklung\b|\bdigitalisierung\b",
     ["Logiciels métier défense (autres)"])
_add(r"\bkünstliche intelligenz\b|\bmachine learning\b|\bki\b|\bmaschinelles lernen\b|\bcomputer ?vision\b",
     ["Plateformes IA / vision défense"])

# === Soldat / protection ===
_add(r"\bhelm\w*\b|\bgefechtshelme?\b|\bkopfschutz\w*\b",
     ["Casques de combat"])
_add(r"\bschutzweste\w*\b|\bballistische platten\b|\bbody[- ]?armou?r\b",
     ["Gilets & plaques pare-balles"])
_add(r"\bbeschussglas\b|\bpanzerglas\b|\bballistisches glas\b",
     ["Vitrages blindés"])
_add(r"\bsoldatenausrüstung\b|\bpersönliche ausrüstung\b|\bidz\b|\binfanterist der zukunft\b",
     ["Équipement du fantassin (général)"])
_add(r"\babc[- ]?schutz\b|\babc[- ]?abwehr\b|\bcbrn\b|\bdekontamination\b",
     ["NRBC (masques, tenues, détecteurs)"])
_add(r"\btextil\w+\b|\btarn\w+\b|\bcamouflage\b|\bbekleidung\b",
     ["Textiles techniques tactiques"])

# === Énergie / propulsion ===
_add(r"\bbatterie\w*\b|\bakku\w*\b|\benergiespeicher\w*\b",
     ["Batteries & packs énergétiques"])
_add(r"\bbrennstoffzelle\w*\b|\bwasserstoff\b|\bh[- ]?2[- ]?antrieb\b",
     ["Piles à combustible & H2"])
_add(r"\bdiesel(motor)?\b|\bschwere antrieb\w*\b",
     ["Moteurs diesel / propulsion lourde"])
_add(r"\btriebwerk\w*\b|\bflugzeugmotor\w*\b|\bturbine\w*\b",
     ["Moteurs aéronautiques"])
_add(r"\bstromaggregat\w*\b|\bgenerator\w*\b|\bbordnetz\b|\bnotstrom\w*\b",
     ["Groupes électrogènes & énergie tactique"])

# === Simulation & formation ===
_add(r"\bsimulator\w*\b|\bsim(ulation)\w*\b|\btrainings(simulation|gerät)\w*\b|\bvr\b|\bar\b|\bvirtuelle realität\b|\baugmented reality\b",
     ["Simulateurs d'entraînement & VR/AR"])

# === Sous-systèmes & matériaux ===
_add(r"\belektronische? komponente\w*\b|\bplatine\w*\b|\bleiterplatte\w*\b|\bpcb\b",
     ["Composants électroniques & cartes"])
_add(r"\bsteckverbinder\w*\b|\bkabel\w*\b|\bkabelbaum\b|\bkonnektor\w*\b|\bverdrahtung\b",
     ["Connectique & câblage durcis"])
_add(r"\bmechanische bearbeitung\b|\bzerspanung\b|\bcnc\b|\bfräs\w+\b|\bdrehen\b|\bfertigung\w+\b",
     ["Pièces mécaniques & usinage"])
_add(r"\bgetriebe\b|\bantriebsstrang\w*\b|\btransmission\w*\b",
     ["Mécanique de transmission"])
_add(r"\bhydraulik\w*\b|\bpneumatik\w*\b",
     ["Hydraulique & motion control"])
_add(r"\bverbundwerkstoff\w*\b|\bcomposite\w*\b|\bfaserverstärkt\w+\b|\bcfk\b|\bgfk\b|\bkohlefaser\w*\b",
     ["Matériaux composites & blindage"])
_add(r"\bstahl\b|\bmetallurgie\b|\baluminium\w*\b|\btitan\w*\b|\bhochfest\w*\b|\bpanzerstahl\b",
     ["Aciers & métallurgie spéciale"])
_add(r"\bkeramik\w*\b|\bballistische keramik\b",
     ["Céramiques techniques & balistiques"])
_add(r"\bschrauben\b|\bbefestigung\w+\b|\bverbindungselement\w+\b",
     ["Fixations & visserie aéronautique"])
_add(r"\bstromversorgung\w*\b|\bumrichter\w*\b|\bwandler\w*\b",
     ["Alimentations & convertisseurs durcis"])
_add(r"\bgehäuse\b|\bchassis\b|\brackelektronik\b",
     ["Boîtiers & châssis durcis"])
_add(r"\bavionik\w*\b|\bbordinstrument\w*\b|\bfluggerätelektronik\b",
     ["Avionique & instruments embarqués"])
_add(r"\bservomotor\w*\b|\bmotion control\b|\bpräzisionsantrieb\w*\b",
     ["Servomoteurs & motion control précision"])

# === Logistique & infrastructure ===
_add(r"\bcontainer\b|\bshelter\b|\btransportkiste\w*\b|\bisokisten\b",
     ["Conteneurs, malles, shelters"])
_add(r"\bmilitärische logistik\b|\btruppentransport\b|\blogistik(dienstleistung)?\b",
     ["Logistique militaire & transport"])
_add(r"\bwasseraufbereitung\b|\bwasserfilter\w*\b|\btrinkwasser(versorgung)?\b",
     ["Stations de purification d'eau & infra"])
_add(r"\bschmierstoff\w*\b|\böl\w*\b|\bhydraulikfluid\b|\bschmieröl\w*\b",
     ["Lubrifiants & fluides"])
_add(r"\bsitz\w+\b|\bcockpitsitz\w*\b|\bbesatzungssitz\w*\b",
     ["Sièges & ergonomie cabine"])
_add(r"\bgnss\b|\bgps\b|\bnavigation(ssystem)?\b|\bsatellitennavigation\b",
     ["Avionique & instruments embarqués"])

# === Institutionnel ===
_add(r"\bberatung\b|\bconsulting\b|\bbusiness consulting\b|\bunternehmensberatung\b",
     ["Conseil stratégique & due-diligence M&A"])
_add(r"\bwerbung\b|\bmarketing\b|\bevent[- ]?management\b",
     ["Organisation de salons & conférences défense"])
_add(r"\bforschung\b|\bentwicklung\b|\bf&e\b|\binnovation\w+\b",
     ["R&D académique & laboratoires"])
_add(r"\bversicherung\b|\bbank\w+\b|\bfinanzierung\b",
     ["Financement, banque & assurance défense"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def map_text_to_categories(text: str, *, max_cats: int = 5) -> list[str]:
    """Return canonical product categories matching the German text."""
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


# ---------------------------------------------------------------------------
# Coverage diagnostic
# ---------------------------------------------------------------------------


def coverage_report():
    import sqlite3
    import sys
    from collections import Counter
    from pathlib import Path

    ROOT = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(ROOT))
    db = ROOT / "data" / "eurosatory.db"
    if not db.exists():
        print(f"Missing {db}")
        return
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute(
        "SELECT id, source_snippet, notes FROM attendance_signals "
        "WHERE source_platform='bdsv-de'"
    )
    matched = Counter()
    n = 0
    n_with_match = 0
    for sid, snippet, notes in cur.fetchall():
        text = (snippet or "")
        # Append portfolio from notes
        import re
        m = re.search(r"\[portfolio\]\s*(.*?)(?=\n\[|\Z)", notes or "", re.DOTALL)
        if m:
            text += " " + m.group(1)
        cats = map_text_to_categories(text)
        n += 1
        if cats:
            n_with_match += 1
            for c in cats:
                matched[c] += 1
    con.close()
    print(f"\nBDSV fiches analysed : {n}")
    print(f"With ≥1 product cat   : {n_with_match} "
          f"({n_with_match*100/max(1,n):.1f}%)")
    print("\nTop 20 mapped categories:")
    for c, k in matched.most_common(20):
        print(f"  {k:>4}  {c}")


if __name__ == "__main__":
    coverage_report()
