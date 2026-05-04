from app.processors.defense_taxonomy import (
    CATEGORY_ANCHORS,
    OTHER,
    filter_defense_by_anchors,
)
from app.processors.sales_card import _pick_primary_category


def test_drops_ai_data_when_no_ai_anchor_built():
    """A holster manufacturer mentions 'AI' once — must not stay flagged AI/data."""
    cats = ["AI / data", "Soldier systems", "Optics / optronics"]
    builts = ["Soldier equipment", "Optronics / Optics", "Weapons"]
    pruned = filter_defense_by_anchors(cats, builts)
    assert "AI / data" not in pruned
    assert "Soldier systems" in pruned
    assert "Optics / optronics" in pruned


def test_keeps_ai_data_when_ai_platforms_built():
    cats = ["AI / data", "C4ISR"]
    builts = ["AI platforms", "Software / Platforms"]
    pruned = filter_defense_by_anchors(cats, builts)
    assert "AI / data" in pruned
    assert "C4ISR" in pruned


def test_service_category_always_passes():
    """Logistics / MRO has no anchor — must always survive pruning."""
    cats = ["Logistics / MRO"]
    pruned = filter_defense_by_anchors(cats, [])
    assert pruned == ["Logistics / MRO"]


def test_pruning_falls_back_to_other_when_everything_drops():
    cats = ["AI / data", "Counter-UAV"]
    builts = ["Logistics equipment"]  # no anchor for either category
    pruned = filter_defense_by_anchors(cats, builts)
    assert pruned == [OTHER]


def test_pick_primary_uses_anchor_strength_over_priority():
    """When two categories are present but only one has matching built anchors,
    the anchored one wins regardless of priority order."""
    # Counter-UAV is higher priority than Naval defense, but no Counter-UAV
    # systems are built — Naval defense is more representative.
    cats = ["Counter-UAV", "Naval defense"]
    builts = ["Naval systems", "Sensors", "Radars"]
    primary = _pick_primary_category(cats, builts)
    assert primary == "Naval defense"


def test_pick_primary_respects_priority_among_equally_anchored():
    cats = ["Counter-UAV", "Naval defense"]
    builts = ["Counter-UAV systems", "Naval systems"]
    # both anchored — Counter-UAV is higher priority
    primary = _pick_primary_category(cats, builts)
    assert primary == "Counter-UAV"


def test_anchor_map_covers_all_priority_categories():
    from app.processors.sales_card import PRIMARY_CATEGORY_PRIORITY
    for cat in PRIMARY_CATEGORY_PRIORITY:
        assert cat in CATEGORY_ANCHORS, f"missing anchor entry for {cat}"
