"""Canonical-taxonomy mapper for the Eurosatory targeting profiles.

What this module does
---------------------
Each ``EurosatoryTargetingProfile`` carries free-text product / service /
technology labels. Those labels are commercially valuable (e.g. "missile
air-air Meteor", "viseur ACOG 4x32") but **terrible for spreadsheet
filtering** : a buyer who wants "all the missile makers" cannot find them
that way.

This module adds, for every record, three parallel **canonical category**
fields (``products_categories``, ``services_categories``,
``technologies_categories``) without losing the original specific labels.
Categories are keyword-driven : a single label can map to one or two
canonical categories, never zero (worst case → ``Autre``).

Design choices
--------------
- **Categories live alongside specifics**, not replacing them. The
  `products` cell still shows "missile air-air Meteor" so the commercial
  reads what the company actually makes ; the new
  `products_categories` column says "Missiles & armements guidés", which
  is what the Excel filter operates on.
- **Categories are stable English-free French**, designed for column
  filtering (no slashes, no parentheses, no version numbers).
- **One label can map to multiple categories** when ambiguity is real
  (e.g. "drone armé" → ["Drones aériens", "Armes & munitions"]).
- Free-text labels we can't classify fall under the catch-all
  ``"Autre — à qualifier"`` (rare, < 1 %).

How to extend
-------------
Add a (regex pattern, list-of-categories) tuple to the relevant
``*_RULES`` list. Order does not matter — every matching rule
contributes its categories. Run :
    .venv/bin/python scripts/normalize_categories.py
to refresh ``data/exports/targeting_profiles_final.json``.
"""
from __future__ import annotations

import re
from typing import Iterable

# ---------------------------------------------------------------------------
# Product categories — ~60 commercial sub-segments useful for buyers.
# ---------------------------------------------------------------------------

PRODUCT_CATEGORIES: tuple[str, ...] = (
    # Plateformes terrestres
    "Véhicules blindés",
    "Véhicules tactiques (non blindés)",
    "Camions militaires & logistique",
    "Engins du génie militaire",
    "Ambulances & véhicules sanitaires",
    "Robots terrestres (UGV / EOD)",
    # Plateformes aériennes & espace
    "Avions & hélicoptères militaires",
    "Drones aériens (UAV)",
    "Drones FPV & munitions rôdeuses",
    "Systèmes anti-drone (C-UAS)",
    "Satellites & CubeSats",
    "Terminaux & antennes spatiales",
    # Plateformes navales
    "Navires & sous-systèmes navals",
    "Sonars & systèmes ASM",
    # Armement
    "Armes légères & accessoires",
    "Armes lourdes & systèmes de tir",
    "Munitions petit/moyen calibre",
    "Munitions gros calibre & obus",
    "Missiles & armements guidés",
    "Roquettes & lance-roquettes",
    "Explosifs & matériaux énergétiques",
    "Tourelles téléopérées (RWS)",
    "Armes non-létales",
    "Pyrotechnie & artifices",
    # Capteurs & électronique défense
    "Optronique & viseurs (EO/IR)",
    "Radars & traitement signal",
    "Capteurs embarqués (généraux)",
    "Capteurs sismiques & acoustiques",
    "Détecteurs IR refroidis & non-refroidis",
    "Systèmes de guerre électronique (EW)",
    "Systèmes IFF & identification",
    # Communications & C4ISR
    "Radios tactiques & SDR",
    "Antennes & infrastructures RF",
    "Réseaux militaires (5G/P-LTE/MANET)",
    "Systèmes C2 & C4ISR",
    "Streaming vidéo & data tactiques",
    # Cyber & logiciels
    "Plateformes IA / vision défense",
    "Cybersécurité (logiciels & appliances)",
    "Logiciels de simulation & cyber range",
    "Logiciels métier défense (autres)",
    # Soldat connecté & protection
    "Équipement du fantassin (général)",
    "Casques de combat",
    "Gilets & plaques pare-balles",
    "Vitrages blindés",
    "Vision nocturne & jumelles",
    "Optiques d'armes (viseurs)",
    "NRBC (masques, tenues, détecteurs)",
    "Textiles techniques tactiques",
    # Énergie & propulsion
    "Batteries & packs énergétiques",
    "Piles à combustible & H2",
    "Moteurs diesel / propulsion lourde",
    "Moteurs aéronautiques",
    "Groupes électrogènes & énergie tactique",
    # Simulation & formation
    "Simulateurs d'entraînement & VR/AR",
    "Caméras tactiques & POV",
    # Sous-systèmes & matériaux
    "Composants électroniques & cartes",
    "Connectique & câblage durcis",
    "Pièces mécaniques & usinage",
    "Mécanique de transmission",
    "Hydraulique & motion control",
    "Matériaux composites & blindage",
    "Aciers & métallurgie spéciale",
    "Céramiques techniques & balistiques",
    "Fixations & visserie aéronautique",
    "Alimentations & convertisseurs durcis",
    "Boîtiers & châssis durcis",
    "Avionique & instruments embarqués",
    "Servomoteurs & motion control précision",
    # Logistique & infrastructure
    "Conteneurs, malles, shelters",
    "Logistique militaire & transport",
    "Mâts, signalisation & balisage",
    "Stations de purification d'eau & infra",
    "Sièges & ergonomie cabine",
    "Lubrifiants & fluides",
    # Institutionnel / non-industriel — services & rôles
    "Représentation institutionnelle (cluster, fédération, chambre)",
    "Achat public défense & politique industrielle",
    "Médias & publications défense",
    "Organisation de salons & conférences défense",
    "Financement, banque & assurance défense",
    "R&D académique & laboratoires",
    "Conseil stratégique & due-diligence M&A",
    "Logistique export défense & transit",
    "Distribution composants & représentation de marques",
    # Catch-all
    "Autre — à qualifier",
)


