"""Infer canonical product categories from any text source.

Why this exists
---------------
After the trinity-coherence rewrite, ``built_products`` from the
previous extractor is no longer trusted (too polluted). For fiches
where no profile family matched, the products field stays empty —
which leaves the Excel filter with nothing to bite on.

This module provides a **second-chance inference** : scan the activity
sentence + short_presentation + headline for known product / sector
keywords and return a list of canonical product categories from
``app.processors.taxonomy_normalize.PRODUCT_CATEGORIES``.

The bar is intentionally HIGH : we only return a category when the
text contains a strong, unambiguous keyword. Wrong > empty here.
"""
from __future__ import annotations

import re
from typing import Optional


# Each entry: (regex_pattern, list of canonical PRODUCT_CATEGORIES values
# from taxonomy_normalize.PRODUCT_CATEGORIES). Keep specifics first.
_TEXT_TO_PRODUCT_CATS: list[tuple[re.Pattern, list[str]]] = [
    # ===== AERIAL / SPACE =====
    (re.compile(r"\b(combat aircraft|fighter|avion de combat|chasseur)\b", re.I),
     ["Avions & hélicoptères militaires"]),
    (re.compile(r"\b(helicopter|h[eé]licopt[eè]re|nh90|tigre|h160)\b", re.I),
     ["Avions & hélicoptères militaires"]),
    (re.compile(r"\b(transport aircraft|avion de transport|a400m|c-130|c-?295)\b", re.I),
     ["Avions & hélicoptères militaires"]),
    (re.compile(r"\bdrones?\s+(fpv|kamikaze|loitering|r[oô]de[ar]?)|\bmunitions? r[oô]de[ar]?\b", re.I),
     ["Drones FPV & munitions rôdeuses"]),
    (re.compile(r"\bcounter[- ]?(uav|drone)|c[- ]?uas|c-uav|anti-?drone\b", re.I),
     ["Systèmes anti-drone (C-UAS)"]),
    (re.compile(r"\b(uav|uavs|drones?|unmanned aerial|unmanned aircraft)\b", re.I),
     ["Drones aériens (UAV)"]),
    (re.compile(r"\b(satellite|cubesat|leo)\b", re.I),
     ["Satellites & CubeSats"]),
    (re.compile(r"\b(antenne|antenna)\s+(satellite|phased[- ]?array)\b", re.I),
     ["Terminaux & antennes spatiales"]),
    (re.compile(r"\b(aerostat|tethered ballon|ballon stratosph[eé]rique|airship)\b", re.I),
     ["Drones aériens (UAV)"]),

    # ===== NAVAL =====
    (re.compile(r"\b(navire|sous-?marin|fr[eé]gate|naval combat|patrol boat|patrouilleur)\b", re.I),
     ["Navires & sous-systèmes navals"]),
    (re.compile(r"\bsonar\b", re.I),
     ["Sonars & systèmes ASM"]),

    # ===== LAND VEHICLES =====
    (re.compile(r"\b(armoured? vehicle|armored vehicle|v[eé]hicule blind[eé]|tank|char\s+(?:lourd|de combat)|"
                r"main battle tank|leopard|abrams)\b", re.I),
     ["Véhicules blindés"]),
    (re.compile(r"\b(robot terrestre|ugv|unmanned ground vehicle)\b", re.I),
     ["Robots terrestres (UGV / EOD)"]),
    (re.compile(r"\b(camion militaire|military truck|truck for defen|hemtt|unimog|zetros|mtvr)\b", re.I),
     ["Camions militaires & logistique"]),
    (re.compile(r"\b(engin du g[eé]nie|engineering vehicle|combat engineer|excavateur militaire)\b", re.I),
     ["Engins du génie militaire"]),
    (re.compile(r"\b(ambulance militaire|military ambulance|sanitaire militaire)\b", re.I),
     ["Ambulances & véhicules sanitaires"]),
    (re.compile(r"\b(v[eé]hicule tactique|tactical vehicle|4x4 tactique|6x6|8x8)\b", re.I),
     ["Véhicules tactiques (non blindés)"]),
    (re.compile(r"\b(modernisation|modernization|retrofit|upgrade)\s+(?:flotte|tank|chars|vehicle)", re.I),
     ["Véhicules blindés"]),

    # ===== WEAPONS =====
    (re.compile(r"\b(missile|guided weapon|armement guid[eé]|spike|javelin|stinger|aster)\b", re.I),
     ["Missiles & armements guidés"]),
    (re.compile(r"\b(roquette|rocket launcher|lance-?roquette)\b", re.I),
     ["Roquettes & lance-roquettes"]),
    (re.compile(r"\b(155\s*mm|105\s*mm|120\s*mm|125\s*mm|obus|gros calibre)\b", re.I),
     ["Munitions gros calibre & obus"]),
    (re.compile(r"\b(5[\.,]?56|7[\.,]?62|9\s*mm|petit calibre|moyen calibre|small arms ammunition)\b", re.I),
     ["Munitions petit/moyen calibre"]),
    (re.compile(r"\bmunitions?\b", re.I),
     ["Munitions petit/moyen calibre"]),
    (re.compile(r"\b(fusil|carabine|pistolet|rifle|carbine|firearm|small arms|armes? l[eé]g[eè]re)\b", re.I),
     ["Armes légères & accessoires"]),
    (re.compile(r"\b(canon|howitzer|obusier|main gun|artillery)\b", re.I),
     ["Armes lourdes & systèmes de tir"]),
    (re.compile(r"\b(gun mount|support d['e]?arme)\b", re.I),
     ["Armes lourdes & systèmes de tir"]),
    (re.compile(r"\b(non[- ]?l[eé]thal|non[- ]?lethal|gaz lacrymog[eè]ne|riot|maintien de l['e]?ordre)\b", re.I),
     ["Armes non-létales"]),
    (re.compile(r"\b(propergol|explosif|propellant|explosive|rdx|hmx|tnt|matiere [eé]nerg[eé]tique)\b", re.I),
     ["Explosifs & matériaux énergétiques"]),
    (re.compile(r"\b(pyrotechnie|fum[ie]g[eè]ne|smoke grenade)\b", re.I),
     ["Pyrotechnie & artifices"]),
    (re.compile(r"\b(rws|tourelle t[eé]l[eé]op[eé]r[eé]e|remote weapon station)\b", re.I),
     ["Tourelles téléopérées (RWS)"]),

    # ===== SENSORS / OPTRONICS / RADAR / EW =====
    (re.compile(r"\b(optronic|optronique|electro[- ]?optical|eo[- ]?ir|eo/ir|sniper pod)\b", re.I),
     ["Optronique & viseurs (EO/IR)"]),
    (re.compile(r"\b(viseur|riflescope|red dot|reflex|holographique|acog|aimpoint|trijicon)\b", re.I),
     ["Optiques d'armes (viseurs)"]),
    (re.compile(r"\b(jumelle|binocular|night vision|vision nocturne)\b", re.I),
     ["Vision nocturne & jumelles"]),
    (re.compile(r"\b(infrared detector|d[eé]tecteur infrarouge|ir detector|mwir|lwir|swir|stirling)\b", re.I),
     ["Détecteurs IR refroidis & non-refroidis"]),
    (re.compile(r"\b(thermal|thermique|imagerie ir|infrared imaging)\b", re.I),
     ["Optronique & viseurs (EO/IR)"]),
    (re.compile(r"\b(radar)\b", re.I),
     ["Radars & traitement signal"]),
    (re.compile(r"\b(electronic warfare|guerre [eé]lectronique|sigint|elint|jamming|brouilleur|brouillage)\b", re.I),
     ["Systèmes de guerre électronique (EW)"]),
    (re.compile(r"\b(iff|identification ami|mode[- ]?5)\b", re.I),
     ["Systèmes IFF & identification"]),
    (re.compile(r"\b(seismic sensor|gunshot detection|capteur sismique|capteur acoustique)\b", re.I),
     ["Capteurs sismiques & acoustiques"]),
    (re.compile(r"\b(sensor|capteur)\s+(?:embarqu|embedd|imu)", re.I),
     ["Capteurs embarqués (généraux)"]),

    # ===== COMMUNICATIONS / C4ISR =====
    (re.compile(r"\b(radio tactique|tactical radio|sdr|software defined radio|manet|mesh)\b", re.I),
     ["Radios tactiques & SDR"]),
    (re.compile(r"\bantennas?\b|\bantennes?\b", re.I),
     ["Antennes & infrastructures RF"]),
    (re.compile(r"\b5g|p[- ]?lte|tetra|p25|tetrapol\b", re.I),
     ["Réseaux militaires (5G/P-LTE/MANET)"]),
    (re.compile(r"\b(c4isr|c2|command (and|et) control|station de commandement)\b", re.I),
     ["Systèmes C2 & C4ISR"]),
    (re.compile(r"\b(streaming|encod|d[eé]cod)\s+(?:vid[eé]o|video)\b", re.I),
     ["Streaming vidéo & data tactiques"]),

    # ===== SOFTWARE / AI / CYBER =====
    (re.compile(r"\b(decision intelligence|intelligence d[eé]cisionnelle|fusion de capteurs|sensor fusion)\b", re.I),
     ["Plateformes IA / vision défense"]),
    (re.compile(r"\bia\b|\bartificial intelligence|machine learning|deep learning\b", re.I),
     ["Plateformes IA / vision défense"]),
    (re.compile(r"\b(cyber range|red team|cyber-?attack training)\b", re.I),
     ["Logiciels de simulation & cyber range"]),
    (re.compile(r"\b(cybers[eé]curit[eé]|cyber[- ]?security|edr|ndr|firewall)\b", re.I),
     ["Cybersécurité (logiciels & appliances)"]),
    (re.compile(r"\b(simulator|simulateur|simulation|vr training|xr|r[eé]alit[eé] (virtuelle|augment[eé]e))\b", re.I),
     ["Logiciels de simulation & cyber range"]),

    # ===== INDIVIDUAL SOLDIER / PROTECTION =====
    (re.compile(r"\b(helmet|casque)\s+(combat|tactique|composite|ballistic|balistique)\b", re.I),
     ["Casques de combat"]),
    (re.compile(r"\b(body armor|gilet pare[- ]?balles|plaque balistique|niveau iv|level iv|level iiia)\b", re.I),
     ["Gilets & plaques pare-balles"]),
    (re.compile(r"\b(vitrage blind[eé]|armoured glass|bulletproof glass)\b", re.I),
     ["Vitrages blindés"]),
    (re.compile(r"\b(textile technique|tissu camouflage|fabric ballist|ignifug)\b", re.I),
     ["Textiles techniques tactiques"]),
    (re.compile(r"\b(boots? de combat|combat boots?|chaussures? tactiques?)\b", re.I),
     ["Équipement du fantassin (général)"]),
    (re.compile(r"\b(soldier system|fantassin connect[eé]|soldier connected|kit fantassin)\b", re.I),
     ["Équipement du fantassin (général)"]),
    (re.compile(r"\b(cbrn|nrbc|nbc|filtration nrbc|d[eé]tection nrbc|gas mask|masque [aà] gaz)\b", re.I),
     ["NRBC (masques, tenues, détecteurs)"]),

    # ===== ENERGY / PROPULSION =====
    (re.compile(r"\b(g[eé]n[eé]rateur|generator)\s+(portable|tactique|militaire)\b", re.I),
     ["Groupes électrogènes & énergie tactique"]),
    (re.compile(r"\b(group[ie] [eé]lectrog[eè]ne|tactical genset|alternateur)\b", re.I),
     ["Groupes électrogènes & énergie tactique"]),
    (re.compile(r"\b(batter[iy]e[s]?|li-?ion|lithium-?ion|fuel cell|pile [aà] combustible|hydrog[eè]ne)\b", re.I),
     ["Batteries & packs énergétiques"]),
    (re.compile(r"\b(diesel engine|moteur diesel|cummins|caterpillar|mtu)\b", re.I),
     ["Moteurs diesel / propulsion lourde"]),
    (re.compile(r"\b(a[eé]ronautical engine|moteur a[eé]ronautique|m88|rtm322|rtm 322)\b", re.I),
     ["Moteurs aéronautiques"]),

    # ===== SUB-SYSTEMS / COMPONENTS =====
    (re.compile(r"\b(connector|connecteur|harness|harnais)\b.*\b(rugged|durci|mil[- ]?grade|military|defense|d[eé]fense)\b", re.I),
     ["Connectique & câblage durcis"]),
    (re.compile(r"\b(connectors?|connecteurs?|harness|harnais)\b", re.I),
     ["Connectique & câblage durcis"]),
    (re.compile(r"\b(machining|usinage|d[eé]colletage|cnc|5[- ]?ax)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(steel forging|forge|forging|forgings?|forg[eé])\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(transmission|gearbox|r[eé]ducteur|bo[iî]te de vitesse)\b", re.I),
     ["Mécanique de transmission"]),
    (re.compile(r"\b(hydraulique|hydraulic|amortisseur hydraulique|cylinder)\b", re.I),
     ["Hydraulique & motion control"]),
    (re.compile(r"\b(composite|carbon fiber|fibre carbone|kevlar|uhmwpe|dyneema)\b", re.I),
     ["Matériaux composites & blindage"]),
    (re.compile(r"\b(armor steel|acier blind|aciers? haute dur)\b", re.I),
     ["Aciers & métallurgie spéciale"]),
    (re.compile(r"\b(superalloy|superalliage|titanium|titane|nickel forge)\b", re.I),
     ["Aciers & métallurgie spéciale"]),
    (re.compile(r"\b(c[eé]ramique balistique|ballistic ceramic|sic|silicon carbide|hexoloy)\b", re.I),
     ["Céramiques techniques & balistiques"]),
    (re.compile(r"\b(visserie|fastener|fixation|boulonnerie|lock[- ]?bolt)\b", re.I),
     ["Fixations & visserie aéronautique"]),
    (re.compile(r"\b(power supply durci|alimentation militaire|convertisseur dc/?dc|power electronic)\b", re.I),
     ["Alimentations & convertisseurs durcis"]),
    (re.compile(r"\b(rugged enclosur|bo[iî]tier durci|rack durci|vpx|vme|schroff)\b", re.I),
     ["Boîtiers & châssis durcis"]),
    (re.compile(r"\b(avionic|avionique|cockpit|calculateur de vol|flight computer)\b", re.I),
     ["Avionique & instruments embarqués"]),
    (re.compile(r"\b(servomotor|servomoteur|brushless|sans balais)\b", re.I),
     ["Servomoteurs & motion control précision"]),
    (re.compile(r"\b(rugged computer|ordinateur durci|tablette durcie|panasonic toughbook|getac)\b", re.I),
     ["Composants électroniques & cartes"]),
    (re.compile(r"\b(printed circuit board|pcb|carte [eé]lectronique)\b", re.I),
     ["Composants électroniques & cartes"]),
    (re.compile(r"\b(semicond|microelec|fpga|asic|hi-rel)\b", re.I),
     ["Composants électroniques & cartes"]),

    # ===== LOGISTICS / INFRASTRUCTURE =====
    (re.compile(r"\b(shelter|abri d[eé]ployable|conteneur durci|tactical container)\b", re.I),
     ["Conteneurs, malles, shelters"]),
    (re.compile(r"\b(emballage technique|industrial packaging|packaging militaire|ttv qualif)\b", re.I),
     ["Conteneurs, malles, shelters"]),
    (re.compile(r"\b(camion logistique|tactical truck|lourd transport|recovery truck)\b", re.I),
     ["Camions militaires & logistique"]),
    (re.compile(r"\b(antenna mast|m[aâ]t antenne|m[aâ]t t[eé]lescopique)\b", re.I),
     ["Mâts, signalisation & balisage"]),
    (re.compile(r"\b(stations? de purification|water purification|filtration eau)\b", re.I),
     ["Stations de purification d'eau & infra"]),
    (re.compile(r"\b(seat anti[- ]?mine|si[eè]ge anti[- ]?mine|si[eè]ge blind)\b", re.I),
     ["Sièges & ergonomie cabine"]),
    (re.compile(r"\b(lubricant|lubrifiant|graisse|grease)\s+(?:militaire|industriel|tactique)\b", re.I),
     ["Lubrifiants & fluides"]),

    # ===== ENGINEERING SERVICES / INDUSTRIAL =====
    (re.compile(r"\b(engineering company|engineering firm|engineering services?|"
                r"bureau d['e]?[eé]tudes|cabinet d['e]?ing[eé]nierie|"
                r"engineering expertise|expertise d['e]?ing[eé]nierie)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(mechatronic|m[eé]catronique)\b", re.I),
     ["Composants électroniques & cartes"]),
    (re.compile(r"\b(software company|[eé]diteur logiciel|software (?:firm|developer|publisher))\b", re.I),
     ["Logiciels métier défense (autres)"]),
    (re.compile(r"\b(document management|gestion documentaire|content management)\b", re.I),
     ["Logiciels métier défense (autres)"]),
    (re.compile(r"\b(connectivity|edge processing|edge computing|edge ai)\b", re.I),
     ["Plateformes IA / vision défense"]),
    (re.compile(r"\b(armour|armor|protection balist|blindage)\s+(engineering|solutions?|systems?)\b", re.I),
     ["Matériaux composites & blindage"]),
    (re.compile(r"\b(aluminium|aluminum)\s+(tanks?|cuves?|reservoirs?)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(special distributor|distributor (?:of|for))\b", re.I),
     ["Véhicules tactiques (non blindés)"]),
    (re.compile(r"\b(automotive group|group automobile)\b", re.I),
     ["Véhicules tactiques (non blindés)"]),
    (re.compile(r"\b(power management|gestion d['e]?[eé]nergie|power solutions?)\b", re.I),
     ["Groupes électrogènes & énergie tactique"]),
    (re.compile(r"\b(motion solutions?|motion control|motion-?driven)\b", re.I),
     ["Servomoteurs & motion control précision"]),

    # ===== STEALTH / SIGNATURE =====
    (re.compile(r"\b(stealth|furtif|signature contr[oô]l|ram (?:material|materials?)|"
                r"radar absorbing)\b", re.I),
     ["Matériaux composites & blindage"]),

    # ===== SPECIFIC PRODUCT TYPES =====
    (re.compile(r"\b(fire suppression|extinction d['e]?incendie|extincteur)\b", re.I),
     ["Conteneurs, malles, shelters"]),
    (re.compile(r"\b(rescue equipment|outils? de secours|hydraulic rescue)\b", re.I),
     ["Conteneurs, malles, shelters"]),
    (re.compile(r"\b(coupling|attelage|towing|recovery)\b", re.I),
     ["Mécanique de transmission"]),
    (re.compile(r"\b(metal foundry|fonderie)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(welded structures?|sheet metal|t[oô]lerie|m[eé]cano-?soud)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(spring|ressort|spring assembl)\s+(?:manufactur|production)", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(slip ring|bague glissante)\b", re.I),
     ["Composants électroniques & cartes"]),
    (re.compile(r"\b(electroplating|surface treatment|finitions? de surface|revêtement)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(bearing|roulement|palier)\b", re.I),
     ["Mécanique de transmission"]),
    (re.compile(r"\b(welding|soudage)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(fabric|tissu|textile finish)\b", re.I),
     ["Textiles techniques tactiques"]),
    (re.compile(r"\b(glove|gant|tactical glove)\b", re.I),
     ["Équipement du fantassin (général)"]),
    (re.compile(r"\b(rugged solution|durci|hardened|battle-?proven)\b", re.I),
     ["Composants électroniques & cartes"]),
    (re.compile(r"\b(printed circuit|carte [eé]lectronique|pcb)\b", re.I),
     ["Composants électroniques & cartes"]),

    # ===== MEDICAL / HEALTH =====
    (re.compile(r"\b(medical countermeasures?|antidote|nrbc treatment|combat medical|"
                r"blood substitute|substitut sanguin)\b", re.I),
     ["NRBC (masques, tenues, détecteurs)"]),
    (re.compile(r"\b(emergency rations?|mre|combat ration|rations? combat)\b", re.I),
     ["Logistique militaire & transport"]),
    (re.compile(r"\b(stretcher|brancard|m[eé]decine de combat)\b", re.I),
     ["Ambulances & véhicules sanitaires"]),

    # ===== POWER / ENERGY EXTRA =====
    (re.compile(r"\b(power solutions?|solutions? d['e]?[eé]nergie)\b", re.I),
     ["Batteries & packs énergétiques"]),
    (re.compile(r"\b(uninterruptible power|alimentation sans coupure|ups)\b", re.I),
     ["Batteries & packs énergétiques"]),

    # ===== CONSULTING / SERVICES (return [] for some — services not products) =====
    (re.compile(r"\b(consulting|cabinet de conseil|due diligence|advisory)\b", re.I),
     []),  # services, not products

    # ===== ADDITIONAL PATTERNS FROM 2nd AUDIT =====
    (re.compile(r"\b(navigation systems?|navigation solutions?|inertial navigation)\b", re.I),
     ["Avionique & instruments embarqués"]),
    (re.compile(r"\b(iot device|iot solution|m2m|connected device)\b", re.I),
     ["Composants électroniques & cartes"]),
    (re.compile(r"\b(field hospital|h[oô]pital de campagne|mobile hospital|hopital mobile)\b", re.I),
     ["Ambulances & véhicules sanitaires"]),
    (re.compile(r"\b(synthetic fibre|synthetic fiber|cordage|rope|ropes?)\b", re.I),
     ["Textiles techniques tactiques"]),
    (re.compile(r"\b(uds excavator|engineering vehicle|excavateur)\b", re.I),
     ["Engins du génie militaire"]),
    (re.compile(r"\b(bullet[- ]?proof|pare[- ]?balles?|armoured? vehicles?|armored vehicles?)\b", re.I),
     ["Véhicules blindés"]),
    (re.compile(r"\b(humvee|tactical kit|special force vehicle)\b", re.I),
     ["Véhicules tactiques (non blindés)"]),
    (re.compile(r"\b(3d printer|imprimante 3d|fff|fused filament|sla|metal 3d printing|fdm)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(precision mill|fabrication de mill|manufacturing of mill)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(cybergouvernance|cyber gouvernance|gouvernance cybers[eé]curit[eé])\b", re.I),
     ["Cybersécurité (logiciels & appliances)"]),
    (re.compile(r"\b(stratégie digitale|digital strategy|digital transformation)\b", re.I),
     ["Logiciels métier défense (autres)"]),
    (re.compile(r"\b(strategic partner to defen[cs]e|partenaire strat[eé]gique)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(facility security|production permit|subcontractor)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(turn-?key project|project[- ]?based solution|cl[eé]-en-main)\b", re.I),
     ["Pièces mécaniques & usinage"]),

    # ===== ALARMS / SURVEILLANCE / DETECTION =====
    (re.compile(r"\b(alarm|alarme|intrusion detection|d[eé]tection (?:p[eé]rim[eé]trique|d['e]?intrusion))\b", re.I),
     ["Cybersécurité (logiciels & appliances)"]),
    (re.compile(r"\b(video surveillance|vid[eé]osurveillance|cctv|cam[eé]ra de surveillance)\b", re.I),
     ["Cybersécurité (logiciels & appliances)"]),
    (re.compile(r"\b(access control|contr[oô]le d['e]?acc[eè]s)\b", re.I),
     ["Cybersécurité (logiciels & appliances)"]),

    # ===== TRANSMISSION / CONNECTORS / WIRES (additional) =====
    (re.compile(r"\b(cable assembl|c[aâ]ble assembl|wire harness|harnais [eé]lectrique)\b", re.I),
     ["Connectique & câblage durcis"]),
    (re.compile(r"\b(electrical (?:connector|connect|wir|cabl)|connecteur [eé]lectrique)\b", re.I),
     ["Connectique & câblage durcis"]),
    (re.compile(r"\b(power conversion|convertisseur d['e]?[eé]nergie|inverter|onduleur)\b", re.I),
     ["Alimentations & convertisseurs durcis"]),

    # ===== TESTING / METROLOGY =====
    (re.compile(r"\b(metrology|m[eé]trologie|3d scanning|scan 3d|coordinate measur)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(non-?destructive testing|ndt|cnd|essais non destructifs?)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(qualif|qualifying|qualified|certified) (test|equipment|essai)\b", re.I),
     ["Pièces mécaniques & usinage"]),

    # ===== MISCELLANEOUS =====
    (re.compile(r"\b(carbon fiber|composite carbon|carbone)\b", re.I),
     ["Matériaux composites & blindage"]),
    (re.compile(r"\b(magnet|aimant)\b", re.I),
     ["Composants électroniques & cartes"]),
    (re.compile(r"\b(nanotech|nano-?materials?|nanomateriaux)\b", re.I),
     ["Matériaux composites & blindage"]),

    # ===== ADDITIONAL FROM 3rd AUDIT =====
    (re.compile(r"\b(industrial contractor|sous[- ]?traitant industriel|integrated offering|"
                r"design (and|et) manufactur)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(power supply|alimentation [eé]lectrique|robust power)\b", re.I),
     ["Alimentations & convertisseurs durcis"]),
    (re.compile(r"\b(beyond[- ]?line[- ]?of[- ]?sight|blos|long[- ]?range communicat)\b", re.I),
     ["Radios tactiques & SDR"]),
    (re.compile(r"\b(software[- ]?defined radio|sdr|radio software)\b", re.I),
     ["Radios tactiques & SDR"]),
    (re.compile(r"\b(inertial|navigation system|pnt|positioning navigation timing)\b", re.I),
     ["Avionique & instruments embarqués"]),
    (re.compile(r"\b(air conditioning|chauffage|heating|hvac|climatis)\b", re.I),
     ["Sièges & ergonomie cabine"]),
    (re.compile(r"\b(military clothing|tactical clothing|tenue militaire|combat clothing)\b", re.I),
     ["Équipement du fantassin (général)"]),
    (re.compile(r"\b(powersport|moto|atv|side[- ]?by[- ]?side|all-terrain)\b", re.I),
     ["Véhicules tactiques (non blindés)"]),
    (re.compile(r"\b(field[- ]?proven|battle-?proven|combat-?proven)\b", re.I),
     ["Véhicules tactiques (non blindés)"]),
    (re.compile(r"\b(ar (development|software)|hmi (development|software))\b", re.I),
     ["Plateformes IA / vision défense"]),
    (re.compile(r"\b(combat collaboratif|combat collaboration|collaborative combat|"
                r"combat aéroter)\b", re.I),
     ["Systèmes C2 & C4ISR"]),
    (re.compile(r"\b(vibration test|shock test|essai vibration|test sismique|"
                r"vibrations? and shock)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(reliability testing|qualification d['e]?[eé]lectronique|test d['e]?[eé]lectroni)\b", re.I),
     ["Composants électroniques & cartes"]),
    (re.compile(r"\b(electronic component|composant [eé]lectronique|eee component)\b", re.I),
     ["Composants électroniques & cartes"]),

    # ===== ADVANCED ELECTRIC MOTORS / DRIVES =====
    (re.compile(r"\b(electric motors?|moteur [eé]lectrique|brushless motor)\b", re.I),
     ["Servomoteurs & motion control précision"]),
    (re.compile(r"\b(drive solution|solution d['e]?entra[iî]nement)\b", re.I),
     ["Servomoteurs & motion control précision"]),
    (re.compile(r"\b(drone propulsion|propulsion drone|electric propulsion)\b", re.I),
     ["Moteurs aéronautiques"]),

    # ===== KNIVES / CUTTING TOOLS / EDGED WEAPONS =====
    (re.compile(r"\b(knives?|knife|couteaux?|cuchillo|coltell)\b", re.I),
     ["Armes légères & accessoires"]),
    (re.compile(r"\b(cutting tool|outil de coupe)\b", re.I),
     ["Pièces mécaniques & usinage"]),

    # ===== INSULATION / GLASS / PROTECTION =====
    (re.compile(r"\b(thermal insulation|isolation thermique)\b", re.I),
     ["Conteneurs, malles, shelters"]),
    (re.compile(r"\b(blast protection|protection (?:explosion|souffle)|anti[- ]?blast)\b", re.I),
     ["Vitrages blindés"]),

    # ===== MISC ENGINEERING / R&D =====
    (re.compile(r"\b(R&D|recherche et développement|deep tech|cutting-edge technol)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(integrated solutions?|solutions? int[eé]gr[eé]es?)\b", re.I),
     ["Pièces mécaniques & usinage"]),

    # ===== ADDITIONAL PATTERNS FROM AUDIT BATCH (industrial long tail) =====
    (re.compile(r"\b(cabling systems?|c[aâ]blage(?:s)?|cables? haute|tubes? telecommunications?)\b", re.I),
     ["Connectique & câblage durcis"]),
    (re.compile(r"\b(electrical harness|harnais [eé]lectriques?)\b", re.I),
     ["Connectique & câblage durcis"]),
    (re.compile(r"\b(rubber-?to-?metal-?bonded|bonded rubber|silentbloc|raillement)\b", re.I),
     ["Mécanique de transmission"]),
    (re.compile(r"\b(maintenance industrielle|industrial maintenance|mco)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(information and communication technolog|ict|technologie de l['e]?information)\b", re.I),
     ["Réseaux militaires (5G/P-LTE/MANET)"]),
    (re.compile(r"\b(spectrum engineering|spectrum management|gestion de spectre)\b", re.I),
     ["Antennes & infrastructures RF"]),
    (re.compile(r"\b(sablage|grenaillage|sand blasting|shot peening)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(armoring vehicles?|armoured vehicles?|blindage de véhicules?)\b", re.I),
     ["Véhicules blindés"]),
    (re.compile(r"\b(special steel|aciers? sp[eé]ciaux|acier blind|armor steel|mars protection)\b", re.I),
     ["Aciers & métallurgie spéciale"]),
    (re.compile(r"\b(supply chain integrator|int[eé]grateur supply chain|aerospace supply chain)\b", re.I),
     ["Logistique militaire & transport"]),
    (re.compile(r"\b(ems provider|electronics manufacturing services?|emp[ie] [eé]lectronique)\b", re.I),
     ["Composants électroniques & cartes"]),
    (re.compile(r"\b(brakes? and braking|freinage|brake systems?|braking systems?)\b", re.I),
     ["Mécanique de transmission"]),
    (re.compile(r"\b(drivetrain|powertrain|cha[iî]ne (?:de )?(?:motrice|cin[eé]matique)|transmission lourde)\b", re.I),
     ["Mécanique de transmission"]),
    (re.compile(r"\b(precision drive|entra[iî]nement de pr[eé]cision|servo[- ]?drive)\b", re.I),
     ["Servomoteurs & motion control précision"]),
    (re.compile(r"\b(filtration|filtres?|filters?)\b.*\b(industrial|industriel|military|militaire)", re.I),
     ["Composants électroniques & cartes"]),
    (re.compile(r"\b(gas detection|d[eé]tection de gaz|sensing solutions?|critical sensing)\b", re.I),
     ["NRBC (masques, tenues, détecteurs)"]),
    (re.compile(r"\b(refrigeration|r[eé]frig[eé]ration)\s+(?:solution|medical|medic|m[eé]dical)", re.I),
     ["Ambulances & véhicules sanitaires"]),
    (re.compile(r"\b(medical reprocessing|st[eé]rilisation m[eé]dicale|reprocessing of medical)\b", re.I),
     ["Ambulances & véhicules sanitaires"]),
    (re.compile(r"\b(LBO fund|fonds de capital|private equity|fonds d['e]?investiss)\b", re.I),
     ["Financement, banque & assurance défense"]),
    (re.compile(r"\b(securit[ye] services?|services? de s[eé]curit[eé]|gardiennage)\b", re.I),
     ["Représentation institutionnelle (cluster, fédération, chambre)"]),
    (re.compile(r"\b(camp infrastructure|infrastructure camps?|deployable camps?)\b", re.I),
     ["Conteneurs, malles, shelters"]),
    (re.compile(r"\b(containerised solutions?|containerized solutions?|solutions? conteneuris)\b", re.I),
     ["Conteneurs, malles, shelters"]),
    (re.compile(r"\b(ruggedised cases?|ruggedized cases?|valises? durcies?|cases? for harsh)\b", re.I),
     ["Conteneurs, malles, shelters"]),
    (re.compile(r"\b(matting|tapis logistiques?|expeditionary matting)\b", re.I),
     ["Logistique militaire & transport"]),
    (re.compile(r"\b(armoring|blindage de v[eé]hicule|blind[eé] (?:civil|police|VIP))\b", re.I),
     ["Véhicules blindés"]),
    (re.compile(r"\b(body systems? for heavy vehicles?|carrosserie pour v[eé]hicules? lourds?)\b", re.I),
     ["Véhicules tactiques (non blindés)"]),
    (re.compile(r"\b(milling machine|fabrication de mill|broyeur|moulin)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(filtration system|filtres? techniques?|industrial filter)\b", re.I),
     ["NRBC (masques, tenues, détecteurs)"]),
    (re.compile(r"\b(public safety technology|technologie de s[eé]curit[eé] publique|taser|protect life)\b", re.I),
     ["Armes non-létales"]),
    (re.compile(r"\b(access protection|protection d['e]?acc[eè]s|portes? blind[eé]es?|secure doors?)\b", re.I),
     ["Vitrages blindés"]),
    (re.compile(r"\b(strategic solution partner|partenaire strat[eé]gique solution|advanced platform)\b", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(off-?highway vehicles?|v[eé]hicules? hors route|special vehicle)\b", re.I),
     ["Véhicules tactiques (non blindés)"]),
    (re.compile(r"\b(land mobility|mobilit[eé] terrestre|land defense mobility)\b", re.I),
     ["Véhicules tactiques (non blindés)"]),
    (re.compile(r"\b(autonomous systems?|syst[eè]mes? autonomes?)\b.*\b(europe|europ[eé]ens?)", re.I),
     ["Plateformes IA / vision défense"]),
    (re.compile(r"\b(public works|travaux publics|construction equipment|terrassement)\b", re.I),
     ["Engins du génie militaire"]),
    (re.compile(r"\b(advanced weaponry|armement avanc[eé]|minigun|gatling)\b", re.I),
     ["Armes lourdes & systèmes de tir"]),
    (re.compile(r"\b(weapons mounts?|montages? d['e]?arme|universal mount)\b", re.I),
     ["Armes lourdes & systèmes de tir"]),
    (re.compile(r"\b(special vehicle|special purpose vehicle|v[eé]hicule sp[eé]cialis[eé])\b", re.I),
     ["Véhicules tactiques (non blindés)"]),
    (re.compile(r"\b(lights?|[eé]clairage|feux)\s+(?:for|pour)\s+(?:vehicles?|v[eé]hicules?|special)", re.I),
     ["Mâts, signalisation & balisage"]),
    (re.compile(r"\b(fuelling|fuel solutions?|ravitaillement|stations? carburant)\b", re.I),
     ["Stations de purification d'eau & infra"]),
    (re.compile(r"\b(rf|microwave|micro-?ondes?)\b.*\b(component|composant|subsystem|sous-syst)", re.I),
     ["Antennes & infrastructures RF"]),
    (re.compile(r"\b(time freq|fr[eé]quence temps|frequency reference)\b", re.I),
     ["Antennes & infrastructures RF"]),
    (re.compile(r"\b(public safety|s[eé]curit[eé] publique|emergency services)\b", re.I),
     ["Cybersécurité (logiciels & appliances)"]),
    (re.compile(r"\b(process automation|automation process|automatisation)\b", re.I),
     ["Logiciels métier défense (autres)"]),
    (re.compile(r"\b(sovereignty requirements?|souverainet[eé]|souverains?)\b.*\b(d[eé]fense|defense)", re.I),
     ["Logiciels métier défense (autres)"]),
    (re.compile(r"\b(spectrum engineering|engineering du spectre)\b", re.I),
     ["Antennes & infrastructures RF"]),
    (re.compile(r"\b(harnesses?|harnais)\b.*\b(electric|[eé]lectrique|military|militaire)", re.I),
     ["Connectique & câblage durcis"]),
    (re.compile(r"\b(coatings?|traitements?|finitions?)\b.*\b(belgium|industriels?|industrial|metal)", re.I),
     ["Pièces mécaniques & usinage"]),
    (re.compile(r"\b(steel forge|aciers? sp[eé]ciaux|industeel|arcelor)\b", re.I),
     ["Aciers & métallurgie spéciale"]),
    (re.compile(r"\b(critical communication|comms? mission-?cr[ie]tical|mission critical communication)\b", re.I),
     ["Réseaux militaires (5G/P-LTE/MANET)"]),
    (re.compile(r"\b(advanced helmet|helmet system|casque (?:int[eé]gr[eé]|connect[eé]|tactique))\b", re.I),
     ["Casques de combat"]),
    (re.compile(r"\b(hearing protection|protection auditive|tactical hearing)\b", re.I),
     ["Équipement du fantassin (général)"]),
    (re.compile(r"\b(respiratory|respiration|protection respiratoire)\b", re.I),
     ["NRBC (masques, tenues, détecteurs)"]),
    (re.compile(r"\b(industrial designer|fabricant industriel|industrial group|groupe industriel)\b", re.I),
     ["Pièces mécaniques & usinage"]),

    # ===== INSTITUTIONAL / NON-PRODUCT (return [] explicitly so we don't infer) =====
    # (These should already be handled by kind-detection, but a safety net.)
]


def _categorize_one(text: str, max_cats: int = 5) -> list[str]:
    """Return canonical categories matched in ``text``."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for pattern, cats in _TEXT_TO_PRODUCT_CATS:
        if pattern.search(text):
            for c in cats:
                if c not in seen:
                    out.append(c)
                    seen.add(c)
                    if len(out) >= max_cats:
                        return out
    return out


def infer_product_categories(
    activity_1liner: Optional[str] = None,
    short_presentation: Optional[str] = None,
    headline: Optional[str] = None,
    presentation: Optional[str] = None,
    keywords: Optional[str] = None,
    crawl: Optional[str] = None,
    activity_summary: Optional[str] = None,
    max_cats: int = 5,
) -> list[str]:
    """Combine signals from EVERY text source, dedup in stable order.

    Priority order — most curated source first :
      1. ``activity_1liner``     — already curated through pattern matching
      2. ``keywords``            — hand-crafted by Finderr, very precise
      3. ``short_presentation``  — official company description
      4. ``presentation``        — long-form description (often empty)
      5. ``activity_summary``    — internal extractor's summary
      6. ``headline``            — homepage meta description
      7. ``crawl``               — raw page text (noisier, last resort)
    """
    out: list[str] = []
    seen: set[str] = set()
    sources = (
        activity_1liner,
        keywords,
        short_presentation,
        presentation,
        activity_summary,
        headline,
        crawl,
    )
    for text in sources:
        if not text:
            continue
        for c in _categorize_one(text, max_cats=max_cats):
            if c not in seen:
                out.append(c)
                seen.add(c)
                if len(out) >= max_cats:
                    return out
    return out


__all__ = ["infer_product_categories"]
