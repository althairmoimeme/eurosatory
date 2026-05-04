"""Detect a company's *kind* before applying activity extraction.

Why this matters
----------------
The previous rule-based pipeline hallucinated industrial activities for
chambers of commerce, journals, banks, ministries, and event organisers
because their ``short_presentation`` mentions defense companies they
work with — not what they themselves do. The fix is to detect the
**kind** of entity first, then apply a kind-specific activity template.

Kinds
-----
``INSTITUTIONAL``  Chambers, federations, clusters, agencies, embassies
``GOVERNMENT``     Ministries, defense procurement bodies (DGA, DSCA…)
``MEDIA``          Journals, magazines, publishers covering defense
``EVENT_ORGANIZER`` Salons, exhibitions, event production companies
``FINANCIAL``      Banks, insurers, investment funds
``ACADEMIC``       Universities, schools, écoles
``RESEARCH``       Research institutes (ONERA, INRIA, Fraunhofer…)
``CONSULTING``     Advisory firms, due-diligence, intelligence economique
``LOGISTICS``      Transit, freight, project logistics providers
``DISTRIBUTOR``    Pure distributors / resellers (Arrow, FDH Aero…)
``INDUSTRIAL``     The default — actual makers / sellers of defense gear

The detection runs ordered patterns. The first matching kind wins ;
``INDUSTRIAL`` is the catch-all when nothing matches.
"""
from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Patterns — each kind is a list of compiled regex run against
# (lower-cased) name + " " + short_presentation. Keep the lists short
# and conservative — false positives here cascade.
# ---------------------------------------------------------------------------

_INSTITUTIONAL = [
    re.compile(p, re.I) for p in [
        # Trade promotion agencies (Czech, Polish, Korean, etc.)
        r"\b(trade|export|business)\b.*\b(agency|agence|promotion)\b",
        r"\bagentura\b|\bczech trade\b|\bbusiness sweden\b",
        r"\b(spanish|french|german|italian|polish|czech|korean|japanese)\s+manufacturers?\s+association\b",
        # Industry associations & representations (BDSV, NIDV, ABIMDE, …)
        r"\bbdsv\b|\bnidv\b|\babimde\b|\bagoria\b|\bpia\b\s*(-|–)",
        r"\b(represents?|repr[eé]sente|promotes?|promeut)\b\s+the\s+(interests?|tissu|industry)",
        r"\baims\s+to\s+(?:facilitate|promote|represent|federate)\b",
        r"\bnetherlands industry for defen[cs]e\b",
        r"\bnational\s+pavilion\b",
        r"\bgicat\s+member\b",
        r"\bchamber\b.*\b(commerce|industry|industrie)\b",
        r"\bchambre\b.*\b(commerce|industrie|m[eé]tiers)\b",
        r"\bagoria\b",  # belgian tech federation
        r"\bfederation\b.*\b(industry|defence|d[eé]fense|aerospace)\b",
        r"\bf[eé]d[eé]ration\b.*\b(industriel|d[eé]fense|a[eé]ro)\b",
        r"\bassociation\b.*\b(industry|industriel|defence|d[eé]fense)\b",
        r"\bassociation of\b",
        r"\bcluster\b.*\b(d[eé]fense|defence|innovation|technologi)\b",
        r"\bcensec\b|\bdi-fos\b",
        r"\bgicat\b|\bgifas\b|\bndia\b|\baia\b|\bads group\b",
        r"\bsyndicat\b",
        r"\baussa\b|\bausa\b",  # association of the US army
        r"\bnational pavilion\b|\bpavilion\b\s*[\(\-,]",
        r"\bbusiness (france|iceland|finland|tampere|sweden|denmark)\b",
        r"\binnovation agency\b",
        r"\bregional partnership\b|\beconomic development\b",
        r"\boffice for\b.*\b(investment|export|business)\b",
        r"\binvestment promotion\b",
        r"\b(we represent|we promote|repr[eé]sente|promeut|f[eé]d[eé]r[ea])\b",
        r"\bawex\b",  # Wallonia export agency
    ]
]

_GOVERNMENT = [
    re.compile(p, re.I) for p in [
        r"\bministry\b|\bminist[eè]re\b",
        r"\bgovernment of\b|\bgouvernement\b",
        r"\bdcsd\b|\bdga\b",
        r"\bdefense security cooperation\b",
        r"\binterministerial directorate\b",
        r"\b(state-owned|gouvernemental)\b.*\b(export|trade|defense)\b",
        r"\bmod\b\s*(/|of|\.)",
        r"\bgarde nationale\b|\bnational guard\b",
        r"\beconomat des arm[eé]es?\b",
    ]
]

_MEDIA = [
    re.compile(p, re.I) for p in [
        r"\bjournal\b(?!.*officiel)",  # avoid "journal officiel" types
        r"\bmagazine\b",
        r"\brevue\b\s+(?:officielle|sp[eé]cialis[eé]|d[eé]fense)",
        r"\bverlag\b",  # german "publisher"
        r"\bmittler[- ]?report\b",
        r"\bhardth[oö]henkurier\b",
        r"\beditions?\b\s+(?:de|du|des|sp[eé]cialis|d[eé]fense)",
        r"\b(publisher|publishing|publication)\b",
        r"\bmedia\b\s+(?:group|company|defense)",
        r"\bnewsroom\b",
        r"\bdefense (one|news|industry daily)\b",
        r"\bjanes\b\s+(?:information|defence|world)",
        r"\bnation shield\b|\bal jundi\b",
        r"\bairview\b|\baviation week\b",
    ]
]

