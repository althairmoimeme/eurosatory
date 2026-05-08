"""Apply critical manual overrides AFTER llm_fix_bad_activities completes.

These are tier-misclassification fixes and institutional reclassifications
that the LLM does not handle well (they require domain knowledge about
institutional vs industrial roles).

Run :
    python -m scripts.apply_critical_overrides
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROF_PATH = ROOT / "data" / "llm_test" / "profiles_manual_overrides.json"
CAT_PATH = ROOT / "data" / "llm_test" / "llm_categories_overrides.json"


# IDs where the LLM cannot reach the right tier without domain knowledge.
# Order: (eid, profile_dict_or_None, cat_dict_or_None).
# Use None to skip that side; only override the cat side typically.
CRITICAL: list[tuple[str, dict | None, dict | None]] = [
    # === #339 Bpifrance — institutional financier, not Tier 4 ===
    ("339", {
        "activity_1liner": "Finance les industriels défense français (PME, start-ups, primes) via prêts, capital et garanties export — banque publique d'investissement.",
        "why_target": "Bailleur public majeur de la BITD française — financement programmes EDF, prêts export, capital-risque deeptech défense.",
        "products": [],
        "services": ["financement export", "capital", "garanties", "prêts"],
        "target_buyers": ["Industriels défense", "PME défense", "Start-ups deeptech"],
        "technologies": [],
    }, {
        "products_categories": ["Financement, banque & assurance défense"],
        "services_categories": ["Financement export & banque défense"],
        "technologies_categories": [],
    }),
    # === #448 Cluster PRIMUS Défense & Sécurité ===
    ("448", {
        "activity_1liner": "Anime un cluster régional de PME défense, sécurité et résilience pour soutenir l'innovation et l'export.",
        "why_target": "Cluster défense régional fédérant ~80 PME — point d'entrée pour identifier des fournisseurs locaux et émergents en défense / sécurité.",
        "products": ["adhésion cluster", "événements professionnels", "mise en relation B2B"],
        "services": ["animation de réseau", "lobbying & représentation", "soutien export PME"],
        "target_buyers": ["PME défense", "Industriels défense", "Acheteurs publics"],
        "technologies": [],
    }, {
        "products_categories": ["Représentation institutionnelle (cluster, fédération, chambre)"],
        "services_categories": ["Lobbying & représentation"],
        "technologies_categories": [],
    }),
    # === #2354 AMCO LES ESCAMOTABLES — bornes escamotables OEM ===
    ("2354", {
        "activity_1liner": "Conçoit et fabrique des bornes escamotables et barrières pour le contrôle d'accès et la sécurisation périmétrique.",
        "why_target": "Fabricant français de protection périmétrique active — fournisseur potentiel pour bases militaires, OIV et points sensibles.",
    }, {
        "products_categories": ["Autre — à qualifier"],
        "services_categories": ["Sous-traitance industrielle"],
        "technologies_categories": ["Hydraulique haute performance"],
    }),
    # === #1313 Markforged — 3D printer OEM (machine tool) ===
    ("1313", {
        "activity_1liner": "Conçoit et fabrique des imprimantes 3D métal et composite (Mark Two, X7) pour la fabrication militaire au point d'usage.",
        "why_target": "Fabricant US de presses 3D métal — fournisseur clé pour ateliers déployés et MRO additif des armées.",
    }, {
        "products_categories": ["Autre — à qualifier"],
        "services_categories": ["Formation & entraînement", "Certification & tests"],
        "technologies_categories": ["Fabrication additive"],
    }),
    # === #95 ALA — global supply chain integrator ===
    ("95", {
        "activity_1liner": "Intègre la chaîne d'approvisionnement (sourcing, kitting, logistique) pour l'aéronautique, la défense et le ferroviaire.",
        "why_target": "Intégrateur supply chain multi-secteurs — partenaire ou concurrent pour les solutions de logistique défense et soutien programme.",
        "products": ["kits sourcés", "pièces réparées"],
        "services": ["sourcing & kitting", "gestion supply chain", "logistique"],
        "target_buyers": ["Industriels défense", "Primes défense", "OEM aéronautique"],
        "technologies": [],
    }, None),
    # === #296 Bertel O. Steen — distributor (not MRO) ===
    ("296", None, {
        "products_categories": ["Distribution composants & représentation de marques"],
        "services_categories": ["Distribution & représentation", "Ingénierie & conseil"],
        "technologies_categories": [],
    }),
    # === #20 Aalborg University — research lab ===
    ("20", {
        "activity_1liner": "Conduit des recherches académiques en ingénierie, énergie et sciences appliquées (Aalborg University, Danemark).",
        "why_target": "Université de recherche danoise — partenaire R&D potentiel pour projets EDF et collaborations académiques défense.",
    }, {
        "products_categories": ["R&D académique & laboratoires"],
        "services_categories": ["R&D sur contrat", "Ingénierie & conseil"],
        "technologies_categories": [],
    }),
    # === #265 Banque Européenne d'Investissement ===
    ("265", {
        "activity_1liner": "Finance les politiques européennes de défense, sécurité et résilience via prêts et garanties (Banque européenne d'investissement).",
        "why_target": "Banque institutionnelle UE — bailleur potentiel pour les programmes EDF, RRF défense et infrastructure critique.",
        "products": [],
        "services": ["financement", "conseil financier"],
        "target_buyers": ["Industriels défense", "Acheteurs publics"],
        "technologies": [],
    }, {
        "products_categories": ["Financement, banque & assurance défense"],
        "services_categories": ["Financement export & banque défense"],
        "technologies_categories": [],
    }),
    # === #450 CMMI Cyprus Marine Institute ===
    ("450", {
        "activity_1liner": "Conduit des recherches maritimes et navales appliquées (CMMI, Chypre) — partenaire programmes UE.",
        "why_target": "Institut de recherche maritime chypriote — partenaire potentiel pour projets navals UE et ONR.",
    }, {
        "products_categories": ["R&D académique & laboratoires"],
        "services_categories": ["R&D sur contrat", "Ingénierie & conseil"],
        "technologies_categories": [],
    }),
    # === #1107 IRON cluster ===
    ("1107", {
        "activity_1liner": "Anime un cluster industrie défense / aéronautique pour mutualiser R&D et accès marché (IRON Cluster).",
        "why_target": "Cluster défense régional — point d'entrée pour identifier des PME spécialisées drones, robotique et logiciel défense.",
    }, {
        "products_categories": ["Représentation institutionnelle (cluster, fédération, chambre)"],
        "services_categories": ["Lobbying & représentation"],
        "technologies_categories": [],
    }),
    # === #1472 NAUDI — Ukrainian defense industry association ===
    ("1472", {
        "activity_1liner": "Représente l'industrie de défense ukrainienne et facilite la coopération internationale (NAUDI).",
        "why_target": "Association faîtière de l'industrie défense ukrainienne — point d'accès pour identification de partenaires UA pour projets de coproduction.",
    }, {
        "products_categories": ["Représentation institutionnelle (cluster, fédération, chambre)"],
        "services_categories": ["Lobbying & représentation"],
        "technologies_categories": [],
    }),
    # === #1216 La Banque Postale ===
    ("1216", {
        "activity_1liner": "Fournit des services bancaires et d'assurance grand public et institutionnels — exposition défense indirecte.",
        "why_target": "Banque publique généraliste — exposition défense uniquement via financements indirects ; prospect commercial à requalifier.",
        "products": ["comptes bancaires", "crédits", "assurance"],
        "services": ["conseil financier", "crédit export"],
        "target_buyers": ["Industriels défense", "Acheteurs publics"],
        "technologies": [],
    }, {
        "products_categories": ["Financement, banque & assurance défense"],
        "services_categories": ["Financement export & banque défense"],
        "technologies_categories": [],
    }),
    # === #1227 Bureau des Légendes ===
    ("1227", {
        "activity_1liner": "(données publiques trop pauvres pour qualification fiable — exposant escape game / loisir à qualifier).",
        "why_target": "Présence Eurosatory atypique — escape game sur thème espionnage. Off-topic défense, à requalifier ou ignorer.",
        "products": [],
        "services": ["expérience immersive"],
        "target_buyers": [],
        "technologies": [],
    }, {
        "products_categories": ["Autre — à qualifier"],
        "services_categories": [],
        "technologies_categories": [],
    }),
]


def main() -> int:
    prof_ov = json.loads(PROF_PATH.read_text(encoding="utf-8")) if PROF_PATH.exists() else {}
    cat_ov = json.loads(CAT_PATH.read_text(encoding="utf-8")) if CAT_PATH.exists() else {}
    n_prof_set = 0
    n_cat_set = 0
    for eid, prof, cat in CRITICAL:
        if prof:
            prof_ov[eid] = {**prof_ov.get(eid, {}), **prof}
            n_prof_set += 1
        if cat:
            cat_ov[eid] = cat
            n_cat_set += 1
    PROF_PATH.write_text(
        json.dumps(prof_ov, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    CAT_PATH.write_text(
        json.dumps(cat_ov, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Applied : {n_prof_set} profile overrides + {n_cat_set} cat overrides")
    print(f"Total prof_ov entries : {len(prof_ov)}")
    print(f"Total cat_ov  entries : {len(cat_ov)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
