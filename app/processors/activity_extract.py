"""Extract a clean ``activity_1liner`` from the company's
``short_presentation`` (or any text source) before falling back to
the ``built_products`` mapping.

Priority order
--------------
1. **Kind-specific template** — for non-INDUSTRIAL kinds (institutional,
   media, government, etc.) the activity is always a template, never
   inferred from products. Avoids the « tagué véhicules militaires alors
   que c'est une chambre de commerce » failure mode.
2. **Pattern-matched activity** — for INDUSTRIAL kind, scan the
   short_presentation for known activity verbs (`designs`, `manufactures`,
   `provides`, …) followed by a recognized object (`generators`,
   `forgings`, `optronics`, …). Each `(verb_pattern, object_pattern) →
   FR activity` mapping is hand-curated and conservative.
3. **Fallback to built_products** — only when no clean text source
   matched. This is the legacy behavior, now used as a true fallback.
4. **Last resort** — short_presentation truncated as-is, or "(données
   pauvres)".

Each call returns ``(activity_1liner, confidence, source)`` so the
caller can flag low-confidence extractions for manual review.
"""
from __future__ import annotations

import re
from typing import Optional

from app.processors.activity_profiles import (
    KIND_TO_PROFILE, PROFILES,
)
from app.processors.company_kind import detect_company_kind


# ---------------------------------------------------------------------------
# Kind-specific templates — these run first and *replace* product-based
# inference. Generic enough to be safe across many companies of that kind.
# ---------------------------------------------------------------------------

_KIND_TEMPLATES: dict[str, str] = {
    "INSTITUTIONAL":
        "Représente le tissu industriel défense et facilite la coopération "
        "internationale.",
    "GOVERNMENT":
        "Pilote la défense nationale et conduit les achats militaires "
        "publics.",
    "MEDIA":
        "Édite des publications spécialisées défense et sécurité pour "
        "professionnels.",
    "EVENT_ORGANIZER":
        "Organise les salons et conférences défense / sécurité pour la "
        "communauté industrielle.",
    "FINANCIAL":
        "Fournit des services bancaires et de financement export pour "
        "les industriels défense.",
    "ACADEMIC":
        "Conduit de la recherche et de la formation en sciences "
        "appliquées et ingénierie de défense.",
    "RESEARCH":
        "Conduit de la R&D appliquée pour la défense, l'aérospatial et "
        "les industriels.",
    "CONSULTING":
        "Conseille en stratégie, intelligence économique et opérations "
        "pour la défense et l'industrie.",
    "LOGISTICS":
        "Fournit des services logistiques mondiaux (transit, fret, "
        "supply chain) pour la défense et l'industrie.",
    "DISTRIBUTOR":
        "Distribue des composants techniques pour la défense, "
        "l'aérospatial et l'industrie.",
}


# ---------------------------------------------------------------------------
# INDUSTRIAL pattern table : (regex_on_text, FR activity sentence ≤ 140c).
# Higher-up rules are matched first, so put MORE-SPECIFIC patterns first.
# Each FR sentence starts with a verb and is ≤ 140 chars including the
# trailing period.
# ---------------------------------------------------------------------------

_ACTIVITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(generators?|gen[eé]ratrices?)\b.*\b(armed forces|military|forces arm[eé]es)\b", re.I),
     "Conçoit et fabrique des générateurs portables et solutions énergétiques pour les forces armées."),
    (re.compile(r"\b(metal|m[eé]tal)\b.*\b3d printing\b|\bimpression 3d m[eé]tal\b", re.I),
     "Conçoit des solutions d'impression 3D métal directement déployables sur site."),
    (re.compile(r"\b3d printing\b|\badditive manufacturing\b|\bfabrication additive\b", re.I),
     "Conçoit des solutions de fabrication additive pour l'industrie défense et aéronautique."),
    (re.compile(r"\b(steel|acier)\b.*\bforgings?\b", re.I),
     "Forge à chaud des pièces en acier pour la défense, l'aéronautique et l'industrie."),
    (re.compile(r"\bforgings?\b|\bforges?\b\s+(de|d')", re.I),
     "Forge des pièces métalliques de précision pour la défense et l'aéronautique."),
    (re.compile(r"\b(machining|usinage)\b.*\b(precision|pr[eé]cision|5[- ]ax|cnc)", re.I),
     "Sous-traite l'usinage de précision (5 axes, CNC) pour la défense et l'aéronautique."),
    (re.compile(r"\b(machining|usinage)\b", re.I),
     "Sous-traite l'usinage mécanique pour la défense et l'industrie."),
    (re.compile(r"\b(robotics?|robotique)\b.*\b(manufacturing|manufacturier)", re.I),
     "Conçoit des systèmes robotiques de fabrication pour l'industrie défense et l'aéronautique."),
    (re.compile(r"\b(unmanned|drone|uav)\b.*\b(loitering|isr|tactical|combat|kamikaze)", re.I),
     "Conçoit et fabrique des drones tactiques (ISR, munitions rôdeuses, frappe de précision)."),
    (re.compile(r"\b(loitering munition)\b", re.I),
     "Conçoit et fabrique des munitions rôdeuses pour les forces armées."),
    (re.compile(r"\b(armoured?|armor|blind[eé])\s+(vehicle|v[eé]hicule)", re.I),
     "Conçoit et fabrique des véhicules blindés tactiques pour les armées et la sécurité."),
    (re.compile(r"\b(armored car|veh[ií]cul[eo]s? blindados?)\b", re.I),
     "Conçoit et fabrique des véhicules blindés VIP pour la sécurité et la défense."),
    (re.compile(r"\b(armour|blindage|protection balistique|ballistic protection)\b", re.I),
     "Conçoit et fabrique des solutions de blindage et protection balistique."),
    (re.compile(r"\b(aerostat|tethered balloon|stratospheric balloon|ballon\b.*\b(strato|captif|tactique))", re.I),
     "Conçoit, fabrique et exploite des aérostats et ballons stratosphériques pour les missions ISR."),
    (re.compile(r"\b(connectivity|connectiv|edge processing|edge computing)\b.*\b(uav|drone|robotics?)", re.I),
     "Conçoit des solutions de connectivité et edge computing pour drones, robots et systèmes autonomes."),
    (re.compile(r"\b(centralisation|centralizing).*\bachats?\b.*\b(d[eé]fense|minist[eè]re|forces)", re.I),
     "Centralise et externalise les achats publics du Ministère de la Défense."),
    (re.compile(r"\b(small arms|fusils?|rifles?|carbines?|pistols?)\b", re.I),
     "Conçoit et fabrique des armes légères et accessoires pour militaires et police."),
    (re.compile(r"\b(ammunition|munitions?)\b.*\b(production|fabrication|manufactur)", re.I),
     "Conçoit et fabrique des munitions de petit, moyen et gros calibre pour les forces armées."),
    (re.compile(r"\b(ammunition|munitions?)\b", re.I),
     "Conçoit et fabrique des munitions et matériels énergétiques pour les armées."),
    (re.compile(r"\bsh(?:elter|ooting range)\b.*\b(ballistic|tir|cible|defense)", re.I),
     "Exploite un stand de tir et conçoit des installations balistiques pour qualifier les munitions."),
    (re.compile(r"\b(ir|infrared|infrarouge|thermal|thermique)\s+(detector|d[eé]tecteur|imaging|imagerie)", re.I),
     "Conçoit et fabrique des détecteurs infrarouges et systèmes d'imagerie thermique."),
    (re.compile(r"\b(optronics?|optronique|riflescope|viseurs? optiques?)", re.I),
     "Conçoit et fabrique des optroniques et viseurs pour armes individuelles et plateformes."),
    (re.compile(r"\b(radar)\b.*\b(aesa|surveillance|tactical|antenne)", re.I),
     "Conçoit et fabrique des radars de surveillance et de tracking pour la défense."),
    (re.compile(r"\b(missile|guided weapon|armement guid[eé])\b", re.I),
     "Conçoit et fabrique des missiles et armements guidés pour les armées."),
    (re.compile(r"\b(electronic warfare|guerre [eé]lectronique|jamming|brouilleur)\b", re.I),
     "Conçoit et fabrique des systèmes de guerre électronique et de brouillage RF."),
    (re.compile(r"\b(cyber security|cybers[eé]curit[eé])\b.*\b(defense|d[eé]fense|critical|infrastructure)", re.I),
     "Édite des solutions de cybersécurité pour la défense et les infrastructures critiques."),
    (re.compile(r"\b(satellite|satcom|leo constellation)\b", re.I),
     "Conçoit et exploite des systèmes satellitaires pour la défense et les communications gouvernementales."),
    (re.compile(r"\b(nbc|nrbc|cbrn|chemical detection|d[eé]tection nrbc|filtration nrbc)\b", re.I),
     "Conçoit et fabrique des équipements NRBC (détection, protection, décontamination)."),
    (re.compile(r"\b(simulator|simulateur|simulation training|cyber range)\b", re.I),
     "Conçoit et fournit des simulateurs et plateformes d'entraînement militaire."),
    (re.compile(r"\b(power management|gestion d['e]?[eé]nergie|alternateurs?|alternators?)", re.I),
     "Conçoit et fabrique des systèmes de gestion d'énergie et alternateurs militaires."),
    (re.compile(r"\b(connector|connecteur|cable assemblies|harness|c[aâ]blage|c[aâ]bles?)\b.*\b(military|durci|mil-grade|aerospace)", re.I),
     "Conçoit et fabrique des connecteurs et harnais durcis pour la défense et l'aéronautique."),
    (re.compile(r"\b(antenna|antenne)\b.*\b(rf|durcie|tactical|phased[- ]?array)", re.I),
     "Conçoit et fabrique des antennes RF durcies pour la défense et les communications tactiques."),
    (re.compile(r"\b(rugged computer|durci|rugged tablet|rugged smartphone)", re.I),
     "Conçoit et fabrique des ordinateurs et terminaux durcis pour la défense et les forces de sécurité."),
    (re.compile(r"\bbattery\b.*\b(li-ion|lithium|military|durcie|lithium-ion)", re.I),
     "Conçoit et fabrique des batteries militaires haute performance pour drones et soldats connectés."),
    (re.compile(r"\b(composite|carbon fiber|fibre carbone|kevlar|uhmwpe)\b", re.I),
     "Conçoit et fournit des matériaux composites et structures pour la défense et l'aéronautique."),
    (re.compile(r"\b(textile)\b.*\b(camouflage|ballistic|technique|tactique|fr|ignifug)", re.I),
     "Conçoit et fabrique des textiles techniques (camouflage, balistique, ignifugés) pour militaires."),
    (re.compile(r"\b(boots?|chaussures?)\b.*\b(military|combat|tactique)", re.I),
     "Conçoit et fabrique des chaussures et bottes de combat pour les forces armées."),
    (re.compile(r"\b(helmet|casque)\b.*\b(combat|composite|ballistic|balistique)", re.I),
     "Conçoit et fabrique des casques de combat composites pour militaires et police."),
    (re.compile(r"\b(rescue|secours|hydraulic rescue)\b.*\b(equipment|outils?|hydraulique)", re.I),
     "Conçoit et fabrique des outils hydrauliques de désincarcération et de secours."),
    (re.compile(r"\b(diesel|engine|moteur)\b.*\b(military|tactical|heavy duty)", re.I),
     "Conçoit et fabrique des moteurs diesel et systèmes de propulsion pour véhicules militaires."),
    (re.compile(r"\b(transmission|gearbox|r[eé]ducteur)\b.*\b(military|tank|chars?)", re.I),
     "Conçoit et fabrique des transmissions et boîtes de vitesses pour véhicules blindés et chars."),
    (re.compile(r"\b(track shoes?|chenille[s]?)\b", re.I),
     "Conçoit et fabrique des chenilles et trains de roulement pour véhicules blindés."),
    (re.compile(r"\b(seat[s]?|si[eè]ge[s]?)\b.*\b(armoured?|blind[eé]|anti-mine)", re.I),
     "Conçoit et fabrique des sièges blindés et anti-mines pour véhicules militaires."),
    (re.compile(r"\b(suspension|amortisseur|shock absorber)\b.*\b(military|tank|blind|tactical)", re.I),
     "Conçoit et fabrique des suspensions et amortisseurs pour véhicules militaires."),
    (re.compile(r"\b(crane|grue)\b.*\b(articul|truck|recovery|military)", re.I),
     "Conçoit et fabrique des grues articulées et de récupération pour applications militaires."),
    (re.compile(r"\b(shelter|abri tactique|conteneur)\b.*\b(deploy|d[eé]ployable|durci|military)", re.I),
     "Conçoit et fabrique des shelters et conteneurs durcis pour les forces armées."),
    (re.compile(r"\b(water purification|purification d['e]?eau)\b", re.I),
     "Conçoit et fabrique des stations de purification d'eau pour les armées et l'humanitaire."),
    (re.compile(r"\b(navigation|inertial|inertielle)\b.*\b(unit|imu|sigma|haute performance)", re.I),
     "Conçoit et fabrique des centrales inertielles et systèmes de navigation pour défense."),
    (re.compile(r"\b(ai|artificial intelligence|intelligence artificielle)\b.*\b(defense|d[eé]fense|defence)", re.I),
     "Édite des solutions d'IA et fusion de capteurs pour la défense."),
    (re.compile(r"\b(osint|open[- ]?source intelligence)\b", re.I),
     "Édite une plateforme OSINT pour le renseignement et la cyberdéfense."),
    (re.compile(r"\b(graph analytics|knowledge graphs?|investigation)\b", re.I),
     "Édite une plateforme d'analyse de graphes pour le renseignement et la lutte anti-fraude."),
    (re.compile(r"\b(secure messaging|messagerie chiffr[eé]e|end-to-end encryption)\b", re.I),
     "Édite une plateforme de messagerie chiffrée bout-en-bout pour les administrations et la défense."),
    (re.compile(r"\b(cyber range|cyber-range|exercices? cyber)", re.I),
     "Édite une plateforme de cyber range et d'entraînement aux cyber-attaques."),
    (re.compile(r"\b(malware analysis|analyse de malware|threat hunting)", re.I),
     "Édite une plateforme d'analyse automatique de malware pour la cyberdéfense."),
    (re.compile(r"\b(ndr|network detection (and|et) response)", re.I),
     "Édite une plateforme NDR (Network Detection & Response) pour la cyberdéfense souveraine."),
    (re.compile(r"\b(formal verification|v[eé]rification formelle)\b", re.I),
     "Édite des outils de vérification formelle de code pour la cybersécurité critique."),
    (re.compile(r"\b(sovereign cloud|cloud souverain)\b", re.I),
     "Édite une plateforme de cloud souverain pour la défense et le secteur public."),
    (re.compile(r"\b(geospatial|sig|cartograph)\b.*\b(defense|d[eé]fense|tactique)", re.I),
     "Édite des solutions cartographiques et géospatiales pour la défense et la sécurité."),
    (re.compile(r"\b(simulation immersive|vr training|virtual reality training|xr (training|defense))", re.I),
     "Édite des plateformes de simulation immersive et de formation VR pour la défense."),
    (re.compile(r"\b(serious games?)\b", re.I),
     "Édite des serious games tactiques pour la formation militaire et de sécurité civile."),
    (re.compile(r"\b(stealth|furtif|stealth materials?)\b", re.I),
     "Conçoit et fabrique des matériaux et revêtements furtifs (RAM, peintures) pour aéronefs et navires."),
    (re.compile(r"\b(servomotor|servomoteur|brushless|sans balais)\b", re.I),
     "Conçoit et fabrique des servomoteurs de précision pour défense, robotique et aéronautique."),
    (re.compile(r"\b(amortis|isolat).*\b(vibrat|hydraulique)", re.I),
     "Conçoit et fabrique des amortisseurs et isolateurs vibratoires pour la défense et l'aéronautique."),
    (re.compile(r"\b(lubricant|lubrifiant|grease|graisse)\b.*\b(military|industrial|durci)", re.I),
     "Conçoit et fournit des lubrifiants et fluides techniques pour véhicules militaires et industriels."),
    (re.compile(r"\b(magnetic|aimant)\b.*\b(permanent|sensor|magn[eé]tique)", re.I),
     "Conçoit et fabrique des aimants permanents et solutions magnétiques pour la défense et l'industrie."),
    (re.compile(r"\b(printed circuit board|pcb|carte [eé]lectronique)\b.*\b(defense|d[eé]fense|durci|haute fiabilit)", re.I),
     "Conçoit et fabrique des cartes électroniques (PCB) haute fiabilité pour défense, spatial et médical."),
    (re.compile(r"\b(emergency rations?|mre|ration)\b.*\b(combat|military|forces)", re.I),
     "Conçoit et fabrique des rations militaires haute performance pour les forces armées."),
    (re.compile(r"\b(camera|cam[eé]ra)\b.*\b(tactical|tactique|helmet|casque|gunshot)", re.I),
     "Conçoit et fabrique des caméras tactiques pour casques d'intervention et armes."),
    (re.compile(r"\b(cutting tools?|outils? de coupe|fraises?|forets?|drills?)", re.I),
     "Conçoit et fabrique des outils de coupe (forets, fraises) pour l'industrie défense et aéronautique."),
    (re.compile(r"\b(3d scanner|scanner 3d|metrology|m[eé]trologie)\b", re.I),
     "Conçoit et fournit des solutions de métrologie 3D et contrôle dimensionnel pour la défense."),
    (re.compile(r"\b(propellant|propergol|explosif|explosive)\b", re.I),
     "Conçoit et fabrique des explosifs militaires, propergols et matériaux énergétiques."),
    (re.compile(r"\bairborne (surveillance|isr|special mission)", re.I),
     "Intègre et qualifie des aéronefs de surveillance ISR à mission spéciale pour les forces armées."),
    (re.compile(r"\b(anti[- ]?drone|c[- ]?uas|c-uav|counter[- ]?drone)", re.I),
     "Conçoit et fabrique des systèmes anti-drone (détection RF, neutralisation, brouillage)."),
    (re.compile(r"\b(armed forces|military)\b.*\b(modular|modulaire|tactical)\s+(?:energy|power|[eé]nergie|portable)", re.I),
     "Conçoit et fabrique des solutions énergétiques modulaires pour les forces armées."),
    (re.compile(r"\b(contract manufacturing|engineering services?)\b.*\b(defen[cs]e|security|d[eé]fense)", re.I),
     "Fournit des services d'ingénierie et de fabrication contractuelle pour la défense et la sécurité."),
    (re.compile(r"\b(engineering)\b.*\b(defence|defense|security|d[eé]fense)", re.I),
     "Conseille en ingénierie et conçoit des solutions techniques pour la défense et la sécurité."),
    # ──────────── extra patterns from sample audit ───────────────────────
    (re.compile(r"\b(fire suppression|extinction d['e]?incendie|suppress(?:es?|ion))\b.*\bvehicle", re.I),
     "Conçoit et fabrique des systèmes d'extinction d'incendie pour véhicules militaires."),
    (re.compile(r"\b(autonomous|autonom)\b.*\b(unmanned|sans pilote|robot)\s+(vehicle|v[eé]hicule)", re.I),
     "Édite un OS et des plateformes pour véhicules autonomes terrestres et drones."),
    (re.compile(r"\b(driveline|suspension|essieux|axles?)\b", re.I),
     "Conçoit des systèmes de transmission, essieux et suspensions pour véhicules militaires."),
    (re.compile(r"\b(membrane switch|claviers? membrane|pcba|switch)\b.*\b(rugged|circuit|industriel|defense)", re.I),
     "Conçoit et fabrique des claviers à membrane et PCB durcis pour applications industrielles et défense."),
    (re.compile(r"\b(mobility|mobilit[eé]|protection)\b.*\b(military|tactical|tactique)\b.*\b(commercial vehicles?|trucks?|camions?)\b", re.I),
     "Conçoit et fabrique des véhicules industriels lourds pour les forces armées et la sécurité."),
    (re.compile(r"\bmilitary and security vehicles?\b|\bvehicles? for defen[cs]e\b", re.I),
     "Conçoit et fabrique des véhicules militaires et de sécurité pour les forces armées."),
    (re.compile(r"\b(tents?|tentes?|hangars?)\b.*\b(deploy|d[eé]ployable|rapide|tactique)", re.I),
     "Conçoit et fabrique des shelters, tentes et hangars déployables pour les armées."),
    (re.compile(r"\b(industrial shelter|shelter industriel|aircraft hangar|hangar)\b", re.I),
     "Conçoit et fabrique des hangars et shelters industriels pour applications militaires."),
    (re.compile(r"\b(geolocation|g[eé]olocalisation)\b.*\b(motion|navigation|inertial|sans gps)", re.I),
     "Conçoit des solutions de géolocalisation et navigation sans GPS pour applications militaires."),
    (re.compile(r"\b(textile)\b.*\b(dyeing|finishing|coating|teinture|finissage)", re.I),
     "Conçoit et fabrique des textiles techniques (teinture, finissage, enduction) pour la défense."),
    (re.compile(r"\b(industrial packaging|emballage industriel|packaging|emballage)\b.*\b"
                r"(defen[cs]e|d[eé]fense|aeronautics?|sensitive|sensible|weaponry|armement)", re.I),
     "Conçoit et fabrique des emballages techniques durcis pour la défense, l'aéronautique et l'armement."),
    (re.compile(r"\b(hmi|ihm|push button|bouton[s]? poussoirs?|interrupteurs?|switches?)\b.*\b"
                r"(custom|personnalis[eé]|industriel|electric|[eé]lectrique)", re.I),
     "Conçoit et fabrique des composants IHM (boutons, interrupteurs) pour applications industrielles et défense."),
    (re.compile(r"\b(gun mounts?|supports? d['e]?armes|armements? supports?)\b", re.I),
     "Conçoit et fabrique des supports d'armes (M2, M240, lance-missiles) pour véhicules et plateformes."),
    (re.compile(r"\b(fiber optic connectors?|connecteurs? fibre optique)", re.I),
     "Conçoit et fabrique des connecteurs électriques et fibre optique durcis pour environnements sévères."),
    (re.compile(r"\b(underwater|sous-?marin|subaquatic|subsea)\b.*\b(drones?|robots?)", re.I),
     "Conçoit et fabrique des drones et robots sous-marins pour la maintenance et l'inspection."),
    (re.compile(r"\b(diving|plong[eé]e)\b.*\b(combinaison|professionnel|combat|tactique)", re.I),
     "Conçoit et fabrique des équipements de plongée techniques pour militaires et forces spéciales."),
    (re.compile(r"\b(obsolescence|sourcing|supply chain security|continuit[eé] op[eé]rationnelle)", re.I),
     "Conseille les industriels défense en gestion d'obsolescence, sourcing et continuité d'approvisionnement."),
    (re.compile(r"\b(packaging qualification|qualification d['e]?emballage)", re.I),
     "Qualifie et teste des emballages industriels pour équipements sensibles défense et aéronautiques."),
    (re.compile(r"\b(precision parts?|pi[eè]ces? de pr[eé]cision)\b.*\b(aerospace|a[eé]rospatial|defense|d[eé]fense)", re.I),
     "Sous-traite la fabrication de pièces mécaniques de précision pour la défense et l'aérospatial."),
    (re.compile(r"\b(geospatial|sig|gis)\b.*\b(satellite|imagerie|tactique|defense|d[eé]fense)", re.I),
     "Conçoit des solutions géospatiales et cartographiques pour la défense et la sécurité."),
    (re.compile(r"\b(security services?|services? de s[eé]curit[eé]|electronic security|s[eé]curit[eé] [eé]lectronique|"
                r"surveillance|gardiennage)\b", re.I),
     "Fournit des services de sécurité (gardiennage, vidéosurveillance, intervention) pour sites sensibles."),
    (re.compile(r"\b(motion capture|capture de mouvement)\b", re.I),
     "Conçoit des solutions de capture de mouvement et navigation inertielle haute précision."),
    (re.compile(r"\b(armoured? doors?|portes? blind[eé]es?|safe[s]?|coffres?-forts?)\b", re.I),
     "Conçoit et fabrique des portes blindées et coffres-forts pour bâtiments sensibles et défense."),
    (re.compile(r"\b(turnkey|cl[eé] en main|bespoke|sur[- ]?mesure)\b.*\b"
                r"(shelter|abri|hangar|installation)", re.I),
     "Conçoit des solutions clé-en-main de shelters, hangars et installations militaires."),
    (re.compile(r"\b(electronic security)\b|\b(integrated security)\b", re.I),
     "Intègre des solutions de sûreté électronique pour bâtiments et sites sensibles."),
    (re.compile(r"\b(network|r[eé]seau)\b.*\b(autonomous|autonom)", re.I),
     "Édite des plateformes pour réseaux autonomes et systèmes décentralisés."),
    (re.compile(r"\b(deep tech|deeptech|technologie de pointe)\b", re.I),
     "Pépite deep-tech au service des industries critiques (défense, spatial, énergie)."),
    (re.compile(r"\b(welding|soudage|soudure)\b", re.I),
     "Conçoit et fabrique des équipements de soudage industriel pour la défense et l'aéronautique."),
    (re.compile(r"\b(coatings?|rev[eê]tements?)\b.*\b(thermal|thermique|surface|stealth)", re.I),
     "Conçoit et applique des revêtements et traitements de surface pour la défense et l'aérospatial."),
    (re.compile(r"\b(uniforms?|uniformes?|combat (clothing|tenue))\b", re.I),
     "Conçoit et fabrique des uniformes et tenues de combat pour les armées et police."),
    (re.compile(r"\b(boots?|chaussures? (de )?(combat|s[eé]curit[eé]))\b", re.I),
     "Conçoit et fabrique des chaussures techniques (combat, sécurité) pour militaires et police."),
    (re.compile(r"\b(gloves?|gants?)\b.*\b(tactique|combat|technique|police|military)", re.I),
     "Conçoit et fabrique des gants tactiques et techniques pour militaires, police et secours."),
    (re.compile(r"\b(backpacks?|sacs? [aà] dos)\b.*\b(tactique|military|combat|forces)", re.I),
     "Conçoit et fabrique des sacs à dos tactiques et équipement individuel pour militaires."),
    (re.compile(r"\b(emergency|urgence)\b.*\b(stockpiling|stockage|m[eé]dical|medical countermeasures)", re.I),
     "Fournit des contre-mesures médicales et stockages stratégiques pour la défense et la sécurité civile."),
    (re.compile(r"\b(antibiotics?|antidotes?|m[eé]dicaments?|pharma)\b.*\b(defen[cs]e|military|d[eé]fense)", re.I),
     "Conçoit et fournit des médicaments et contre-mesures pharmaceutiques pour la défense."),
    (re.compile(r"\b(reagents?|r[eé]actifs?)\b.*\b(detection|d[eé]tection|chemical|chimique)", re.I),
     "Conçoit et fabrique des réactifs de détection chimique et biologique pour la défense."),
    (re.compile(r"\b(world['s]*\s+largest|top|leading)\s+(?:aerospace|aerospace and defense|"
                r"defense|defence)\s+company\b", re.I),
     "Prime mondial aérospatial et défense — conçoit avions, missiles, radars, systèmes navals et électronique militaire."),
    (re.compile(r"\b(modernize[sd]|refurbish(?:es|ed|ment)?|reset)\b.*\b(legacy|military) vehicles?", re.I),
     "Modernise et remet à niveau les flottes de véhicules militaires existants pour les armées."),
    (re.compile(r"\b(obsolescence|gestion d['e]?obsolescence)\b", re.I),
     "Conseille en gestion d'obsolescence, sourcing et continuité d'approvisionnement pour la défense."),
    (re.compile(r"\bgun\s+mounts?\b|\bsupports?\s+d['e]?armes?\b|\blance[- ]?missiles?\s+supports?\b", re.I),
     "Conçoit et fabrique des supports d'armes (M2, M240, lance-missiles) pour véhicules et plateformes."),
    (re.compile(r"\bheating\b.*\b(elements?|r[eé]sistances?)", re.I),
     "Conçoit et fabrique des éléments de chauffage et résistances industrielles."),
    (re.compile(r"\bagency\b.*\b(promote|trade|export|investment|business)\b", re.I),
     "Promeut le tissu industriel et facilite l'export auprès des marchés étrangers."),
    # ──────────── more patterns from sample audit (rich SP cases) ────────
    (re.compile(r"\b(doppler radars?|radars? doppler)\b", re.I),
     "Conçoit et fabrique des radars Doppler de haute précision pour la défense."),
    (re.compile(r"\b(springs?|ressorts?)\b.*\b(mechanical|m[eé]caniques?|mechatron)", re.I),
     "Conçoit et fabrique des ressorts et composants mécaniques de précision pour la défense."),
    (re.compile(r"\b(interconnection|connectique)\b.*\b(harsh|durci|mil-?grade)", re.I),
     "Conçoit et fabrique des systèmes d'interconnexion durcis pour environnements sévères."),
    (re.compile(r"\b(bulletproof|pare-?balles?)\b.*\b(helmet|casque|body armor|gilets?)", re.I),
     "Conçoit et fabrique des casques et gilets pare-balles pour militaires et police."),
    (re.compile(r"\b(electroplating|electro[- ]?d[eé]position|finishing|finissage|surface treatment|traitement de surface)", re.I),
     "Conçoit et applique des traitements de surface et finitions pour la défense et l'aéronautique."),
    (re.compile(r"\b(rugged|durci) (laptops?|tablets?|computers?|notebooks?|smartphones?)\b", re.I),
     "Conçoit et fabrique des ordinateurs et tablettes durcis pour la défense et la sécurité."),
    (re.compile(r"\b(mtu|rolls[- ]?royce)\b.*\b(power|propulsion|engines?|moteurs?)", re.I),
     "Conçoit et fabrique des moteurs et systèmes de propulsion pour véhicules militaires et navires."),
    (re.compile(r"\b(radiation detection|d[eé]tection de rayonnement|cbrne)\b", re.I),
     "Conçoit et fabrique des détecteurs de rayonnement et solutions CBRNe pour défense et sécurité."),
    (re.compile(r"\b(demining|d[eé]minage)\b.*\b(robot|ugv|platform)", re.I),
     "Conçoit et fabrique des robots de déminage pour militaires et déminage humanitaire."),
    (re.compile(r"\b(specialized vehicles?|v[eé]hicules? sp[eé]cialis[eé]s?)\b.*\b(defen[cs]e|d[eé]fense|forces|trusted)", re.I),
     "Conçoit et fabrique des véhicules spécialisés pour la défense et les forces de sécurité."),
    (re.compile(r"\b(firearms?|accessoires? d['e]?arme|weapon accessor)\b", re.I),
     "Conçoit et fabrique des accessoires d'armes (chargeurs, rails, crosses) pour militaires et police."),
    (re.compile(r"\bonsite\s+(nitrogen|oxygen|azote|oxyg[eè]ne)\b|\b(nitrogen|oxygen|azote|oxyg[eè]ne) generators?\b", re.I),
     "Conçoit et fabrique des générateurs d'azote et d'oxygène on-site pour militaires et hospitaliers."),
    (re.compile(r"\b(milling|turning|usinage|fraisage)\s+(centers?|centres?)", re.I),
     "Conçoit et fabrique des centres d'usinage CNC pour l'industrie défense et aéronautique."),
    (re.compile(r"\b(towing|attelage|recovery|r[eé]cup[eé]ration|coupling|d[eé]pannage)\b", re.I),
     "Conçoit et fabrique des dispositifs d'attelage et récupération pour véhicules militaires."),
    (re.compile(r"\b(aluminum|aluminium)\s+(extrusion|extruded|composants?)", re.I),
     "Conçoit et fabrique des composants aluminium par extrusion pour la défense et l'industrie."),
    (re.compile(r"\b(digital twin|jumeau num[eé]rique)\b", re.I),
     "Édite des plateformes de jumeau numérique pour la simulation de systèmes autonomes."),
    (re.compile(r"\b(uds excavator|excavateur|engins? du g[eé]nie)\b", re.I),
     "Conçoit et fabrique des engins du génie militaire (excavateurs, chargeurs blindés)."),
    (re.compile(r"\b(antenna masts?|m[aâ]ts? antenne|m[aâ]ts? d['e]?antennes?)\b", re.I),
     "Conçoit et fabrique des mâts d'antenne mobiles pour communications militaires déployables."),
    (re.compile(r"\b(military camps?|humanitarian camps?|camps? d[eé]ployables?|camps? militaires?)\b", re.I),
     "Conçoit et fabrique des camps militaires et humanitaires déployables pour OPEX."),
    (re.compile(r"\b(electro[- ]?optical|[eé]lectro[- ]?optique)\b", re.I),
     "Conçoit et fabrique des solutions électro-optiques (viseurs, capteurs, lasers) pour la défense."),
    (re.compile(r"\b(mission[- ]?critical communications?|communications? mission[- ]?critiques?)", re.I),
     "Fournit des solutions de communications mission-critique pour gouvernements et premiers répondants."),
    (re.compile(r"\bhi[- ]?rel\b|\bhigh[- ]?reliability electronics?\b", re.I),
     "Conçoit et fabrique de l'électronique haute fiabilité (Hi-Rel) pour défense, spatial et médical."),
    (re.compile(r"\b(custom training solutions?|solutions? de formation sur[- ]?mesure|simulation training)", re.I),
     "Conçoit des solutions de formation et simulation sur-mesure pour la défense."),
    (re.compile(r"\b(itar[- ]?free|alternatives?\s+itar)\b", re.I),
     "Conçoit des alternatives ITAR-free aux technologies US pour les forces françaises et européennes."),
    (re.compile(r"\b(unmanned ground vehicles?|robotized ground|ugv)\b.*\b(produces?|manufactur|develops?)", re.I),
     "Conçoit et fabrique des UGV et plateformes terrestres robotisées pour militaires et déminage."),
    (re.compile(r"\b(power solutions?|solutions? d['e]?[eé]nergie)\b.*\b(world[- ]?class|complete|life[- ]?cycle)", re.I),
     "Fournit des solutions énergétiques complètes (moteurs, support cycle de vie) pour défense et naval."),
    (re.compile(r"\b(spring(?:s?)|ressorts?)\b.*\b(production|fabrication|expertise)", re.I),
     "Conçoit et fabrique des ressorts techniques et composants mécaniques de précision."),
    (re.compile(r"\b(decision intelligence|intelligence d[eé]cisionnelle)\b", re.I),
     "Édite une plateforme de decision intelligence pour les commerciaux et acquisition défense."),
    (re.compile(r"\b(autonomous (operations|systems?|robots?)|autonomie op[eé]rationnelle)\b.*\b(gps[- ]?denied|offline|hors ligne)", re.I),
     "Édite des modèles IA pour systèmes autonomes en environnement GPS-denied et offline."),
    (re.compile(r"\b(seamless|lunker[- ]?free|extrusion)\b.*\b(aluminum|aluminium|copper|cuivre)", re.I),
     "Conçoit et fabrique des composants aluminium et cuivre par extrusion à impact pour la défense."),
    (re.compile(r"\b(treatment|traitement)\b.*\b(materials?|mat[eé]riaux)\b.*\b(aerospace|d[eé]fense|defense)", re.I),
     "Traite et finit les matériaux pour l'aérospatial, la défense et le ferroviaire."),
    (re.compile(r"\b(power solutions? brand mtu|mtu engines?)\b", re.I),
     "Conçoit et fabrique les moteurs MTU pour véhicules blindés, navires et systèmes industriels."),
    # ──────────── catch-all fallbacks (broad generic patterns last) ────────
    (re.compile(r"\b(developer|manufactur(?:er|ing)|maker|d[eé]veloppeur|fabricant)\b.*\b"
                r"(connectivity|connectivit|electronic|m[eé]canique|mechanical|"
                r"composite|optical|optique|hydraulic|hydraulique|software|logiciel)\b", re.I),
     "Conçoit et fabrique des sous-systèmes techniques pour la défense et l'aéronautique."),
    (re.compile(r"\b(provider|provides|fournisseur|fournit)\b.*\b"
                r"(defen[cs]e|d[eé]fense|military|forces? arm[eé]es|security|s[eé]curit[eé])\b", re.I),
     "Fournit des solutions et services techniques pour la défense et la sécurité."),
    (re.compile(r"\b(specialis[ze]es? in|sp[eé]cialis[eé]e? dans)\b.*\b"
                r"(defen[cs]e|d[eé]fense|military|tactical|critical|aerospace)\b", re.I),
     "Spécialisée dans la fourniture de solutions techniques à la défense et l'aéronautique."),
]