_EVENT_ORGANIZER = [
    re.compile(p, re.I) for p in [
        r"\bcomexposium\b",
        r"\bexhibition co\b|\bexhibitions? company\b",
        r"\bevent (production|company|management)\b",
        r"\bsalon\b\s+(?:de|du|des|d[eé]fense|professionnel)",
        r"\b(organize|organise|organis[eé]) (the|a|le|la|les)\b.*\b(salon|show|exhibition|event|conference)",
        r"\bmilipol\b|\beurosatory\b\s+(organi)",
        r"\bdsei\b",
        r"\bworld defense show\b",
        r"\bniec\b|\bnieco\b",
        r"\bmesse\b",
        r"\bclarion (defence|events?)\b",
    ]
]

_FINANCIAL = [
    re.compile(p, re.I) for p in [
        r"\bbpifrance\b",
        r"\bsoci[eé]t[eé] g[eé]n[eé]rale\b",
        r"\ballianz\b",
        r"\bbnp paribas\b",
        r"\b(commercial|investment|merchant) bank\b",
        r"\bbanque\b\s+(?:populaire|d['e]?investiss|publique)",
        r"\binsurance\b\s+(?:company|broker|provider|defense)",
        r"\bassurance\b\s+(?:industrielle|export|d[eé]fense)",
        r"\bcapital\b\s+(?:risk|venture|investment|partners)",
    ]
]

_ACADEMIC = [
    re.compile(p, re.I) for p in [
        r"\buniversity\b|\buniversit[eé]\b",
        r"\b[eé]cole\b\s+(?:polytechnique|nationale|sp[eé]cial|sup[eé]rieure)",
        r"\bschool of\b\s+(?:engineering|business|defence|economics)",
        r"\bhochschule\b",
        r"\baalborg university\b",
    ]
]

_RESEARCH = [
    re.compile(p, re.I) for p in [
        r"\bonera\b|\binria\b|\bcea\b\s",
        r"\bfraunhofer\b",
        r"\b(research|recherche)\b\s+(?:institute|institut|center|laboratory)",
        r"\binstitute\b\s+(?:of|de|für|saint-louis|st\.?\s*louis)",
        r"\bisl\b\s*[\(\-,]",  # Institut Saint-Louis
        r"\bcsi\b\s+entwicklungstechnik\b",
        r"\bcritt\b",
        r"\bseibersdorf\b",
        r"\btekno?logisk institut\b",
    ]
]

_CONSULTING = [
    re.compile(p, re.I) for p in [
        r"\bconsulting\b",
        r"\bconsult\b\s+(?:&|and|company|firm)",
        r"\badvisory\b\s+(?:firm|services|partners)",
        r"\bcabinet\b\s+(?:de\s+conseil|d['e]?[eé]tudes?|d['e]?audit)",
        r"\bconseil\b\s+(?:en|strat[eé]g)",
        r"\bintelligence [eé]conomique\b",
        r"\bdue diligence\b",
        r"\bm&a\b\s+(?:advisory|conseil)",
        r"\baccuracy\b\s*$",
        r"\badit\b",  # ADIT — French intelligence économique
        r"\bartem information\b",
    ]
]

_LOGISTICS = [
    re.compile(p, re.I) for p in [
        r"\bdsv\b\s+air",
        r"\bgeodis\b",
        r"\bbollor[eé]\b\s+logistics?",
        r"\b(global|integrated) logistics\b",
        r"\bfreight\b\s+(?:forwarding|forwarder|forwarders)",
        r"\btransitaire\b",
        r"\b(transit douanier|customs broker)\b",
        r"\bsupply chain\b\s+(?:management|provider)",
    ]
]

_DISTRIBUTOR = [
    re.compile(p, re.I) for p in [
        r"\barrow electronics\b",
        r"\bfdh aero\b",
        r"\bdistributor\b\s+(?:of|de|d['e])",
        r"\bdistributeur\b\s+(?:de|d['e]|exclusif)",
        r"\bauthorised? distributor\b|\bofficial distributor\b",
        r"\bsole supplier of\b",
    ]
]


def detect_company_kind(
    name: str,
    short_presentation: Optional[str],
    business_areas: Optional[list[str]] = None,
    keywords: Optional[list[str]] = None,
) -> str:
    """Classify the company into one of the kinds documented above.

    Returns ``INDUSTRIAL`` as a catch-all default. Detection is purely
    pattern-based on the lower-cased ``name + ' ' + short_presentation``.
    The ordering matters : we check the most specific kinds first.
    """
    text = (name or "") + " " + (short_presentation or "")
    text_low = text.lower()

    # Order matters : check more specific kinds first.
    detectors: list[tuple[str, list[re.Pattern]]] = [
        ("GOVERNMENT", _GOVERNMENT),
        ("INSTITUTIONAL", _INSTITUTIONAL),
        ("MEDIA", _MEDIA),
        ("EVENT_ORGANIZER", _EVENT_ORGANIZER),
        ("FINANCIAL", _FINANCIAL),
        ("ACADEMIC", _ACADEMIC),
        ("RESEARCH", _RESEARCH),
        ("CONSULTING", _CONSULTING),
        ("LOGISTICS", _LOGISTICS),
        ("DISTRIBUTOR", _DISTRIBUTOR),
    ]
    for kind, patterns in detectors:
        for p in patterns:
            if p.search(text_low):
                return kind
    return "INDUSTRIAL"


__all__ = ["detect_company_kind"]
