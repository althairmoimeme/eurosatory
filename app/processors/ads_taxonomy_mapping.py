"""Map ADS Group UK ``knowsAbout`` capability tags onto our canonical
75 product categories + 5 supply-chain tiers.

The ADS schema.org tags follow a 3-level hierarchy in their members
directory : Domain → Sub-domain → Specifics.  Examples :
    Platform Integration → Avionics / Computers
    Platform Integration → Engine / Engine Control
    Platforms (Whole)    → Combat Vehicle (Tank)
    Materials            → Carbon / PEEK
    Materials            → Composites
    Manufacturing        → Adhesive Bonding
    Standards            → ISO 9001

We build a *substring rule list* — order matters (most specific first).
Run ``python -m app.processors.ads_taxonomy_mapping`` to see coverage
stats against the live DB.
"""
from __future__ import annotations

import re
from typing import Optional

from app.processors.taxonomy_normalize import PRODUCT_CATEGORIES


# ---------------------------------------------------------------------------
# Mapping rules — each entry is (substring_pattern, [canonical_categories])
# ---------------------------------------------------------------------------
# Patterns are case-insensitive substrings tested against the ADS tag.
# A given tag may match multiple rules — we collect all matched
# canonical categories (union, deduplicated).

_RULES: list[tuple[re.Pattern, list[str]]] = []


def _add(pattern: str, categories: list[str]) -> None:
    _RULES.append((re.compile(pattern, re.I), categories))


# === Whole platforms (OEM) ===
_add(r"\bwhole aircraft\b|\bfixed wing\b|\brotor wing\b|\bhelicopter\b",
     ["Avions & hélicoptères militaires"])
_add(r"\bcombat vehicle\b|\btank\b|\bapc\b|\barmou?red\b",
     ["Véhicules blindés"])
_add(r"\bpatrol\b|\bspecialist vehicle\b|\brough terrain\b",
     ["Véhicules tactiques (non blindés)"])
_add(r"\blorries?\b|\bgoods\b|\btrucks?\b",
     ["Camions militaires & logistique"])
_add(r"\btrailers?\b",
     ["Camions militaires & logistique"])
_add(r"\bbikes?\b|\bmotorbikes?\b|\bbicycles?\b|\bquads?\b",
     ["Véhicules tactiques (non blindés)"])
_add(r"\bambulance\b",
     ["Ambulances & véhicules sanitaires"])
_add(r"\bfire fighting\b|\bsearch and rescue\b",
     ["Engins du génie militaire"])
_add(r"\bwhole vessel\b|\bship\b|\bboat\b|\brhib\b",
     ["Navires & sous-systèmes navals"])
_add(r"\bsatellites?\b|\bspacecrafts?\b",
     ["Satellites & CubeSats"])
_add(r"\brockets?\b",
     ["Roquettes & lance-roquettes"])
_add(r"\bunmanned\b|\buav\b|\budrones?\b",
     ["Drones aériens (UAV)"])
_add(r"\bugv\b|\bunmanned ground\b|\beod\b",
     ["Robots terrestres (UGV / EOD)"])

# === Sub-systems / Tier 1 ===
_add(r"\bavionics?\b|\bin flight\b",
     ["Avionique & instruments embarqués"])
_add(r"\bnavigation\b|\bgps\b|\bgnss\b",
     ["Avionique & instruments embarqués"])
_add(r"\bradar\b",
     ["Radars & traitement signal"])
_add(r"\bsonar\b",
     ["Sonars & systèmes ASM"])
_add(r"\bcommand and control\b|\bc2\b|\bc4isr\b",
     ["Systèmes C2 & C4ISR"])
_add(r"\bcommunications? \(inc audio\b|\btactical comms?\b",
     ["Radios tactiques & SDR"])
_add(r"\bengine\b",
     ["Moteurs aéronautiques"])
_add(r"\blanding gear\b|\blanding guidance\b",
     ["Avionique & instruments embarqués"])
_add(r"\bflight control\b|\bauto flight\b",
     ["Avionique & instruments embarqués"])
_add(r"\bweapons?\b|\bgun\b|\bturrets?\b",
     ["Tourelles téléopérées (RWS)"])
_add(r"\bmissiles?\b|\bguided weapons?\b",
     ["Missiles & armements guidés"])
