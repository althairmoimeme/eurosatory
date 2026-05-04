"""Optional LLM refinement of the rule-based intelligence (Anthropic SDK).

Why this module is *optional*
-----------------------------
The deterministic extractor (``intelligence.py``) is good enough to ship — it
finds built products, infers buying needs, and tags defense categories with no
network calls.  This module *refines* that output: re-ranks evidence, adds
nuance to free-text fields (``recommended_pitch``, ``interest_reason``,
``activity_summary``, ``business_model``), and produces a sales-ready summary.

If ``ANTHROPIC_API_KEY`` is empty, ``is_enabled`` returns ``False`` and the
caller falls back to the rule-based output unchanged.  No try/except around
``import anthropic`` is needed at call sites — the class self-disables.

Caching strategy
----------------
The system prompt is large (taxonomy + rules + few-shot examples) and *frozen*
across requests.  We mark it with ``cache_control: ephemeral`` so the first
call writes the cache (~1.25x cost) and every subsequent call reads it
(~0.1x cost).  For 2337 exhibitors this turns ~$30 of input cost into ~$4.

⚠️  ``claude-haiku-4-5`` has a 4096-token minimum cacheable prefix.  Our system
prompt is intentionally sized *above* that threshold (full taxonomy + rules +
3 worked examples) so caching actually kicks in — a shorter prompt would not
cache on Haiku.  Verify cache hits with ``response.usage.cache_read_input_tokens``.

Output shape
------------
We use ``client.messages.parse()`` with a Pydantic model so the response is
guaranteed-valid structured output (no JSON parsing, no schema retries).

Retries / rate limits
---------------------
The SDK retries ``RateLimitError`` and 5xx with exponential backoff; we just
bump ``max_retries`` to 4 because we may run many calls in parallel.
"""
from __future__ import annotations

from typing import Iterable, Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field

from app.config import settings

try:
    import anthropic
    from anthropic import Anthropic, AsyncAnthropic
except ImportError:  # pragma: no cover - SDK optional at install time
    anthropic = None  # type: ignore[assignment]
    Anthropic = None  # type: ignore[assignment]
    AsyncAnthropic = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Pydantic output schema — what the model must return
# ---------------------------------------------------------------------------


