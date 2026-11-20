from types import SimpleNamespace

import pytest

from ai_brain import BOT_TUNINGS, StrategyGenome
from trade_policy_experiments import (
    apply_trade_policy_preset,
    available_trade_policy_presets,
)


ARCHETYPES = ("human", "merchant", "t800", "chaos")


def make_roster():
    return [
        SimpleNamespace(archetype=name, tuning=StrategyGenome())
        for name in ARCHETYPES
    ]


def values_by_archetype(brains, attribute):
    return {
        brain.archetype: getattr(brain.tuning, attribute)
        for brain in brains
    }


def test_available_presets_are_stable_and_explicit():
    assert available_trade_policy_presets() == (
        "a2_reference",
        "candidate_gate",
        "candidate_gate_legacy_scale",
    )


def test_a2_reference_preset_reproduces_stage_26_controls():
    brains = make_roster()
    metadata = apply_trade_policy_preset(brains, "a2_reference")

    assert values_by_archetype(brains, "trade_player_min_acceptance") == {
        "human": pytest.approx(0.142),
        "merchant": pytest.approx(0.118),
        "t800": pytest.approx(0.151),
        "chaos": pytest.approx(0.133),
    }
    assert values_by_archetype(
        brains, "trade_player_score_probability_source"
    ) == {name: "model" for name in ARCHETYPES}
    assert values_by_archetype(
        brains, "trade_player_score_probability_weight"
    ) == {name: pytest.approx(0.65) for name in ARCHETYPES}
    assert values_by_archetype(
        brains, "trade_player_score_floor"
    ) == {name: pytest.approx(0.25) for name in ARCHETYPES}
    assert values_by_archetype(
        brains, "trade_player_leader_help_weight"
    ) == {name: pytest.approx(1.0) for name in ARCHETYPES}
    assert metadata["gate_probability_source"] == "model"


def test_candidate_gate_changes_only_thresholds():
    brains = make_roster()
    metadata = apply_trade_policy_preset(brains, "candidate_gate")

    assert values_by_archetype(brains, "trade_player_min_acceptance") == {
        "human": pytest.approx(0.121),
        "merchant": pytest.approx(0.102),
        "t800": pytest.approx(0.129),
        "chaos": pytest.approx(0.118),
    }
    assert values_by_archetype(
        brains, "trade_player_score_probability_source"
    ) == {name: "model" for name in ARCHETYPES}
    assert metadata["score_probability_source"] == "model"


def test_legacy_scale_preset_keeps_gate_identical_to_candidate_gate():
    candidate = make_roster()
    scaled = make_roster()

    candidate_meta = apply_trade_policy_preset(candidate, "candidate_gate")
    scaled_meta = apply_trade_policy_preset(
        scaled, "candidate_gate_legacy_scale"
    )

    assert values_by_archetype(
        candidate, "trade_player_min_acceptance"
    ) == values_by_archetype(scaled, "trade_player_min_acceptance")
    assert values_by_archetype(
        scaled, "trade_player_score_probability_source"
    ) == {name: "model_legacy_scale" for name in ARCHETYPES}
    assert scaled_meta["gate_probability_source"] == "model"
    assert candidate_meta["thresholds"] == scaled_meta["thresholds"]


def test_invalid_roster_is_rejected_before_any_tuning_is_mutated():
    brains = make_roster()
    before = [
        (
            brain.tuning.trade_player_min_acceptance,
            brain.tuning.trade_player_score_probability_source,
            brain.tuning.trade_player_score_probability_weight,
            brain.tuning.trade_player_score_floor,
            brain.tuning.trade_player_leader_help_weight,
        )
        for brain in brains
    ]
    brains[-1].archetype = "merchant"  # duplicate; chaos is now absent

    with pytest.raises(ValueError, match="duplicate archetype"):
        apply_trade_policy_preset(brains, "candidate_gate")

    after = [
        (
            brain.tuning.trade_player_min_acceptance,
            brain.tuning.trade_player_score_probability_source,
            brain.tuning.trade_player_score_probability_weight,
            brain.tuning.trade_player_score_floor,
            brain.tuning.trade_player_leader_help_weight,
        )
        for brain in brains
    ]
    assert after == before


def test_preset_does_not_mutate_global_default_tunings():
    before = {
        name: tuning.trade_player_min_acceptance
        for name, tuning in BOT_TUNINGS.items()
    }
    apply_trade_policy_preset(make_roster(), "candidate_gate")
    after = {
        name: tuning.trade_player_min_acceptance
        for name, tuning in BOT_TUNINGS.items()
    }
    assert after == before


def test_unknown_preset_and_unsupported_archetype_fail_closed():
    with pytest.raises(ValueError, match="unknown trade-policy preset"):
        apply_trade_policy_preset(make_roster(), "experimental_guess")

    brains = make_roster()
    brains[0].archetype = "unexpected"
    with pytest.raises(ValueError, match="unsupported normalized archetype"):
        apply_trade_policy_preset(brains, "a2_reference")
