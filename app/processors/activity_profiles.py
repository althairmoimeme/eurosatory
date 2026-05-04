"""Coherent activity → (products / technos / cibles / why) profiles.

Why this module exists
----------------------
After the activity-extraction redesign each rule-based fiche gets a
correct ``activity_1liner`` from a pattern match. But the old
``products_categories`` / ``technologies_categories`` / ``why_target``
fields were still built from the polluted ``built_products`` of the
previous extractor — so a fiche with the activity « Forge à chaud des
pièces en acier » could still show « véhicules militaires » as its
canonical product.

This module fixes that by giving every pattern a **profile family**.
A profile family is a coherent bundle :
  - **products**       : canonical product categories that match the activity
  - **technologies**   : canonical tech categories that flow from the activity
  - **target_buyers**  : the closed-taxonomy values (1-4 of the 5)
  - **why_template**   : an actionable « pourquoi cibler » sentence

When a pattern matches, the caller can use the family's profile *instead
of* the legacy product-mapping path. That makes every fiche internally
consistent : activity, sector, technologies, cibles, and pourquoi cibler
all describe the same thing.

Each family value lives in our existing canonical taxonomies
(``app.processors.taxonomy_normalize``) so filtering still works.
"""
from __future__ import annotations

from typing import TypedDict


class ActivityProfile(TypedDict):
    products: list[str]
    technologies: list[str]
    target_buyers: list[str]
    why_template: str


# Closed buyer taxonomy (5 valeurs)
_MOD = "MoD / Armées"
_PRIME = "Primes défense"
_CIVIL = "Sécurité civile"
_INDUS = "Industriels défense"
_EXPORT = "Export / international"


