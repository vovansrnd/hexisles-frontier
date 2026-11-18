"""Regression tests for the frozen public-information trade model."""
from enum import Enum

import math
import pytest

from ai_brain import (
    StrategyGenome,
    _player_trade_score,
    _trade_score_probability,
    _LEGACY_SCORE_SCALE_PLATT_SLOPE,
    _LEGACY_SCORE_SCALE_PLATT_INTERCEPT,
)
from trade_intelligence import (
    COEFFICIENTS,
    FEATURE_MEAN,
    FEATURE_ORDER,
    FEATURE_SCALE,
    ZERO_VARIANCE_FEATURES,
    features_from_state,
    infer,
)


class ResourceKey(Enum):
    WOOD = "wood"
    CLAY = "clay"
    GRAIN = "grain"
    WOOL = "wool"
    ORE = "ore"


def sample_state():
    return {
        "hand": {ResourceKey.WOOD: 2, ResourceKey.GRAIN: 1},
        "goal_kind": "settlement",
        "goal_needed": {ResourceKey.WOOD: 1, ResourceKey.CLAY: 1},
        "give": {ResourceKey.WOOD: 1},
        "want": {ResourceKey.ORE: 1},
        "own_vp": 7,
        "opponents": [
            {
                "total_res": 5,
                "public_vp": 8,
                "visible_production": {ResourceKey.ORE: 4.0, ResourceKey.WOOD: 2.0},
            },
            {
                "total_res": 3,
                "public_vp": 6,
                "visible_production": {"ore": 0.0, "wood": 3.0},
            },
            {
                "total_res": 8,
                "public_vp": 4,
                "visible_production": {"ore": 2.0, "wood": 1.0},
            },
        ],
        "global_turn": 50,
        "personal_turn": 17,
        "revealed_hexes": 20,
        "points_to_win": 12,
    }


def test_model_arrays_match_feature_contract():
    assert len(FEATURE_ORDER) == 40
    assert len(COEFFICIENTS) == len(FEATURE_ORDER)
    assert len(FEATURE_MEAN) == len(FEATURE_ORDER)
    assert len(FEATURE_SCALE) == len(FEATURE_ORDER)
    assert ZERO_VARIANCE_FEATURES == ("same_res", "goal_charter", "goal_idle")


def test_feature_builder_normalizes_enum_keys_and_uses_public_opponent_fields():
    state = sample_state()
    features = features_from_state(**state)
    by_name = dict(zip(FEATURE_ORDER, features))

    assert len(features) == 40
    assert by_name["give_wood"] == 1.0
    assert by_name["want_ore"] == 1.0
    assert by_name["have_wood"] == 2.0
    assert by_name["hand_total"] == 3.0
    assert by_name["goal_settlement"] == 1.0
    assert by_name["goal_missing"] == 1.0
    assert by_name["rank"] == 2.0
    assert by_name["opp_total_mean"] == pytest.approx(16.0 / 3.0)
    assert by_name["opp_total_min"] == 3.0
    assert by_name["opp_total_max"] == 8.0
    assert by_name["opp_ge4_count"] == 2.0
    assert by_name["prod_want_sum"] == 6.0
    assert by_name["prod_want_max"] == 4.0
    assert by_name["prod_want_who"] == 2.0
    assert by_name["prod_give_sum"] == 6.0
    assert by_name["revealed_hexes"] == 20.0
    assert by_name["turn_div100"] == 0.5
    assert by_name["personal_turn_div30"] == pytest.approx(17.0 / 30.0)


def test_visible_production_preserves_fractional_values():
    state = sample_state()
    state["opponents"][1]["visible_production"]["ore"] = 0.25
    features = features_from_state(**state)
    by_name = dict(zip(FEATURE_ORDER, features))
    assert by_name["prod_want_sum"] == pytest.approx(6.25)
    assert by_name["prod_want_who"] == 3.0