# ---------------------------------------------------------------------------
# Service categories — ~25 commercially-meaningful service buckets.
# ---------------------------------------------------------------------------

SERVICE_CATEGORIES: tuple[str, ...] = (
    "MCO / MRO",
    "Intégration de systèmes",
    "Ingénierie & conseil",
    "Formation & entraînement",
    "Distribution & représentation",
    "Sous-traitance industrielle",
    "Certification & tests",
    "Services cloud & data",
    "Support opérationnel & OPEX",
    "Modernisation de flottes",
    "Logistique militaire & transit",
    "Audit & cybersécurité OT",
    "Organisation de salons & événements",
    "Représentation institutionnelle (cluster/fédération)",
    "Achat public & marchés défense",
    "Financement export & banque défense",
    "R&D sur contrat",
    "Métrologie & essais matériaux",
    "Approvisionnement composants & sourcing",
    "Marketing & développement commercial",
    "Documentation technique & ILS",
    "Conseil en gestion de programme",
    "Promotion territoriale & investissement",
    "Autre — à qualifier",
)


# ---------------------------------------------------------------------------
# Technology categories — ~30 buckets driven by what buyers query for.
# ---------------------------------------------------------------------------

TECHNOLOGY_CATEGORIES: tuple[str, ...] = (
    "IA / machine learning",
    "Vision par ordinateur",
    "Robotique & autonomie",
    "Edge computing & GPU embarqué",
    "Cloud & cloud souverain",
    "Big data & analytics",
    "Cybersécurité",
    "Cybersécurité OT / ICS",
    "Cryptographie",
    "Communications RF / micro-ondes",
    "Radar (AESA, monopulse, …)",
    "Lidar / Laser",
    "Imagerie infrarouge",
    "Imagerie SWIR / multispectrale",
    "Optique de précision",
    "5G / réseaux radio mobiles",
    "GNSS / navigation satellite",
    "Navigation inertielle",
    "Simulation & VR/AR",
    "Matériaux composites",
    "Matériaux balistiques (UHMWPE, céramiques)",
    "Matériaux énergétiques",
    "Métallurgie spéciale (alliages, blindages)",
    "Fabrication additive",
    "Microélectronique & semiconducteurs durcis",
    "MEMS & capteurs miniaturisés",
    "Capteurs IoT industriels",
    "Énergie portative & piles à combustible",
    "Filtration NRBC",
    "Hydraulique haute performance",
    "Photonique & fibres optiques",
    "Guerre électronique (EW)",
    "Vol autonome & GPS-denied",
    "Autre — à qualifier",
)


# ===========================================================================
# Mapping rules : (regex_pattern_against_lower_label, list_of_categories)
# ===========================================================================

# Helper : a rule is `(pattern, categories)`. We don't anchor patterns —
# they match anywhere in the lowercased label.