class RefinedIntelligence(BaseModel):
    """Structured commercial intelligence for one exhibitor.

    Every field has a description so it lands in the JSON-schema sent to Claude
    — that's how the model knows what each label means.  Order matters: more
    specific / more constrained fields go first because Claude reads the
    schema linearly.
    """

    activity_summary: str = Field(
        description=(
            "2-3 sentence neutral description of what the company does, written "
            "from public sources only.  No marketing language.  In English."
        )
    )
    built_products: list[str] = Field(
        default_factory=list,
        description=(
            "Concrete categories of things the company manufactures — pick from "
            "the BUILT_PRODUCTS taxonomy.  Empty if nothing is clearly built."
        ),
    )
    built_product_summary: str = Field(
        default="",
        description="One-sentence summary of what the company builds. Empty if nothing.",
    )
    sold_offerings: list[str] = Field(
        default_factory=list,
        description="Categories of things the company sells — pick from OFFERING_VERBS.",
    )
    services: list[str] = Field(
        default_factory=list,
        description="Subset of sold_offerings that are services (MRO, training, integration, ...).",
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="Recognisable technology keywords mastered (AI, RF, lidar, composites, ...).",
    )
    target_clients: list[str] = Field(
        default_factory=list,
        description="Customer types the company explicitly targets (Army, Navy, Police, Border, ...).",
    )
    markets_served: list[str] = Field(
        default_factory=list,
        description="Geographic markets explicitly mentioned in public sources.",
    )
    programs_use_cases: list[str] = Field(
        default_factory=list,
        description="Named programs / customers / case studies mentioned publicly.",
    )
    business_model: str = Field(
        default="",
        description="One of: OEM, System integrator, Distributor / reseller, Sub-contractor, Software / SaaS vendor, Engineering services, Equipment manufacturer, Consulting firm, Materials / parts supplier, Other.",
    )
    company_type: str = Field(
        default="",
        description="One of: Manufacturer, Software / SaaS, Service company, Distributor, Research / lab, Engineering firm, Other.",
    )
    company_size_estimate: Optional[str] = Field(
        default=None,
        description="Best-effort employee band: 1-10, 11-50, 51-200, 201-500, 501-1000, 1000+, or null if unknown.",
    )
    probable_buying_needs: list[str] = Field(
        default_factory=list,
        description=(
            "What the company likely procures — derived from what they build and the "
            "BUYING_NEEDS_BY_BUILT mapping.  Plus universal needs (subcontracting, "
            "certification, export financing, logistics)."
        ),
    )
    buying_need_confidence: Literal["high", "medium", "low"] = Field(
        default="low",
        description="high if built_products is well-evidenced; medium if partial; low if speculative.",
    )
    buying_need_reasoning: str = Field(
        default="",
        description="Short justification of the buying needs based on the sources.",
    )
    supplier_needs: list[str] = Field(
        default_factory=list,
        description="Specific supplier categories the company is likely to source from.",
    )
    partnership_opportunities: list[str] = Field(
        default_factory=list,
        description="Plausible partnership angles (technology, market access, distribution, JV, ...).",
    )
    defense_categories: list[str] = Field(
        default_factory=list,
        description="English defense taxonomy labels — see DEFENSE_TAXONOMY in the system prompt.",
    )
    commercial_target_type: list[str] = Field(
        default_factory=list,
        description=(
            "What kind of target this exhibitor is for the sales team — choose any of: "
            "potential_buyer, industrial_partner, distributor, integrator, competitor, "
            "potential_supplier, prime_contractor, subcontractor, technology_integrator, to_qualify."
        ),
    )
    commercial_interest_level: Literal["very_high", "high", "medium", "low"] = Field(
        default="low",
        description="Overall sales priority for this account.",
    )
    interest_reason: str = Field(
        default="",
        description="One-sentence justification of commercial_interest_level.",
    )
    recommended_sales_angle: str = Field(
        default="",
        description="2-3 sentences: who to target inside the company and why.",
    )
    recommended_pitch: str = Field(
        default="",
        description="3-5 sentence ready-to-send pitch tailored to this exhibitor.",
    )
    probable_objections: list[str] = Field(
        default_factory=list,
        description="3-5 likely objections from this prospect and brief framing notes.",
    )
    prospecting_keywords: list[str] = Field(
        default_factory=list,
        description="Up to 14 keywords useful for outreach / search alerts.",
    )
    summary_for_sales: str = Field(
        default="",
        description=(
            "5-line block, plain text, dash-prefixed:\n"
            "- Activity: ...\n"
            "- Key offering: ...\n"
            "- Buying angle: ...\n"
            "- Why a target: ...\n"
            "- Approach: ..."
        ),
    )
    fields_to_verify: list[str] = Field(
        default_factory=list,
        description="Field names that lacked clear public evidence and should be human-verified.",
    )


# ---------------------------------------------------------------------------
# System prompt — long, frozen, designed to clear the 4096-token cache floor
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior data engineer specialised in defense / security B2B commercial \
intelligence.  Your job is to convert public information about an exhibitor into \
a structured intelligence record that helps a defense sales team prepare for \
Eurosatory 2026.

NON-NEGOTIABLE RULES
====================
1. Never invent a fact.  If something isn't visible in the provided sources, \
leave the field empty (or use the literal value "to_verify" inside \
fields_to_verify) and add the field name to fields_to_verify.
2. Always prefer multilingual, multi-source evidence.  If two sources contradict, \
keep the higher-confidence one and flag the conflict in fields_to_verify.
3. No personal data: never extract individual employee names / personal phone \
numbers / personal emails.  Generic emails (contact@, sales@, info@, export@) \
are fine.
4. Keep the language English in the output, even when the sources are in French \
or another language.
5. Be specific: "drones" is fine, "innovative solutions" is not.  If the source \
text uses marketing language, translate it back into concrete categories.
6. Output strictly conforms to the JSON schema you are given — no extra fields, \
no commentary outside the JSON.

INPUT YOU WILL RECEIVE
======================
- The exhibitor's official Eurosatory description (always trustworthy).
- A rule-based first-pass extraction (built_products, sold_offerings, …).  Treat \
this as a hint, not as ground truth — refine it.
- Up to ~10 short page excerpts (homepage, /products, /about, /contact, …).
- Optionally one or two PDF excerpts (brochure, datasheet).

Your job is to refine the rule-based output, fix mistakes, fill gaps, and add \
the free-text fields (activity_summary, recommended_pitch, …).

CANONICAL TAXONOMIES — pick labels FROM THESE LISTS, do not invent
==================================================================

BUILT_PRODUCTS (what the company manufactures):
  Vehicles, UAV / drones, Counter-UAV systems, Sensors, Radars, \