_add(r"\bammunit",
     ["Munitions petit/moyen calibre"])
_add(r"\bexplosives?\b|\benerget",
     ["Explosifs & matériaux énergétiques"])
_add(r"\boptical\b|\boptronics?\b|\beyepiece\b|\biewing aids?\b",
     ["Optronique & viseurs (EO/IR)"])
_add(r"\binfrared\b|\bthermal imag",
     ["Détecteurs IR refroidis & non-refroidis"])
_add(r"\bnight vision\b",
     ["Vision nocturne & jumelles"])
_add(r"\bsimulator\b|\bsimulation\b|\bvr\b|\baugmented",
     ["Simulateurs d'entraînement & VR/AR"])

# === Components / Tier 2 ===
_add(r"\belectrical components?\b|\bcabling\b|\bwires?\b|\bconnectors?\b|\bharness",
     ["Connectique & câblage durcis"])
_add(r"\belectronic components?\b|\bcircuit boards?\b|\bbox build\b|\bflexible circuits?\b",
     ["Composants électroniques & cartes"])
_add(r"\bsensors?\b|\btransducers?\b|\bdetectors?\b",
     ["Capteurs embarqués (généraux)"])
_add(r"\bantenna",
     ["Antennes & infrastructures RF"])
_add(r"\bpower supply\b|\bconverters?\b|\binverters?\b",
     ["Alimentations & convertisseurs durcis"])
_add(r"\bservomot|\bactuators? \(precision",
     ["Servomoteurs & motion control précision"])
_add(r"\boptical components?\b|\blens(?:es)?\b",
     ["Composants électroniques & cartes"])

# === Pieces / Operations / Tier 3 ===
_add(r"\bmechanical components?\b|\bpipes?\b|\bhoses?\b|\bsprings?\b|\bfans?\b",
     ["Pièces mécaniques & usinage"])
_add(r"\bmachining\b|\bmilling\b|\bturning\b|\bcnc\b",
     ["Pièces mécaniques & usinage"])
_add(r"\bfasteners?\b|\bbolts?\b|\bscrews?\b",
     ["Fixations & visserie aéronautique"])
_add(r"\badhesive\b|\btapes?\b",
     ["Fixations & visserie aéronautique"])
_add(r"\bhydraulic\b|\bpneumatic\b",
     ["Hydraulique & motion control"])
_add(r"\bcoatings?\b|\bsurface treatment\b|\bplating\b|\banodi[zs]ing\b",
     ["Pièces mécaniques & usinage"])
_add(r"\bstorage\b|\bracking\b|\bcabinets?\b|\biso containers?\b",
     ["Conteneurs, malles, shelters"])
_add(r"\btransmission\b|\bgearbox",
     ["Mécanique de transmission"])
_add(r"\btextiles?\b|\bclothing\b|\buniforms?\b",
     ["Textiles techniques tactiques"])
_add(r"\blubricants?\b|\boils?\b|\bgreases?\b|\bfluids?\b",
     ["Lubrifiants & fluides"])
_add(r"\bseat\b|\bseating\b|\bcabin\b|\bergonom",
     ["Sièges & ergonomie cabine"])

# === Materials / Tier 4 ===
_add(r"\bcomposites?\b|\bcarbon\b|\bpeek\b|\bkevlar\b",
     ["Matériaux composites & blindage"])
_add(r"\bsteel\b|\balloys?\b|\btitanium\b|\baluminium\b|\bmetallurg",
     ["Aciers & métallurgie spéciale"])
_add(r"\bceramics?\b|\bceramic coatings?\b",
     ["Céramiques techniques & balistiques"])
_add(r"\bbatter|\benergy storage\b",
     ["Batteries & packs énergétiques"])
_add(r"\bfuel cell|\bhydrogen\b",
     ["Piles à combustible & H2"])
_add(r"\bpolymers?\b|\bplastics?\b|\bteflon\b",
     ["Matériaux composites & blindage"])

# === Soldier-system / personal protection ===
_add(r"\bbody armou?r\b|\bballistic protection\b|\bbullet[- ]?proof\b",
     ["Gilets & plaques pare-balles"])
_add(r"\bhelmets?\b|\bhead protection\b",
     ["Casques de combat"])
_add(r"\briot control\b|\bcrowd control\b|\bpublic order\b|\bnon[- ]?lethal\b",
     ["Armes non-létales"])