PRODUCT_RULES: tuple[tuple[re.Pattern, list[str]], ...] = tuple(
    (re.compile(p, re.I), cats) for p, cats in [
        # ================================================================
        # FALLBACKS — generic French labels emitted by the rule-based
        # transformer. These fire FIRST and provide a sensible default
        # category when the more-specific rules below don't match.
        # Specific rules below can ADD additional categories on top.
        # ================================================================
        (r"^v[eé]hicules? militaires?$", ["Véhicules tactiques (non blindés)"]),
        (r"^munitions?$", ["Munitions petit/moyen calibre"]),
        (r"^armes?$", ["Armes légères & accessoires"]),
        (r"^moteurs? */ *propulsion$", ["Moteurs aéronautiques"]),
        (r"^r[eé]seaux de communication$", ["Réseaux militaires (5G/P-LTE/MANET)"]),
        (r"^mat[eé]riaux composites$", ["Matériaux composites & blindage"]),
        (r"^protection balistique$", ["Gilets & plaques pare-balles"]),
        (r"^syst[eè]mes embarqu[eé]s$", ["Composants électroniques & cartes"]),
        (r"^simulateurs? d'?entra[iî]nement$", ["Simulateurs d'entraînement & VR/AR"]),
        (r"^syst[eè]mes satellitaires$", ["Satellites & CubeSats"]),
        (r"^batteries? */ *sources d'[eé]nergie$", ["Batteries & packs énergétiques"]),
        (r"^[eé]quipement logistique$", ["Logistique militaire & transport"]),
        (r"^drones? a[eé]riens?$", ["Drones aériens (UAV)"]),
        (r"^capteurs? embarqu[eé]s?$", ["Capteurs embarqués (généraux)"]),
        (r"^robots? terrestres? \(ugv\)$", ["Robots terrestres (UGV / EOD)"]),
        (r"^logiciels? m[eé]tier d[eé]fense$", ["Logiciels métier défense (autres)"]),
        (r"^syst[eè]mes? optroniques?$", ["Optronique & viseurs (EO/IR)"]),
        (r"^syst[eè]mes? anti[- ]?drones?$", ["Systèmes anti-drone (C-UAS)"]),
        (r"^syst[eè]mes? navals?$", ["Navires & sous-systèmes navals"]),
        (r"^radios? tactiques?$", ["Radios tactiques & SDR"]),
        (r"^[eé]quipement (du )?fantassins?$", ["Équipement du fantassin (général)"]),
        (r"^composants? [eé]lectroniques?$", ["Composants électroniques & cartes"]),
        (r"^pi[eè]ces? m[eé]caniques?$", ["Pièces mécaniques & usinage"]),
        (r"^radars?$", ["Radars & traitement signal"]),
        (r"^[eé]quipement nrbc$", ["NRBC (masques, tenues, détecteurs)"]),
        (r"^solutions? cyber$", ["Cybersécurité (logiciels & appliances)"]),
        (r"^plateformes? ia$", ["Plateformes IA / vision défense"]),
        # === Patterns institutionnels (non-industriels) ===
        (r"\b(représentation des industriels|cluster|fédération|chambre|"
         r"accompagnement export PME|mise en relation B2B)\b",
         ["Représentation institutionnelle (cluster, fédération, chambre)"]),
        (r"\b(achat public d[eé]fense|politique industrielle militaire|"
         r"passation de marchés|coopération inter-États|coopération inter-?"
         r"etats)\b",
         ["Achat public défense & politique industrielle"]),
        (r"\b(publication spécialisée|couverture salons|newsletters? professionnelles?|"
         r"études et rapports sectoriels)\b",
         ["Médias & publications défense"]),
        (r"\b(organisation de salons|conférences sectorielles|"
         r"matchmaking B2B sur salons|stands clé-?en-?main)\b",
         ["Organisation de salons & conférences défense"]),
        (r"\b(financement export|garanties? bancaires?|assurance industrielle|"
         r"capital-risque|capital risque|venture)\b.*\b(défense|defense|"
         r"d[eé]fense)?",
         ["Financement, banque & assurance défense"]),
        (r"\b(formation d['e]?ingénieurs|recherche académique|thèses?\s*CIFRE|"
         r"projets collaboratifs (EDF|Horizon Europe))\b",
         ["R&D académique & laboratoires"]),
        (r"\b(R&D sur contrat|essais matériaux et qualification|brevets duaux|"
         r"transfert de technologie)\b",
         ["R&D académique & laboratoires"]),
        (r"\b(conseil stratégique|due-?diligence M&A|études de marché|"
         r"intelligence économique)\b",
         ["Conseil stratégique & due-diligence M&A"]),
        (r"\b(transit douanier|fret aérien et maritime|logistique projet|"
         r"stockage sécurisé)\b",
         ["Logistique export défense & transit"]),
        (r"\b(distribution composants|distribution de matériel militaire|"
         r"représentation de marques étrangères|ingénierie d['e]?application|"
         r"sourcing BOM)\b",
         ["Distribution composants & représentation de marques"]),
        # Patterns for manual-fiche specific labels that we want classified
        (r"plateforme.*\b(ia|ai|airudit|adwäiseo|adwaiseo)", ["Plateformes IA / vision défense"]),
        (r"modules? ia\b|modules? ai\b|module.*d[eé]tection automatique", ["Plateformes IA / vision défense"]),
        (r"logiciel.*(planification|propagation|couverture|fusion|c4isr|tactique)|outils?.*(analyse|fusion|d[eé]tection)",
         ["Logiciels métier défense (autres)"]),
        (r"modules? de communication tactique|module.*comms?", ["Réseaux militaires (5G/P-LTE/MANET)"]),
        (r"calculateur.*(embarqu|durci|vol)", ["Avionique & instruments embarqués"]),
        (r"syst[eè]me.*gestion.*[eé]nergie|gestion d['\u2019]?[eé]nergie", ["Groupes électrogènes & énergie tactique"]),
        (r"remorque.*(secours|sanitaire|d[eé]ployable)", ["Ambulances & véhicules sanitaires"]),
        (r"modules? sanitaires?|module.*soin", ["Ambulances & véhicules sanitaires"]),
        (r"cubesat|nano[- ]?satellite", ["Satellites & CubeSats"]),
        # ================================================================
        # SPECIFIC RULES — match more granular labels (manual fiches use
        # those ; e.g. "viseur ACOG 4x32", "drone armé Bayraktar TB2"…).
        # ================================================================
        # Plateformes terrestres
        (r"v[eé]hicule.*(blind[eé]|arm[oa]r|tactique|military|tank|char|leopard|panther|abrams|stryker|jltv|m-?atv|amv|piranha|boxer|ascod|pizarro|wahash|nimr|amphibie)",
         ["Véhicules blindés"]),
        (r"\b(char|tank|blind[eé])\b", ["Véhicules blindés"]),
        (r"v[eé]hicule.*(4x4|6x6|8x8|tout[- ]terrain|tactique|patrol|recon|tactical)",
         ["Véhicules tactiques (non blindés)"]),
        (r"camion.*(militaire|tactique|logist|hemtt|unimog|zetros|mtvr|hx)", ["Camions militaires & logistique"]),
        (r"truck", ["Camions militaires & logistique"]),
        (r"unimog|zetros", ["Camions militaires & logistique"]),
        (r"engin.*(g[eé]nie|d[eé]molition)|excavateur.*blind|chargeur.*blind|d[eé]neigeuse",
         ["Engins du génie militaire"]),
        (r"ambulance|sanitaire.*(militaire|tactique)|module.*soin", ["Ambulances & véhicules sanitaires"]),
        (r"\bugv\b|robot.*(terrestre|sol|eod|d[eé]minage)", ["Robots terrestres (UGV / EOD)"]),
        # Aérien / espace
        (r"avion.*(combat|attaque|chasse|transport|militaire|leger)|h[eé]licopt[eè]re|helicopter|eurofighter|rafale|f-?35|h145|tigre|nh90|c295|a400m|caracal",
         ["Avions & hélicoptères militaires"]),
        (r"drone(?!.*kamikaze|.*fpv|.*r[oô]de|.*loiter)", ["Drones aériens (UAV)"]),
        (r"\buav\b", ["Drones aériens (UAV)"]),
        (r"isr.*(aircraft|plateforme|platform)|aeronef.*surveillance", ["Avions & hélicoptères militaires", "Drones aériens (UAV)"]),
        (r"drone.*(fpv|kamikaze|loiter|r[oô]de|attaque)|munition.*r[oô]de|loitering munition", ["Drones FPV & munitions rôdeuses"]),
        (r"anti[- ]?drone|c[- ]?uas|c-uav|counter[- ]?drone", ["Systèmes anti-drone (C-UAS)"]),
        (r"satellite|cubesat|nano[- ]?satellite|leo\b|kuiper", ["Satellites & CubeSats"]),
        (r"terminal.*satellite|station.*satellite|antenne.*satellite|antennes? phased[- ]?array",
         ["Terminaux & antennes spatiales"]),
        # Naval
        (r"navire|sous-marin|fr[eé]gate|naval|marin", ["Navires & sous-systèmes navals"]),
        (r"sonar", ["Sonars & systèmes ASM"]),
        # Armement
        (r"fusil|carabine|pistolet|mitrailleuse|ar-?15|aug|ssg|qbz|aks?-|akkm|ak[1-9]+|m4|m16|hk|sig|glock|beretta",
         ["Armes légères & accessoires"]),
        (r"\barme[s]? l[eé]g[eè]re", ["Armes légères & accessoires"]),
        (r"obusier|canon (155|105|120|125|130)|piece.*artillerie|howitzer|caesar|panzerhaubitze|pzh", ["Armes lourdes & systèmes de tir"]),
        (r"munition (5,?56|7,?62|9 ?mm|12,?7|petit calibre|moyen calibre)", ["Munitions petit/moyen calibre"]),
        (r"munition (gros calibre|105|120|125|155|130|cartouche.*155)", ["Munitions gros calibre & obus"]),
        (r"\bobus\b", ["Munitions gros calibre & obus"]),
        (r"missile|meteor|aster|exocet|scalp|patriot|stinger|javelin|himars|iron dome|pac-?3|hellfire|amraam",
         ["Missiles & armements guidés"]),
        (r"munition.*guid[eé]e", ["Missiles & armements guidés"]),
        (r"roquette|lance-?roquette|fz275|fz70", ["Roquettes & lance-roquettes"]),
        (r"explosif|propergol|propul.*solide|d[eé]tonateur|amor[cç]age|fus[eé]e.*munition|rdx|hmx|tnt",
         ["Explosifs & matériaux énergétiques"]),
        (r"tourelle.*(t[eé]l[eé]op|rws|stabilis[eé])", ["Tourelles téléopérées (RWS)"]),
        (r"non[- ]?l[eé]tale|non[- ]?lethal|gaz lacrymog|grenade.*fum", ["Armes non-létales"]),
        (r"pyrotechni|artifice|fumig[eè]ne", ["Pyrotechnie & artifices"]),
        # Capteurs & électronique
        (r"optronique|optronic|jumelle.*(thermi|infrarouge)|viseur.*(thermique|infrarouge|optique|holographique|red dot|reflex|ir)|nacelle.*(eo|ir|optronique)|sophie|paseo|jim |acog|rmr|aimpoint|trijicon",
         ["Optronique & viseurs (EO/IR)"]),
        (r"radar(?!.*surveillance|.*ground)|radome|aesa|monopulse|kalkan|ground master|el/?m-2084", ["Radars & traitement signal"]),
        (r"radar.*(surveillance|ground)", ["Radars & traitement signal"]),
        (r"capteur.*(embarqu|imu|sismique|acoustique|gunshot|magn[eé]tique)|\bimu\b", ["Capteurs embarqués (généraux)"]),
        (r"capteur.*sismique|gunshot|capteur.*acoustique", ["Capteurs sismiques & acoustiques"]),
        (r"d[eé]tecteur.*(ir|infrarouge|mwir|lwir|swir)|stirling|cryocool|t2sl|qwip|hgcdte",
         ["Détecteurs IR refroidis & non-refroidis"]),
        (r"guerre [eé]lectronique|\bew\b|brouilleur|jamming|jammer|electronic warfare|sigint|elint",
         ["Systèmes de guerre électronique (EW)"]),
        (r"\biff\b|identification ami|mode.5|mode-?5", ["Systèmes IFF & identification"]),
        # Communications
        (r"radio.*(tactique|sdr|hf|vhf|uhf|pr4g|synaps|trellisware|silvus|harris|persistent|manet|mesh|phased[- ]?array)|\bsdr\b",
         ["Radios tactiques & SDR"]),
        (r"radio mimo|radio mesh|mesh rider|radio relay", ["Radios tactiques & SDR"]),
        (r"antenne(?!.*satellite)|infrastructure rf|m[aâ]t.*antenne", ["Antennes & infrastructures RF"]),
        (r"\b5g\b|p-?lte|r[eé]seau.*(5g|priv[eé])|p-?5g|core mobile", ["Réseaux militaires (5G/P-LTE/MANET)"]),
        (r"\bmanet\b|mesh tactique", ["Réseaux militaires (5G/P-LTE/MANET)"]),
        (r"c4isr|c2|command.*control|station.*commandement|\bc-?2\b", ["Systèmes C2 & C4ISR"]),
        (r"streaming vid|encodeur.*vid|d[eé]codeur.*vid|makito|srt|hevc", ["Streaming vidéo & data tactiques"]),
        # Cyber & logiciels
        (r"plateforme ia|module ia|\bia\b.*(d[eé]tection|fusion|vision|temps r[eé]el)|altra|helsing",
         ["Plateformes IA / vision défense"]),
        (r"cyber[ -]?range|cybersecurit[eé].*(plate|appliance)|firewall.*durci|firepower", ["Cybersécurité (logiciels & appliances)"]),
        (r"cyber|cybers[eé]curit[eé]", ["Cybersécurité (logiciels & appliances)"]),
        (r"simulateur.*(tir|combat|tactique|exercice|crisis|crise)|simulation.*(tactique|cyber)|exonaut",
         ["Logiciels de simulation & cyber range"]),
        (r"logiciel.*(c2|c4isr|metier|d[eé]fense|gestion exercice|c-?uas|fusion|sigint)|plateforme.*(d[eé]fense|c2|c4isr)|exonaut",
         ["Logiciels métier défense (autres)"]),
        # Soldat & protection
        (r"casque(?!.*pilote).*(combat|tactique|fantassin|composite|balist)", ["Casques de combat"]),
        (r"casque pilote|hmds|hmd helmet", ["Avionique & instruments embarqués"]),
        (r"gilet.*(pare[- ]?balles|tactique|protection|porte[- ]?[eé]quipement)|plaque.*(balist|niveau iv|niveau iiia)",
         ["Gilets & plaques pare-balles"]),
        (r"vitrage.*(blind|pare[- ]?balles)|verre balistique|vitre.*blind", ["Vitrages blindés"]),
        (r"vision nocturne|nv goggle|night vision|onyx|jumelle.*nuit", ["Vision nocturne & jumelles"]),
        (r"viseur.*(arme|optique|holographique|red dot|acog|rmr|aimpoint|trijicon|reflex|mro|vcog)",
         ["Optiques d'armes (viseurs)"]),
        (r"nrbc|cbrn|filtre.*nrbc|tenue.*nrbc|masque.*nrbc|d[eé]tecteur.*gaz|drager", ["NRBC (masques, tenues, détecteurs)"]),
        (r"textile.*(technique|tactique|balist|camouflage|ignifug|fr)|tissu.*(camouflage|haute|technique|balistique)",
         ["Textiles techniques tactiques"]),
        (r"[eé]quipement.*fantassin|soldat connect[eé]|soldier|sac.*tactique|holster|porte[- ]?[eé]quipement|ceinturon",
         ["Équipement du fantassin (général)"]),
        # Énergie / propulsion
        (r"batterie(?!.*plate)|pack.*[eé]nerg|cellule.*li|li-?ion|silicon.*anode", ["Batteries & packs énergétiques"]),
        (r"pile.*combustible|fuel.cell|hydrog[eè]ne|dmfc", ["Piles à combustible & H2"]),
        (r"moteur.*(diesel|cummins|caterpillar|cat |c9|c13|c18|mtu|tatra)", ["Moteurs diesel / propulsion lourde"]),
        (r"moteur.*(a[eé]ronau|m88|rtm|rolls|tigre|aneto|arrius|sam146|h[eé]lico)", ["Moteurs aéronautiques"]),
        (r"groupe.*[eé]lectrog|generator|alternator|alternateur(?!.*spec)", ["Groupes électrogènes & énergie tactique"]),
        # Simulation
        (r"simulateur.*(entrainement|tir|combat|battle)|cockpit.*simulator|\bvr\b|\bxr\b|r[eé]alit[eé] augment|r[eé]alit[eé] virtuelle",
         ["Simulateurs d'entraînement & VR/AR"]),
        (r"cam[eé]ra.*(tactique|casque|arme|pov|montage)|mohoc", ["Caméras tactiques & POV"]),
        # Sous-systèmes & matériaux
        (r"composant.*[eé]lectronique|carte.*[eé]lectronique|cms|sbc|vpx|vme|fpga|semicond", ["Composants électroniques & cartes"]),
        (r"connecteur|harnais|c[aâ]bl(e|age).*(durci|militaire|a[eé]ro)", ["Connectique & câblage durcis"]),
        (r"pi[eè]ce.*(usin|m[eé]canique|d[eé]colletage|cnc)|usinage|d[eé]colletage|chaudronnerie|m[eé]cano-?soud",
         ["Pièces mécaniques & usinage"]),
        (r"transmission|r[eé]ducteur|essieu|freinage|differentielle", ["Mécanique de transmission"]),
        (r"hydrauli|amortisseur|v[eé]rin", ["Hydraulique & motion control"]),
        (r"composite (?!.*textile)|carbone|kevlar|carb-?fibre|fibre carbone", ["Matériaux composites & blindage"]),
        (r"acier(?!.*inox).*(blind|haute dur|armor|swebor|miilux|arcelor)", ["Aciers & métallurgie spéciale"]),
        (r"superalliage|titane.*forge|nickel|aubert|alliage haute", ["Aciers & métallurgie spéciale"]),
        (r"c[eé]ramique|hexoloy|sic|paxis|coorstek|ceramtec|aluminium oxide", ["Céramiques techniques & balistiques"]),
        (r"uhmwpe|dyneema|spectra|fibre balistique|panneau composite balist", ["Matériaux balistiques (UHMWPE, céramiques)"]),
        (r"visserie|fixation|boulonnerie|jpb|écrou|lock-?bolt|lock-?nut", ["Fixations & visserie aéronautique"]),
        (r"alimentation.*durcie|convertisseur.*(dc|dur|durci)|alim militaire|module.*alim", ["Alimentations & convertisseurs durcis"]),
        (r"bo[iî]tier.*durci|ch[aâ]ssis.*(durci|vpx|vme|19 pouces)|armoire.*[eé]lectronique|rack.*durci|schroff",
         ["Boîtiers & châssis durcis"]),
        (r"avionique|calculateur.*(vol|navigation|cockpit)|cockpit|interfaces? cockpit|hmds", ["Avionique & instruments embarqués"]),
        (r"servomoteur|motion control|brushless|sans balais|elmo", ["Servomoteurs & motion control précision"]),
        # Logistique
        (r"conteneur|shelter|malle|valise.*durcie|pelican|nanuk|peli", ["Conteneurs, malles, shelters"]),
        (r"[eé]quipement.*logist|logistic equipment|conteneur iso", ["Logistique militaire & transport"]),
        (r"m[aâ]t.*(t[eé]lescop|antenne)|signalisation.*(tactique|d[eé]ployable)|balisage", ["Mâts, signalisation & balisage"]),
        (r"purification.*eau|water purification|emergency station", ["Stations de purification d'eau & infra"]),
        (r"si[eè]ge.*(blind|anti-?mine|cabine|combat|suspendu)|seating.*military|tek seating", ["Sièges & ergonomie cabine"]),
        (r"lubrifiant|graisse|fluide hydraulique|fuchs", ["Lubrifiants & fluides"]),
    ]
)


