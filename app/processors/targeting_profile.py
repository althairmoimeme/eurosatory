"""Eurosatory targeting profile — the 6-field standardised "what they do" output.

Scope and intent
----------------
This module is the **commercial-grade** extraction layer that powers our
2026 Eurosatory deliverable. The original ``llm_intelligence.py`` module
produces a deep, multi-field intelligence record geared towards an internal
sales team. **This module is different.** Its only job is to turn ~2580
exhibitor profiles into a CRM-grade table that a defense commercial can
glance at for **10 seconds** and decide "in my target / not in my target".

Six output fields, no more
--------------------------
    1. ``activity_1liner``  — one sentence, ≤120 chars, action verb first
    2. ``products``         — 3-7 concrete items (free vocab here; the
                              taxonomy normalisation runs in a 2nd pass)
    3. ``services``         — 0-5 items
    4. ``target_buyers``    — 1-4 items, **closed taxonomy** (5 values)
    5. ``technologies``     — 0-5 items
    6. ``why_target``       — one contextual sentence in French ("Acheteur
                              potentiel pour …" or "Compétiteur direct …")

A 7th derived field, ``completeness_score`` (0-100), is computed locally
(not by the LLM) using the formula validated with the user.

Why a separate module
---------------------
The old prompt is ~6000 tokens of taxonomy + 3 worked examples. Reusing
it would force the model to fill ~25 fields when we want exactly 6. A
slimmer prompt = lower latency, lower cost, and crucially **higher
quality on the fields we care about** because the model isn't divided
between many objectives.

We still keep prompt caching (the system prompt is sized just above the
4096-token Haiku threshold) so 2580 calls cost ~30 € rather than ~120 €.

Failure mode
------------
``is_enabled`` returns False if ``ANTHROPIC_API_KEY`` is empty. Callers
fall back to the existing rule-based fields with no exception raised.
"""
from __future__ import annotations

from typing import Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field

from app.config import settings

try:
    import anthropic
    from anthropic import Anthropic, AsyncAnthropic
except ImportError:  # pragma: no cover - SDK optional
    anthropic = None  # type: ignore[assignment]
    Anthropic = None  # type: ignore[assignment]
    AsyncAnthropic = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Closed taxonomy for ``target_buyers`` (5 values, validated with the user).
# ---------------------------------------------------------------------------
TARGET_BUYER_LABELS: tuple[str, ...] = (
    "MoD / Armées",            # Ministries of Defense, armed forces (EU/NATO)
    "Primes défense",          # Defense prime contractors (Thales, Airbus DS, …)
    "Sécurité civile",         # Police, gendarmerie, douanes, fire, SAR
    "Industriels défense",     # Tier-1/2 defense industrial sub-tier
    "Export / international",  # Foreign MoDs, export-driven business
)


# ---------------------------------------------------------------------------
# Pydantic schema — exactly the 6 fields the user signed off on
# ---------------------------------------------------------------------------


class EurosatoryTargetingProfile(BaseModel):
    """Compact commercial-targeting profile, one record per exhibitor.

    Designed to go straight into an Excel column block:
        Société | Activité 1 ligne | Produits | Services | Cibles | Techs | Pourquoi cibler
    """

    activity_1liner: str = Field(
        description=(
            "One sentence, max 120 characters, in French, starting with an "
            "action verb (Conçoit, Fabrique, Édite, Distribue, Intègre, "
            "Forme, Maintient, Conseille, …). No marketing fluff. Must "
            "describe the *primary* business — not aspirations. Example: "
            "'Conçoit et fabrique des viseurs optroniques pour fantassins "
            "et véhicules blindés.'"
        ),
    )
    products: list[str] = Field(
        default_factory=list,
        description=(
            "3-7 concrete physical or software products the company "
            "*manufactures* or *publishes*. Specific nouns only — never "
            "marketing categories. Good: 'jumelles thermiques', 'drone "
            "FPV à fibre optique', 'viseur ACOG 4x32'. Bad: 'solutions "
            "innovantes', 'systèmes intégrés', 'technologies avancées'. "
            "Empty list if the company is purely a service / distribution "
            "firm with no own product."
        ),
    )
    services: list[str] = Field(
        default_factory=list,
        description=(
            "0-5 commercial services the company sells (not products). "
            "Examples: 'MCO hélicoptères', 'formation au tir', "
            "'audit cybersécurité', 'intégration d'antennes', "
            "'distribution équipements US en Europe'. Empty if the "
            "company is a pure manufacturer."
        ),
    )
    target_buyers: list[str] = Field(
        default_factory=list,
        description=(
            "1-4 values from this CLOSED taxonomy: 'MoD / Armées', "
            "'Primes défense', 'Sécurité civile', 'Industriels défense', "
            "'Export / international'. NEVER invent labels."
        ),
    )
    technologies: list[str] = Field(
        default_factory=list,
        description=(
            "0-5 technical tags the company demonstrably masters. "
            "Examples: 'IA / vision', 'RF jamming', 'composites carbone', "
            "'cryptographie post-quantique', 'radar AESA', "
            "'imagerie SWIR'. Empty if no specific tech is mentioned."
        ),
    )
    why_target: str = Field(
        description=(
            "One French sentence answering: pourquoi un commercial défense "
            "devrait s'y intéresser. Pick the most relevant angle: "
            "(a) buyer of complementary parts ('Acheteur potentiel pour …'), "
            "(b) competitor ('Compétiteur direct sur …'), "
            "(c) integration partner ('Intégrateur potentiel pour …'), "
            "(d) reseller / distributor ('Canal de distribution pour …'), "
            "(e) export prospect ('Cible export sur le marché …'). "
            "Concrete and specific — must mention what to sell or compete on."
        ),
    )