_add(r"\bfield hospital\b|\bfield kitchen\b|\brations?\b|\bsleeping bag",
     ["Équipement du fantassin (général)"])
_add(r"\bequipment bag\b|\bbackpacks?\b|\bload[- ]?carrying\b",
     ["Équipement du fantassin (général)"])
_add(r"\bcbrn\b|\bdecontamination\b|\bnbc\b",
     ["NRBC (masques, tenues, détecteurs)"])
_add(r"\bblast protection\b|\bprotective structures?\b",
     ["Matériaux composites & blindage"])
_add(r"\barmou?red glass\b|\bballistic glass\b",
     ["Vitrages blindés"])

# === Energy / Power ===
_add(r"\bgenerators?\b|\bgensets?\b",
     ["Groupes électrogènes & énergie tactique"])

# === Cyber / Software ===
_add(r"\bcyber\b|\bcyber security\b|\binfo[- ]?security\b",
     ["Cybersécurité (logiciels & appliances)"])
_add(r"\bsoftware\b|\bdigital services?\b|\bdata analysis\b|\binformation technology\b"
     r"|\bsoftware modelling\b|\bsoftware optimisation\b",
     ["Logiciels métier défense (autres)"])
_add(r"\bartificial intelligence\b|\bmachine learning\b|\bcomputer vision\b",
     ["Plateformes IA / vision défense"])
_add(r"\b5g\b|\bp[- ]?lte\b|\bmesh networks?\b|\bmanet\b",
     ["Réseaux militaires (5G/P-LTE/MANET)"])

# === Surveillance / Reconnaissance ===
_add(r"\bsurveillance\b|\bisr\b|\bintelligence gathering\b",
     ["Optronique & viseurs (EO/IR)"])
_add(r"\bcovert technolog|\belectronic warfare\b|\bew\b|\bsig[- ]?int\b",
     ["Systèmes de guerre électronique (EW)"])

# === Tools / Inspection / Test ===
_add(r"\btools?\b|\binspection\b|\btest equipment\b",
     ["Pièces mécaniques & usinage"])

# === Pressure Control / Vibration Mounts / Mechanical Equipment ===
_add(r"\bpressure control\b|\bvalves?\b",
     ["Hydraulique & motion control"])
_add(r"\bvibration\b|\bstabili[zs]ers?\b|\btelescopic mounts?\b|\bmechanical equipment\b",
     ["Pièces mécaniques & usinage"])

# === Nuclear / Specialized Engineering ===
_add(r"\bnuclear engineering\b|\bnuclear\b",
     ["Engins du génie militaire"])

# === Furniture / Office / Cabinets ===
_add(r"\bfurniture\b|\boffice\b",
     ["Conteneurs, malles, shelters"])

# === Building / Infrastructure ===
_add(r"\bbuilding materials?\b|\binfrastructure\b|\bconstruction\b",
     ["Engins du génie militaire"])

# === Platform Systems / Structures ===
_add(r"\bplatform systems?\b|\bplatform structures?\b|\bsubcomponents?\b",
     ["Pièces mécaniques & usinage"])

# === Engineering / Manufacturing services (T3 catch-all) ===
_add(r"\bengineering and manufacturing\b|\bmanufacturing services?\b"
     r"|\bnon[- ]?mechanical\b|\bstructural\b",
     ["Pièces mécaniques & usinage"])

# === Services (Defence Consultancy, Training, Corporate, Subcomponents) ===
# These are NOT products — but worth flagging in a non-product category.
# We map them to a "service-only" generic category so they appear in
# search but don't pollute the product tier. (Maps to N/A on tier.)

# === Components catch-all (when only "Components" is given) ===
_add(r"^components?$",
     ["Composants électroniques & cartes"])
_add(r"^equipment / technolog",
     ["Composants électroniques & cartes"])

# === Search / Rescue / Fire-fighting ===
_add(r"\bsearch and rescue\b|\bfire fighting\b",
     ["Engins du génie militaire"])

# === Soldier kit niches (non-lethal, restraint, breaching) ===
_add(r"\btruncheons?\b|\bbatons?\b|\brestrain\b|\bhand cuffs?\b|\bleg cuffs?\b",
     ["Armes non-létales"])
_add(r"\bhostage\b|\bsiege\b|\bhijack\b|\bforced entry\b|\bbreaching\b",
     ["Armes légères & accessoires"])