SERVICE_RULES: tuple[tuple[re.Pattern, list[str]], ...] = tuple(
    (re.compile(p, re.I), cats) for p, cats in [
        (r"\bmco\b|mro\b|maintenance.*(operationnel|conditionnel|industrielle)", ["MCO / MRO"]),
        (r"int[eé]gration", ["Intégration de systèmes"]),
        (r"ing[eé]ni[eé]rie|conseil(?!.*export)|bureau d['e]?[eé]tudes", ["Ingénierie & conseil"]),
        (r"formation(?!.*export)", ["Formation & entraînement"]),
        (r"distribut|repr[eé]sent.*(commerciale|march|marque)", ["Distribution & représentation"]),
        (r"sous[- ]?trait|fournit.*OEM|fourniture OEM|emp(s)? [eé]lectronique", ["Sous-traitance industrielle"]),
        (r"certifi|test|qualification|essais (?!matr|mat[eé]riaux)|cem", ["Certification & tests"]),
        (r"essais (matr|mat[eé]riaux)|m[eé]trologie|tests m[eé]canique", ["Métrologie & essais matériaux"]),
        (r"cloud|infogérance|managed services|saas defense|services data", ["Services cloud & data"]),
        (r"support op[eé]rationnel|support op[eé]rationnel|opex|support terrain", ["Support opérationnel & OPEX"]),
        (r"modernisation.*flotte|retrofit|surblindage", ["Modernisation de flottes"]),
        (r"transit|logistique projet|fret|stockage s[eé]curis|export logistique", ["Logistique militaire & transit"]),
        (r"audit.*(cyber|s[eé]curit[eé]|ot|ics)|cybers[eé]curit[eé].*audit", ["Audit & cybersécurité OT"]),
        (r"organisation.*salon|matchmaking|[eé]v[eé]nementiel|stand cl[eé] en main", ["Organisation de salons & événements"]),
        (r"repr[eé]sentation.*institut|cluster|f[eé]d[eé]ration|chambre de commerce|business",
         ["Représentation institutionnelle (cluster/fédération)"]),
        (r"achat public|march[eé]s? d[eé]fense|achat.*minist|approvisionnement.*forces", ["Achat public & marchés défense"]),
        (r"financement.*export|garantie.*bancaire|banque.*d[eé]fense|lettre.*cr[eé]dit", ["Financement export & banque défense"]),
        (r"r&d|r[eé]&d|recherche.*contrat|projet.*edf|horizon europe|fui|cifre", ["R&D sur contrat"]),
        (r"sourcing.*composant|approvisionnement.*composant|distribution.*composant", ["Approvisionnement composants & sourcing"]),
        (r"marketing|d[eé]veloppement commercial|accompagnement.*export(?!.*formation)", ["Marketing & développement commercial"]),
        (r"documentation technique|gestion documentaire|ils\b|integrated logistic support", ["Documentation technique & ILS"]),
        (r"gestion.*programme|conseil.*programme", ["Conseil en gestion de programme"]),
        (r"promotion territoriale|facilitation invest|d[eé]veloppement industriel.*r[eé]gion", ["Promotion territoriale & investissement"]),
    ]
)