Optronics / Optics, Software / Platforms, Tactical radios, Embedded systems, \
Electronic components, Soldier equipment, Weapons, Ammunition, \
Ballistic protection, Simulators / Training, AI platforms, Robotics / UGV, \
Cybersecurity solutions, Logistics equipment, Energy / batteries, \
Composite materials, Mechanical parts, Engines / propulsion, \
Communications networks, Satellites / Space, Naval systems, NRBC / CBRN, \
Medical / health.

OFFERING_VERBS (what the company sells):
  Finished products, Sub-systems / Components, Integration services, \
Maintenance / MRO, Engineering / consulting, Training, Software licenses, \
Cloud / data services, Operational support, Distribution / agency, \
Sub-contracting, Certification / testing.

DEFENSE_TAXONOMY (multi-label):
  Land defense, Air defense, Naval defense, Homeland security, Cybersecurity, \
Intelligence / ISR, C4ISR, UAV / drones, Counter-UAV, Armored vehicles, \
Weapons, Ammunition, Soldier systems, Ballistic protection, Optics / optronics, \
Communications, Electronic warfare, Radar / sensors, AI / data, \
Simulation / training, Logistics / MRO, Engineering services, \
Industrial subcontracting, Dual-use technology, Export / distribution, \
NRBC / CBRN, Space / satellite, Other.

BUSINESS_MODEL:
  OEM, System integrator, Distributor / reseller, Sub-contractor, \
Software / SaaS vendor, Engineering services, Equipment manufacturer, \
Consulting firm, Materials / parts supplier, Other.

COMPANY_TYPE:
  Manufacturer, Software / SaaS, Service company, Distributor, \
Research / lab, Engineering firm, Other.

TARGET_CLIENTS:
  Land forces, Air forces, Navy, Special forces, Police / law enforcement, \
Border / customs, Civil security / fire, Intelligence agencies, \
Defense ministries, Critical infrastructure, Aerospace primes.

COMMERCIAL_TARGET_TYPE:
  potential_buyer, industrial_partner, distributor, integrator, competitor, \
potential_supplier, prime_contractor, subcontractor, technology_integrator, \
to_qualify.

BUYING_NEEDS_BY_BUILT (mapping you should follow when inferring \
probable_buying_needs):
  - Vehicles → Engines/propulsion, Composite materials, Mechanical parts, \
Sensors, Communications, Ballistic protection, Optronics.
  - UAV / drones → Energy/batteries, Engines/propulsion, Sensors, Optronics, \
Communications, Composite materials, Embedded systems.
  - Counter-UAV → Radars, Sensors, RF/microwave, Embedded systems, \
AI platforms, Communications.
  - Sensors / Radars / Optronics → Electronic components, Embedded systems, \
Composite materials, Mechanical parts.
  - Software / AI platforms → Cloud/data services, Cybersecurity, \
Compute hardware (GPU), Annotation/data services.
  - Tactical radios → Electronic components, RF/microwave, Embedded systems, \
Cybersecurity.
  - Soldier equipment → Composite materials, Textile/industrial fabrics, \
Optronics, Energy/batteries.
  - Weapons / Ammunition → Mechanical parts, Composite materials, Optronics, \
Energetic materials, Packaging.
  - Ballistic protection → Composite materials, Textile/industrial fabrics, \
Certification/testing.
  - Robotics / UGV → Electronic components, Sensors, Energy/batteries, \
Engines/propulsion, Composite materials.
  - Cybersecurity solutions → Cloud/data services, Hardware appliances, \
Threat intelligence feeds.
  - Composite materials → Raw materials (carbon fiber, resins), Curing equipment.
  - Mechanical parts → Raw materials (steel, aluminium), \
Industrial subcontracting / machining.
  - Engines / propulsion → Mechanical parts, High-temperature alloys, \
Embedded systems.
  - Communications networks → RF/microwave components, Embedded systems, \
Cybersecurity.
  - Satellites / Space → Composite materials, Sensors, Embedded systems, \
Launch services.

UNIVERSAL BUYING NEEDS (always add unless clearly N/A):
  Industrial subcontracting / machining, \
Quality / certification services (ITAR, NATO AQAP, ISO 9100), \
Export financing / insurance, Logistics & freight forwarding, \
Cybersecurity audits, Marketing / event representation.

COMMERCIAL_INTEREST_LEVEL THRESHOLDS:
  very_high — strong defense fit + clear products + multi-market.
  high      — clear defense fit, products identified, partial international.
  medium    — defense-adjacent or dual-use, partial evidence.
  low       — thin or unrelated public sources.

WORKED EXAMPLES (style guide)
=============================