_add(r"\bcustody equipment\b|\bjail\b|\bprison\b",
     ["Armes non-létales"])
_add(r"\bsearchlights?\b|\btorches?\b|\bbeacons?\b|\billuminators?\b|\bmarkers?\b",
     ["Équipement du fantassin (général)"])
_add(r"\bmedical\b|\bmedical kit",
     ["Équipement du fantassin (général)"])

# === Asset tracking / RFID / Smart sensors ===
_add(r"\basset tracking\b|\brfid\b|\bsmart dots\b|\bsmart water\b",
     ["Capteurs embarqués (généraux)"])

# === Lifting / Load handling / Cranes ===
_add(r"\blifting\b|\bload handling\b|\bwinches\b|\bcranes\b|\bhoists?\b",
     ["Engins du génie militaire"])

# === Naval-specific operations ===
_add(r"\breplenishment[- ]?at[- ]?sea\b|\bras\b",
     ["Navires & sous-systèmes navals"])

# === Nanotechnology, Coatings ===
_add(r"\bnanotechnolog",
     ["Matériaux composites & blindage"])

# === Laboratory / Bio / Chem ===
_add(r"\blaboratory\b|\bbio analyser\b|\bchem analyser\b",
     ["NRBC (masques, tenues, détecteurs)"])

# === Noise / Sound / EMI ===
_add(r"\bnoise\b|\bsound control\b|\bemi\b|\bemc\b",
     ["Composants électroniques & cartes"])

# === Systems Integrator / Systems ===
_add(r"\bsystems integrator\b|^systems$",
     ["Systèmes C2 & C4ISR"])

# === Promotional / Giftware (filter out) ===
# Intentionally NOT mapped — these are off-topic for defense B2B.

# === Surveillance & Security (generic) ===
# Already covered above — "Security" alone is too generic; map to
# Cybersécurité as a sensible default since most ADS members so labelled
# are cyber/IT/services rather than physical security.
_add(r"^security$",
     ["Cybersécurité (logiciels & appliances)"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def map_tag_to_categories(tag: str) -> list[str]:
    """Return the canonical product categories matching a single ADS tag."""
    out: list[str] = []
    for pat, cats in _RULES:
        if pat.search(tag):
            for c in cats:
                if c not in out:
                    out.append(c)
    return out


def map_tags_to_categories(tags: list[str]) -> list[str]:
    """Aggregate canonical categories from a list of ADS tags (unique)."""
    found: list[str] = []
    for t in tags or []:
        for c in map_tag_to_categories(t):
            if c not in found:
                found.append(c)
    return found


# ---------------------------------------------------------------------------
# Coverage diagnostic — run as a script
# ---------------------------------------------------------------------------


def coverage_report():
    import json
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
        "SELECT notes FROM attendance_signals "
        "WHERE source_platform='ads-group-uk' "
        "  AND notes LIKE '%[ads-detail]%'"
    )
    matched = Counter()
    unmatched = Counter()
    n_fiches = 0
    n_with_match = 0
    for (notes,) in cur.fetchall():
        idx = notes.find("[ads-detail]")
        try:
            blob = json.loads(notes[idx + len("[ads-detail]"):].strip().split("\n")[0])
        except Exception:
            continue
        n_fiches += 1
        cats = map_tags_to_categories(blob.get("knowsAbout") or [])
        if cats:
            n_with_match += 1
        for c in cats:
            matched[c] += 1
        for t in blob.get("knowsAbout") or []:
            if not map_tag_to_categories(t):
                unmatched[t.strip()] += 1
    con.close()

    print(f"\nFiches analysed: {n_fiches}")
    print(f"Fiches with ≥1 canonical category: {n_with_match} "
          f"({n_with_match*100/max(1,n_fiches):.1f}%)")
    print(f"\nUnique product categories used: {len(matched)} / "
          f"{len(PRODUCT_CATEGORIES)-1}")
    print("Top 20 mapped categories:")
    for c, n in matched.most_common(20):
        print(f"  {n:>4}  {c}")

    print(f"\nTop 30 UNMAPPED ADS tags ({len(unmatched)} total):")
    for t, n in unmatched.most_common(30):
        print(f"  {n:>4}  {t}")


if __name__ == "__main__":
    coverage_report()