PROFILES: dict[str, ActivityProfile] = {

    # ===================================================================
    # ENERGY / POWER
    # ===================================================================
    "ENERGY": {
        "products": ["générateurs portables", "groupes électrogènes mobiles",
                     "kits énergie tactique", "batteries haute densité"],
        "technologies": ["énergie portative", "électronique de puissance"],
        "target_buyers": [_MOD, _PRIME, _CIVIL, _EXPORT],
        "why_template": "Acheteur potentiel de batteries Li-ion militaires, "
                        "modules de gestion d'énergie embarqués et composants "
                        "de génération électrique.",
    },
    "BATTERIES": {
        "products": ["batteries militaires Li-ion", "packs énergie pour drones",
                     "modules de gestion d'énergie embarquée"],
        "technologies": ["énergie portative", "batteries lithium primaires"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Acheteur de cellules Li-ion qualifiées militaire et "
                        "fournisseur potentiel pour drones, soldats connectés "
                        "et véhicules tactiques.",
    },
    "FUEL_CELLS": {
        "products": ["piles à combustible portables", "générateurs DMFC",
                     "kits soldat connecté énergie"],
        "technologies": ["énergie portative", "piles à combustible H2"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur sur piles à combustible tactiques — "
                        "fournisseur potentiel pour soldats connectés et "
                        "drones longue endurance.",
    },

    # ===================================================================
    # 3D PRINTING / ADDITIVE MANUFACTURING
    # ===================================================================
    "ADDITIVE_MANUF": {
        "products": ["machines de fabrication additive", "plateformes FA métal",
                     "logiciels d'industrialisation FA",
                     "matières premières poudres métalliques"],
        "technologies": ["fabrication additive", "matériaux composites"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Compétiteur sur les machines FA — partenaire pour "
                        "primes cherchant à industrialiser l'impression 3D "
                        "métal pour pièces de rechange critiques.",
    },

    # ===================================================================
    # METAL : forging, machining, foundry, sheet metal
    # ===================================================================
    "FORGING": {
        "products": ["pièces forgées en acier", "produits forgés titane",
                     "ensembles forgés aéronautiques"],
        "technologies": ["métallurgie spéciale", "forgeage de précision"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Sous-traitant Tier-1 forgeage pour primes "
                        "véhicules blindés (KMW, Rheinmetall) et aéronautique "
                        "(turbines, paliers).",
    },
    "MACHINING": {
        "products": ["pièces usinées de précision", "ensembles micro-mécaniques",
                     "composants CNC complexes"],
        "technologies": ["mécanique de précision", "usinage 5 axes"],
        "target_buyers": [_PRIME, _INDUS],
        "why_template": "Sous-traitant éligible EN9100/AQAP — fournisseur "
                        "potentiel pour pièces série courte des programmes "
                        "défense (Scorpion, Eurofighter, MGCS).",
    },
    "FOUNDRY": {
        "products": ["pièces moulées sur-mesure",
                     "fonderie aluminium sable", "moulages haute pression"],
        "technologies": ["métallurgie spéciale"],
        "target_buyers": [_PRIME, _INDUS],
        "why_template": "Sous-traitant Tier-1 fonderie — fournisseur de "
                        "pièces complexes en aluminium pour primes véhicules "
                        "et aéronautique.",
    },
    "SHEET_METAL": {
        "products": ["tôlerie de précision", "ensembles mécano-soudés",
                     "structures aluminium"],
        "technologies": ["mécano-soudure", "métallurgie spéciale"],
        "target_buyers": [_PRIME, _INDUS],
        "why_template": "Sous-traitant Tier-1 tôlerie — fournisseur pour "
                        "primes véhicules et naval.",
    },

    # ===================================================================
    # VEHICLES
    # ===================================================================
    "VEHICLES_ARMORED": {
        "products": ["véhicules blindés", "kits de surblindage",
                     "tourelles téléopérées"],
        "technologies": ["blindage modulaire", "véhicules tout-terrain"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur direct sur véhicules blindés — acheteur "
                        "stratégique de motorisation, optronique, électronique "
                        "embarquée et capteurs.",
    },
    "VEHICLES_TACTICAL": {
        "products": ["véhicules tactiques 4x4 / 6x6", "véhicules logistiques",
                     "modernisation flottes"],
        "technologies": ["véhicules tout-terrain"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Acheteur de pneus run-flat, transmissions lourdes et "
                        "trains roulants — compétiteur sur le segment "
                        "véhicules tactiques 4x4 / 6x6.",
    },
    "VEHICLES_TRUCKS": {
        "products": ["camions militaires", "camions logistiques",
                     "véhicules ravitailleurs"],
        "technologies": ["moteurs diesel haute puissance",
                         "véhicules tout-terrain"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur sur camions militaires (vs MAN HX, "
                        "Tatra, Renault Trucks Defense) — acheteur de "
                        "trains roulants et superstructures.",
    },
    "VEHICLES_ENGINEERING": {
        "products": ["engins du génie blindés", "chargeurs articulés blindés",
                     "excavateurs militaires"],
        "technologies": ["engins du génie blindés", "hydraulique haute pression"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Fournisseur d'engins du génie pour les armées "
                        "et la sécurité civile — partenaire OPEX et "
                        "constructions tactiques.",
    },
    "VEHICLES_AMBULANCE": {
        "products": ["ambulances militaires 4x4", "modules sanitaires Role 1/2",
                     "véhicules sanitaires sur châssis blindé"],
        "technologies": ["véhicules tactiques"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Fournisseur d'ambulances militaires — partenaire "
                        "pour le SSA (Service de Santé des Armées) et OPEX.",
    },
    "VEHICLES_MODERNIZATION": {
        "products": ["modernisation Leopard 2", "modernisation BMP / OT",
                     "kits de surblindage"],
        "technologies": ["modernisation blindés", "blindage composite"],
        "target_buyers": [_MOD, _EXPORT],
        "why_template": "Spécialiste modernisation véhicules blindés — "
                        "partenaire pour pays cherchant à étendre la durée "
                        "de vie de leurs flottes.",
    },

    # ===================================================================
    # AIRBORNE / SPACE
    # ===================================================================
    "AIRCRAFT": {
        "products": ["avions militaires", "avions de transport tactique",
                     "ravitailleurs"],
        "technologies": ["aéronefs militaires", "moteurs aéronautiques"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur sur avions militaires — acheteur "
                        "stratégique de sous-systèmes (radars, optronique, "
                        "moteurs) pour les programmes futurs.",
    },
    "DRONES_ISR": {
        "products": ["drones tactiques ISR", "stations de contrôle au sol",
                     "modules charge utile drones"],
        "technologies": ["vol autonome", "RF / micro-ondes",
                         "matériaux composites"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Acheteur potentiel de nacelles EO/IR, batteries "
                        "Li-ion militaires et liaisons de données chiffrées "
                        "— compétiteur indirect sur drones tactiques.",
    },
    "DRONES_FPV": {
        "products": ["drones FPV de combat", "munitions rôdeuses tactiques",
                     "stations de contrôle"],
        "technologies": ["vol autonome", "IA / vision"],
        "target_buyers": [_MOD, _EXPORT],
        "why_template": "Compétiteur de niche sur drones FPV battle-proven "
                        "(Ukraine) — partenaire pour intégrateurs de drones "
                        "à grande échelle.",
    },
    "DRONES_AEROSTAT": {
        "products": ["aérostats captifs", "ballons stratosphériques",
                     "plateformes ISR persistantes"],
        "technologies": ["composites carbone", "matériaux composites"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Niche aérostats — partenaire pour programmes ISR "
                        "persistants et surveillance frontalière (concurrent "
                        "TCOM, RT LTA).",
    },
    "COUNTER_DRONE": {
        "products": ["systèmes anti-drone", "détecteurs RF anti-drone",
                     "modules de neutralisation C-UAS"],
        "technologies": ["RF / micro-ondes", "guerre électronique",
                         "détection drone"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Compétiteur sur le segment anti-drone — partenaire "
                        "pour intégrateurs de protection événementielle, "
                        "infrastructures critiques et bases militaires.",
    },
    "SATELLITE": {
        "products": ["satellites LEO", "terminaux satellite", "antennes "
                     "phased-array"],
        "technologies": ["communications satellite", "GNSS / GPS"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Compétiteur sur connectivité satellite militaire "
                        "(vs Starlink, Hughes) — partenaire pour les "
                        "programmes de souveraineté spatiale.",
    },

    # ===================================================================
    # NAVAL
    # ===================================================================
    "NAVAL": {
        "products": ["navires militaires", "sous-systèmes navals",
                     "patrouilleurs"],
        "technologies": ["construction navale", "sonars"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur naval — acheteur de sous-systèmes "
                        "(sonars, communications HF, optronique) pour "
                        "frégates et patrouilleurs.",
    },

    # ===================================================================
    # WEAPONS / AMMO
    # ===================================================================
    "ARMS_LIGHT": {
        "products": ["fusils d'assaut", "pistolets de service",
                     "fusils de précision"],
        "technologies": ["mécanique d'armement"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Compétiteur sur armes légères pour fantassins et "
                        "police — acheteur de matériaux énergétiques et "
                        "composants mécaniques de précision.",
    },
    "AMMUNITION": {
        "products": ["munitions petit calibre", "munitions moyen calibre",
                     "munitions gros calibre", "amorçages et fusées"],
        "technologies": ["matériaux énergétiques"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Munitionnaire — acheteur stratégique de poudres, "
                        "explosifs, douilles et amorçages. Programmes en "
                        "forte montée en cadence (155mm, 5.56, 7.62).",
    },
    "EXPLOSIVES": {
        "products": ["explosifs militaires", "propergols",
                     "matériaux énergétiques"],
        "technologies": ["matériaux énergétiques"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Fournisseur Tier-1 d'explosifs et propergols — "
                        "partenaire stratégique pour munitionnaires et "
                        "missilistes.",
    },
    "MISSILES": {
        "products": ["missiles guidés", "armements air-sol",
                     "armements anti-tank"],
        "technologies": ["matériaux énergétiques", "guidage IR / radar"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur sur missiles guidés — acheteur "
                        "stratégique de composants embarqués (autodirecteurs, "
                        "propulseurs, matériaux énergétiques).",
    },
    "GUN_MOUNTS": {
        "products": ["supports d'armes lourdes (M2, M240)",
                     "lance-missiles supports", "tourelles téléopérées"],
        "technologies": ["mécanique d'armement"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Sous-traitant Tier-1 de supports d'armes pour "
                        "primes véhicules blindés et naval.",
    },
    "PYROTECHNICS": {
        "products": ["fumigènes tactiques", "munitions de signalisation",
                     "pyrotechnie d'entraînement"],
        "technologies": ["matériaux énergétiques"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Fournisseur de pyrotechnie pour systèmes "
                        "d'auto-protection véhicules et entraînement militaire.",
    },
    "NON_LETHAL": {
        "products": ["lanceurs 37/40 mm", "munitions caoutchouc / gaz",
                     "grenades fumigènes"],
        "technologies": ["munitions cinétiques"],
        "target_buyers": [_CIVIL, _MOD, _EXPORT],
        "why_template": "Compétiteur sur armes non-létales (vs Combined "
                        "Systems, Rheinmetall NLW) — cible pour les forces "
                        "de l'ordre et armées en maintien de l'ordre.",
    },

    # ===================================================================
    # ELECTRONICS / SENSORS / OPTRONICS / RADAR / EW
    # ===================================================================
    "OPTRONICS_EOIR": {
        "products": ["systèmes optroniques EO/IR", "viseurs optiques",
                     "modules d'imagerie thermique"],
        "technologies": ["imagerie infrarouge", "optique de précision"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur direct sur optronique fantassin et "
                        "véhicules — fournisseur potentiel pour primes "
                        "intégrateurs.",
    },
    "IR_DETECTORS": {
        "products": ["détecteurs IR refroidis", "machines Stirling",
                     "ROIC / circuits de lecture"],
        "technologies": ["imagerie infrarouge", "microélectronique durcie"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Compétiteur sur détecteurs IR refroidis (vs Lynred, "
                        "AIM, Sofradir) — fournisseur Tier-1 pour primes "
                        "optronique.",
    },
    "RADAR": {
        "products": ["radars de surveillance", "modules de traitement signal",
                     "antennes radar"],
        "technologies": ["radar AESA", "RF / micro-ondes"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur sur radars tactiques — acheteur de "
                        "RF / micro-ondes, antennes phased-array et "
                        "traitement signal embarqué.",
    },
    "EW": {
        "products": ["systèmes de guerre électronique", "brouilleurs RF",
                     "détecteurs SIGINT"],
        "technologies": ["guerre électronique", "RF / micro-ondes"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur sur le marché EW — fournisseur de "
                        "briques (brouilleurs, antennes phased-array) "
                        "pour primes intégrateurs.",
    },
    "RADIOS_TACTICAL": {
        "products": ["radios tactiques SDR", "stations relais",
                     "antennes durcies"],
        "technologies": ["radios tactiques", "RF / micro-ondes"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur sur radios SDR / MANET — partenaire pour "
                        "les programmes de modernisation des comms tactiques.",
    },
    "SENSORS_GENERIC": {
        "products": ["capteurs embarqués", "modules de détection",
                     "réseaux de capteurs"],
        "technologies": ["capteurs IoT industriels"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Fournisseur Tier-1 de capteurs — partenaire pour "
                        "primes optronique, drones et systèmes de surveillance.",
    },
    "RADIATION": {
        "products": ["détecteurs de rayonnement", "dosimètres personnels",
                     "spectromètres gamma"],
        "technologies": ["détection NRBC"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Compétiteur sur détection radiologique — partenaire "
                        "pour primes NRBC et programmes de protection "
                        "contre menaces sales.",
    },

    # ===================================================================
    # CONNECTORS / WIRING / PCB / RUGGED ELECTRONICS
    # ===================================================================
    "CONNECTORS": {
        "products": ["connecteurs militaires durcis",
                     "harnais durcis", "ensembles d'interconnexion"],
        "technologies": ["connectique mil-grade"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Fournisseur Tier-1 de connectique militaire — "
                        "partenaire pour primes véhicules, naval et avionique.",
    },
    "WIRING_HARNESS": {
        "products": ["harnais militaires", "câblage durci aéronautique",
                     "ensembles de câblage"],
        "technologies": ["câbles haute performance"],
        "target_buyers": [_PRIME, _INDUS],
        "why_template": "Sous-traitant câblage militaire — fournisseur Tier-1 "
                        "pour primes véhicules, naval et aéronautique.",
    },
    "PCB_HIREL": {
        "products": ["PCB haute fiabilité", "PCB rigid-flex militaires",
                     "PCB pour applications spatiales"],
        "technologies": ["microélectronique durcie"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Sous-traitant PCB Tier-1 pour défense, spatial et "
                        "médical — partenaire pour primes électronique.",
    },
    "RUGGED_COMPUTING": {
        "products": ["ordinateurs durcis", "tablettes durcies",
                     "smartphones durcis"],
        "technologies": ["informatique durcie"],
        "target_buyers": [_MOD, _CIVIL, _PRIME, _EXPORT],
        "why_template": "Compétiteur sur informatique durcie (vs Getac, "
                        "Panasonic) — fournisseur pour véhicules tactiques "
                        "et soldats connectés.",
    },
    "POWER_ELECTRONICS": {
        "products": ["convertisseurs DC/DC durcis", "alimentations militaires",
                     "modules d'énergie embarquée"],
        "technologies": ["électronique de puissance"],
        "target_buyers": [_PRIME, _INDUS],
        "why_template": "Fournisseur d'électronique de puissance durcie — "
                        "partenaire pour primes véhicules cherchant des "
                        "alternatives compactes.",
    },

    # ===================================================================
    # SOFTWARE / AI / CYBER
    # ===================================================================
    "AI_VISION": {
        "products": ["modules IA de détection automatique",
                     "logiciels de fusion de capteurs",
                     "plateformes IA tactiques"],
        "technologies": ["IA / vision", "vision par ordinateur"],
        "target_buyers": [_MOD, _PRIME, _CIVIL, _EXPORT],
        "why_template": "Pépite IA — partenaire pour primes optronique et "
                        "intégrateurs ISR cherchant des briques de détection "
                        "automatique temps réel.",
    },
    "CYBER_ENTERPRISE": {
        "products": ["plateformes cybersécurité",
                     "modules NDR", "outils de détection d'intrusion"],
        "technologies": ["cybersécurité"],
        "target_buyers": [_MOD, _CIVIL, _INDUS, _EXPORT],
        "why_template": "Compétiteur sur cyber défense — partenaire pour "
                        "SOC militaires et OIV cherchant des briques "
                        "souveraines.",
    },
    "CYBER_OT": {
        "products": ["plateforme cybersécurité OT",
                     "outils d'audit ICS / SCADA"],
        "technologies": ["cybersécurité OT / ICS"],
        "target_buyers": [_CIVIL, _MOD, _INDUS],
        "why_template": "Niche cybersécurité industrielle — partenaire pour "
                        "la protection d'infrastructures critiques et bases "
                        "militaires.",
    },
    "CYBER_RANGE": {
        "products": ["plateforme cyber range", "exercices Red/Blue Team",
                     "scénarios SIM-OT"],
        "technologies": ["cybersécurité", "simulation"],
        "target_buyers": [_MOD, _CIVIL, _INDUS],
        "why_template": "Compétiteur sur cyber range — cible pour les "
                        "armées européennes investissant dans la formation "
                        "cyber.",
    },
    "CRYPTO": {
        "products": ["solutions cryptographiques souveraines",
                     "messagerie chiffrée", "modules HSM"],
        "technologies": ["cryptographie"],
        "target_buyers": [_MOD, _CIVIL, _INDUS],
        "why_template": "Pépite cryptographique souveraine — partenaire pour "
                        "ANSSI / COMCYBER et SI militaires.",
    },
    "OSINT": {
        "products": ["plateforme OSINT", "outils de collecte automatisée",
                     "outils d'analyse renseignement"],
        "technologies": ["OSINT", "IA / NLP"],
        "target_buyers": [_MOD, _CIVIL],
        "why_template": "Pépite OSINT — partenaire pour services de "
                        "renseignement (DGSE, DRM) et lutte anti-criminalité.",
    },
    "SIMULATION_TRAINING": {
        "products": ["simulateurs d'entraînement", "modules de formation VR",
                     "plateformes scenarios tactiques"],
        "technologies": ["simulation", "réalité virtuelle"],
        "target_buyers": [_MOD, _CIVIL, _PRIME],
        "why_template": "Compétiteur simulation/formation — partenaire pour "
                        "les écoles militaires et centres de formation OPEX.",
    },
    "CLOUD_SOVEREIGN": {
        "products": ["plateforme cloud souverain", "infrastructure SecNumCloud"],
        "technologies": ["cloud souverain"],
        "target_buyers": [_MOD, _CIVIL, _INDUS],
        "why_template": "Pépite cloud souverain — alternative aux "
                        "hyperscalers US pour les SI militaires sensibles.",
    },

    # ===================================================================
    # PROTECTION / TEXTILE / NRBC / SHELTERS
    # ===================================================================
    "BALLISTIC_PROTECTION": {
        "products": ["plaques balistiques", "panneaux blindés composites",
                     "boucliers tactiques"],
        "technologies": ["matériaux composites balistiques"],
        "target_buyers": [_MOD, _CIVIL, _PRIME, _EXPORT],
        "why_template": "Compétiteur direct sur blindage composite — "
                        "fournisseur potentiel pour primes véhicules blindés.",
    },
    "HELMETS": {
        "products": ["casques de combat composites",
                     "casques tactiques", "modules de protection casque"],
        "technologies": ["matériaux composites balistiques"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Compétiteur direct sur casques de combat (vs "
                        "Mehler, Galvion, MKU) — équipementier majeur "
                        "des armées européennes.",
    },
    "BODY_ARMOR": {
        "products": ["gilets pare-balles", "plaques niveau IV",
                     "porte-équipement modulaire"],
        "technologies": ["matériaux balistiques (UHMWPE, céramiques)"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Compétiteur sur gilets pare-balles — fournisseur "
                        "potentiel pour primes équipement individuel.",
    },
    "TEXTILE_TECH": {
        "products": ["tissus camouflage IR", "tissus ignifugés",
                     "textiles balistiques"],
        "technologies": ["textiles techniques tactiques"],
        "target_buyers": [_MOD, _CIVIL, _PRIME, _EXPORT],
        "why_template": "Sous-traitant textiles techniques — fournisseur "
                        "potentiel pour habilleurs fantassins (Paul Boyé, "
                        "Marom Dolphin, Sioen).",
    },
    "BOOTS": {
        "products": ["bottes de combat", "chaussures tactiques",
                     "rangers militaires"],
        "technologies": ["textiles techniques tactiques"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Bottier militaire — fournisseur pour les armées "
                        "et police, partenaire pour exports régionaux.",
    },
    "GLOVES_TACTICAL": {
        "products": ["gants tactiques", "gants anti-coupure",
                     "gants haute température"],
        "technologies": ["textiles techniques tactiques"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Fournisseur de gants tactiques — partenaire pour "
                        "les armées et police en gants spécialisés.",
    },
    "BACKPACKS_LOAD_CARRYING": {
        "products": ["sacs à dos tactiques", "harnais porte-équipement",
                     "ensembles de portage"],
        "technologies": ["textiles techniques tactiques"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Équipementier sacs et harnais tactiques — "
                        "partenaire pour les armées et forces spéciales.",
    },
    "NRBC": {
        "products": ["masques NRBC", "tenues NRBC",
                     "détecteurs de gaz", "filtres NBC"],
        "technologies": ["filtration NRBC"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Compétiteur sur le marché NRBC — partenaire pour "
                        "les armées en quête de souveraineté NRBC.",
    },
    "DECONTAMINATION": {
        "products": ["systèmes de décontamination NRBC",
                     "agents de neutralisation chimique",
                     "abris de décontamination"],
        "technologies": ["décontamination NRBC"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Compétiteur décontamination NRBC — partenaire pour "
                        "les armées en projection extérieure et le SSA.",
    },
    "SHELTERS": {
        "products": ["shelters tactiques modulaires", "conteneurs durcis",
                     "abris déployables"],
        "technologies": ["shelters durcis"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Fournisseur de shelters tactiques — partenaire "
                        "pour les forces armées en OPEX et la protection "
                        "des bases déployées.",
    },
    "CAMPS_DEPLOYABLE": {
        "products": ["camps déployables", "tentes militaires modulaires",
                     "structures gonflables"],
        "technologies": ["shelters durcis"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Fournisseur de camps déployables — partenaire pour "
                        "les forces armées et l'aide humanitaire.",
    },
    "PACKAGING": {
        "products": ["caisses techniques durcies", "conteneurs étanches",
                     "emballages personnalisés mil-spec"],
        "technologies": ["conteneurs étanches"],
        "target_buyers": [_MOD, _PRIME, _INDUS, _EXPORT],
        "why_template": "Fournisseur d'emballage technique — partenaire "
                        "pour la projection de matériel sensible (avionique, "
                        "optronique, munitions).",
    },

    # ===================================================================
    # ENGINES / TRANSMISSION / HYDRAULIC / SUSPENSIONS
    # ===================================================================
    "ENGINES_DIESEL": {
        "products": ["moteurs diesel militaires",
                     "groupes électrogènes tactiques",
                     "ensembles de propulsion"],
        "technologies": ["moteurs diesel haute puissance"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Fournisseur Tier-1 de motorisation diesel pour "
                        "véhicules blindés (LAV, Stryker, Boxer) — partenaire "
                        "stratégique des primes véhicules.",
    },
    "ENGINES_AERO": {
        "products": ["moteurs aéronautiques militaires",
                     "ensembles de propulsion hélico",
                     "modules turbomachinerie"],
        "technologies": ["moteurs aéronautiques"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur sur moteurs aéronautiques militaires — "
                        "partenaire pour les programmes futurs (FCAS, "
                        "Eurofighter, NH90).",
    },
    "TRANSMISSIONS": {
        "products": ["transmissions militaires",
                     "boîtes de vitesses pour blindés",
                     "réducteurs précision"],
        "technologies": ["mécanique de transmission"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Fournisseur Tier-1 transmissions militaires — "
                        "partenaire pour primes blindés et naval.",
    },
    "HYDRAULICS": {
        "products": ["amortisseurs hydrauliques", "vérins militaires",
                     "modules hydrauliques sur-mesure"],
        "technologies": ["hydraulique haute pression"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Fournisseur Tier-1 hydraulique militaire — "
                        "partenaire pour primes véhicules blindés et "
                        "engins du génie.",
    },
    "TRACK_SHOES": {
        "products": ["chenilles métalliques", "trains de roulement",
                     "patins chenille"],
        "technologies": ["métallurgie chenilles"],
        "target_buyers": [_PRIME, _MOD, _EXPORT],
        "why_template": "Sous-traitant Tier-1 chenilles pour primes "
                        "blindés (Hyundai Rotem, Hanwha, KMW).",
    },
    "TIRES_RUNFLAT": {
        "products": ["pneus run-flat blindés", "pneus militaires",
                     "ensembles pneumatiques tactiques"],
        "technologies": ["pneumatiques run-flat"],
        "target_buyers": [_PRIME, _MOD, _EXPORT],
        "why_template": "Fournisseur Tier-1 de pneus militaires — "
                        "partenaire incontournable pour primes véhicules "
                        "blindés et tactiques.",
    },
    "SEATING_ARMORED": {
        "products": ["sièges anti-mine blindés",
                     "sièges suspendus haute performance",
                     "ergonomie cabine blindée"],
        "technologies": ["sièges anti-mines"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Fournisseur Tier-1 sièges anti-mines pour primes "
                        "véhicules blindés (Boxer, Leopard, Lynx).",
    },
    "SUSPENSIONS": {
        "products": ["suspensions hydropneumatiques",
                     "amortisseurs blindés",
                     "ensembles de suspension chars"],
        "technologies": ["amortisseurs militaires"],
        "target_buyers": [_PRIME, _MOD, _EXPORT],
        "why_template": "Fournisseur Tier-1 suspensions chars — partenaire "
                        "pour primes véhicules blindés.",
    },
    "FIRE_SUPPRESSION": {
        "products": ["systèmes d'extinction véhicules",
                     "modules anti-incendie compartimentés"],
        "technologies": ["protection passive"],
        "target_buyers": [_PRIME, _MOD, _EXPORT],
        "why_template": "Fournisseur de systèmes d'extinction — partenaire "
                        "pour primes véhicules blindés cherchant à durcir "
                        "leurs plateformes.",
    },
    "RESCUE_HYDRAULIC": {
        "products": ["cisailles hydrauliques", "écarteurs",
                     "vérins de secours", "groupes hydrauliques mobiles"],
        "technologies": ["hydraulique haute pression"],
        "target_buyers": [_CIVIL, _MOD, _EXPORT],
        "why_template": "Compétiteur outils de secours hydrauliques (vs "
                        "Holmatro, Lukas) — fournisseur pour pompiers "
                        "militaires et secours de combat.",
    },

    # ===================================================================
    # RUBBER / MATERIALS / COMPOSITES / CERAMICS / MAGNETS
    # ===================================================================
    "COMPOSITES": {
        "products": ["matériaux composites carbone",
                     "structures composites aéronautiques",
                     "pré-imprégnés"],
        "technologies": ["matériaux composites", "composites carbone"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Fournisseur Tier-1 composites — partenaire pour "
                        "primes aéro, hélico et naval (structures, capots, "
                        "radomes).",
    },
    "CERAMICS_BALLISTIC": {
        "products": ["plaques céramiques niveau IV",
                     "panneaux composites céramique-UHMWPE"],
        "technologies": ["céramiques techniques"],
        "target_buyers": [_PRIME, _INDUS, _MOD, _EXPORT],
        "why_template": "Fournisseur Tier-1 céramiques balistiques — "
                        "concurrent direct de CoorsTek et CeramTec.",
    },
    "STEEL_ARMOR": {
        "products": ["aciers blindés", "tôles haute dureté", "aciers HLE"],
        "technologies": ["aciers blindage"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Métallurgiste Tier-1 d'aciers blindés — concurrent "
                        "direct sur le marché des véhicules blindés européens.",
    },
    "FASTENERS_AERO": {
        "products": ["visserie aéronautique", "fixations auto-freinantes",
                     "boulonnerie spéciale"],
        "technologies": ["fixations haute performance"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Sous-traitant Tier-1 visserie aéro — fournisseur "
                        "stratégique pour Safran, GE Aviation, Pratt & Whitney.",
    },
    "MAGNETS": {
        "products": ["aimants permanents Néodyme/Samarium",
                     "modules magnétiques sur-mesure"],
        "technologies": ["magnétisme appliqué"],
        "target_buyers": [_PRIME, _INDUS],
        "why_template": "Fournisseur d'aimants permanents — partenaire pour "
                        "servomoteurs militaires, capteurs et moteurs "
                        "électriques durcis.",
    },
    "COATINGS_SURFACE": {
        "products": ["revêtements PVD", "traitements thermiques aéronautiques",
                     "revêtements anti-corrosion"],
        "technologies": ["revêtements de surface"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Sous-traitant Tier-1 traitements de surface — "
                        "partenaire pour les chaînes de production aéro/"
                        "défense (Safran, Aubert & Duval).",
    },

    # ===================================================================
    # COMMUNICATIONS / NETWORKS
    # ===================================================================
    "ANTENNAS": {
        "products": ["antennes RF durcies", "antennes phased-array militaires"],
        "technologies": ["antennes durcies"],
        "target_buyers": [_PRIME, _MOD, _EXPORT],
        "why_template": "Fournisseur Tier-1 d'antennes durcies — partenaire "
                        "pour primes véhicules et naval.",
    },
    "FIBER_OPTICS": {
        "products": ["fibres optiques spécialisées",
                     "câbles fibre durcis", "capteurs DAS distribués"],
        "technologies": ["fibres optiques", "photonique"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Fournisseur Tier-1 fibres optiques — partenaire "
                        "pour gyrolasers, optronique et monitoring "
                        "d'infrastructures.",
    },
    "5G_PRIVATE": {
        "products": ["stations 5G privées", "core mobile défense",
                     "transport optique militaire"],
        "technologies": ["5G / sans-fil"],
        "target_buyers": [_MOD, _CIVIL, _INDUS],
        "why_template": "Fournisseur d'infrastructure 5G/P-LTE pour réseaux "
                        "militaires souverains — partenaire stratégique "
                        "des programmes de comms tactiques.",
    },
    "SATCOM": {
        "products": ["terminaux VSAT déployables",
                     "modems satcom durcis", "modules SatCom-on-the-Move"],
        "technologies": ["communications satellite"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Compétiteur satcom (vs Hughes, ViaSat) — fournisseur "
                        "pour intégrateurs C4ISR et OPEX.",
    },
    "STREAMING_VIDEO": {
        "products": ["encodeurs vidéo SRT", "décodeurs durcis",
                     "modules streaming basse latence"],
        "technologies": ["streaming vidéo"],
        "target_buyers": [_MOD, _CIVIL, _PRIME],
        "why_template": "Compétiteur sur le streaming vidéo militaire — "
                        "fournisseur pour intégrateurs C4ISR.",
    },
    "C4ISR": {
        "products": ["plateformes C2 / C4ISR",
                     "modules de fusion de capteurs",
                     "outils de visualisation tactique"],
        "technologies": ["IA / data fusion"],
        "target_buyers": [_MOD, _PRIME, _EXPORT],
        "why_template": "Compétiteur C4ISR — partenaire pour primes "
                        "intégrateurs cherchant des briques métier (fusion, "
                        "visualisation 3D, planning).",
    },
    "TSCM_COUNTERESPIONAGE": {
        "products": ["détecteurs OSCOR",
                     "détecteurs de transmissions cachées",
                     "outils CMA contre-espionnage"],
        "technologies": ["RF / micro-ondes"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Référence TSCM — fournisseur pour services de "
                        "renseignement (CIA, MI6, DGSE).",
    },

    # ===================================================================
    # NAVIGATION / GEOSPATIAL
    # ===================================================================
    "INERTIAL_NAVIGATION": {
        "products": ["centrales inertielles",
                     "AHRS pour drones", "INS-D haute précision"],
        "technologies": ["navigation inertielle", "MEMS gyroscopes"],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Compétiteur sur navigation inertielle (vs Honeywell, "
                        "iXblue, Northrop) — fournisseur pour drones, "
                        "missiles guidés et plateformes UGV.",
    },
    "GEOSPATIAL": {
        "products": ["plateformes SIG défense",
                     "imagerie satellite analysée",
                     "modèles 3D terrain"],
        "technologies": ["imagerie satellite", "SIG / cartographie tactique"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Niche géospatial défense — partenaire pour services "
                        "de renseignement géospatial et planification tactique.",
    },
    "GNSS_RTK": {
        "products": ["abonnement RTK haute précision",
                     "stations de base GNSS"],
        "technologies": ["GNSS / GPS"],
        "target_buyers": [_MOD, _INDUS],
        "why_template": "Opérateur GNSS RTK — partenaire pour drones "
                        "autonomes et opérations de précision.",
    },

    # ===================================================================
    # MEDICAL / RATIONS
    # ===================================================================
    "MEDICAL_COUNTERMEASURES": {
        "products": ["antidotes NRBC", "kits d'urgence chimique",
                     "médicaments de défense biologique"],
        "technologies": ["pharmaceutique défense"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Pharmaceutique spécialisé contre-mesures médicales — "
                        "partenaire pour le SSA et stockages stratégiques NRBC.",
    },
    "RATIONS_MILITARY": {
        "products": ["rations MRE 24h",
                     "kits alimentaires de projection"],
        "technologies": ["lyophilisation alimentaire"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Fournisseur MREs militaires — partenaire pour "
                        "exports et opérations humanitaires.",
    },
    "WATER_PURIFICATION": {
        "products": ["stations de purification d'eau mobiles",
                     "kits de filtration tactique"],
        "technologies": ["purification d'eau tactique"],
        "target_buyers": [_MOD, _CIVIL, _EXPORT],
        "why_template": "Fournisseur de purification d'eau militaire — "
                        "partenaire pour OPEX et aide humanitaire.",
    },

    # ===================================================================
    # CONSULTING / SERVICES / TESTING
    # ===================================================================
    "CONSULTING_OBSOLESCENCE": {
        "products": [],
        "technologies": [],
        "target_buyers": [_PRIME, _INDUS, _MOD],
        "why_template": "Cabinet conseil obsolescence — partenaire pour "
                        "primes cherchant à pérenniser leurs programmes "
                        "long-cycle.",
    },
    "QUALIFICATION_TESTING": {
        "products": [],
        "technologies": ["essais environnementaux", "CEM militaire"],
        "target_buyers": [_PRIME, _INDUS, _MOD],
        "why_template": "Centre d'essais — partenaire pour qualifications "
                        "AQAP, STANAG et CEM militaire des primes.",
    },
    "ENGINEERING_SERVICES": {
        "products": [],
        "technologies": [],
        "target_buyers": [_PRIME, _INDUS],
        "why_template": "Bureau d'études défense — partenaire pour primes "
                        "cherchant des prestations d'ingénierie agile.",
    },

    # ===================================================================
    # KIND-BASED PROFILES (non-industrial)
    # The ``products`` lists for these kinds describe the *function /
    # role* the entity plays — not physical goods. Filling them keeps
    # the products cell informative even for chambers, ministries and
    # media outlets that don't sell anything tangible.
    # ===================================================================
    "KIND_INSTITUTIONAL": {
        "products": [
            "représentation des industriels défense",
            "accompagnement export PME défense",
            "mise en relation B2B défense",
            "montage de projets EDF / Horizon Europe",
        ],
        "technologies": [],
        "target_buyers": [_INDUS, _EXPORT],
        "why_template": "Porte d'entrée institutionnelle vers le tissu "
                        "industriel défense — utile pour identifier sous-"
                        "traitants, partenaires R&D et opportunités EDF.",
    },
    "KIND_GOVERNMENT": {
        "products": [
            "achat public défense",
            "politique industrielle militaire",
            "passation de marchés publics défense",
            "coopération inter-États défense",
        ],
        "technologies": [],
        "target_buyers": [_MOD, _EXPORT],
        "why_template": "Acheteur public défense — porte d'entrée pour "
                        "fournisseurs cherchant à pénétrer le marché public "
                        "militaire local.",
    },
    "KIND_MEDIA": {
        "products": [
            "publication spécialisée défense / sécurité",
            "couverture salons défense",
            "newsletters professionnelles",
            "études et rapports sectoriels",
        ],
        "technologies": [],
        "target_buyers": [_INDUS],
        "why_template": "Média défense de référence — vitrine pour les "
                        "industriels souhaitant gagner en visibilité auprès "
                        "des armées et primes.",
    },
    "KIND_EVENT": {
        "products": [
            "organisation de salons défense",
            "conférences sectorielles défense",
            "matchmaking B2B sur salons",
            "stands clé-en-main pour exposants",
        ],
        "technologies": [],
        "target_buyers": [_INDUS],
        "why_template": "Plateforme événementielle défense — partenaire pour "
                        "la présence sur les salons sectoriels.",
    },
    "KIND_FINANCIAL": {
        "products": [
            "financement export défense",
            "garanties bancaires & lettres de crédit",
            "assurance industrielle défense",
            "capital-risque deep-tech défense",
        ],
        "technologies": [],
        "target_buyers": [_INDUS, _EXPORT],
        "why_template": "Banque ou assureur défense — partenaire pour "
                        "structurer le financement export, garanties "
                        "bancaires et risques industriels.",
    },
    "KIND_ACADEMIC": {
        "products": [
            "formation d'ingénieurs défense",
            "recherche académique appliquée",
            "thèses CIFRE",
            "projets collaboratifs EDF / Horizon Europe",
        ],
        "technologies": [],
        "target_buyers": [_MOD, _INDUS],
        "why_template": "Université ou école — partenaire R&D sur projets "
                        "EDF / Horizon Europe et formation des futurs cadres "
                        "défense.",
    },
    "KIND_RESEARCH": {
        "products": [
            "R&D sur contrat défense",
            "essais matériaux et qualification",
            "brevets duaux civils-militaires",
            "transfert de technologie",
        ],
        "technologies": [],
        "target_buyers": [_MOD, _INDUS],
        "why_template": "Centre de R&D défense — partenaire incontournable "
                        "pour primes et PME en développement de nouveaux "
                        "alliages, procédés et technologies de rupture.",
    },
    "KIND_CONSULTING": {
        "products": [
            "conseil stratégique défense",
            "due-diligence M&A défense",
            "études de marché défense",
            "intelligence économique",
        ],
        "technologies": [],
        "target_buyers": [_INDUS, _MOD],
        "why_template": "Cabinet conseil défense — partenaire pour la "
                        "transformation, due-diligence M&A et études "
                        "de marché.",
    },
    "KIND_LOGISTICS": {
        "products": [
            "transit douanier export défense",
            "fret aérien et maritime militaire",
            "logistique projet OPEX",
            "stockage sécurisé matériel sensible",
        ],
        "technologies": [],
        "target_buyers": [_INDUS, _EXPORT],
        "why_template": "Logisticien spécialisé export défense — partenaire "
                        "pour structurer les flux logistiques OPEX et grands "
                        "contrats internationaux.",
    },
    "KIND_DISTRIBUTOR": {
        "products": [
            "distribution composants électroniques durcis",
            "distribution de matériel militaire",
            "représentation de marques étrangères",
            "ingénierie d'application & sourcing BOM",
        ],
        "technologies": [],
        "target_buyers": [_PRIME, _INDUS, _EXPORT],
        "why_template": "Distributeur Tier-1 — partenaire pour "
                        "l'approvisionnement BOM des primes défense.",
    },
}


KIND_TO_PROFILE: dict[str, str] = {
    "INSTITUTIONAL": "KIND_INSTITUTIONAL",
    "GOVERNMENT": "KIND_GOVERNMENT",
    "MEDIA": "KIND_MEDIA",
    "EVENT_ORGANIZER": "KIND_EVENT",
    "FINANCIAL": "KIND_FINANCIAL",
    "ACADEMIC": "KIND_ACADEMIC",
    "RESEARCH": "KIND_RESEARCH",
    "CONSULTING": "KIND_CONSULTING",
    "LOGISTICS": "KIND_LOGISTICS",
    "DISTRIBUTOR": "KIND_DISTRIBUTOR",
}


__all__ = ["PROFILES", "KIND_TO_PROFILE", "ActivityProfile"]