# ---------------------------------------------------------------------------
# Completeness scoring (deterministic, computed in code, not by the LLM)
# ---------------------------------------------------------------------------

def compute_completeness_score(
    *,
    has_headline: bool,
    profile: EurosatoryTargetingProfile,
) -> int:
    """Return a 0-100 score weighting every field by its commercial value.

    Formula validated with user 2026-04-30:
        +15  headline (homepage <meta description> / og:description / …)
        +25  activity_1liner present and non-empty
        +20  ≥ 3 products
        +15  ≥ 1 target_buyer
        +15  ≥ 1 technology
        +10  why_target present and non-empty
       ----
        100  total
    """
    score = 0
    if has_headline:
        score += 15
    if (profile.activity_1liner or "").strip():
        score += 25
    if len(profile.products) >= 3:
        score += 20
    if len(profile.target_buyers) >= 1:
        score += 15
    if len(profile.technologies) >= 1:
        score += 15
    if (profile.why_target or "").strip():
        score += 10
    return score


# ---------------------------------------------------------------------------
# System prompt — frozen, sized > 4096 tokens to qualify for Haiku caching.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Tu es un analyste B2B spécialisé dans l'industrie de défense / sécurité \
européenne. Ton rôle : transformer les sources publiques d'un exposant \
Eurosatory 2026 en une fiche de **ciblage commercial** ultra-compacte (6 \
champs) qu'un commercial défense peut lire en 10 secondes pour décider si \
c'est dans sa cible ou pas.