def test_unseen_or_private_opponent_fields_are_not_consumed():
    state = sample_state()
    features_before = features_from_state(**state)
    state["opponents"][0]["private_resources"] = {"ore": 999}
    state["opponents"][0]["private_goal"] = {"kind": "city"}
    state["opponents"][0]["personality"] = {"trading": 999}
    assert features_from_state(**state) == features_before


def test_constant_train_features_have_no_effect_on_inference():
    state = sample_state()
    features = features_from_state(**state)
    p0 = infer(features)
    by_name = dict(zip(FEATURE_ORDER, features))
    by_name["same_res"] = 1.0
    by_name["goal_charter"] = 1.0
    by_name["goal_idle"] = 1.0
    p1 = infer([by_name[name] for name in FEATURE_ORDER])
    assert p1 == pytest.approx(p0, abs=1e-12)


def test_inference_is_bounded_and_deterministic():
    features = features_from_state(**sample_state())
    probability = infer(features)
    assert 0.0 < probability < 1.0
    assert infer(features) == probability


def test_inference_rejects_wrong_shape_and_non_finite_values():
    with pytest.raises(ValueError):
        infer([0.0] * 39)
    features = features_from_state(**sample_state())
    features[0] = math.nan
    with pytest.raises(ValueError):
        infer(features)


def test_feature_builder_does_not_mutate_inputs():
    state = sample_state()
    snapshot = repr(state)
    features_from_state(**state)
    assert repr(state) == snapshot


def test_default_trade_score_preserves_v23_2_formula():
    tuning = StrategyGenome()
    assert tuning.trade_player_score_probability_weight == pytest.approx(0.65)
    assert tuning.trade_player_score_floor == pytest.approx(0.25)
    assert tuning.trade_player_leader_help_weight == pytest.approx(1.0)

    gain, probability, leader_help = 3.5, 0.319, 0.2
    actual = _player_trade_score(
        gain,
        probability,
        leader_help,
        probability_weight=tuning.trade_player_score_probability_weight,
        leader_penalty_weight=tuning.trade_player_leader_help_weight,
    )
    expected = gain * (0.35 + 0.65 * probability) - leader_help
    assert actual == pytest.approx(expected, abs=1e-12)


def test_trade_score_controls_separate_probability_and_leader_terms():
    gain, probability, leader_help = 3.5, 0.319, 0.2

    default_score = _player_trade_score(
        gain, probability, leader_help,
        probability_weight=0.65,
        leader_penalty_weight=1.0,
    )
    gain_primary_score = _player_trade_score(
        gain, probability, leader_help,
        probability_weight=0.25,
        leader_penalty_weight=0.0,
    )

    assert gain_primary_score > default_score
    assert gain_primary_score == pytest.approx(gain * (0.75 + 0.25 * probability))

def test_score_probability_source_is_separate_from_model_gate_probability():
    model_p = 0.319
    legacy_p = 0.645

    assert _trade_score_probability(model_p, legacy_p, source="model") == pytest.approx(model_p)
    assert _trade_score_probability(model_p, legacy_p, source="legacy_raw") == pytest.approx(legacy_p)

    legacy_scale = _trade_score_probability(
        model_p, legacy_p, source="model_legacy_scale"
    )
    calibrated_back = 1.0 / (
        1.0
        + math.exp(
            -(
                _LEGACY_SCORE_SCALE_PLATT_SLOPE
                * math.log(legacy_scale / (1.0 - legacy_scale))
                + _LEGACY_SCORE_SCALE_PLATT_INTERCEPT
            )
        )
    )
    assert calibrated_back == pytest.approx(model_p, abs=1e-10)

    # The acceptance gate is deliberately evaluated from the model output,
    # regardless of which value is selected for the offer-score multiplier.
    gate = 0.34
    assert (model_p >= gate) is False
    assert (_trade_score_probability(model_p, legacy_p, source="legacy_raw") >= gate) is True


def test_invalid_score_probability_source_is_rejected():
    with pytest.raises(ValueError):
        _trade_score_probability(0.2, 0.7, source="unknown")
