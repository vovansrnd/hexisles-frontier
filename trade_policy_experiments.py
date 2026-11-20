"""Reproducible, opt-in trade-policy presets for controlled experiments.

This module is deliberately not imported by the game runtime. An experiment
runner may apply one named preset to the already-created BotBrain instances.
The gate always remains the v23.2 model probability; presets only configure
the per-archetype gate threshold and the separate score calculation.
"""
from __future__ import annotations

from typing import Any, Iterable


# These are the normalized runtime keys produced by BotBrain._normalise_archetype.
# The experiment runner must verify its four-player roster resolves to these keys.
_ARCHETYPES = ("human", "merchant", "t800", "chaos")

# Original Stage 2.6 runtime overrides.
_A2_THRESHOLDS = {
    "human": 0.142,    # strategist profile
    "merchant": 0.118, # trading profile
    "t800": 0.151,     # conqueror profile
    "chaos": 0.133,    # expansionist profile
}

# Auditor's candidate-wide A0 pass-rate estimates, rounded to 3 decimals.
_CANDIDATE_WIDE_THRESHOLDS = {
    "human": 0.121,
    "merchant": 0.102,
    "t800": 0.129,
    "chaos": 0.118,
}

# Keep all non-target scoring terms fixed across this experiment.
_SCORE_WEIGHT = 0.65
_SCORE_FLOOR = 0.25
_LEADER_HELP_WEIGHT = 1.0

_PRESETS = {
    # Reference: current A2 gate and current v23.2 score scale.
    "a2_reference": {
        "thresholds": _A2_THRESHOLDS,
        "score_probability_source": "model",
    },
    # Gate-only treatment: change thresholds; hold scoring fixed.
    "candidate_gate": {
        "thresholds": _CANDIDATE_WIDE_THRESHOLDS,
        "score_probability_source": "model",
    },
    # Score-scale treatment: same gate as candidate_gate, only score scale changes.
    "candidate_gate_legacy_scale": {
        "thresholds": _CANDIDATE_WIDE_THRESHOLDS,
        "score_probability_source": "model_legacy_scale",
    },
}


def available_trade_policy_presets() -> tuple[str, ...]:
    """Return stable preset names in experiment order."""
    return tuple(_PRESETS)


def apply_trade_policy_preset(
    brains: Iterable[Any], preset_name: str
) -> dict[str, Any]:
    """Apply one preset to exactly one instance of each supported archetype.

    Validation is completed before any tuning is mutated. BotBrain instances
    must own distinct tuning objects, as they do when created by BotBrain.
    The returned mapping is suitable for recording in an experiment manifest.
    """
    try:
        preset = _PRESETS[preset_name]
    except KeyError as exc:
        raise ValueError(
            f"unknown trade-policy preset {preset_name!r}; "
            f"choose from {', '.join(_PRESETS)}"
        ) from exc

    instances = list(brains)
    if len(instances) != len(_ARCHETYPES):
        raise ValueError(
            f"expected {len(_ARCHETYPES)} bot instances, received {len(instances)}"
        )

    by_archetype: dict[str, Any] = {}
    tuning_ids: set[int] = set()
    required_fields = (
        "trade_player_min_acceptance",
        "trade_player_score_probability_source",
        "trade_player_score_probability_weight",
        "trade_player_score_floor",
        "trade_player_leader_help_weight",
    )

    for brain in instances:
        archetype = str(getattr(brain, "archetype", "")).strip().lower()
        if archetype not in _ARCHETYPES:
            raise ValueError(f"unsupported normalized archetype: {archetype!r}")
        if archetype in by_archetype:
            raise ValueError(f"duplicate archetype in roster: {archetype!r}")

        tuning = getattr(brain, "tuning", None)
        if tuning is None:
            raise ValueError(f"bot {archetype!r} has no tuning object")
        if id(tuning) in tuning_ids:
            raise ValueError("bot instances must not share a tuning object")
        tuning_ids.add(id(tuning))

        missing = [name for name in required_fields if not hasattr(tuning, name)]
        if missing:
            raise ValueError(
                f"bot {archetype!r} tuning is missing fields: {', '.join(missing)}"
            )
        by_archetype[archetype] = tuning

    missing_archetypes = sorted(set(_ARCHETYPES) - set(by_archetype))
    if missing_archetypes:
        raise ValueError(
            "incomplete archetype roster: " + ", ".join(missing_archetypes)
        )

    thresholds = dict(preset["thresholds"])
    if set(thresholds) != set(_ARCHETYPES):
        raise RuntimeError("preset threshold map does not cover the supported roster")

    # All validation above is side-effect free; apply atomically from here.
    for archetype in _ARCHETYPES:
        tuning = by_archetype[archetype]
        tuning.trade_player_min_acceptance = float(thresholds[archetype])
        tuning.trade_player_score_probability_source = str(
            preset["score_probability_source"]
        )
        tuning.trade_player_score_probability_weight = _SCORE_WEIGHT
        tuning.trade_player_score_floor = _SCORE_FLOOR
        tuning.trade_player_leader_help_weight = _LEADER_HELP_WEIGHT

    return {
        "preset": preset_name,
        "gate_probability_source": "model",
        "score_probability_source": preset["score_probability_source"],
        "score_probability_weight": _SCORE_WEIGHT,
        "score_floor": _SCORE_FLOOR,
        "leader_help_weight": _LEADER_HELP_WEIGHT,
        "thresholds": thresholds,
        "archetypes": list(_ARCHETYPES),
    }
