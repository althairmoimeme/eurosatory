"""Audit the FACTUAL quality of activity_1liner across the full export.

Usage :
    python -m scripts.audit_activity_quality

Detects 4 classes of issue, prints counts and 6 samples per class :
  • generic_template  : rule-based hallucinated lists ("véhicules
    militaires, équipement logistique, batteries…").
  • generic_tail      : ends with "pour la défense et la sécurité" /
    "pour primes défense" without specifics.
  • marketing_fluff   : "leader", "innovant", "with X employees",
    "Founded in", "global leader".
  • short_or_eng      : <50 chars OR contains an English fragment.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

EXPORT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "exports" / "targeting_profiles_final.json"
)


_TEMPLATE_SLOT = re.compile(
    r"\b(véhicules militaires|véhicules tactiques|équipement logistique|"
    r"équipement du fantassin|batteries / sources d['’]énergie|"
    r"réseaux de communication(?:\s+militaires)?|capteurs embarqués|"
    r"moteurs / propulsion|infrastructure RF durcie|"
    r"stations de commandement|systèmes optroniques|"
    r"protection balistique|drones aériens|systèmes anti-drone|"
    r"robots terrestres \(UGV\)|logiciels métier défense|"
    r"systèmes navals|simulateurs d['’]entraînement|munitions|"
    r"armes|radars)\b",
    re.I | re.U,
)
_TEMPLATE_2SLOTS = re.compile(
    rf"{_TEMPLATE_SLOT.pattern}.+?{_TEMPLATE_SLOT.pattern}",
    re.I | re.U,
)
_GENERIC_TAIL = re.compile(
    r"pour (les |la |des |l['’])?"
    r"(armées(\s+et\s+forces\s+de\s+sécurité)?|forces de sécurité|"
    r"primes défense|défense et (la )?sécurité|"
    r"défense et (l['’])?industrie|industriels défense)\s*\.?\s*$",
    re.I | re.U,
)
_MARKETING = re.compile(
    r"\b(global leader|world.?leader|leading\s|innovative|innovant|"
    r"cutting[- ]edge|state[- ]of[- ]the[- ]art|world.?class|"
    r"with more than \d+ employees|fond(ée|é) en \d{4}|"
    r"founded in \d{4}|with a \d+ year track record)\b",
    re.I | re.U,
)
_ENG_FRAGMENT = re.compile(
    r"\b(With more than|Founded|provides? (an? )?(solutions?|comprehensive)|"
    r"is a (leading|global|world)|specializes in|years? of "
    r"(experience|track)|We are|Our company|We offer)\b",
    re.I,
)


def _is_data_pauvres(s: str) -> bool:
    return "données publiques trop pauvres" in s.lower()


def main() -> int:
    recs = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    n = len(recs)
    classes: dict[str, list] = {
        "generic_template": [],
        "generic_tail": [],
        "marketing_fluff": [],
        "short_or_eng": [],
    }
    for r in recs:
        a = (r.get("activity_1liner") or "").strip()
        if not a or _is_data_pauvres(a):
            continue
        if _TEMPLATE_2SLOTS.search(a) or (
            _TEMPLATE_SLOT.search(a) and _GENERIC_TAIL.search(a)
        ):
            classes["generic_template"].append(r)
            continue
        if _GENERIC_TAIL.search(a):
            classes["generic_tail"].append(r)
            continue
        if _MARKETING.search(a) or _ENG_FRAGMENT.search(a):
            classes["marketing_fluff"].append(r)
            continue
        if len(a) < 50:
            classes["short_or_eng"].append(r)
            continue

    print(f"Audit qualité activité — {n} fiches")
    print("=" * 78)
    total_bad = sum(len(v) for v in classes.values())
    print(f"Total à corriger : {total_bad} ({total_bad * 100 / n:.1f}%)")
    print()
    for kind, fiches in classes.items():
        print(f"[{kind}] {len(fiches)} fiches  ({len(fiches) * 100 / n:.1f}%)")
        for r in fiches[:6]:
            print(
                f"  #{r['exhibitor_id']:5} "
                f"{r['company_name'][:30]:30} | "
                f"{(r.get('activity_1liner') or '')[:140]}"
            )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