TECHNOLOGY_RULES: tuple[tuple[re.Pattern, list[str]], ...] = tuple(
    (re.compile(p, re.I), cats) for p, cats in [
        (r"\bia\b|machine learning|ml\b|deep learning|fusion(?!.*capteur)|nlp\b", ["IA / machine learning"]),
        (r"vision (par )?ordinateur|computer vision|d[eé]tection.*image|reconnaissance.*image", ["Vision par ordinateur"]),
        (r"robotique|autonomie|autonom(e|ous)", ["Robotique & autonomie"]),
        (r"edge computing|edge ai|jetson|gpu embarqu", ["Edge computing & GPU embarqué"]),
        (r"cloud(?!.*op)|cloud souverain|aws|azure", ["Cloud & cloud souverain"]),
        (r"big data|analytics|analyse de donn", ["Big data & analytics"]),
        (r"cybers[eé]curit[eé](?!.*ot|.*ics)", ["Cybersécurité"]),
        (r"cyber(s[eé]curit[eé])?.*(ot|ics|industriel)", ["Cybersécurité OT / ICS"]),
        (r"cryptogr|chiffrement|chiff[eé]?", ["Cryptographie"]),
        (r"\brf\b|micro[- ]?ondes|hyperfr[eé]quence", ["Communications RF / micro-ondes"]),
        (r"radar(?!.*lidar)", ["Radar (AESA, monopulse, …)"]),
        (r"lidar|laser", ["Lidar / Laser"]),
        (r"thermique|infrarouge|imagerie ir|mwir|lwir|imageur ir", ["Imagerie infrarouge"]),
        (r"swir|multispectrale|hyperspectrale", ["Imagerie SWIR / multispectrale"]),
        (r"optique.*(pr[eé]cision|cryogenique|multicouche|fibre)", ["Optique de précision"]),
        (r"\b5g\b|p-?lte|sans[- ]?fil|wireless", ["5G / réseaux radio mobiles"]),
        (r"gnss|\bgps\b", ["GNSS / navigation satellite"]),
        (r"navigation inertielle|imu|centrale inertielle|sigma|pure-play inertia", ["Navigation inertielle"]),
        (r"simulation|vr\b|xr\b|r[eé]alit[eé] (augment|virtuelle)", ["Simulation & VR/AR"]),
        (r"composite|carbone|fibre haute", ["Matériaux composites"]),
        (r"uhmwpe|c[eé]ramique balistique|kevlar|dyneema|plaque balistique", ["Matériaux balistiques (UHMWPE, céramiques)"]),
        (r"mat[eé]riaux [eé]nerg|propergol|explosif|rdx|hmx|pyrotechni", ["Matériaux énergétiques"]),
        (r"(acier|alliage|titane|nickel|superalliage).*(blind|aero|haute)", ["Métallurgie spéciale (alliages, blindages)"]),
        (r"fabrication additive|impression 3d|additive manufacturing|am250|am500|formup|lpbf|ded\b", ["Fabrication additive"]),
        (r"semicond|microelec|fpga|asic|hi-rel|durci.*silicon", ["Microélectronique & semiconducteurs durcis"]),
        (r"mems|microcapteur|gyro mems", ["MEMS & capteurs miniaturisés"]),
        (r"capteur.*(iot|industriel)", ["Capteurs IoT industriels"]),
        (r"batterie|fuel cell|pile.*combustible|li-?ion|silicon-?anode|hydrog[eè]ne", ["Énergie portative & piles à combustible"]),
        (r"filtration nrbc|charbon actif|nrbc|cbrn", ["Filtration NRBC"]),
        (r"hydrauli|amortisseur|v[eé]rin", ["Hydraulique haute performance"]),
        (r"photonique|fibre optique|fibre laser|tritium", ["Photonique & fibres optiques"]),
        (r"guerre [eé]lectronique|\bew\b|jamming|brouillage", ["Guerre électronique (EW)"]),
        (r"vol autonome|gps[- ]?denied|gps brouill", ["Vol autonome & GPS-denied"]),
    ]
)