# Verbs / nouns that indicate the company itself does the action (vs
# talking about what its clients do). Broad — we want to gate against
# "Our clients use X" (which is about the customer's activity), not
# against perfectly valid noun-phrases like "DESIGNER AND MANUFACTURER".
_SELF_VERBS = re.compile(
    r"\b("
    # active verbs — third person sg/pl
    r"designs?|develops?|manufactur(?:es?|ing|er)|produces?|productions?|"
    r"specializ(?:es?|ing|ed)|sp[eé]cialis[eé]e?s?|provides?|offers?|"
    r"delivers?|operates?|supplies|integrates?|builds?|builds?\s+core|"
    r"engineer(?:s|ing)?|markets?|sells?|invests?|consults?|advises?|"
    r"supports?|assists?|enables?|empowers?|modernize[sd]?|refurbishes?|"
    # noun forms (very common in defense industry blurbs)
    r"designer|manufacturer|provider|supplier|developer|maker|"
    r"publisher|distributor|integrator|operator|expert|experts|leader|"
    r"specialists?|sp[eé]cialistes?|partners?|partenaires?|"
    r"founder|founders?|fondateurs?|"
    # FR equivalents
    r"con[cç]oit|fabrique|produit|d[eé]veloppe|[eé]dite|fournit|"
    r"distribue|propose|op[eè]re|sp[eé]cialis|conseille|"
    # "is/are" + descriptor — accepts passive presentations
    r"is\s+(?:a|an|the|one|now)\s+|"
    r"are\s+(?:a|an|the|characterized|particularly|specialised|specialized)\s+|"
    r"est\s+(?:un|une|le|la|les|sp[eé]cialis|expert)|"
    r"sommes\s+(?:sp[eé]cialis|expert|le|la|les)|"
    r"we\s+(?:are|do|design|develop|manufactur|produce|specializ|provide|offer|operate|supply|build)|"
    r"our\s+(?:systems?|products?|technology|solutions|expertise|portfolio|company)|"
    r"\bnous\b\s+"
    r")\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Map FR-sentence keywords → profile family. When a pattern matches, we
# look up the family of its FR sentence here. Most-specific keywords go
# first, generic ones last. The function returns the first family that
# matches — falls back to None (caller treats as INDUSTRIAL_DEFAULT).
# ---------------------------------------------------------------------------
_FR_TO_FAMILY: list[tuple[re.Pattern, str]] = [
    # ===== SPECIFIC FAMILIES FIRST (must match before generic VEHICLES) =====
    (re.compile(r"\bextinction d['e]?incendie|fire suppression", re.I), "FIRE_SUPPRESSION"),
    (re.compile(r"\bsupports? d['e]?armes?|gun mounts?\b", re.I), "GUN_MOUNTS"),
    (re.compile(r"\b(secours hydrauliques?|cisailles? hydrauliques?|écarteurs?)\b", re.I), "RESCUE_HYDRAULIC"),
    (re.compile(r"\bambulances? militaires?|véhicules? sanitaires?|sanitaires? militaires?", re.I), "VEHICLES_AMBULANCE"),
    (re.compile(r"\bengins? du génie\b|déneigeuse|excavateur militaire", re.I), "VEHICLES_ENGINEERING"),
    (re.compile(r"\bmodernise[a-z]*|modernisation flott|modernisation [a-zA-Z\-_2 ]*Leopard", re.I), "VEHICLES_MODERNIZATION"),
    (re.compile(r"\bsièges? (anti-mines?|blindés?|militaires?)\b", re.I), "SEATING_ARMORED"),
    (re.compile(r"\bsuspensions?|amortisseurs? (blindés?|militaires?)\b", re.I), "SUSPENSIONS"),
    (re.compile(r"\bchenilles?|trains? de roulement", re.I), "TRACK_SHOES"),
    (re.compile(r"\bpneus? (run-?flat|militaires?|tactiques?)\b", re.I), "TIRES_RUNFLAT"),
    # English keywords that ALSO map to families — we apply the same map
    # to first-sentence fallbacks (which can be in English) so the
    # profile stays coherent.
    (re.compile(r"\b(heavy|specialized) transport vehicles?\b", re.I), "VEHICLES_TRUCKS"),
    (re.compile(r"\b(transport|special) vehicles?\b", re.I), "VEHICLES_TRUCKS"),
    (re.compile(r"\b(rugged|durci) (computers?|laptops?|tablets?|terminals?|mobile)", re.I), "RUGGED_COMPUTING"),
    (re.compile(r"\b(fiber optic|optical fiber|fibres? optiques?)\b", re.I), "FIBER_OPTICS"),
    (re.compile(r"\b(precision (parts?|components?)|machining|m[eé]canique de pr[eé]cision)\b", re.I), "MACHINING"),
    (re.compile(r"\b(bearings?|paliers?|roulements?)\b", re.I), "MACHINING"),
    (re.compile(r"\b(slip rings?|bagues? glissantes?)", re.I), "POWER_ELECTRONICS"),
    (re.compile(r"\b(connectors?|interconnection|cable assemblies)\b", re.I), "CONNECTORS"),
    (re.compile(r"\b(electronic (security|surveillance)|s[eé]curit[eé] [eé]lectronique)\b", re.I), "CYBER_ENTERPRISE"),
    (re.compile(r"\b(membrane switches?|push button|claviers? membrane|interrupteurs?)\b", re.I), "POWER_ELECTRONICS"),
    (re.compile(r"\b(antennas? masts?|m[aâ]ts? d['e]?antennes?)\b", re.I), "ANTENNAS"),
    (re.compile(r"\b(advanced electro-?optical|electro[- ]?optic)\b", re.I), "OPTRONICS_EOIR"),
    (re.compile(r"\b(advanced photonic systems|photonic|photonique)\b", re.I), "FIBER_OPTICS"),
    (re.compile(r"\b(modular digital twin simulation|digital twin)\b", re.I), "SIMULATION_TRAINING"),
    (re.compile(r"\b(unmanned ground vehicles?|robotized ground|ugv)\b", re.I), "DRONES_ISR"),
    (re.compile(r"\b(autonomous robotics?|collaborative autonomy)\b", re.I), "AI_VISION"),
    (re.compile(r"\b(industrial shelter|tents? for rapid|tactical tents?)\b", re.I), "SHELTERS"),
    (re.compile(r"\b(industrial packaging|emballage industriel)\b", re.I), "PACKAGING"),
    (re.compile(r"\b(custom training solutions?|simulation training)\b", re.I), "SIMULATION_TRAINING"),
    (re.compile(r"\b(decision intelligence platform)\b", re.I), "AI_VISION"),
    (re.compile(r"\b(secure messaging|messagerie chiffr[eé]e)\b", re.I), "CRYPTO"),
    (re.compile(r"\b(special vehicles? manufacturer|trusted manufacturer in (the )?defen[cs]e)", re.I), "VEHICLES_TACTICAL"),
    (re.compile(r"\b(supply chain integrator|integrator (?:to|for) the aerospace)\b", re.I), "ENGINEERING_SERVICES"),
    (re.compile(r"\b(modernizes? legacy military|refurbishment.*military|reset and modernization)\b", re.I), "VEHICLES_MODERNIZATION"),
    (re.compile(r"\b(deploys? a constellation|leo constellation|low-earth orbit constellation)\b", re.I), "SATELLITE"),
    # ENERGY / POWER
    (re.compile(r"\b(générateurs?|énergétiques?|gestion d['e]?[eé]nergie|alternateur)\b", re.I), "ENERGY"),
    (re.compile(r"\bpiles? à combustible|hydrogène\b", re.I), "FUEL_CELLS"),
    (re.compile(r"\bbatteries?\s+(li-?ion|militaire|haute densité|primaires?)", re.I), "BATTERIES"),
    # 3D / ADDITIVE
    (re.compile(r"\b(impression 3D|fabrication additive|FA métal)\b", re.I), "ADDITIVE_MANUF"),
    # METAL
    (re.compile(r"\b(forge|forgé|forgeons)\b", re.I), "FORGING"),
    (re.compile(r"\businage\b|\bmécanique de précision\b|\bdécolletage\b|\bcentres? d['e]?usinage\b", re.I), "MACHINING"),
    (re.compile(r"\b(fonderie|moul[eé][a-z]*|aluminum extrusion|extrusion d['e]?aluminium)\b", re.I), "FOUNDRY"),
    (re.compile(r"\b(tôlerie|chaudronnerie|mécano-?soud)", re.I), "SHEET_METAL"),
    (re.compile(r"\b(aciers? blindés?|aciers haute dureté)\b", re.I), "STEEL_ARMOR"),
    (re.compile(r"\b(visserie|fixations?|boulonnerie)\b", re.I), "FASTENERS_AERO"),
    (re.compile(r"\b(aimants? permanents?|magnétiques?|magnétisme)\b", re.I), "MAGNETS"),
    (re.compile(r"\b(revêtements? de surface|traitement de surface|revêtements? PVD|finitions?)", re.I), "COATINGS_SURFACE"),
    # VEHICLES
    (re.compile(r"\b(modernise[a-z]*|modernisation flott|modernisation [a-zA-Z\-_2 ]*Leopard)\b", re.I), "VEHICLES_MODERNIZATION"),
    (re.compile(r"\b(ambulances? militaires?|sanitaires? militaires?|véhicules? sanitaires?)", re.I), "VEHICLES_AMBULANCE"),
    (re.compile(r"\bengins? du génie\b|\bdéneigeuse|\bexcavateur militaire", re.I), "VEHICLES_ENGINEERING"),
    (re.compile(r"\bcamions? (militaires?|tactiques?|logistiques?|lourds?)|\b(Unimog|Zetros|HEMTT|MTVR|HX|Actros)\b", re.I), "VEHICLES_TRUCKS"),
    (re.compile(r"\bvéhicules? (blindés?|tactiques?|tactical|sécurité)\b|\bblindage modulaire\b", re.I), "VEHICLES_ARMORED"),
    (re.compile(r"\bvéhicules? militaires?\b", re.I), "VEHICLES_TACTICAL"),
    # AERO / SPACE / DRONES
    (re.compile(r"\b(aérostats?|ballons? stratosphériques?|tethered ballon)\b", re.I), "DRONES_AEROSTAT"),
    (re.compile(r"\b(drones? FPV|munitions? rôdeuses?|loitering)\b", re.I), "DRONES_FPV"),
    (re.compile(r"\banti-?drones?\b|\bC[- ]?UAS\b|\bbrouilleurs? anti-drone\b", re.I), "COUNTER_DRONE"),
    (re.compile(r"\bdrones? (aériens?|tactiques?|ISR|cargo)\b|\bUAV\b", re.I), "DRONES_ISR"),
    (re.compile(r"\bsatellites?|terminaux? satellite|antennes? phased[- ]?array|constellation\b", re.I), "SATELLITE"),
    (re.compile(r"\bavions? (militaires?|de combat|de transport tactique)|\bhélicoptère\b|\bEurofighter|Rafale|F-?35\b", re.I), "AIRCRAFT"),
    # NAVAL
    (re.compile(r"\bnavires?|sous-marins?|sonars?|frégates?\b", re.I), "NAVAL"),
    # WEAPONS
    (re.compile(r"\bsupports? d['e]?armes?|gun mounts?\b", re.I), "GUN_MOUNTS"),
    (re.compile(r"\bmissiles?|armements? guidés?\b", re.I), "MISSILES"),
    (re.compile(r"\bmunitions?\b", re.I), "AMMUNITION"),
    (re.compile(r"\bexplosifs?|propergols?|matériaux énergétiques?", re.I), "EXPLOSIVES"),
    (re.compile(r"\barmes? (légères?|individuelles?|de poing|longues?)|\baccessoires? d['e]?arme", re.I), "ARMS_LIGHT"),
    (re.compile(r"\bpyrotechnie|fumigènes?|artifices?", re.I), "PYROTECHNICS"),
    (re.compile(r"\b(non-?létal|grenades? caoutchouc|gaz lacrymogène)", re.I), "NON_LETHAL"),
    # ELECTRONICS / SENSORS
    (re.compile(r"\bdétecteurs? IR|détecteurs? infrarouges?|imagerie (thermique|infrarouge)|optique cryogén", re.I), "IR_DETECTORS"),
    (re.compile(r"\boptronique|viseurs? (optiques?|holographique|red dot|reflex)|EO/IR\b", re.I), "OPTRONICS_EOIR"),
    (re.compile(r"\bradars?\b", re.I), "RADAR"),
    (re.compile(r"\bguerre électronique|jamming|brouilleurs? RF\b", re.I), "EW"),
    (re.compile(r"\bradios? tactiques?|MANET|SDR\b", re.I), "RADIOS_TACTICAL"),
    (re.compile(r"\bcapteurs? embarqués?|capteurs? sismiques?|capteurs? acoustiques?", re.I), "SENSORS_GENERIC"),
    (re.compile(r"\b(détecteurs? de rayonnement|CBRNe?\b)", re.I), "RADIATION"),
    # CONNECTORS / RUGGED ELEC
    (re.compile(r"\bconnecteurs?\b", re.I), "CONNECTORS"),
    (re.compile(r"\bharnais|câblage durci\b", re.I), "WIRING_HARNESS"),
    (re.compile(r"\bPCB|cartes? électroniques? durcies?\b", re.I), "PCB_HIREL"),
    (re.compile(r"\bordinateurs? durcis?|tablettes? durcies?|smartphones? durcis?", re.I), "RUGGED_COMPUTING"),
    (re.compile(r"\bconvertisseurs? DC/?DC|alimentations? militaires?|électronique de puissance\b", re.I), "POWER_ELECTRONICS"),
    # SOFTWARE
    (re.compile(r"\bIA / vision|détection automatique|fusion de capteurs", re.I), "AI_VISION"),
    (re.compile(r"\bcyber range\b", re.I), "CYBER_RANGE"),
    (re.compile(r"\bcybersécurité OT|cyber/OT|ICS\b", re.I), "CYBER_OT"),
    (re.compile(r"\b(NDR|Network Detection|cybersécurité|malware analysis|cybers[eé]curit[eé])", re.I), "CYBER_ENTERPRISE"),
    (re.compile(r"\b(cryptograph|chiffrement|messagerie chiffrée|cloud souverain)\b", re.I), "CRYPTO"),
    (re.compile(r"\bOSINT|knowledge graph|graph analytics", re.I), "OSINT"),
    (re.compile(r"\bsimulateurs? d['e]?entraînement|VR|réalité (virtuelle|augmentée)|XR\b", re.I), "SIMULATION_TRAINING"),
    (re.compile(r"\bcloud (souverain|de défense|sov)\b", re.I), "CLOUD_SOVEREIGN"),
    # PROTECTION
    (re.compile(r"\bcasques? de combat|casques? composites?", re.I), "HELMETS"),
    (re.compile(r"\bgilets? pare-balles?|plaques? balistiques?|niveau IV", re.I), "BODY_ARMOR"),
    (re.compile(r"\bblindage|panneaux? blindés?|protection balistique\b", re.I), "BALLISTIC_PROTECTION"),
    (re.compile(r"\btextiles? techniques?|camouflage|ignifugés?|tissus?\b", re.I), "TEXTILE_TECH"),
    (re.compile(r"\bbottes?|chaussures? (de )?(combat|tactiques?|sécurité)", re.I), "BOOTS"),
    (re.compile(r"\bgants? tactiques?", re.I), "GLOVES_TACTICAL"),
    (re.compile(r"\bsacs? à dos|harnais porte-équipement", re.I), "BACKPACKS_LOAD_CARRYING"),
    (re.compile(r"\bdécontamination NRBC", re.I), "DECONTAMINATION"),
    (re.compile(r"\bNRBC|CBRN|filtration NRBC|masques? NRBC", re.I), "NRBC"),
    (re.compile(r"\bshelters?|abris déployables?|conteneurs? durcis?", re.I), "SHELTERS"),
    (re.compile(r"\bcamps? (déployables?|militaires?|humanitaires?)\b", re.I), "CAMPS_DEPLOYABLE"),
    (re.compile(r"\bemballages? (techniques?|durcis?|militaires?|industriels?)|\bcaisses? techniques?\b", re.I), "PACKAGING"),
    # MEDICAL
    (re.compile(r"\b(antidotes?|contre-mesures? médicales?|pharmaceutique)\b", re.I), "MEDICAL_COUNTERMEASURES"),
    (re.compile(r"\brations? (MRE|militaires?|de combat)\b", re.I), "RATIONS_MILITARY"),
    (re.compile(r"\bpurification d['e]?eau|stations? mobile[s]? eau", re.I), "WATER_PURIFICATION"),
    # ENGINES
    (re.compile(r"\bmoteurs? (aéronautiques?|hélico)\b|\bturbomachine\b", re.I), "ENGINES_AERO"),
    (re.compile(r"\bmoteurs? (diesel|haute puissance)\b", re.I), "ENGINES_DIESEL"),
    (re.compile(r"\btransmissions?|réducteurs?|boîtes de vitesses\b", re.I), "TRANSMISSIONS"),
    (re.compile(r"\bhydraulique|amortisseurs? hydrauliques?", re.I), "HYDRAULICS"),
    (re.compile(r"\bchenilles?|trains? de roulement", re.I), "TRACK_SHOES"),
    (re.compile(r"\bpneus? (run-?flat|militaires?|tactiques?)\b", re.I), "TIRES_RUNFLAT"),
    (re.compile(r"\bsièges? (anti-mines?|blindés?|militaires?)\b", re.I), "SEATING_ARMORED"),
    (re.compile(r"\bsuspensions?|amortisseurs? (blindés?|militaires?)\b", re.I), "SUSPENSIONS"),
    (re.compile(r"\bextinction d['e]?incendie|fire suppression", re.I), "FIRE_SUPPRESSION"),
    (re.compile(r"\b(secours hydrauliques?|cisailles? hydrauliques?|écarteurs?)\b", re.I), "RESCUE_HYDRAULIC"),
    # MATERIALS
    (re.compile(r"\bmatériaux composites?|composites? carbone\b", re.I), "COMPOSITES"),
    (re.compile(r"\bcéramiques? balistiques?|céramiques? techniques?", re.I), "CERAMICS_BALLISTIC"),
    # COMMS / NETWORK
    (re.compile(r"\bantennes? (RF|durcies?|militaires?|phased)", re.I), "ANTENNAS"),
    (re.compile(r"\bfibres? optiques?|photonique\b", re.I), "FIBER_OPTICS"),
    (re.compile(r"\b5G|P-?LTE|réseaux? privés?\b", re.I), "5G_PRIVATE"),
    (re.compile(r"\bsatcom|satellite (communications?|connectivité)\b", re.I), "SATCOM"),
    (re.compile(r"\bstreaming vidéo|encodeurs? vidéo|décodeurs?", re.I), "STREAMING_VIDEO"),
    (re.compile(r"\bC4ISR|C2|fusion de données\b", re.I), "C4ISR"),
    (re.compile(r"\bTSCM|contre-espionnage", re.I), "TSCM_COUNTERESPIONAGE"),
    # NAVIGATION / GEO
    (re.compile(r"\bnavigation inertielle|centrales? inertielles?|MEMS\b", re.I), "INERTIAL_NAVIGATION"),
    (re.compile(r"\bSIG / cartograph|géospatial|imagerie satellite analysée\b", re.I), "GEOSPATIAL"),
    (re.compile(r"\bGNSS|RTK|haute précision GPS", re.I), "GNSS_RTK"),
    # CONSULTING
    (re.compile(r"\bobsolescence|sourcing\b", re.I), "CONSULTING_OBSOLESCENCE"),
    (re.compile(r"\b(CEM militaire|essais environnementaux|qualification)\b", re.I), "QUALIFICATION_TESTING"),
    (re.compile(r"\b(bureau d['e]?[eé]tudes|ingénierie système|conseil ingénierie|engineering services?)\b", re.I), "ENGINEERING_SERVICES"),
]


def _family_for_activity(activity_fr: str) -> Optional[str]:
    """Return the profile family for a French activity sentence."""
    for pattern, family in _FR_TO_FAMILY:
        if pattern.search(activity_fr):
            return family
    return None


# ---------------------------------------------------------------------------
# Lightweight EN → FR verb converter — applied as a *final* polish on the
# first-sentence fallback so the activity reads more like French even when
# we couldn't match a specific pattern.  We don't try to translate the full
# sentence (that needs an LLM) ; we just rewrite the verb / leading noun
# phrase so the user sees a French-leading sentence.
# ---------------------------------------------------------------------------
_EN_FR_REWRITES: list[tuple[re.Pattern, str]] = [
    # Leading "X is a/the leading manufacturer/supplier/provider of Y" → "Conçoit et fabrique des Y."
    (re.compile(r"^(?:[A-Z][\w&\s\.,\-]{0,40}\s+)?(?:is|was)\s+(?:a|an|the)\s+"
                r"(?:global\s+|world['s]*\s+|leading\s+|top\s+|major\s+|"
                r"trusted\s+|innovative\s+|recognized\s+)*"
                r"(?:manufacturer|supplier|provider|maker|producer)\s+of\s+(.+?)$", re.I),
     r"Conçoit et fabrique \1"),
    (re.compile(r"^(?:[A-Z][\w&\s\.,\-]{0,40}\s+)?(?:is|was)\s+(?:a|an|the)\s+"
                r"(?:global\s+|world['s]*\s+|leading\s+|top\s+|major\s+|"
                r"trusted\s+|innovative\s+|recognized\s+|)*"
                r"(?:designer|developer|engineering company)\s+of\s+(.+?)$", re.I),
     r"Conçoit \1"),
    (re.compile(r"^(?:[A-Z][\w&\s\.,\-]{0,40}\s+)?(?:is|was)\s+(?:a|an|the)\s+"
                r"(?:leading\s+|trusted\s+|innovative\s+|specialized\s+)*"
                r"(?:integrator|distributor|reseller)\s+(?:of\s+|for\s+|to\s+)(.+?)$", re.I),
     r"Intègre et distribue \1"),
    # "X specializes in Y" / "X is specialized in Y" → "Spécialisée dans Y."
    (re.compile(r"^(?:[A-Z][\w&\s\.,\-]{0,40}\s+)?(?:is\s+)?(?:specialise[sd]|specializ(?:es|ed))\s+in\s+(.+?)$", re.I),
     r"Spécialisée dans \1"),
    # "X provides Y" → "Fournit Y."
    (re.compile(r"^(?:[A-Z][\w&\s\.,\-]{0,40}\s+)?(?:provides|delivers|offers|supplies)\s+(.+?)$", re.I),
     r"Fournit \1"),
    # "X develops Y" / "X designs and manufactures Y" → "Conçoit et fabrique Y."
    (re.compile(r"^(?:[A-Z][\w&\s\.,\-]{0,40}\s+)?(?:designs?\s+(?:and|et)\s+manufactures?|"
                r"manufactur(?:es|ing)\s+(?:and|et)\s+sells?|"
                r"develops?\s+(?:and|et)\s+manufactures?|"
                r"engineers?\s+and\s+manufactures?|"
                r"designs?\s+and\s+builds?)\s+(.+?)$", re.I),
     r"Conçoit et fabrique \1"),
    (re.compile(r"^(?:[A-Z][\w&\s\.,\-]{0,40}\s+)?(?:designs?|develops?|builds?|engineers?|"
                r"produces?|manufactur(?:es|ing))\s+(.+?)$", re.I),
     r"Conçoit \1"),
    # "X operates Y" → "Opère Y."
    (re.compile(r"^(?:[A-Z][\w&\s\.,\-]{0,40}\s+)?operates?\s+(.+?)$", re.I),
     r"Opère \1"),
    # "X supports Y" → "Accompagne Y."
    (re.compile(r"^(?:[A-Z][\w&\s\.,\-]{0,40}\s+)?(?:supports|assists|enables|empowers)\s+(.+?)$", re.I),
     r"Accompagne \1"),
    # "X is a leader in Y" → "Leader sur Y."
    (re.compile(r"^(?:[A-Z][\w&\s\.,\-]{0,40}\s+)?(?:is|was)\s+(?:a|the)\s+"
                r"(?:global\s+|world['s]*\s+|market\s+)?leader\s+in\s+(.+?)$", re.I),
     r"Leader sur \1"),
    # Bare "A leading X for Y" / "An innovative X" → "Société X" prefix
    (re.compile(r"^A\s+(global|leading|trusted|innovative|recognized)\s+"
                r"(manufacturer|supplier|provider|company|specialist)\s+(.+?)$", re.I),
     r"Société \1 \2 \3"),
    # "We are X" → "Société X" (drop "We are")
    (re.compile(r"^We\s+are\s+(?:a|an|the)\s+(.+?)$", re.I),
     r"Société \1"),
]


def _polish_first_sentence(text: str) -> str:
    """Apply lightweight English→French verb rewrites on a first-sentence
    fallback. Each rule matches the *whole* sentence and rewrites only the
    leading verb phrase ; the rest stays in English. This produces
    half-French-half-English output but the cell now reads as French copy
    rather than a foreign-language paragraph.
    """
    if not text:
        return text
    for pattern, replacement in _EN_FR_REWRITES:
        new = pattern.sub(replacement, text, count=1)
        if new != text:
            text = new
            break  # only one rewrite per sentence
    # Re-capitalize first letter
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    # Remove trailing dot if added by replacement
    text = text.rstrip(" ,.;-")
    if not text.endswith((".", "!", "?", "…")):
        text += "."
    return text


def _first_sentences(text: str, n: int = 2) -> str:
    """Return the first ``n`` *meaningful* sentences of ``text``.

    A "meaningful" sentence is at least 25 characters long — this skips
    over single-word fragments like "Drive." / "Control." / "Protect."
    that some marketing copy uses as taglines, and instead picks up the
    next real sentence describing the company.
    """
    if not text:
        return ""
    parts = re.split(r"(?<=[\.!?])\s+", text.strip())
    meaningful = [p for p in parts if len(p) >= 25]
    if not meaningful:
        return text.strip()
    return " ".join(meaningful[:n]).strip()


def extract_activity(
    name: str,
    short_presentation: Optional[str],
    business_areas: Optional[list[str]] = None,
    keywords: Optional[list[str]] = None,
    headline: Optional[str] = None,
    business_model: Optional[str] = None,
    built_products: Optional[list[str]] = None,
) -> tuple[str, str, str, Optional[str]]:
    """Return ``(activity_1liner, kind, source, profile_family)``.

    ``profile_family`` is the key into ``activity_profiles.PROFILES`` —
    the caller can use it to look up the coherent set of canonical
    products / technologies / target_buyers / why_template that matches
    the activity. ``None`` when no family matched (caller should fall
    back to the legacy product-mapping).

    ``source`` is one of :
      - ``kind_template`` (kind-based, no product inference applied)
      - ``pattern_match`` (matched a known activity pattern in text)
      - ``first_sentence`` (first-sentence fallback, EN→FR polished)
      - ``fallback_built_products`` (legacy product-mapping path)
      - ``thin_data`` (nothing usable)
    """
    sp = (short_presentation or "").strip()
    hl = (headline or "").strip()
    text = sp or hl

    kind = detect_company_kind(name, sp, business_areas, keywords)

    # 1) Non-INDUSTRIAL kinds : always template, profile based on kind.
    if kind != "INDUSTRIAL":
        return (
            _KIND_TEMPLATES[kind], kind, "kind_template",
            KIND_TO_PROFILE.get(kind),
        )

    # 2) INDUSTRIAL : try pattern-match on the first 3 sentences.
    if text and len(text) > 30 and _SELF_VERBS.search(_first_sentences(text)):
        first_par = _first_sentences(text, n=3)
        for pattern, fr in _ACTIVITY_PATTERNS:
            if pattern.search(first_par):
                family = _family_for_activity(fr)
                return fr, kind, "pattern_match", family

        # 2.5) Smarter fallback : take the first sentence of the
        # short_presentation truncated to 140c. This is rough English
        # (no translation) but it's the company's own description rather
        # than the buggy ``built_products`` from the previous extractor.
        # Always preferred over ``built_products`` when text is rich.
        if sp and len(sp) > 60:
            first = _first_sentences(sp, n=1).strip()
            # Drop opening "filler" prefixes that don't carry the activity:
            #   "Founded in 1965, "
            #   "Born in Italy in 1945, "
            #   "Since 1963, we have continually expanded our expertise…"
            #   "Established 10+ years, "
            #   "With over 25 years of experience, "
            #   "For more than 40 years, "
            #   "Welcome to X — "
            #   "About us: "
            preamble = re.compile(
                r"^("
                r"Founded\s+in[^,.]+[,.]|"
                r"Established[^,.]+[,.]|"
                r"Born\s+in\s+\w+[^,.]+[,.]|"
                r"Since\s+(?:its establishment|\d{4})[^,.]+[,.]|"
                r"With\s+(?:over\s+)?\d+\s*\+?\s*years[^,.]+[,.]|"
                r"For\s+(?:over\s+|more than\s+)?\d+\s*\+?\s*years[^,.]+[,.]|"
                r"Over\s+\d+\s+years[^,.]+[,.]|"
                r"Welcome\s+to[^.!]+[.!\u2014\-]|"
                r"About\s+us\s*[:\-\u2014]\s*|"
                r"Bienvenue[^,.]+[,.]|"
                r"Fond[eé]e?\s+en[^,.]+[,.]|"
                r"Cr[eé][eé]e?\s+en[^,.]+[,.]|"
                r"Depuis\s+\d{4}[^,.]+[,.]"
                r")\s*",
                re.I,
            )
            first = preamble.sub("", first).strip()
            # If the sentence STARTS with the company name, drop it so
            # we don't repeat what's already in the cell next door.
            if name:
                first = re.sub(
                    rf"^{re.escape(name)}\s+(is|was|provides|designs|"
                    r"manufactures|develops|specialise[sd]|specializes|"
                    r"offers|delivers|operates|supplies|integrates|"
                    r"produces?|builds?|engineers?|"
                    r"a\s+|an\s+|the\s+|now\s+)",
                    "", first, flags=re.I,
                ).strip()
                # Also drop "X is " constructions where X is a short
                # acronym variant of the name (e.g. "DCT designs" when
                # name is "Digital Core Technologies").
                acronym_match = re.match(
                    rf"^([A-Z]{{2,6}})\s+(is|was|provides|designs|"
                    r"manufactures|develops|specializes|specialise[sd]|"
                    r"offers|delivers|operates|supplies|integrates|"
                    r"produces?|builds?|engineers?)\s+",
                    first,
                )
                if acronym_match:
                    first = first[acronym_match.end():]
                if first and first[0].islower():
                    first = first[0].upper() + first[1:]
            # Final cleanup : drop trailing fragments / capitalize
            first = first.strip(" ,;-")
            if len(first) > 140:
                first = first[:137].rstrip(" ,.;-") + "…"
            if len(first) >= 30:
                # Apply lightweight EN→FR verb rewrite as the final polish.
                first = _polish_first_sentence(first)
                if len(first) > 140:
                    first = first[:137].rstrip(" ,.;-") + "…"
                    if not first.endswith((".", "!", "?", "…")):
                        first += "."
                # Try to infer a family even from polished first-sentence.
                family = _family_for_activity(first)
                return first, kind, "first_sentence", family

    # 3) Fallback : caller will use the legacy built_products mapping.
    return (
        "", kind,
        "fallback_built_products" if built_products else "thin_data",
        None,
    )


__all__ = ["extract_activity"]