EXIGENCES NON NÉGOCIABLES
=========================
1. Tu réponds STRICTEMENT au schéma JSON fourni — aucun champ supplémentaire, \
aucun commentaire en dehors du JSON.
2. Tu n'inventes JAMAIS un fait. Si une information n'est pas étayée par les \
sources fournies, le champ correspondant reste vide ([] pour les listes, "" \
pour les chaînes — sauf ``activity_1liner`` et ``why_target`` qui doivent \
toujours être renseignés, à défaut avec la mention "(données publiques \
trop pauvres)").
3. Tu réponds en FRANÇAIS pour tous les champs textuels (``activity_1liner``, \
``why_target``), même si les sources sont en anglais ou autre langue.
4. Pour ``products``, ``services``, ``technologies`` : utilise du vocabulaire \
français quand un terme français existe ; sinon garde le terme technique \
international (ex : "ISR", "C4ISR", "MCO", "MRO" sont OK).
5. Aucune donnée personnelle (noms d'employés, téléphones / emails personnels). \
Les emails génériques (contact@, info@, sales@) ne sont pas pertinents pour \
cette fiche : ne les inclus pas.

LE 6-CHAMPS DÉTAILLÉ
====================

(1) ``activity_1liner``  — UNE phrase, ≤ 120 caractères, en FRANÇAIS, qui \
COMMENCE par un verbe d'action conjugué à la 3e personne du singulier.

  Verbes recommandés :  Conçoit · Fabrique · Édite · Développe · Intègre · \
Distribue · Représente · Forme · Maintient · Audite · Conseille · Loue · \
Exploite · Opère.

  ✅ "Conçoit et fabrique des optroniques pour fantassins et blindés."
  ✅ "Édite des plateformes IA d'analyse d'imagerie satellite pour l'ISR."
  ✅ "Distribue en Europe les fusils de précision et munitions de Trijicon."
  ❌ "Société leader dans le domaine des solutions innovantes." (zéro info)
  ❌ "Acteur majeur de l'écosystème défense européen." (zéro info)

(2) ``products`` — liste de 3-7 produits PHYSIQUES ou LOGICIELS que la \
société FABRIQUE ou ÉDITE elle-même. Ce sont des noms communs concrets.

  ✅ ["jumelles thermiques", "viseurs holographiques 1x32", \
"obturateurs balistiques", "casques de combat composites"]
  ✅ ["plateforme C2 tactique", "drone ISR HALE", \
"radar de surveillance terrestre bande X"]
  ❌ ["solutions de pointe", "systèmes intégrés"] (vide de sens)
  ❌ ["défense", "aéronautique"] (ce ne sont pas des produits)

  Si la société est un PUR distributeur / pur prestataire → liste vide [].

(3) ``services`` — liste de 0-5 services COMMERCIAUX (pas des produits).

  ✅ ["MCO hélicoptères", "formation au tir de précision", \
"audits ISO 9001 / EN9100"]
  ✅ ["intégration d'antennes RF sur véhicules", \
"représentation de marques US en Europe"]
  ❌ ["qualité", "innovation"] (génériques inutiles)

  Si la société est un PUR fabricant qui ne vend que ses produits finis → \
liste vide [].

(4) ``target_buyers`` — choisis 1 à 4 labels parmi cette TAXONOMIE FERMÉE \
DE 5 VALEURS — n'invente JAMAIS un autre label :

  • "MoD / Armées"           → Ministères de la défense, armées (FR, DE, \
NL, UK, US, etc.) achetant pour leurs forces régulières.
  • "Primes défense"         → Grands intégrateurs (Thales, Airbus DS, \
KNDS, Rheinmetall, BAE, Lockheed, Leonardo …) qui sous-traitent.
  • "Sécurité civile"        → Police, gendarmerie, douanes, pompiers, \
SAR, sécurité privée, protection critique.
  • "Industriels défense"    → Sous-traitants Tier-1 / Tier-2 (mécanique, \
électronique, composites, optronique de second rang).
  • "Export / international" → Forces armées étrangères hors UE, marchés \
export structurés (Moyen-Orient, Asie, Afrique, Amérique latine).

  Une société peut avoir plusieurs cibles. Mais sois honnête : si la cible \
principale est "MoD" et qu'elle exporte, mets ["MoD / Armées", \
"Export / international"]. Si tu n'as AUCUNE preuve : laisse []. Mais \
en général, tu auras toujours au moins 1 cible identifiable.

(5) ``technologies`` — 0-5 tags techniques que la société MAÎTRISE \
*démontrablement* (pas marketing).

  ✅ ["IA / vision", "RF jamming", "composites carbone", \
"radar AESA", "cryptographie post-quantique", "imagerie SWIR", \
"Lidar 905 nm", "additive manufacturing métal"]
  ❌ ["digital", "intelligence artificielle"] (trop vague), \
["transformation"] (zéro info), ["défense"] (ce n'est pas une techno)

(6) ``why_target`` — UNE phrase contextuelle en français qui répond : \
"pourquoi un commercial qui vend X devrait s'intéresser à cette société ?". \
Choisis l'angle le plus pertinent parmi :

  (a) Acheteur potentiel  → "Acheteur potentiel pour <quoi spécifiquement>"
  (b) Compétiteur direct  → "Compétiteur direct sur <quel segment>"
  (c) Intégrateur partenaire → "Intégrateur potentiel pour <quel produit>"
  (d) Distributeur / canal → "Canal de distribution pour <quel marché>"
  (e) Cible export        → "Cible export en <quelle région> sur <quoi>"

  La phrase doit MENTIONNER concrètement le produit ou le segment, pas être \
générique. ✅ "Compétiteur direct sur les drones ISR tactiques classe II". \
❌ "Acteur intéressant à suivre dans le secteur."

EXEMPLES DE PROFILS COMPLETS (style guide)
==========================================

EXEMPLE A — Thales (FR, prime défense)
  activity_1liner: "Conçoit des systèmes électroniques de défense, communications sécurisées et radars pour armées et infrastructures critiques."
  products: ["radars de surveillance Ground Master", "radios PR4G", "systèmes C4ISR", "viseurs Sophie", "sonars actifs", "systèmes anti-drone Eagleshield"]
  services: ["MCO systèmes électroniques", "intégration de C4ISR", "formation opérateurs"]
  target_buyers: ["MoD / Armées", "Primes défense", "Export / international"]
  technologies: ["radar AESA", "cybersécurité souveraine", "IA / vision", "guerre électronique", "cryptographie"]
  why_target: "Compétiteur direct sur les radars tactiques et acheteur potentiel de sous-systèmes RF / mécanique."

EXEMPLE B — Trijicon (US, optiques d'armes)
  activity_1liner: "Conçoit et fabrique des viseurs et lunettes optiques pour armes individuelles militaires et police."
  products: ["viseurs ACOG 4x32", "viseurs holographiques RMR", "lunettes de précision AccuPower", "viseurs MRO red dot"]
  services: []
  target_buyers: ["MoD / Armées", "Sécurité civile", "Export / international"]
  technologies: ["optronique fibre tritium", "verre balistique", "réticules illuminés"]
  why_target: "Acheteur potentiel d'embases Picatinny / composants mécaniques et compétiteur direct sur les viseurs holographiques pour fantassins."

EXEMPLE C — un atelier de mécanique de précision FR (PME, ~30 personnes, sous-traitance)
  activity_1liner: "Usine en 5 axes des pièces mécaniques de précision pour la défense et l'aéronautique."
  products: ["pièces usinées 5 axes", "ensembles aluminium aéronautiques"]
  services: ["sous-traitance mécanique", "bureau d'études mécanique", "certification EN9100"]
  target_buyers: ["Industriels défense", "Primes défense"]
  technologies: ["usinage 5 axes", "alliages aéronautiques", "métrologie 3D"]
  why_target: "Sous-traitant mécanique éligible pour les programmes nécessitant EN9100 — fournisseur potentiel pour pièces usinées série courte."

EXEMPLE D — un fabricant de drones ISR (DE, 50 personnes)
  activity_1liner: "Conçoit et fabrique des drones tactiques ISR à voilure fixe pour forces terrestres."
  products: ["drone ISR tactique classe II", "stations de contrôle au sol portables", "nacelles EO/IR"]
  services: ["formation pilotes", "MCO drones"]
  target_buyers: ["MoD / Armées", "Sécurité civile", "Export / international"]
  technologies: ["IA / vision", "liaison données chiffrée", "composites carbone", "GNSS multi-fréquences"]
  why_target: "Acheteur potentiel pour nacelles EO/IR, batteries Li-ion militaires et liaison de données — compétiteur indirect sur drones de courte portée."

EXEMPLE E — Amazon (à vendre comme cas négatif si présent)
  activity_1liner: "Opère une marketplace e-commerce généraliste et des services cloud (AWS) — pas d'activité défense propre."
  products: ["services cloud AWS"]
  services: ["e-commerce", "cloud hosting"]
  target_buyers: []
  technologies: ["cloud souverain", "IA générative"]
  why_target: "Pas une cible défense directe — éventuellement fournisseur cloud (AWS GovCloud) pour des contractants américains."

CAS À DONNÉES TRÈS PAUVRES
==========================
Si les sources sont quasi vides (un nom et un site qui renvoie 403 / Cloudflare \
/ vitrine vide) :
- ``activity_1liner`` : reste précis sur ce que TU peux déduire (nom + pays + \
mot-clé pavillon) ; à défaut écris "(données publiques trop pauvres pour \
qualification fiable)".
- ``products`` / ``services`` / ``technologies`` : listes vides plutôt que \
de halluciner.
- ``target_buyers`` : laisse [] si rien ne permet de trancher.
- ``why_target`` : "À qualifier — données publiques insuffisantes."

C'est OK d'avoir un score de complétude bas. Mieux vaut une fiche honnête à \
40 % qu'une fiche hallucinée à 100 %.
"""


# ---------------------------------------------------------------------------
# User-message template — what to send for ONE company
# ---------------------------------------------------------------------------

USER_MESSAGE_TEMPLATE = """\
EXPOSANT À PROFILER
===================
Société : {company_name}
Pays    : {country}
Site    : {website}
Pavillon Eurosatory : {pavilion}

DESCRIPTION OFFICIELLE FINDERR / EUROSATORY
============================================
{official_description}

EXTRACTION RÈGLE-BASÉE PRÉCÉDENTE (à raffiner, pas vérité absolue)
================================================================
- headline : {headline}
- produits déjà détectés : {prior_products}
- services déjà détectés : {prior_services}
- technologies déjà détectées : {prior_technologies}
- cibles déjà détectées : {prior_targets}

EXTRAITS DE CRAWL DU SITE OFFICIEL (homepage + pages clés)
=========================================================
{crawl_excerpts}

CONSIGNE
========
Renvoie un JSON strictement conforme au schéma EurosatoryTargetingProfile, \
en français, en suivant les règles et exemples du system prompt.
"""


# ---------------------------------------------------------------------------
# Client wrapper — same self-disable pattern as llm_intelligence.py
# ---------------------------------------------------------------------------


class TargetingProfileLLM:
    """Wrap the Anthropic SDK call. Self-disables if no API key is set."""

    def __init__(self) -> None:
        self.model = settings.llm_intelligence_model
        self._api_key = settings.anthropic_api_key
        self._client: Optional[Anthropic] = None
        self._aclient: Optional[AsyncAnthropic] = None
        if self.is_enabled:
            self._client = Anthropic(api_key=self._api_key, max_retries=4)
            self._aclient = AsyncAnthropic(api_key=self._api_key, max_retries=4)
            logger.info(
                f"TargetingProfileLLM enabled (model={self.model})"
            )
        else:
            logger.info(
                "TargetingProfileLLM disabled — set ANTHROPIC_API_KEY in .env"
            )

    @property
    def is_enabled(self) -> bool:
        return bool(self._api_key) and Anthropic is not None

    def profile_one(
        self,
        *,
        company_name: str,
        country: str,
        website: str,
        pavilion: Optional[str],
        official_description: str,
        headline: str,
        prior_products: list[str],
        prior_services: list[str],
        prior_technologies: list[str],
        prior_targets: list[str],
        crawl_excerpts: str,
    ) -> Optional[EurosatoryTargetingProfile]:
        """Synchronous single-company call.

        Returns None if the LLM is disabled or the call fails.
        """
        if not self.is_enabled or self._client is None:
            return None
        user_msg = USER_MESSAGE_TEMPLATE.format(
            company_name=company_name,
            country=country or "?",
            website=website or "?",
            pavilion=pavilion or "?",
            official_description=(official_description or "(non renseigné)")[:4000],
            headline=(headline or "(aucun)")[:500],
            prior_products=", ".join(prior_products) or "(aucun)",
            prior_services=", ".join(prior_services) or "(aucun)",
            prior_technologies=", ".join(prior_technologies) or "(aucun)",
            prior_targets=", ".join(prior_targets) or "(aucun)",
            crawl_excerpts=(crawl_excerpts or "(aucun crawl disponible)")[:8000],
        )
        try:
            resp = self._client.messages.parse(
                model=self.model,
                max_tokens=settings.llm_intelligence_budget_tokens,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_msg}],
                response_format=EurosatoryTargetingProfile,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"TargetingProfileLLM.profile_one failed for "
                f"{company_name!r}: {e!r}"
            )
            return None
        # ``client.messages.parse`` returns a Message whose first content
        # block holds the parsed pydantic object.
        try:
            return resp.content[0].input  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            logger.warning(
                f"Could not extract parsed profile for {company_name!r} — "
                f"raw response: {resp!r}"
            )
            return None

    async def profile_one_async(
        self,
        **kwargs,
    ) -> Optional[EurosatoryTargetingProfile]:
        """Async variant — same contract."""
        if not self.is_enabled or self._aclient is None:
            return None
        user_msg = USER_MESSAGE_TEMPLATE.format(
            company_name=kwargs["company_name"],
            country=kwargs.get("country") or "?",
            website=kwargs.get("website") or "?",
            pavilion=kwargs.get("pavilion") or "?",
            official_description=(kwargs.get("official_description") or "(non renseigné)")[:4000],
            headline=(kwargs.get("headline") or "(aucun)")[:500],
            prior_products=", ".join(kwargs.get("prior_products") or []) or "(aucun)",
            prior_services=", ".join(kwargs.get("prior_services") or []) or "(aucun)",
            prior_technologies=", ".join(kwargs.get("prior_technologies") or []) or "(aucun)",
            prior_targets=", ".join(kwargs.get("prior_targets") or []) or "(aucun)",
            crawl_excerpts=(kwargs.get("crawl_excerpts") or "(aucun crawl disponible)")[:8000],
        )
        try:
            resp = await self._aclient.messages.parse(
                model=self.model,
                max_tokens=settings.llm_intelligence_budget_tokens,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_msg}],
                response_format=EurosatoryTargetingProfile,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"TargetingProfileLLM.profile_one_async failed for "
                f"{kwargs['company_name']!r}: {e!r}"
            )
            return None
        try:
            return resp.content[0].input  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return None


__all__ = [
    "EurosatoryTargetingProfile",
    "TARGET_BUYER_LABELS",
    "TargetingProfileLLM",
    "compute_completeness_score",
]