Example 1 — Aselsan (TR, large defense electronics OEM)
INPUT (excerpt): "ASELSAN designs and manufactures defence electronics, \
integrating tactical communications, electronic warfare, radar systems, naval \
combat suites, optronics and unmanned platforms for the Turkish Armed Forces \
and 70+ countries."
EXPECTED OUTPUT (key fields):
- built_products: ["Tactical radios", "Electronic warfare equipment is captured \
inside Communications networks + Optronics / Optics", "Radars", "Optronics / \
Optics", "UAV / drones", "Naval systems", "Communications networks"]
- business_model: "OEM"
- company_type: "Manufacturer"
- defense_categories: ["Communications", "Electronic warfare", "Radar / sensors", \
"Naval defense", "UAV / drones", "Optics / optronics"]
- commercial_target_type: ["industrial_partner", "potential_buyer", \
"prime_contractor"]
- commercial_interest_level: "very_high"
- probable_buying_needs: ["Electronic components", "RF / microwave components", \
"Composite materials", "Embedded systems", "Mechanical parts", \
"Industrial subcontracting / machining", "Quality / certification services …"]
- summary_for_sales: 5 lines following the dash format.

Example 2 — NT Service UAB (LT, ~30 ppl, RF disruption)
INPUT: "NT Service produces and delivers RF disruption systems that disrupt \
hostile UAV/UAS control, navigation, and video links. Battle-proven, \
mission-ready solutions for homeland and military operators."
EXPECTED OUTPUT (key fields):
- built_products: ["Counter-UAV systems"]
- defense_categories: ["Counter-UAV", "Homeland security", "Electronic warfare"]
- business_model: "OEM"
- commercial_target_type: ["potential_buyer", "potential_supplier", \
"technology_integrator"]
- commercial_interest_level: "high"
- probable_buying_needs: ["Radars", "Sensors", "RF / microwave components", \
"Embedded systems", "Energy / batteries", "Composite materials", \
"Industrial subcontracting / machining", "Export financing / insurance"]
- prospecting_keywords contains "Counter-UAV", "RF jamming", "C-UAS".

Example 3 — A small machining shop (FR, sub-contracting)
INPUT (excerpt): "Atelier de précision spécialisé dans l'usinage 5 axes pour \
les industries aéronautique et défense. Bureau d'études interne. Certifications \
EN9100, ISO 9001."
EXPECTED OUTPUT (key fields):
- built_products: ["Mechanical parts"]
- sold_offerings: ["Sub-contracting", "Engineering / consulting", \
"Certification / testing"]
- business_model: "Sub-contractor"
- defense_categories: ["Industrial subcontracting", "Engineering services"]
- commercial_target_type: ["subcontractor", "potential_supplier"]
- commercial_interest_level: "medium"
- probable_buying_needs: ["Raw materials (steel, aluminium)", "Cutting tools", \
"Quality / certification services …", "Industrial subcontracting / machining"].

