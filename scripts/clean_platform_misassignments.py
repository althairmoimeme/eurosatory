"""Clean misassigned PLATFORM categories from products_categories.

The LLM Pass 1-3 sometimes tagged a company with a platform category
(e.g. "Véhicules blindés") because its activity contains the phrase
"pour véhicules blindés", even though the company actually makes
COMPONENTS / SERVICES — not the platform itself.

Heuristic : if the activity_1liner starts with a verb that signals
component-making or services AND mentions a platform with a "pour"
preposition, remove the platform category.

  Activity verb signals component/service-orientation :
    Sous-traite | Fournit | Conseille | Audite | Forme | Maintient
    Distribue | Édite | Fabrique des composants | Fabrique des
    pièces | Fabrique des moteurs | Fabrique des cartes | etc.

  Platform categories at risk :
    Véhicules blindés, Véhicules tactiques (non blindés),
    Camions militaires & logistique, Ambulances & véhicules sanitaires,
    Engins du génie militaire,
    Avions & hélicoptères militaires, Drones aériens (UAV),
    Drones FPV & munitions rôdeuses, Satellites & CubeSats,
    Navires & sous-systèmes navals, Robots terrestres (UGV / EOD).

Run :
    python -m scripts.clean_platform_misassignments
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT_PATH = ROOT / "data" / "exports" / "targeting_profiles_final.json"
CAT_PATH = ROOT / "data" / "llm_test" / "llm_categories_overrides.json"


PLATFORM_CATS = {
    "Véhicules blindés",
    "Véhicules tactiques (non blindés)",
    "Camions militaires & logistique",
    "Ambulances & véhicules sanitaires",
    "Engins du génie militaire",
    "Avions & hélicoptères militaires",
    "Drones aériens (UAV)",
    "Drones FPV & munitions rôdeuses",
    "Satellites & CubeSats",
    "Navires & sous-systèmes navals",
    "Robots terrestres (UGV / EOD)",
}

# Component / service / sub-system makers — they make things FOR the
# platform, not the platform itself.
_COMPONENT_PRODUCT_HINT = re.compile(
    r"^(Fabrique|Conçoit (et fabrique )?|Forge|Usine|Imprime|Sous[- ]traite|"
    r"Soude|Assemble|Construit|Produit|Met)\s+"
    r"(des?\s+|de\s+l['’]?\s*)?"
    r"(composants?|pièces?|cartes?|circuits?|"
    r"systèmes? embarqués?|moteurs?|transmissions?|"
    r"connecteurs?|c[âa]bles?|c[âa]blages?|capteurs?|"
    r"alimentations?|servomoteurs?|réducteurs?|engrenages?|"
    r"vérins?|roulements?|joints?|ressorts?|tôles?|"
    r"matériaux?|aciers?|composites?|céramiques?|polymères?|"
    r"batteries?|cellules?|électrons?|optiques?|viseurs?|"
    r"radars?|antennes?|lasers?|détecteurs?|imageurs?|"
    r"logiciels?|plateformes?|données?|IA|cybersécurité|"
    r"vêtements?|textiles?|tissus?|fibres?|protections? "
    r"balistiques?|gilets?|casques?|harnais?|sangles?|"
    r"vivres?|énergie|eau|carburants?|lubrifiants?|"
    r"emballages?|conteneurs?|shelters?|abris?|"
    r"générateurs?|groupes? électrogènes?)",
    re.I | re.U,
)
_SERVICE_VERB_RX = re.compile(
    r"^(Sous[- ]traite|Fournit\s+(?:des?\s+)?(?:services?|de l['’]ingénierie|"
    r"du conseil|de la formation|de la maintenance)|Conseille|Audite|"
    r"Forme|Maintient|Op[èe]re|Réalise|Distribue|Édite|Anime|"
    r"Représente|Coordonne|Loue|Pilote|Conduit|Promeut|Finance|"
    r"Investit|Soutient|Accompagne|Modernise|Démantèle|"
    r"D[ée]ploie\s+(?:des?\s+)?(?:services?|connectivités?))",
    re.I | re.U,
)


def is_component_or_service_maker(activity: str) -> bool:
    """True when the activity verb + first noun signals the company
    makes COMPONENTS or provides SERVICES, not platforms."""
    if not activity:
        return False
    a = activity.strip()
    if _SERVICE_VERB_RX.match(a):
        return True
    if _COMPONENT_PRODUCT_HINT.match(a):
        return True
    return False


# Platform name → keyword detection in activity text. If the activity
# contains "pour {platform}" / "destinés aux {platform}" / etc.
# (i.e. the platform is the *target* not the *output*), the company
# makes components or services for that platform — not the platform.
_PLATFORM_TARGET_RX = re.compile(
    r"\b(pour|destin[ée]s? (?:aux?|à)|dédiés? (?:aux?|à)|"
    r"adapt[ée]s? (?:aux?|à)|équipant)\s+"
    r"(?:les?\s+|des?\s+|la\s+|l['’]\s*)?"
    r"(véhicules?(?:\s+militaires?|\s+blindés?|\s+tactiques?)?|"
    r"chars?|engins? blindés?|"
    r"drones?|UAV|UAS|"
    r"avions?(?:\s+militaires?)?|h[ée]licoptères?|aéronefs?|"
    r"satellites?|cubesats?|engins? spatiaux|"
    r"navires?|sous[- ]marins?|frégates?|patrouilleurs?|"
    r"robots? terrestres?|UGV)",
    re.I | re.U,
)


def has_target_platform(activity: str, platform_cat: str) -> bool:
    """True when activity says e.g. "pour véhicules" and platform_cat
    is a vehicle category — implies the company makes the COMPONENTS
    targeting this platform, not the platform itself."""
    if not activity:
        return False
    a = activity.lower()
    cat_keywords = {
        "Véhicules blindés": (r"v[ée]hicules?\s+blind[ée]s?", "véhicule", "char", "blind"),
        "Véhicules tactiques (non blindés)": (r"v[ée]hicules?\s+tactiques?", "véhicule"),
        "Camions militaires & logistique": ("camion", "véhicule"),
        "Ambulances & véhicules sanitaires": ("ambulance", "sanitaire"),
        "Engins du génie militaire": ("génie", "engin", "blind"),
        "Avions & hélicoptères militaires": ("avion", "hélicopt", "aéronef"),
        "Drones aériens (UAV)": ("drone", "uav", "uas"),
        "Drones FPV & munitions rôdeuses": ("drone", "fpv", "munition rôdeuse"),
        "Satellites & CubeSats": ("satellite", "cubesat", "spatial"),
        "Navires & sous-systèmes navals": ("navire", "sous-marin", "naval", "frégate", "patrouilleur"),
        "Robots terrestres (UGV / EOD)": ("robot terrestre", "ugv", "eod"),
    }
    keywords = cat_keywords.get(platform_cat, ())
    if not keywords:
        return False
    # Check for "pour {keyword}" pattern. If found, this is a downstream
    # target signal.
    for kw in keywords:
        rx = re.compile(
            rf"\b(pour|destin[ée]s? (?:aux?|à)|dédiés? (?:aux?|à)|"
            rf"adapt[ée]s? (?:aux?|à)|équipant)\s+"
            rf"(?:les?\s+|des?\s+|la\s+|l['’]\s*)?[^.,;]*?{kw}",
            re.I | re.U,
        )
        if rx.search(a):
            return True
    return False


def has_platform_in_text(activity: str, products: list[str]) -> bool:
    """True when the activity mentions a platform NOT in the 'pour X'
    construction (= the company actually makes the platform)."""
    text = (activity or "").lower() + " " + " ".join(products or []).lower()
    # If the company's products list explicitly mentions the platform
    # by name (e.g. ["Humvee", "Boxer", "Leclerc"]), assume they make it.
    platform_keywords = (
        "humvee", "boxer", "leclerc", "leopard", "abrams", "rafale",
        "mirage", "patroller", "fremm", "destroyer", "frigate",
    )
    return any(kw in text for kw in platform_keywords)


def main() -> int:
    recs = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    cat_ov = json.loads(CAT_PATH.read_text(encoding="utf-8")) if CAT_PATH.exists() else {}

    n_examined = 0
    n_cleaned = 0
    samples = []
    for r in recs:
        cats = list(r.get("products_categories") or [])
        platform_cats_present = [c for c in cats if c in PLATFORM_CATS]
        if not platform_cats_present:
            continue
        n_examined += 1
        activity = r.get("activity_1liner") or ""
        products = r.get("products") or []

        # Two paths for cleanup :
        #   (1) the activity verb + first noun signals component / service
        #   (2) the activity uses "pour {platform}" — the platform is the
        #       target, not the output.
        # We remove ONLY the platform cats whose name matches the "pour"
        # context, so we keep legitimate platform makers untouched.
        cats_to_remove: list[str] = []
        if is_component_or_service_maker(activity) and not has_platform_in_text(
            activity, products
        ):
            cats_to_remove = list(platform_cats_present)
        else:
            # Targeted removal : only the platform cat that matches a
            # "pour X" downstream target hint AND is not directly named
            # in the products list.
            for pc in platform_cats_present:
                if has_target_platform(activity, pc) and not has_platform_in_text(
                    activity, products
                ):
                    # Sanity : the company products list shouldn't be
                    # heavy on platform-named items.
                    cats_to_remove.append(pc)

        if not cats_to_remove:
            continue

        cleaned = [c for c in cats if c not in cats_to_remove]
        if not cleaned:
            cleaned = ["Autre — à qualifier"]
        eid = str(r["exhibitor_id"])
        existing = cat_ov.get(eid, {})
        existing.setdefault("services_categories",
                            r.get("services_categories") or [])
        existing.setdefault("technologies_categories",
                            r.get("technologies_categories") or [])
        existing["products_categories"] = cleaned
        cat_ov[eid] = existing
        n_cleaned += 1
        if len(samples) < 12:
            samples.append((
                eid, r["company_name"][:32],
                cats_to_remove, activity[:90],
            ))

    CAT_PATH.write_text(
        json.dumps(cat_ov, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Examined  : {n_examined} fiches with platform cats")
    print(f"Cleaned   : {n_cleaned} ({n_cleaned*100/max(n_examined,1):.1f}%)")
    print()
    print("Samples (eid, name, removed cats, activity):")
    for eid, name, removed, act in samples:
        print(f"  #{eid:5} {name:32} | -{removed}")
        print(f"          act: {act}")
    print()
    print(f"Saved → {CAT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
