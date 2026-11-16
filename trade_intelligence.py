"""Public-information trade acceptance model for HexIsles: Frontier.

Model: logistic regression (L2), trained on 480 No-Blight games
(seeds 61001-61020, all 24 seat permutations), artifact v23.2.
Target: probability that at least one opponent accepts a 1:1 proposal.

Only proposer-visible/public state is accepted by the feature builder.
Never pass opponents' private resource maps, private goals, brains, or genomes.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping, Optional, Sequence

MODEL_VERSION = "trade-any-accept-logreg-v23.2"
TRAINING_DATA = "480 no-blight games; seeds 61001-61020; 24 seat permutations; 19995 offers"
REGULARIZATION = {"type": "L2", "lambda": 1.0, "intercept_regularized": False}

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

COEFFICIENTS = (
    0.01287259, 0.08445572, 0.11480038, -0.34426758, 0.20875921,
    -0.07518477, -0.05995382, 0.02371342, 0.09236738, 0.09149936,
    0.0,
    -0.18672943, -0.07639123, 0.05054338, 0.02829354, 0.01462293, -0.08296213,
    -0.01177004, -0.02942250, 0.04093843, 0.0, 0.0, 0.00459106,
    0.22027233, 0.09342462, -0.04337918,
    0.03475506, 0.05420195, 0.06712182, 0.06012564,
    -0.26064623, 0.24407134, 0.02439581,
    0.22799851, 0.10095082, 0.15996012, -0.36008615, 0.01810397,
    0.39540517, -0.40362767,
)

INTERCEPT = -1.47094218

FEATURE_MEAN = (
    0.23215804, 0.16974244, 0.14603651, 0.28542136, 0.16664166,
    0.28272068, 0.34753688, 0.17564391, 0.06391598, 0.13018255,
    0.0,
    0.97249312, 0.72053013, 1.45976494, 1.00600150, 1.04971243, 5.20850213,
    0.22740685, 0.42595649, 0.34663666, 0.0, 0.0, 1.39869967,
    4.54043511, 2.04631158, 0.01965491,
    5.62132200, 3.44916229, 7.85646412, 2.30452613,
    4.54960407, 5.73433358, 0.12283071,
    19.92893223, 11.30157539, 2.52108027, 15.02215554, 20.60660165,
    0.48483971, 0.41670751,
)

FEATURE_SCALE = (
    0.42220929, 0.37540637, 0.35314282, 0.45161489, 0.37265563,
    0.45032177, 0.47618799, 0.38051692, 0.24460320, 0.33650416,
    1.0,
    1.17311023, 1.13094468, 1.43639648, 1.18819904, 1.29071522, 2.17395245,
    0.41915746, 0.49448717, 0.47589882, 1.0, 1.0, 0.48963072,
    2.24839203, 1.06326780, 0.13881138,
    1.87764723, 1.82548261, 2.91156692, 0.74288289,
    1.99347935, 2.65648324, 0.37676502,
    11.08629907, 6.34489081, 0.60455323, 10.26262099, 1.33394951,
    0.27473904, 0.22865306,
)

ZERO_VARIANCE_FEATURES = ("same_res", "goal_charter", "goal_idle")

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
    }