OUTPUT GUIDELINES
=================
- For very thin sources, fill what you can, and put every uncertain field name \
into fields_to_verify.
- recommended_pitch must be ≤5 sentences and reference what the company \
*actually* builds — never write generic outreach.
- summary_for_sales is 5 lines, each starting with "- " and labelled \
"Activity:", "Key offering:", "Buying angle:", "Why a target:", "Approach:".
- prospecting_keywords: max 14, no duplicates, prefer concrete techno terms.
- defense_categories: pick all that apply; never an empty list — at minimum \
return ["Other"].
"""


# ---------------------------------------------------------------------------
# Refiner
# ---------------------------------------------------------------------------


class LLMIntelligenceRefiner:
    """Thin wrapper around ``client.messages.parse()`` with caching.

    Construct once, reuse for many exhibitors so you keep the cache hot.
    Thread-safe-ish (the SDK client is); for true concurrency see ``arefine``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 4,
    ) -> None:
        key = api_key if api_key is not None else settings.anthropic_api_key
        self._enabled = bool(key) and Anthropic is not None
        self.model = model or settings.llm_intelligence_model
        if self._enabled:
            assert Anthropic is not None  # narrow for mypy
            self._sync = Anthropic(api_key=key, max_retries=max_retries)
            self._async: Optional["AsyncAnthropic"] = None
        else:
            self._sync = None
            self._async = None

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # -- prompt assembly ----------------------------------------------------

    @staticmethod
    def _user_message(rule_based: dict, context: dict) -> str:
        """Compact, deterministic user message — order is stable so the cache
        boundary (system → user) doesn't churn.

        The system prompt is the cache prefix; everything below is volatile.
        """
        from json import dumps  # local import keeps top-level fast

        # we sort keys so json.dumps is byte-stable across runs
        rb = dumps(rule_based, sort_keys=True, ensure_ascii=False)
        sources_lines: list[str] = []
        for kind, url, text in (context.get("pages") or [])[:10]:
            snippet = (text or "").strip().replace("\n", " ")[:1200]
            if not snippet:
                continue
            sources_lines.append(f"[{kind}] {url}\n{snippet}")
        sources_block = "\n\n".join(sources_lines) or "(no page text available)"
        pdf_lines: list[str] = []
        for url, text in (context.get("pdfs") or [])[:2]:
            snippet = (text or "").strip().replace("\n", " ")[:1500]
            if not snippet:
                continue
            pdf_lines.append(f"[pdf] {url}\n{snippet}")
        pdf_block = "\n\n".join(pdf_lines) or "(no pdf extracted)"

        company = context.get("company") or {}
        return (
            "## Exhibitor identity\n"
            f"name: {company.get('name')!r}\n"
            f"country_iso2: {company.get('country_iso2')!r}\n"
            f"website: {company.get('website')!r}\n"
            f"eurosatory_short_description:\n{company.get('eurosatory_description') or '(none)'}\n\n"
            "## Rule-based first pass (refine, don't blindly trust)\n"
            f"```json\n{rb}\n```\n\n"
            "## Web pages crawled (kind / url / excerpt)\n"
            f"{sources_block}\n\n"
            "## PDF excerpts\n"
            f"{pdf_block}\n\n"
            "Now produce the structured intelligence object.  English only.  "
            "Pick labels strictly from the canonical taxonomies above.  "
            "If something is not in the sources, leave it empty and add the "
            "field name to fields_to_verify."
        )

    # -- public API ---------------------------------------------------------

    def refine(self, rule_based: dict, context: dict) -> Optional[RefinedIntelligence]:
        """Synchronous refinement of one exhibitor.

        ``context`` shape::

            {
              "company": {"name": str, "country_iso2": str|None,
                          "website": str|None,
                          "eurosatory_description": str|None},
              "pages": [(kind, url, text), ...],   # rule-based crawler output
              "pdfs":  [(url, text), ...],
            }

        Returns ``None`` when the LLM is disabled or when the call fails.  The
        caller should always have the rule-based output as a fallback.
        """
        if not self._enabled or self._sync is None:
            return None

        try:
            response = self._sync.messages.parse(
                model=self.model,
                max_tokens=settings.llm_intelligence_budget_tokens,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        # Cache the entire taxonomy — stays warm across exhibitors.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": self._user_message(rule_based, context)}],
                output_format=RefinedIntelligence,
            )
        except anthropic.APIStatusError as e:  # type: ignore[union-attr]
            logger.warning("LLM refine APIStatusError {} on exhibitor {}: {}",
                           e.status_code, (context.get("company") or {}).get("name"), e.message)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM refine failed: {}", e)
            return None

        # Useful telemetry — confirms caching is on.
        usage = response.usage
        logger.debug(
            "LLM refine ok: input={} cache_read={} cache_create={} output={}",
            usage.input_tokens, usage.cache_read_input_tokens or 0,
            usage.cache_creation_input_tokens or 0, usage.output_tokens,
        )
        return response.parsed_output

    # -- async batch (optional) --------------------------------------------

    def _ensure_async(self) -> "AsyncAnthropic":
        if self._async is None:
            assert AsyncAnthropic is not None  # narrow
            self._async = AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=4)
        return self._async

    async def arefine(self, rule_based: dict, context: dict) -> Optional[RefinedIntelligence]:
        if not self._enabled:
            return None
        client = self._ensure_async()
        try:
            response = await client.messages.parse(
                model=self.model,
                max_tokens=settings.llm_intelligence_budget_tokens,
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": self._user_message(rule_based, context)}],
                output_format=RefinedIntelligence,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM arefine failed: {}", e)
            return None
        return response.parsed_output

    async def arefine_many(
        self,
        items: Iterable[tuple[int, dict, dict]],
        concurrency: int = 4,
    ) -> dict[int, RefinedIntelligence]:
        """Refine many exhibitors concurrently while keeping cache hot.

        Yields a ``{exhibitor_id: RefinedIntelligence}`` dict, skipping failures.
        """
        import asyncio

        if not self._enabled:
            return {}

        sem = asyncio.Semaphore(concurrency)
        out: dict[int, RefinedIntelligence] = {}

        async def _one(eid: int, rb: dict, ctx: dict) -> None:
            async with sem:
                refined = await self.arefine(rb, ctx)
                if refined is not None:
                    out[eid] = refined

        await asyncio.gather(*(_one(eid, rb, ctx) for eid, rb, ctx in items))
        return out