# ===========================================================================
# Public API
# ===========================================================================


def _classify(label: str, rules) -> list[str]:
    """Return all categories matched by ``label`` against ``rules``.

    Order matches the order of definition in the source file. The function
    de-duplicates matches in arrival order so the output is stable.
    """
    if not label:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for pattern, cats in rules:
        if pattern.search(label):
            for c in cats:
                if c not in seen:
                    out.append(c)
                    seen.add(c)
    return out


def categorize_products(labels: Iterable[str]) -> list[str]:
    cats: list[str] = []
    seen: set[str] = set()
    for lab in labels or []:
        for c in _classify(lab, PRODUCT_RULES):
            if c not in seen:
                cats.append(c)
                seen.add(c)
    if not cats and any(labels or []):
        cats.append("Autre — à qualifier")
    return cats


def categorize_services(labels: Iterable[str]) -> list[str]:
    cats: list[str] = []
    seen: set[str] = set()
    for lab in labels or []:
        for c in _classify(lab, SERVICE_RULES):
            if c not in seen:
                cats.append(c)
                seen.add(c)
    if not cats and any(labels or []):
        cats.append("Autre — à qualifier")
    return cats


def categorize_technologies(labels: Iterable[str]) -> list[str]:
    cats: list[str] = []
    seen: set[str] = set()
    for lab in labels or []:
        for c in _classify(lab, TECHNOLOGY_RULES):
            if c not in seen:
                cats.append(c)
                seen.add(c)
    if not cats and any(labels or []):
        cats.append("Autre — à qualifier")
    return cats


def add_categories(record: dict) -> dict:
    """Mutate and return a profile record by adding the three category fields."""
    record["products_categories"] = categorize_products(record.get("products") or [])
    record["services_categories"] = categorize_services(record.get("services") or [])
    record["technologies_categories"] = categorize_technologies(
        record.get("technologies") or []
    )
    return record


__all__ = [
    "PRODUCT_CATEGORIES",
    "SERVICE_CATEGORIES",
    "TECHNOLOGY_CATEGORIES",
    "categorize_products",
    "categorize_services",
    "categorize_technologies",
    "add_categories",
]
