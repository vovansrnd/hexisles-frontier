"""Public-information trade acceptance model for HexIsles: Frontier.

Model: logistic regression (L2), trained on 480 No-Blight games
(seeds 61001-61020, all 24 seat permutations), artifact v23.2.
Target: probability that at least one opponent accepts a 1:1 proposal.

Only proposer-visible/public state is accepted by the feature builder.
Never pass opponents' private resource maps, private goals, brains, or genomes.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

_ARTIFACT_PATH = Path(__file__).with_name("trade_intelligence_model_v23_2.json")
try:
    MODEL_ARTIFACT = json.loads(_ARTIFACT_PATH.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise RuntimeError(f"Cannot load trade model artifact: {_ARTIFACT_PATH}") from exc

MODEL_VERSION = f"trade-any-accept-logreg-{MODEL_ARTIFACT['version']}"
TRAINING_DATA = MODEL_ARTIFACT["data"]
REGULARIZATION = dict(MODEL_ARTIFACT["regularization"])

RESOURCES = ("wood", "clay", "grain", "wool", "ore")
GOAL_KINDS = ("city", "settlement", "road", "charter", "idle")

FEATURE_ORDER = (
    [f"give_{r}" for r in RESOURCES]
    + [f"want_{r}" for r in RESOURCES]
    + ["same_res"]
    + [f"have_{r}" for r in RESOURCES]
    + ["hand_total"]
    + [f"goal_{kind}" for kind in GOAL_KINDS]
    + ["goal_missing", "vp", "rank", "near_win"]
    + [
        "opp_total_mean", "opp_total_min", "opp_total_max", "opp_ge4_count",
        "opp_vp_mean", "opp_vp_max", "opp_near_win_count",
    ]
    + [
        "prod_want_sum", "prod_want_max", "prod_want_who", "prod_give_sum",
        "revealed_hexes",
    ]
    + ["turn_div100", "personal_turn_div30"]
)

if MODEL_ARTIFACT.get("feature_order") != list(FEATURE_ORDER):
    raise RuntimeError("Trade model artifact feature order does not match implementation")

COEFFICIENTS = tuple(float(value) for value in MODEL_ARTIFACT["coefficients"])
INTERCEPT = float(MODEL_ARTIFACT["intercept"])
FEATURE_MEAN = tuple(
    float(value) for value in MODEL_ARTIFACT["standardization"]["mu"]
)
FEATURE_SCALE = tuple(
    float(value) for value in MODEL_ARTIFACT["standardization"]["sd"]
)
ZERO_VARIANCE_FEATURES = tuple(
    MODEL_ARTIFACT["standardization"]["sd_zero_variance_features"]
)

assert len(FEATURE_ORDER) == 40
assert len(COEFFICIENTS) == len(FEATURE_ORDER)
assert len(FEATURE_MEAN) == len(FEATURE_ORDER)
assert len(FEATURE_SCALE) == len(FEATURE_ORDER)





def _resource_map(values: Optional[Mapping]) -> dict[str, int]:
    """Normalize Resource enums and string keys to resource-name keys."""
    out = {resource: 0 for resource in RESOURCES}
    for raw_key, raw_value in (values or {}).items():
        key = str(getattr(raw_key, "value", raw_key))
        if key not in out:
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        if value > 0:
            out[key] += value
    return out


def features_from_state(
    *,
    hand: Mapping,
    goal_kind: Optional[str],
    goal_needed: Mapping,
    give: Mapping,
    want: Mapping,
    own_vp: int,
    opponents: Sequence[Mapping],
    global_turn: int,
    personal_turn: int,
    revealed_hexes: int,
    points_to_win: int = 12,
) -> list[float]:
    """Build the 40 model features using only proposer-visible information.

    Each opponent item must contain:
      total_res: public resource-card count
      public_vp: public victory points
      visible_production: {resource-name: visible production pips}

    The opponent's exact hand, goal, private victory points, or AI profile
    must never be supplied here.
    """
    hand_map = _resource_map(hand)
    goal_map = _resource_map(goal_needed)
    give_map = _resource_map(give)
    want_map = _resource_map(want)

    give_resource = next((r for r in RESOURCES if give_map[r]), None)
    want_resource = next((r for r in RESOURCES if want_map[r]), None)

    opponent_totals = [int(item.get("total_res", 0)) for item in opponents]
    opponent_vps = [int(item.get("public_vp", 0)) for item in opponents]
    opponent_production = [
        _resource_map(item.get("visible_production") or {}) for item in opponents
    ]
    production_want = [
        float(prod.get(want_resource, 0.0)) if want_resource else 0.0
        for prod in opponent_production
    ]
    production_give = [
        float(prod.get(give_resource, 0.0)) if give_resource else 0.0
        for prod in opponent_production
    ]

    def mean(values: Sequence[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    own_vp = int(own_vp)
    points_to_win = int(points_to_win)
    goal_missing = sum(
        max(0, amount - hand_map.get(resource, 0))
        for resource, amount in goal_map.items()
    )
    rank = 1 + sum(1 for vp in opponent_vps if vp > own_vp)

    return [
        *[float(give_map[r]) for r in RESOURCES],
        *[float(want_map[r]) for r in RESOURCES],
        1.0 if give_resource is not None and give_resource == want_resource else 0.0,
        *[float(hand_map[r]) for r in RESOURCES],
        float(sum(hand_map.values())),
        *[1.0 if goal_kind == kind else 0.0 for kind in GOAL_KINDS],
        float(goal_missing),
        float(own_vp),
        float(rank),
        1.0 if own_vp >= points_to_win - 2 else 0.0,
        mean(opponent_totals),
        float(min(opponent_totals)) if opponent_totals else 0.0,
        float(max(opponent_totals)) if opponent_totals else 0.0,
        float(sum(1 for total in opponent_totals if total >= 4)),
        mean(opponent_vps),
        float(max(opponent_vps)) if opponent_vps else 0.0,
        float(sum(1 for vp in opponent_vps if vp >= points_to_win - 2)),
        float(sum(production_want)),
        float(max(production_want)) if production_want else 0.0,
        float(sum(1 for value in production_want if value > 0)),
        float(sum(production_give)),
        float(revealed_hexes),
        float(global_turn) / 100.0,
        float(personal_turn) / 30.0,
    ]


def infer(features: Iterable[float]) -> float:
    """Run the frozen v23.2 logistic model; no runtime ML dependencies."""
    values = list(features)
    if len(values) != len(FEATURE_ORDER):
        raise ValueError(
            f"expected {len(FEATURE_ORDER)} features, received {len(values)}"
        )

    logit = INTERCEPT
    for index, (value, mean, scale, coefficient) in enumerate(
        zip(values, FEATURE_MEAN, FEATURE_SCALE, COEFFICIENTS)
    ):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"feature {FEATURE_ORDER[index]} is not finite")
        effective_scale = scale if scale > 0.0 else 1.0
        logit += ((value - mean) / effective_scale) * coefficient

    logit = max(-30.0, min(30.0, logit))
    return 1.0 / (1.0 + math.exp(-logit))


def predict_any_acceptance(**state) -> tuple[float, list[float]]:
    """Return (P(any opponent accepts), feature vector) for auditability."""
    features = features_from_state(**state)
    return infer(features), features


def model_metadata() -> dict:
    """Small immutable-by-convention metadata snapshot for diagnostics."""
    return {
        "version": MODEL_VERSION,
        "training_data": TRAINING_DATA,
        "feature_order": list(FEATURE_ORDER),
        "zero_variance_features": list(ZERO_VARIANCE_FEATURES),
        "regularization": dict(REGULARIZATION),
        "cv_metrics_oof": dict(MODEL_ARTIFACT.get("cv_metrics_oof", {})),
        "artifact_file": _ARTIFACT_PATH.name,
    }
