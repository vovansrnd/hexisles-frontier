"""HexIsles: Frontier — стратегический бот.

Версия рассчитана на современный isles_core.py. В отличие от старого набора
правил «действие X имеет фиксированный приоритет Y», здесь бот оценивает
легальные действия одной шкалой, планирует достижимые поселения через сеть
дорог, учитывает стоимость ресурсов, эндгейм, туман и стоимость упущенной
возможности.

Personality и StrategyGenome намеренно используют имена полей тренера V5.
Это сохраняет возможность переносить старые JSON-геномы. Совместимость имён
не означает, что старые численные значения обязательно подходят новой шкале:
после стабилизации логики параметры надо обучать заново.

Правила честности информации:
- ресурсы противников по типам не читаются;
- в тумане не читаются terrain/number_token неразведанных гексов;
- при оценке чужого производства учитываются только гексы, видимые самому боту;
- выбирается только Action из переданного списка legal_actions.
"""

# ── ВЕРСИЯ ДВИЖКА ────────────────────────────────────────────────────────
# ЭТОТ ФАЙЛ СИНХРОНИЗИРУЕТСЯ ИЗ МАСТЕР-КОПИИ:
#   Мастер-копия: корень проекта (синхронизируется в интеграции платформы)
# Мастер-копия — ВСЕГДА новейшая версия. Не редактируйте этот файл напрямую!
# Дата последней синхронизации: см. git log.
# ─────────────────────────────────────────────────────────────────────────


from __future__ import annotations

import copy
import math
import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Optional

from isles_core import (
    COST_CHARTER,
    COST_METROPOLIS,
    COST_OUTPOST,
    COST_ROUTE,
    Action,
    CharterType,
    GamePhase,
    HexGame,
    Resource,
    StructureType,
    TerrainType,
)

RESOURCE_ORDER = (
    Resource.WOOD,
    Resource.CLAY,
    Resource.GRAIN,
    Resource.WOOL,
    Resource.ORE,
)


@dataclass
class Personality:
    """Обучаемый поведенческий профиль; все поля участвуют в оценке."""

    greed: float = 1.0
    risk: float = 1.0
    aggression: float = 1.0
    expansion: float = 1.0
    devcards: float = 1.0
    trading: float = 1.0
    randomness: float = 0.08
    city_bias: float = 1.0
    settlement_bias: float = 1.0
    road_bias: float = 1.0
    comeback: float = 1.0
    fog_curiosity: float = 1.0

    # Переходные алиасы для кода, использовавшего новую схему имён.
    @property
    def charters(self) -> float:
        return self.devcards

    @charters.setter
    def charters(self, value: float) -> None:
        self.devcards = float(value)

    @property
    def metropolis_bias(self) -> float:
        return self.city_bias

    @metropolis_bias.setter
    def metropolis_bias(self, value: float) -> None:
        self.city_bias = float(value)

    @property
    def outpost_bias(self) -> float:
        return self.settlement_bias

    @outpost_bias.setter
    def outpost_bias(self, value: float) -> None:
        self.settlement_bias = float(value)

    @property
    def route_bias(self) -> float:
        return self.road_bias

    @route_bias.setter
    def route_bias(self, value: float) -> None:
        self.road_bias = float(value)


@dataclass
class StrategyGenome:
    """Обучаемые тактические пороги, совместимые с тренером V5."""

    build_city_min_score: float = 2.7
    build_settlement_min_score: float = 2.8
    build_road_min_score: float = 1.15
    buy_dev_card_min_score: float = 2.45

    play_knight_min_threat: float = 2.4
    play_pirate_hunter_min_utility: float = 1.6
    play_year_of_plenty_min_utility: float = 1.3
    play_monopoly_min_utility: float = 1.7
    play_explorer_min_utility: float = 1.6

    trade_bank_min_gain: float = 1.10
    trade_player_min_acceptance: float = 0.34
    panic_trade_floor: float = -0.15

    road_open_spot_bonus: float = 4.7
    free_road_bonus: float = 3.2
    closeout_city_bonus: float = 12.0
    closeout_settlement_bonus: float = 7.5
    closeout_road_bonus: float = 5.0

    endgame_vp_threshold: int = 7
    endgame_aggression_bonus: float = 1.4

    # Переходные алиасы к новой схеме имён.
    @property
    def build_metropolis_min_score(self) -> float:
        return self.build_city_min_score

    @build_metropolis_min_score.setter
    def build_metropolis_min_score(self, value: float) -> None:
        self.build_city_min_score = float(value)

    @property
    def build_outpost_min_score(self) -> float:
        return self.build_settlement_min_score

    @build_outpost_min_score.setter
    def build_outpost_min_score(self, value: float) -> None:
        self.build_settlement_min_score = float(value)

    @property
    def build_route_min_score(self) -> float:
        return self.build_road_min_score

    @build_route_min_score.setter
    def build_route_min_score(self, value: float) -> None:
        self.build_road_min_score = float(value)

    @property
    def buy_charter_min_score(self) -> float:
        return self.buy_dev_card_min_score

    @buy_charter_min_score.setter
    def buy_charter_min_score(self, value: float) -> None:
        self.buy_dev_card_min_score = float(value)

    @property
    def play_vanguard_min_threat(self) -> float:
        return self.play_knight_min_threat

    @play_vanguard_min_threat.setter
    def play_vanguard_min_threat(self, value: float) -> None:
        self.play_knight_min_threat = float(value)

    @property
    def route_open_spot_bonus(self) -> float:
        return self.road_open_spot_bonus

    @route_open_spot_bonus.setter
    def route_open_spot_bonus(self, value: float) -> None:
        self.road_open_spot_bonus = float(value)

    @property
    def free_route_bonus(self) -> float:
        return self.free_road_bonus

    @free_route_bonus.setter
    def free_route_bonus(self, value: float) -> None:
        self.free_road_bonus = float(value)

    @property
    def closeout_metropolis_bonus(self) -> float:
        return self.closeout_city_bonus

    @closeout_metropolis_bonus.setter
    def closeout_metropolis_bonus(self, value: float) -> None:
        self.closeout_city_bonus = float(value)

    @property
    def closeout_outpost_bonus(self) -> float:
        return self.closeout_settlement_bonus

    @closeout_outpost_bonus.setter
    def closeout_outpost_bonus(self, value: float) -> None:
        self.closeout_settlement_bonus = float(value)

    @property
    def closeout_route_bonus(self) -> float:
        return self.closeout_road_bonus

    @closeout_route_bonus.setter
    def closeout_route_bonus(self, value: float) -> None:
        self.closeout_road_bonus = float(value)


@dataclass
class Goal:
    kind: str
    target_id: Optional[int] = None
    score: float = 0.0
    needed: dict[Resource, int] = field(default_factory=dict)
    locked_until_turn: int = -1
    created_turn: int = 0


@dataclass
class ScoredAction:
    action: Action
    score: float
    reason: str = ""


# Baseline profiles only. These are starting points, not a claim that the
# personalities have been re-trained against this revision of the engine.
BOT_PERSONALITIES: dict[str, Personality] = {
    "human": Personality(
        greed=1.00,
        risk=1.00,
        aggression=0.90,
        expansion=1.00,
        devcards=1.12,
        trading=1.00,
        randomness=0.10,
        city_bias=1.08,
        settlement_bias=1.05,
        road_bias=1.00,
        comeback=1.10,
        fog_curiosity=1.00,
    ),
    "t800": Personality(
        greed=1.04,
        risk=1.25,
        aggression=1.15,
        expansion=1.25,
        devcards=1.12,
        trading=0.90,
        randomness=0.07,
        city_bias=1.15,
        settlement_bias=1.03,
        road_bias=1.15,
        comeback=1.40,
        fog_curiosity=1.20,
    ),
    "merchant": Personality(
        greed=1.02,
        risk=1.05,
        aggression=0.90,
        expansion=1.10,
        devcards=1.00,
        trading=1.25,
        randomness=0.10,
        city_bias=1.05,
        settlement_bias=1.12,
        road_bias=1.05,
        comeback=1.25,
        fog_curiosity=1.10,
    ),
    "chaos": Personality(
        greed=1.00,
        risk=1.35,
        aggression=1.25,
        expansion=1.20,
        devcards=0.95,
        trading=0.95,
        randomness=0.13,
        city_bias=1.00,
        settlement_bias=1.08,
        road_bias=1.25,
        comeback=1.05,
        fog_curiosity=1.35,
    ),
    "t1000": Personality(
        greed=1.08,
        risk=1.10,
        aggression=1.05,
        expansion=1.18,
        devcards=1.18,
        trading=0.90,
        randomness=0.04,
        city_bias=1.18,
        settlement_bias=1.05,
        road_bias=1.10,
        comeback=1.25,
        fog_curiosity=1.10,
    ),
}

BOT_TUNINGS: dict[str, StrategyGenome] = {
    "human": StrategyGenome(),
    "t800": StrategyGenome(
        # Агрессивно идет в города и армию, не любит строить лишние дороги
        build_city_min_score=1.8,
        build_settlement_min_score=2.9,
        build_road_min_score=1.3,
        buy_dev_card_min_score=1.9,
        play_knight_min_threat=1.5,
        trade_bank_min_gain=1.2,
        trade_player_min_acceptance=0.38,
        panic_trade_floor=-0.50,
        road_open_spot_bonus=2.5,
        free_road_bonus=2.0,
        closeout_city_bonus=15.0,
        closeout_settlement_bonus=8.0,
        closeout_road_bonus=4.0,
        endgame_vp_threshold=7,
        endgame_aggression_bonus=2.5,
    ),
    "merchant": StrategyGenome(
        # Легко торгует, скупает карты, строит города
        build_city_min_score=2.2,
        build_settlement_min_score=2.8,
        build_road_min_score=1.1,
        buy_dev_card_min_score=1.8,
        play_knight_min_threat=2.5,
        trade_bank_min_gain=0.85,  # Легко меняет с банком
        trade_player_min_acceptance=0.28,  # Охотно предлагает сделки
        panic_trade_floor=-0.35,
        road_open_spot_bonus=3.5,
        free_road_bonus=2.5,
        closeout_city_bonus=12.0,
        closeout_settlement_bonus=7.0,
        closeout_road_bonus=4.5,
        endgame_vp_threshold=7,
        endgame_aggression_bonus=1.0,
    ),
    "chaos": StrategyGenome(
        # Спамит дороги и поселения, перехватывает самый длинный тракт
        build_city_min_score=3.2,
        build_settlement_min_score=2.1,
        build_road_min_score=0.45,  # Строит дороги по любому поводу
        buy_dev_card_min_score=2.6,
        play_knight_min_threat=3.0,
        trade_bank_min_gain=1.1,
        trade_player_min_acceptance=0.34,
        panic_trade_floor=-0.20,
        road_open_spot_bonus=6.5,
        free_road_bonus=4.5,
        closeout_city_bonus=10.0,
        closeout_settlement_bonus=9.0,
        closeout_road_bonus=8.0,
        endgame_vp_threshold=7,
        endgame_aggression_bonus=1.8,
    ),
    "t1000": StrategyGenome(
        build_city_min_score=1.9,
        build_settlement_min_score=2.4,
        build_road_min_score=0.8,
        buy_dev_card_min_score=1.8,
        play_knight_min_threat=1.8,
        trade_bank_min_gain=0.9,
        trade_player_min_acceptance=0.35,
        panic_trade_floor=-0.60,
        road_open_spot_bonus=4.0,
        free_road_bonus=3.5,
        closeout_city_bonus=14.0,
        closeout_settlement_bonus=8.5,
        closeout_road_bonus=6.0,
        endgame_vp_threshold=7,
        endgame_aggression_bonus=2.2,
    ),
}


class BotBrain:
    """Stateful heuristic AI. Public API kept compatible with simulate.py."""

    DICE_PIPS = {
        2: 1,
        3: 2,
        4: 3,
        5: 4,
        6: 5,
        7: 0,
        8: 5,
        9: 4,
        10: 3,
        11: 2,
        12: 1,
        0: 0,
    }
    RESOURCES = list(RESOURCE_ORDER)
    # Current engine has five Triumph cards among 30 development cards. The AI
    # uses this only as a prior probability and never inspects the shuffled deck.
    TRIUMPH_PRIOR = 5.0 / 30.0

    def __init__(
        self,
        player_id: int,
        game: HexGame,
        personality: Optional[Personality] = None,
        tuning: Optional[StrategyGenome] = None,
        archetype: str = "t800",
        difficulty: Optional[str] = None,
        mod_genes: Optional[dict[str, float]] = None,
    ):
        self.pid = int(player_id)
        self.game = game
        self.archetype = self._normalise_archetype(archetype)
        self.personality = copy.copy(
            personality
            or BOT_PERSONALITIES.get(self.archetype, BOT_PERSONALITIES["human"])
        )
        self.tuning = copy.copy(
            tuning or BOT_TUNINGS.get(self.archetype, BOT_TUNINGS["human"])
        )
        self.difficulty = (
            difficulty or getattr(game, "bot_difficulty", "hard") or "hard"
        ).lower()
        # Optional dynamically discovered genes belonging to installed mods.
        # Names are namespaced (e.g. "blight.purify_bias") and kept separate
        # from the stable base Personality/StrategyGenome schemas.
        self.mod_genes = dict(mod_genes or {})
        seed = f"{getattr(game, 'room_id', 'game')}:{self.pid}:{getattr(game, 'seed', 0)}:{self.archetype}"
        self.rng = random.Random(seed)

        self.current_goal: Optional[Goal] = None
        self.last_personal_turn = -1
        self.bank_trades_this_turn = 0
        self.player_offers_this_turn = 0
        self.trade_rejected_this_turn = False
        self.last_offer_personal_turn = -10_000
        self.our_offer_pending = False
        self.offer_resources_before: Optional[dict[Resource, int]] = None
        self.offer_expected_after: Optional[dict[Resource, int]] = None
        self._setup_anchor_vertex: Optional[int] = None
        self.last_decision: dict[str, Any] = {}
        self._decision_candidates_snapshot: list[dict[str, Any]] = []
        # Diagnostic-only snapshots: these do not affect action scoring.
        self._last_goal_selection: dict[str, Any] = {}
        self._last_bank_trade_search: dict[str, Any] = {}
        self._last_player_trade_search: dict[str, Any] = {}
        self._expansion_cache_key: Any = None
        self._expansion_cache_value: tuple[Optional[int], Optional[int], int, float] = (
            None,
            None,
            999,
            -1e9,
        )

    @staticmethod
    def _normalise_archetype(name: str) -> str:
        raw = str(name or "human").strip().lower()
        aliases = {
            "conqueror": "t800",
            "завоеватель": "t800",
            "strategist": "human",
            "стратег": "human",
            "торговец": "merchant",
            "экспансионист": "chaos",
        }
        raw = aliases.get(raw, raw)
        return raw if raw in BOT_PERSONALITIES else "human"

    def _player(self):
        return self.game.players[self.pid]

    def _my_vp(self) -> int:
        return int(self.game.get_victory_points(self.pid, public=False))

    def _vp(self, pid: int, public: bool = True) -> int:
        try:
            return int(self.game.get_victory_points(pid, public=public))
        except Exception:
            return 0

    # --- Tunable/regression knobs (defaults reproduce the shipped behaviour) ---
    OFFER_COOLDOWN_BASE = 7.0  # personal turns between P2P offers at trading=1.0
    OFFER_COOLDOWN_MIN = 5  # hard floor on that cooldown
    LEGACY_PID_TIEBREAK = False  # True = pre-fix "lowest pid wins ties" (replays only)

    def _player_order_distance(self, other_pid: int) -> int:
        """Clockwise distance to another player in this game's shuffled turn order.

        Player IDs are stable array indices, not seat positions.  Using them as a
        tie-breaker makes the lowest ID win every otherwise-equal ``max()``.
        The per-game shuffled turn order supplies a deterministic, reproducible
        ordering without favouring the same ID across all games.
        """
        n = len(self.game.players)
        try:
            other_pid = int(other_pid)
        except (TypeError, ValueError):
            return n + 1
        if getattr(self, "LEGACY_PID_TIEBREAK", False):
            return other_pid  # smaller pid => smaller distance => wins ties (old bug)
        if n <= 1 or not (0 <= self.pid < n and 0 <= other_pid < n):
            return n + 1

        order = getattr(self.game, "turn_order", None)
        if isinstance(order, (list, tuple)) and len(order) == n:
            try:
                normalized = [int(pid) for pid in order]
                if len(set(normalized)) == n and set(normalized) == set(range(n)):
                    own_pos = normalized.index(self.pid)
                    other_pos = normalized.index(other_pid)
                    distance = (other_pos - own_pos) % n
                    return distance if distance else n
            except (TypeError, ValueError):
                pass

        # Compatibility fallback for test doubles/older snapshots that do not
        # retain turn_order. This is deterministic, but real matches use the
        # shuffled order above.
        distance = (other_pid - self.pid) % n
        return distance if distance else n

    def _leader_id(self) -> Optional[int]:
        others = [p.player_id for p in self.game.players if p.player_id != self.pid]
        if not others:
            return None
        return max(
            others,
            key=lambda i: (self._vp(i, public=True), -self._player_order_distance(i)),
        )

    def _leader_vp(self) -> int:
        return max(
            (
                self._vp(p.player_id, public=True)
                for p in self.game.players
                if p.player_id != self.pid
            ),
            default=0,
        )

    def _comeback_factor(self) -> float:
        gap = self._leader_vp() - self._my_vp()
        if gap <= 0:
            return 1.0
        return min(1.0 + 0.12 * gap * max(0.2, self.personality.comeback), 1.8)

    def _in_endgame(self) -> bool:
        target = int(getattr(self.game, "points_to_win", 12))
        threshold = max(5, min(target - 1, int(self.tuning.endgame_vp_threshold)))
        return self._my_vp() >= threshold

    # ---- trade-acceptance calibration (diagnostic only; never affects play) ----
    def _p2p_pred_cache(self) -> dict:
        cache = getattr(self, "_p2p_pred_cache_store", None)
        if cache is None:
            cache = {}
            self._p2p_pred_cache_store = cache
        return cache

    def _trade_key(self, give: Optional[dict], want: Optional[dict]) -> tuple:
        g = self._normalise_resource_map(give)
        w = self._normalise_resource_map(want)
        return (
            tuple((r.value, g[r]) for r in RESOURCE_ORDER if g[r]),
            tuple((r.value, w[r]) for r in RESOURCE_ORDER if w[r]),
        )

    def _record_trade_calibration(
        self, predicted: Optional[float], accepted: bool
    ) -> None:
        if predicted is None:
            return
        log = getattr(self, "trade_calibration", None)
        if log is None:
            log = []
            self.trade_calibration = log
        if len(log) < 400:
            log.append([round(float(predicted), 4), 1 if accepted else 0])

    def _reset_turn_memory_if_needed(self) -> None:
        own_turn = int(getattr(self._player(), "personal_turns_taken", 0))
        if own_turn != self.last_personal_turn:
            self.last_personal_turn = own_turn
            self.bank_trades_this_turn = 0
            self.player_offers_this_turn = 0
            self.trade_rejected_this_turn = False

        # run_to_completion cancels unresolved offers after all opponents act.
        # Detect the result on the next callback for the proposer. The offer is
        # only 1:1 in the core, so the expected post-trade resource map is exact.
        offer = getattr(self.game, "active_trade_offer", None)
        if self.our_offer_pending and (offer is None or offer.from_player != self.pid):
            now = self._normalise_resource_map(self._player().resources)
            if (
                self.offer_expected_after is not None
                and now == self.offer_expected_after
            ):
                self.trade_rejected_this_turn = False
                self._record_trade_calibration(
                    getattr(self, "_pending_offer_pred", None), True
                )
            else:
                self.trade_rejected_this_turn = True
                self._record_trade_calibration(
                    getattr(self, "_pending_offer_pred", None), False
                )
            self._pending_offer_pred = None
            self.our_offer_pending = False
            self.offer_resources_before = None
            self.offer_expected_after = None

    @staticmethod
    def _as_resource(value: Any) -> Optional[Resource]:
        if isinstance(value, Resource):
            return value if value in RESOURCE_ORDER else None
        try:
            r = Resource(str(value))
            return r if r in RESOURCE_ORDER else None
        except Exception:
            return None

    def _normalise_resource_map(self, values: Optional[dict]) -> dict[Resource, int]:
        out = {r: 0 for r in RESOURCE_ORDER}
        for raw, amount in (values or {}).items():
            r = self._as_resource(raw)
            if r is not None:
                try:
                    n = int(amount)
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    out[r] += n
        return out

    def _hex_visible(self, hid: int) -> bool:
        if not getattr(self.game, "fog_of_war", False):
            return True
        return hid in self._player().revealed_hexes

    def _vertex_visible(self, vid: int) -> bool:
        v = self.game.map.vertices[vid]
        if not getattr(self.game, "fog_of_war", False):
            return True
        if v.structure_owner == self.pid:
            return True
        return any(hid in self._player().revealed_hexes for hid in v.adjacent_hexes)

    def _known_vertex_owner(self, vid: int) -> Optional[int]:
        v = self.game.map.vertices[vid]
        if v.structure_owner == self.pid:
            return self.pid
        if self._vertex_visible(vid):
            return v.structure_owner
        return None

    def _edge_visible(self, eid: int) -> bool:
        e = self.game.map.edges[eid]
        if not getattr(self.game, "fog_of_war", False):
            return True
        if e.route_owner == self.pid:
            return True
        return any(hid in self._player().revealed_hexes for hid in e.adjacent_hexes)

    def _known_route_owner(self, eid: int) -> Optional[int]:
        e = self.game.map.edges[eid]
        if e.route_owner == self.pid:
            return self.pid
        if self._edge_visible(eid):
            return e.route_owner
        return None

    def _resource_base(self, res: Resource) -> float:
        values = {
            Resource.WOOD: 1.00,
            Resource.CLAY: 1.00,
            Resource.GRAIN: 1.07,
            Resource.WOOL: 0.94,
            Resource.ORE: 1.12,
        }
        value = values[res]
        turn = int(getattr(self.game, "global_turn", 0))
        if turn <= 4:
            value *= {
                Resource.WOOD: 1.22,
                Resource.CLAY: 1.22,
                Resource.GRAIN: 1.02,
                Resource.WOOL: 0.98,
                Resource.ORE: 0.83,
            }[res]
        elif turn >= 20:
            value *= {
                Resource.WOOD: 0.98,
                Resource.CLAY: 0.98,
                Resource.GRAIN: 1.12,
                Resource.WOOL: 0.98,
                Resource.ORE: 1.17,
            }[res]
        return value

    def _resource_weight(
        self,
        res: Resource,
        goal: Optional[Goal] = None,
        holdings: Optional[dict] = None,
    ) -> float:
        hand = self._normalise_resource_map(
            self._player().resources if holdings is None else holdings
        )
        have = hand.get(res, 0)
        scarcity = (
            1.42
            if have == 0
            else 1.18
            if have == 1
            else 1.0
            if have <= 3
            else 0.78
            if have <= 5
            else 0.60
        )
        goal_bonus = 0.0
        if goal and goal.needed:
            missing = max(0, goal.needed.get(res, 0) - have)
            if missing > 0:
                goal_bonus = 0.45 + 0.25 * min(2, missing)
        greed = 0.85 + 0.15 * max(0.4, min(2.0, self.personality.greed))
        return self._resource_base(res) * scarcity * greed + goal_bonus

    def _resource_bundle_value(
        self, res_map: dict, goal: Optional[Goal] = None
    ) -> float:
        holdings = self._normalise_resource_map(res_map)
        value = 0.0
        for res in RESOURCE_ORDER:
            base = self._resource_base(res) * (
                0.85 + 0.15 * max(0.4, min(2.0, self.personality.greed))
            )
            for n in range(holdings[res]):
                marginal = (
                    1.42
                    if n == 0
                    else 1.18
                    if n == 1
                    else 1.0
                    if n <= 3
                    else 0.78
                    if n <= 5
                    else 0.60
                )
                value += base * marginal
                if goal and n < goal.needed.get(res, 0):
                    value += 0.45 + 0.15 * min(2, goal.needed[res] - n)
        return value

    def _goal_missing(self, res_map: dict, goal: Optional[Goal]) -> int:
        if not goal or not goal.needed:
            return 0
        holdings = self._normalise_resource_map(res_map)
        return sum(
            max(0, amount - holdings.get(res, 0)) for res, amount in goal.needed.items()
        )

    def _goal_completion_bonus(self, res_map: dict, goal: Optional[Goal]) -> float:
        if not goal or not goal.needed:
            return 0.0
        missing = self._goal_missing(res_map, goal)
        if missing == 0:
            return 2.6
        if missing == 1:
            return 1.10
        if missing == 2:
            return 0.35
        return 0.0

    def _apply_trade_to_map(
        self, holdings: dict, give: dict, want: dict
    ) -> Optional[dict[Resource, int]]:
        out = self._normalise_resource_map(holdings)
        give_map = self._normalise_resource_map(give)
        want_map = self._normalise_resource_map(want)
        if any(out[r] < want_map[r] for r in RESOURCE_ORDER):
            return None
        for r in RESOURCE_ORDER:
            out[r] -= want_map[r]
            out[r] += give_map[r]
        return out

    def _vertex_production_score(self, vid: int) -> float:
        v = self.game.map.vertices[vid]
        score = 0.0
        known_resources: list[Resource] = []
        unknown_count = 0
        for hid in v.adjacent_hexes:
            if not self._hex_visible(hid):
                unknown_count += 1
                continue
            tile = self.game.map.hexes[hid]
            if (
                tile.terrain in (TerrainType.WASTELAND, TerrainType.RUINS)
                or tile.number_token <= 0
            ):
                continue
            pips = self.DICE_PIPS.get(tile.number_token, 0)
            if not pips:
                continue
            res = tile.resource
            known_resources.append(res)
            score += (pips / 5.0) * self._resource_base(res)
        unique = set(known_resources)
        score += 0.38 * len(unique) + 0.14 * len(known_resources)
        score += (
            unknown_count
            * 0.50
            * max(0.15, self.personality.fog_curiosity)
            * (0.70 + 0.30 * self.personality.risk)
        )
        if v.is_port:
            score += 0.35 if getattr(v, "port_type", None) == Resource.ANY else 0.48
        return score

    def _vertex_forward_bonus(self, vid: int) -> float:
        """Small bonus for sites that preserve multiple future branches."""
        v = self.game.map.vertices[vid]
        open_neighbours = 0
        for eid in v.adjacent_edges:
            owner = self._known_route_owner(eid)
            # None means either visibly empty or unknown under fog; count it as
            # a possible branch without inspecting the hidden route_owner.
            if owner is None:
                open_neighbours += 1
        return min(1.15, 0.28 * open_neighbours)

    def _vertex_score(self, vid: int, setup_round: int = 1) -> float:
        v = self.game.map.vertices[vid]
        base = self._vertex_production_score(vid)
        # Resource diversity is valuable at start; later scarcity weights dominate.
        known = []
        for hid in v.adjacent_hexes:
            if self._hex_visible(hid):
                tile = self.game.map.hexes[hid]
                if (
                    tile.terrain not in (TerrainType.WASTELAND, TerrainType.RUINS)
                    and tile.number_token > 0
                ):
                    known.append(tile.resource)
        base += self._vertex_forward_bonus(vid)
        base += 0.22 * len(set(known))
        if setup_round <= 1:
            base += 0.25 * sum(
                1
                for r in known
                if r in (Resource.WOOD, Resource.CLAY, Resource.GRAIN, Resource.WOOL)
            )

        # Grain is a gating resource for cities, settlements and development
        # cards. At setup, the ordinary early-game resource weights otherwise
        # favour wood/clay enough that the AI can start with no visible grain
        # access. Reward known grain production, without reading hidden fog tiles.
        if getattr(self.game, "phase", None) == GamePhase.SETUP:
            grain_pips = 0
            for hid in v.adjacent_hexes:
                if not self._hex_visible(hid):
                    continue
                tile = self.game.map.hexes[hid]
                if tile.terrain in (TerrainType.WASTELAND, TerrainType.RUINS):
                    continue
                if tile.resource == Resource.GRAIN:
                    grain_pips += self.DICE_PIPS.get(tile.number_token, 0)

            if grain_pips > 0:
                # Было: base += 0.90 + 0.55 * min(5, grain_pips) / 5.0
                # Стало: Гораздо сильнее ценим зерно, так как без него наступает "тупик"
                base += 1.80 + 1.20 * min(5, grain_pips) / 5.0

        return base

    def _metropolis_score(self, vid: int) -> float:
        v = self.game.map.vertices[vid]
        if v.structure_owner != self.pid or v.structure_type != StructureType.OUTPOST:
            return -1e6
        return self._vertex_production_score(vid) * 0.5 + 4.0

    def _reachable_vertices_known(self) -> set[int]:
        """Reachability using own routes and only visible opponent blockages."""
        starts = [
            vid
            for vid, v in self.game.map.vertices.items()
            if v.structure_owner == self.pid
        ]
        reachable = set(starts)
        q = deque(starts)
        while q:
            vid = q.popleft()
            for eid in self.game.map.vertices[vid].adjacent_edges:
                if self._known_route_owner(eid) != self.pid:
                    continue
                edge = self.game.map.edges[eid]
                nxt = edge.vertices[1] if edge.vertices[0] == vid else edge.vertices[0]
                owner = self._known_vertex_owner(nxt)
                if owner is not None and owner != self.pid:
                    continue
                if nxt not in reachable:
                    reachable.add(nxt)
                    q.append(nxt)
        return reachable

    def _potential_settlement(self, vid: int) -> tuple[bool, float]:
        """Return legal-looking future spot plus an uncertainty penalty.

        Under fog, hidden occupancy is treated as unknown, never read directly.
        """
        if self._known_vertex_owner(vid) is not None:
            return False, 0.0
        uncertainty = 0.0
        for adj in self.game.map.vertices[vid].adjacent_vertices:
            owner = self._known_vertex_owner(adj)
            if owner is not None:
                return False, 0.0
            if not self._vertex_visible(adj):
                uncertainty += 0.30
        return True, uncertainty

    def _expansion_paths(self) -> dict[int, tuple[Optional[int], int, float]]:
        """Map target-vertex -> (first edge to build, distance, uncertainty).

        BFS starts at our currently reachable network, so `first_edge` really is
        the edge nearest our network, not the far edge next to the destination.
        Opponent settlements known to us are barriers. Unknown fog cells are
        treated as uncertain open space rather than read from the hidden state.
        """
        reachable = self._reachable_vertices_known()
        info: dict[int, tuple[Optional[int], int, float]] = {}
        q = deque()
        for vid in reachable:
            info[vid] = (None, 0, 0.0)
            q.append(vid)
        while q:
            vid = q.popleft()
            first, dist, uncertainty = info[vid]
            for eid in self.game.map.vertices[vid].adjacent_edges:
                owner = self._known_route_owner(eid)
                if owner is not None:
                    # Own routes already belong to the known network; enemy
                    # routes are not traversable. Unknown hidden edges stay uncertain.
                    continue
                edge = self.game.map.edges[eid]
                nxt = edge.vertices[1] if edge.vertices[0] == vid else edge.vertices[0]
                known_owner = self._known_vertex_owner(nxt)
                if known_owner is not None and known_owner != self.pid:
                    continue
                if known_owner == self.pid:
                    continue
                edge_uncertainty = 0.0 if self._edge_visible(eid) else 0.35
                vertex_uncertainty = 0.0 if self._vertex_visible(nxt) else 0.25
                new_uncertainty = uncertainty + edge_uncertainty + vertex_uncertainty
                new_dist = dist + 1
                new_first = eid if first is None else first
                old = info.get(nxt)
                if (
                    old is None
                    or new_dist < old[1]
                    or (new_dist == old[1] and new_uncertainty < old[2])
                ):
                    info[nxt] = (new_first, new_dist, new_uncertainty)
                    q.append(nxt)
        return info

    def _best_expansion_target(self) -> tuple[Optional[int], Optional[int], int, float]:
        """(target vertex, first legal road edge, distance, strategic value)."""
        cache_key = (
            getattr(self.game, "state_version", None),
            getattr(self.game, "global_turn", None),
            self.pid,
        )
        if cache_key == self._expansion_cache_key:
            return self._expansion_cache_value
        paths = self._expansion_paths()
        best: tuple[Optional[int], Optional[int], int, float] = (None, None, 999, -1e9)
        for vid in self.game.map.vertices:
            possible, uncertainty = self._potential_settlement(vid)
            if not possible or vid not in paths:
                continue
            first_edge, distance, path_uncertainty = paths[vid]
            if first_edge is None:
                # Already reachable; this is an immediate settlement target,
                # not a road-expansion objective.
                continue
            raw = self._vertex_score(vid, setup_round=2)
            distance_penalty = 0.72 * max(0, distance - 1)
            score = raw - distance_penalty - uncertainty - path_uncertainty
            if score > best[3]:
                best = (vid, first_edge, distance, score)
        self._expansion_cache_key = cache_key
        self._expansion_cache_value = best
        return best

    def _route_edge_score(
        self, eid: int, expansion: Optional[tuple[int, int, int, float]] = None
    ) -> float:
        edge = self.game.map.edges[eid]
        if self._known_route_owner(eid) is not None:
            return -1e6
        reachable = self._reachable_vertices_known()
        v1, v2 = edge.vertices
        if v1 not in reachable and v2 not in reachable:
            return -1e6

        target_info = expansion or self._best_expansion_target()
        target, first_edge, distance, target_value = target_info
        score = 0.15 + 0.25 * self.personality.road_bias
        if first_edge == eid:
            closeness = 1.0 / (1.0 + 0.28 * max(0, distance - 1))
            score += max(0.0, target_value) * 0.70 * closeness
            score += self.tuning.road_open_spot_bonus * closeness * 0.24
            score *= max(0.5, self.personality.expansion)
        else:
            # Still reward useful dead-end repair / network connection, but do
            # not build arbitrary edges merely because they are legal.
            frontier = v2 if v1 in reachable else v1
            fv = self.game.map.vertices[frontier]
            if self._potential_settlement(frontier)[0]:
                score += 0.25 * self._vertex_score(frontier)
            if v1 in reachable and v2 in reachable:
                score += 0.35

        own_length = self._longest_route_length(self.pid, extra_edge=eid)
        current_length = self._longest_route_length(self.pid)
        if own_length > current_length:
            score += min(1.4, 0.28 * (own_length - current_length))
        if (
            own_length >= 5
            and getattr(self.game, "longest_route_player", None) != self.pid
        ):
            score += 1.0 + 0.25 * self.personality.expansion
        if getattr(self._player(), "free_routes_pending", 0) > 0:
            score += self.tuning.free_road_bonus
        if self._in_endgame():
            score += self.tuning.closeout_road_bonus * 0.45 * self._comeback_factor()
        return score

    def _longest_route_length(self, pid: int, extra_edge: Optional[int] = None) -> int:
        """Compute trail length from visible/owned data; don't inspect hidden opponent routes."""
        owned = {
            eid for eid, edge in self.game.map.edges.items() if edge.route_owner == pid
        }
        if extra_edge is not None:
            owned.add(extra_edge)
        if not owned:
            return 0
        graph: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for eid in owned:
            edge = self.game.map.edges[eid]
            v1, v2 = edge.vertices
            for a, b in ((v1, v2), (v2, v1)):
                owner = self._known_vertex_owner(a)
                if owner is not None and owner != pid:
                    continue
                graph[a].append((b, eid))
        best = 0

        def dfs(node: int, used: set[int], length: int) -> None:
            nonlocal best
            if length > best:
                best = length
            for nxt, eid in graph.get(node, ()):
                if eid in used:
                    continue
                used.add(eid)
                dfs(nxt, used, length + 1)
                used.remove(eid)

        starts = {v for eid in owned for v in self.game.map.edges[eid].vertices}
        for start in starts:
            dfs(start, set(), 0)
        return best

    def _route_would_win(self, eid: int) -> bool:
        target = int(getattr(self.game, "points_to_win", 12))
        before = self._my_vp()
        current_holder = getattr(self.game, "longest_route_player", None)
        own_after = self._longest_route_length(self.pid, extra_edge=eid)
        if own_after < 5:
            return False
        if current_holder == self.pid:
            return False
        if getattr(self.game, "fog_of_war", False):
            # Opponent lengths and some road-blocking vertices are hidden. Do
            # not call a road a forced win unless all vertices on our route are
            # visible to us; an unseen settlement could interrupt the trail.
            own_edges = [
                eid
                for eid, edge in self.game.map.edges.items()
                if edge.route_owner == self.pid
            ]
            vertices_on_route = {
                v
                for eid in own_edges + [eid]
                for v in self.game.map.edges[eid].vertices
            }
            if any(not self._vertex_visible(v) for v in vertices_on_route):
                return False
            return current_holder is None and before + 2 >= target
        opp_lengths = [
            self._longest_route_length(p.player_id)
            for p in self.game.players
            if p.player_id != self.pid
        ]
        max_opp = max(opp_lengths, default=0)
        return own_after > max_opp and before + 2 >= target

    def _goal_still_valid(self, goal: Goal) -> bool:
        if goal.kind == "city":
            if goal.target_id not in self.game.map.vertices:
                return False
            v = self.game.map.vertices[goal.target_id]
            return (
                v.structure_owner == self.pid
                and v.structure_type == StructureType.OUTPOST
            )
        if goal.kind == "settlement":
            possible, _ = (
                self._potential_settlement(goal.target_id)
                if goal.target_id in self.game.map.vertices
                else (False, 0.0)
            )
            return possible and goal.target_id in self._reachable_vertices_known()
        if goal.kind == "road":
            possible, _ = (
                self._potential_settlement(goal.target_id)
                if goal.target_id in self.game.map.vertices
                else (False, 0.0)
            )
            return possible and self._best_expansion_target()[0] == goal.target_id
        return False

    def _get_goal(self, legal_actions: Optional[list[Action]] = None) -> Goal:
        own_turn = int(getattr(self._player(), "personal_turns_taken", 0))
        previous_goal_before_recompute = self.current_goal
        if (
            self.current_goal
            and own_turn <= self.current_goal.locked_until_turn
            and self._goal_still_valid(self.current_goal)
        ):
            self._last_goal_selection = {
                "mode": "reused_locked_goal",
                "own_turn": own_turn,
                "goal_kind": self.current_goal.kind,
                "target_id": self.current_goal.target_id,
                "created_turn": int(self.current_goal.created_turn),
                "locked_until_turn": int(self.current_goal.locked_until_turn),
                "missing_now": int(
                    self._goal_missing(self._player().resources, self.current_goal)
                ),
            }
            return self.current_goal

        candidates: list[Goal] = []
        p = self._player()

        # 1. Город рассматриваем, если есть что улучшать и есть фишки городов
        for vid, v in self.game.map.vertices.items():
            if (
                v.structure_owner == self.pid
                and v.structure_type == StructureType.OUTPOST
                and p.metropolises_left > 0
            ):
                score = 6.4 + 0.45 * self._metropolis_score(vid)
                if self._my_vp() >= int(self.game.points_to_win) - 2:
                    score += 2.0
                candidates.append(Goal("city", vid, score, dict(COST_METROPOLIS)))

        # 2. Факторию рассматриваем ТОЛЬКО ЕСЛИ ОСТАЛИСЬ ФИШКИ В ИНВЕНТАРЕ!
        # (Это предотвращает зацикливание при outposts_left == 0)
        reachable = self._reachable_vertices_known()
        if p.outposts_left > 0:
            for vid in reachable:
                possible, uncertainty = self._potential_settlement(vid)
                if not possible:
                    continue
                score = (
                    5.1 + 0.70 * self._vertex_score(vid, setup_round=2) - uncertainty
                )
                candidates.append(Goal("settlement", vid, score, dict(COST_OUTPOST)))

        target, first_edge, distance, target_value = self._best_expansion_target()
        if p.routes_left > 0 and target is not None:
            score = 2.5 + max(0.0, target_value) * 0.45 - 0.35 * (distance - 1)
            candidates.append(Goal("road", target, score, dict(COST_ROUTE)))

        # Если фишек построек нет — целью становится скупка патентов победы!
        if p.outposts_left <= 0 and p.metropolises_left <= 0:
            candidates.append(Goal("charter", None, 5.0, dict(COST_CHARTER)))

        if not candidates:
            self.current_goal = Goal("idle", score=0.0, needed={})
            self._last_goal_selection = {
                "mode": "recomputed_idle",
                "own_turn": own_turn,
                "previous_goal_kind": getattr(
                    previous_goal_before_recompute, "kind", None
                ),
                "previous_target_id": getattr(
                    previous_goal_before_recompute, "target_id", None
                ),
                "candidate_count": 0,
            }
            return self.current_goal

        holdings = self._normalise_resource_map(p.resources)
        for goal in candidates:
            missing_by_res = sum(
                max(0, amount - holdings.get(res, 0))
                * (1.0 + 0.08 * self._resource_base(res))
                for res, amount in goal.needed.items()
            )
            goal.score -= 1.42 * missing_by_res
            if missing_by_res <= 2:
                goal.score += 1.4
            if goal.kind == "city":
                goal.score *= max(0.5, self.personality.city_bias)
            elif goal.kind == "settlement":
                goal.score *= max(0.5, self.personality.settlement_bias)
            elif goal.kind == "road":
                goal.score *= max(0.5, self.personality.road_bias)

        previous_goal = self.current_goal
        best = max(candidates, key=lambda g: g.score)
        best.created_turn = own_turn
        best.locked_until_turn = own_turn + (
            1 if self._goal_missing(holdings, best) <= 2 else 0
        )
        self.current_goal = best
        ranked = sorted(candidates, key=lambda g: g.score, reverse=True)[:5]
        self._last_goal_selection = {
            "mode": "recomputed",
            "own_turn": own_turn,
            "previous_goal_kind": getattr(previous_goal, "kind", None),
            "previous_target_id": getattr(previous_goal, "target_id", None),
            "selected_goal_kind": best.kind,
            "selected_target_id": best.target_id,
            "created_turn": int(best.created_turn),
            "locked_until_turn": int(best.locked_until_turn),
            "selected_missing_now": int(self._goal_missing(holdings, best)),
            "candidate_count": len(candidates),
            "top_candidates": [
                {
                    "kind": g.kind,
                    "target_id": g.target_id,
                    "score": round(float(g.score), 4),
                    "missing": int(self._goal_missing(holdings, g)),
                }
                for g in ranked
            ],
        }
        return best

    def _immediate_build_value(
        self,
        holdings: dict,
        goal: Optional[Goal] = None,
    ) -> float:
        """Estimate immediate construction value for a hypothetical hand."""
        res = self._normalise_resource_map(holdings)
        p = self._player()

        can_city = (
            p.metropolises_left > 0
            and all(res.get(r, 0) >= amount for r, amount in COST_METROPOLIS.items())
            and any(
                v.structure_owner == self.pid
                and v.structure_type == StructureType.OUTPOST
                for v in self.game.map.vertices.values()
            )
        )

        can_settle = False
        if p.outposts_left > 0 and all(
            res.get(r, 0) >= amount for r, amount in COST_OUTPOST.items()
        ):
            for vid in self._reachable_vertices_known():
                possible, _ = self._potential_settlement(vid)
                if possible:
                    can_settle = True
                    break

        can_route = False
        if (
            goal is not None
            and goal.kind == "road"
            and goal.target_id is not None
            and p.routes_left > 0
            and all(res.get(r, 0) >= amount for r, amount in COST_ROUTE.items())
            and self._goal_still_valid(goal)
        ):
            target, first_edge, _, _ = self._best_expansion_target()
            can_route = target == goal.target_id and first_edge is not None

        if can_city:
            return 6.2
        if can_settle:
            return 5.0
        if can_route:
            return 3.2
        return 0.0

    def _visible_production_by_player(self) -> dict[int, dict[Resource, float]]:
        out: dict[int, dict[Resource, float]] = {
            p.player_id: {r: 0.0 for r in RESOURCE_ORDER} for p in self.game.players
        }
        for hid, tile in self.game.map.hexes.items():
            if not self._hex_visible(hid):
                continue
            if (
                tile.terrain in (TerrainType.WASTELAND, TerrainType.RUINS)
                or tile.number_token <= 0
            ):
                continue
            pips = self.DICE_PIPS.get(tile.number_token, 0)
            for vid in tile.vertex_ids:
                owner = self._known_vertex_owner(vid)
                if owner is None:
                    continue
                amount = (
                    2.0
                    if self.game.map.vertices[vid].structure_type
                    == StructureType.METROPOLIS
                    else 1.0
                )
                out[owner][tile.resource] += pips * amount
        return out

    def _hand_pressure_for(self, res_map: dict) -> float:
        """Считает давление руки для КОНКРЕТНОГО состояния ресурсов."""
        total = sum(res_map.values())
        if total <= 6:
            return 0.0
        risk_factor = max(0.2, 2.0 - self.personality.risk)
        return min(8.0, (total - 6) * 0.85 * risk_factor)

    def _estimate_opponent_acceptance(self, oid: int, give: dict, want: dict) -> float:
        give_map = self._normalise_resource_map(give)
        want_map = self._normalise_resource_map(want)
        give_r = next((r for r in RESOURCE_ORDER if give_map[r]), None)
        want_r = next((r for r in RESOURCE_ORDER if want_map[r]), None)
        if give_r is None or want_r is None:
            return 0.0

        opp = self.game.players[oid]
        total_cards = int(getattr(opp, "total_resources", 0))
        if total_cards <= 0:
            return 0.0

        prod = self._visible_production_by_player().get(
            oid, {r: 0.0 for r in RESOURCE_ORDER}
        )

        # ЭМПАТИЯ: Оцениваем, насколько оппоненту нужна наша карта (give_r)
        # и насколько ему не жалко отдать свою (want_r).
        # Используем видимое производство как прокси для его руки/потребностей.

        want_r_prod = prod[want_r]
        give_r_prod = prod[give_r]

        # Если он вообще не производит то, что мы просим, вероятность что оно у него есть - минимальна
        if want_r_prod == 0 and total_cards < 4:
            return 0.05

        # Оцениваем полезность для оппонента
        # Ему выгодно, если он производит много того, что мы просим, и мало того, что мы предлагаем
        utility_for_opp = (want_r_prod - give_r_prod) * 0.5

        # Учитываем порог жадности (приближенно)
        expected_threshold = 0.40  # Стандартный порог из respond_to_trade
        if self._my_vp() >= int(self.game.points_to_win) - 2:
            expected_threshold += 0.45  # Он знает, что нам невыгодно помогать лидеру

        if utility_for_opp < expected_threshold:
            # Сделка не выглядит выгодной для него, но есть шанс, что у него просто кривая рука
            base_prob = 0.15
        else:
            # Сделка выглядит честной
            base_prob = 0.50 + min(0.35, (utility_for_opp - expected_threshold) * 0.2)

        # Модификатор доступности (готов ли он делиться)
        # Для живых игроков - боты предлагают только когда уверены:
        if total_cards <= 2:
            availability = 0.10  # У него почти пустая рука, даже не спрашиваем
        elif total_cards <= 5:
            availability = 0.40  # Шансы средние
        else:
            availability = 0.70  # Шансы хорошие
        prob = base_prob * availability
        return max(0.0, min(0.90, prob))

    def _best_player_trade(
        self, goal: Goal, legal_actions: list[Action]
    ) -> Optional[ScoredAction]:
        own_turn = int(getattr(self._player(), "personal_turns_taken", 0))
        current_vp = self._my_vp()
        target_vp = int(self.game.points_to_win)

        # КВОТА вместо кулдауна: 2 предложения за ход (или 3 для Торговца)
        max_offers = 3 if self.personality.trading > 1.1 else 2

        diag: dict[str, Any] = {"status": "searching", "candidates": []}
        self._last_player_trade_search = diag

        if current_vp >= target_vp - 2:
            if not goal or self._goal_missing(self._player().resources, goal) > 1:
                return None

        # Проверяем КВОТУ, а не время с прошлого предложения
        if self.player_offers_this_turn >= max_offers or self.trade_rejected_this_turn:
            return None

        if not goal or not goal.needed:
            return None

        current = self._normalise_resource_map(self._player().resources)
        missing_before = self._goal_missing(current, goal)
        if missing_before <= 0 or missing_before > 2:
            return None

        current_value = (
            self._resource_bundle_value(current, goal)
            + self._goal_completion_bonus(current, goal)
            + 0.55 * self._immediate_build_value(current, goal)
        )
        best: Optional[ScoredAction] = None
        others = [
            p.player_id
            for p in self.game.players
            if p.player_id != self.pid and p.connected
        ]
        leader = self._leader_id()

        for act in legal_actions:
            if act.type != "propose_trade":
                continue
            give = self._normalise_resource_map(act.p.get("give", {}))
            want = self._normalise_resource_map(act.p.get("want", {}))
            if sum(give.values()) != 1 or sum(want.values()) != 1:
                continue

            after = self._apply_trade_to_map(current, want, give)
            if after is None:
                continue
            missing_after = self._goal_missing(after, goal)
            if missing_after >= missing_before:
                continue

            value_after = (
                self._resource_bundle_value(after, goal)
                + self._goal_completion_bonus(after, goal)
                + 0.55 * self._immediate_build_value(after, goal)
            )

            gain = value_after - current_value

            closeout = current_vp >= target_vp - 2
            min_gain = self.tuning.panic_trade_floor if closeout else 0.35

            # Паника! Если карт 8+, порог выгоды падает ниже нуля
            if int(getattr(self._player(), "total_resources", 0)) >= 8:
                min_gain = self.tuning.panic_trade_floor

            if gain < min_gain:
                continue

            probabilities = {
                oid: self._estimate_opponent_acceptance(oid, give, want)
                for oid in others
            }
            if not probabilities:
                continue

            any_accept = 1.0 - math.prod(1.0 - q for q in probabilities.values())
            self._p2p_pred_cache()[self._trade_key(give, want)] = float(any_accept)

            leader_help = 0.0
            if leader is not None and probabilities.get(leader, 0.0) > 0:
                leader_help = 0.8 if self._vp(leader, True) >= target_vp - 2 else 0.2

            if any_accept < self.tuning.trade_player_min_acceptance:
                continue

            score = gain * (0.35 + 0.65 * any_accept) - leader_help
            if score <= 0.25:
                continue

            if best is None or score > best.score:
                best = ScoredAction(
                    act, score, f"P2P gain={gain:.2f}, acceptance≈{any_accept:.2f}"
                )

        return best

    def respond_to_trade(self, offer: Any) -> bool:
        self._reset_turn_memory_if_needed()
        if not offer or offer.from_player == self.pid:
            return False
        p = self._player()
        if not p.has_resources(offer.want):
            return False
        goal = self._get_goal()
        current = self._normalise_resource_map(p.resources)
        after = self._apply_trade_to_map(current, offer.give, offer.want)
        if after is None:
            return False
        now = (
            self._resource_bundle_value(current, goal)
            + self._goal_completion_bonus(current, goal)
            + 0.55 * self._immediate_build_value(current, goal)
        )
        new = (
            self._resource_bundle_value(after, goal)
            + self._goal_completion_bonus(after, goal)
            + 0.55 * self._immediate_build_value(after, goal)
        )
        gain = new - now
        threshold = 0.40 / max(0.65, min(1.8, self.personality.trading))
        proposer_vp = self._vp(offer.from_player, public=True)
        if proposer_vp >= int(self.game.points_to_win) - 2:
            threshold += 0.45
        if self._my_vp() >= int(self.game.points_to_win) - 2:
            threshold = min(threshold, 0.25)
        if self._immediate_build_value(after, goal) > self._immediate_build_value(
            current, goal
        ):
            threshold = min(threshold, 0.20)
        # Lower difficulties are more willing to accept marginal bargains;
        # harder profiles demand a larger positive utility. This is deliberately
        # bounded and does not override resource/legality checks above.
        threshold *= {
            "easy": 0.62,
            "normal": 1.0,
            "hard": 1.28,
            "tournament": 1.55,
            "skynet": 1.85,
        }.get(str(self.difficulty or "normal").strip().lower(), 1.0)
        return gain >= threshold

    def _city_recovery_mode(
        self, goal: Optional[Goal], legal_actions: list[Action]
    ) -> bool:
        """Whether an upgrade is the only remaining repeatable construction path."""
        p = self._player()
        return (
            bool(goal)
            and goal.kind == "city"
            and self._goal_still_valid(goal)
            and p.metropolises_left > 0
            and p.outposts_left <= 0
            and p.routes_left <= 0
            and not any(
                a.type in ("build_metropolis", "build_outpost", "build_route")
                for a in legal_actions
            )
        )

    def _best_bank_trade(
        self, goal: Goal, legal_actions: list[Action]
    ) -> Optional[ScoredAction]:
        diag: dict[str, Any] = {"status": "searching", "candidates": []}
        self._last_bank_trade_search = diag

        max_bank_trades = 2 if self.personality.trading > 1.0 else 1
        if self.bank_trades_this_turn >= max_bank_trades:
            return None

        current = self._normalise_resource_map(self._player().resources)
        total_cards = sum(current.values())
        current_val = (
            self._resource_bundle_value(current, goal)
            + self._goal_completion_bonus(current, goal)
            + 0.60 * self._immediate_build_value(current, goal)
        )
        no_build_piece_recovery = self._city_recovery_mode(goal, legal_actions)
        best = None

        for act in legal_actions:
            if act.type != "trade_bank":
                continue
            pars = act.p
            give_r = self._as_resource(pars.get("give"))
            recv_r = self._as_resource(pars.get("receive"))
            if give_r is None or recv_r is None or give_r == recv_r:
                continue

            rate = int(self.game._bank_rate(self.pid, give_r))
            if current[give_r] < rate:
                continue

            after = dict(current)
            after[give_r] -= rate
            after[recv_r] += 1
            new_val = (
                self._resource_bundle_value(after, goal)
                + self._goal_completion_bonus(after, goal)
                + 0.60 * self._immediate_build_value(after, goal)
            )

            # Считаем, насколько разгрузилась рука
            hp_before = self._hand_pressure_for(current)
            hp_after = self._hand_pressure_for(after)
            gain = (new_val - current_val) + ((hp_before - hp_after) * 0.8)

            missing_before = self._goal_missing(current, goal)
            missing_after = self._goal_missing(after, goal)

            # --- ИНСТИНКТ ВЫЖИВАНИЯ (STARVATION OVERRIDE) ---
            # Если мы получаем ресурс, которого у нас НОЛЬ, и он критически нужен для цели
            if current[recv_r] == 0 and goal and goal.needed.get(recv_r, 0) > 0:
                # Даем огромный искусственный бонус, чтобы перебить потерю 4-х карт!
                gain += 3.5

                # Если у нас скопилось много "мусора" (например 6+ дерева), желание обменять его на дефицит становится непреодолимым
                if current[give_r] >= rate + 2:
                    gain += 2.0

            if missing_after < missing_before:
                gain += 0.55 * (missing_before - missing_after)
                if no_build_piece_recovery:
                    gain = max(gain, 4.25)

            closeout = self._my_vp() >= int(self.game.points_to_win) - 1
            if closeout and self._immediate_build_value(
                after, goal
            ) > self._immediate_build_value(current, goal):
                gain += self.tuning.endgame_aggression_bonus

            threshold = self.tuning.trade_bank_min_gain

            if total_cards >= 8:
                threshold = self.tuning.panic_trade_floor

            passed = gain >= threshold
            if passed and (best is None or gain > best.score):
                best = ScoredAction(act, gain, f"bank gain={gain:.2f}; rate={rate}:1")

        return best

    def _setup_second_vertex_score(self, first_vid: int, second_vid: int) -> float:
        score = self._vertex_score(second_vid, setup_round=2)
        first = self.game.map.vertices[first_vid]
        second = self.game.map.vertices[second_vid]
        first_res = set()
        for hid in first.adjacent_hexes:
            if self._hex_visible(hid):
                tile = self.game.map.hexes[hid]
                if (
                    tile.terrain not in (TerrainType.WASTELAND, TerrainType.RUINS)
                    and tile.number_token > 0
                ):
                    first_res.add(tile.resource)
        second_res = set()
        for hid in second.adjacent_hexes:
            if self._hex_visible(hid):
                tile = self.game.map.hexes[hid]
                if (
                    tile.terrain not in (TerrainType.WASTELAND, TerrainType.RUINS)
                    and tile.number_token > 0
                ):
                    second_res.add(tile.resource)
        score += 0.65 * len(second_res - first_res)
        score += 0.20 * len(second_res.intersection({Resource.GRAIN, Resource.ORE}))
        # Do not overvalue adjacency to the first settlement; the distance rule
        # already excludes immediate neighbours, so moderate geographic spread
        # is a useful hedge against one blocked production area.
        distance = self._hex_distance_between_vertices(first_vid, second_vid)
        score += min(0.60, 0.12 * distance)
        return score

    def _hex_distance_between_vertices(self, a: int, b: int) -> int:
        va = self.game.map.vertices[a]
        vb = self.game.map.vertices[b]
        if not va.adjacent_hexes or not vb.adjacent_hexes:
            return 0
        best = 999
        for ha in va.adjacent_hexes:
            ta = self.game.map.hexes[ha]
            for hb in vb.adjacent_hexes:
                tb = self.game.map.hexes[hb]
                d = max(
                    abs(ta.q - tb.q),
                    abs(ta.r - tb.r),
                    abs((ta.q + ta.r) - (tb.q + tb.r)),
                )
                best = min(best, d)
        return 0 if best == 999 else best

    def _raider_target_score(self, hid: int, *, allow_current: bool = False) -> float:
        # For ordinary moves the current raider tile is an illegal target. The
        # Raider Hunter card, however, needs to value the production currently
        # blocked there, so it may explicitly request this tile's raw score.
        if hid == getattr(self.game, "raider_hex", None) and not allow_current:
            return -1e6
        if not self._hex_visible(hid):
            # No hidden terrain, number token, or building adjacency inspection.
            return 0.25 * self.personality.fog_curiosity
        tile = self.game.map.hexes[hid]
        if tile.terrain in (TerrainType.WASTELAND, TerrainType.RUINS):
            return -0.7
        pips = self.DICE_PIPS.get(tile.number_token, 0)
        enemy_prod = 0.0
        own_prod = 0.0
        leader = self._leader_id()
        leader_prod = 0.0
        for vid in tile.vertex_ids:
            owner = self._known_vertex_owner(vid)
            if owner is None:
                continue
            v = self.game.map.vertices[vid]
            amount = 2.0 if v.structure_type == StructureType.METROPOLIS else 1.0
            if owner == self.pid:
                own_prod += amount
            else:
                enemy_prod += amount
                if owner == leader:
                    leader_prod += amount
        value = pips * (1.0 * enemy_prod + 0.65 * leader_prod - 1.35 * own_prod)
        return value * (0.75 + 0.25 * self.personality.aggression)

    def _play_charter_score(self, act: Action, goal: Goal) -> float:
        ct_raw = act.p.get("charter_type", "")
        try:
            ct = ct_raw if isinstance(ct_raw, CharterType) else CharterType(ct_raw)
        except Exception:
            return -1e6
        pars = act.p
        p = self._player()
        target = int(self.game.points_to_win)

        if ct == CharterType.VANGUARD:
            best_threat = max(
                (self._raider_target_score(h) for h in self.game.map.hexes), default=0.0
            )
            # More Vanguards may eventually award the Grand Garrison (+2 VP).
            garrison_progress = max(
                0,
                p.vanguards_played
                + 1
                - max(
                    (
                        x.vanguards_played
                        for x in self.game.players
                        if x.player_id != self.pid
                    ),
                    default=0,
                ),
            )
            score = (
                1.0
                + 0.70 * best_threat
                + 0.55 * garrison_progress * self.personality.aggression
            )
            if self._my_vp() >= target - 2:
                score += 0.45 * self.tuning.endgame_aggression_bonus
            if score < self.tuning.play_knight_min_threat:
                return -1e6
            return score

        if ct == CharterType.RAIDER_HUNTER:
            hid = getattr(self.game, "raider_hex", None)
            # Score the resource production restored by removing the raider
            # from its current hex. Do not use the ordinary illegal-target
            # sentinel (-1e6), or the sign inversion creates a bogus +400,000
            # card score and makes the AI waste turns playing this card.
            relief = (
                0.0
                if hid is None
                else max(0.0, -self._raider_target_score(hid, allow_current=True))
            )
            score = 0.7 + 0.40 * relief
            if score < self.tuning.play_pirate_hunter_min_utility:
                return -1e6
            return score

        if ct == CharterType.EXPANSION_ACT:
            if p.routes_left <= 0:
                return -1e6
            road_actions = [
                a
                for a in self.game.enumerate_legal_actions(self.pid)
                if a.type == "build_route"
            ]
            if not road_actions:
                return -1e6
            expansion = self._best_expansion_target()
            scores = sorted(
                (
                    self._route_edge_score(int(a.p["edge_id"]), expansion)
                    for a in road_actions
                ),
                reverse=True,
            )
            score = self.tuning.free_road_bonus + (scores[0] if scores else 0.0)
            if len(scores) > 1 and p.routes_left > 1:
                score += 0.45 * scores[1]
            return score

        if ct == CharterType.ABUNDANCE:
            r1 = self._as_resource(pars.get("resource1"))
            r2 = self._as_resource(pars.get("resource2"))
            if r1 is None or r2 is None:
                return -1e6
            before = self._normalise_resource_map(p.resources)
            after = dict(before)
            after[r1] += 1
            after[r2] += 1
            utility = self._resource_bundle_value(
                after, goal
            ) - self._resource_bundle_value(before, goal)
            utility += 0.50 * (
                self._goal_missing(before, goal) - self._goal_missing(after, goal)
            )
            if utility < self.tuning.play_year_of_plenty_min_utility:
                return -1e6
            return utility + 0.15

        if ct == CharterType.MONOPOLY:
            wanted = self._as_resource(pars.get("resource"))
            if wanted is None:
                return -1e6
            prod = self._visible_production_by_player()
            expected_taken = 0.0
            for opp in self.game.players:
                if opp.player_id == self.pid:
                    continue
                # Public total count only; distribution by resource remains hidden.
                total = int(getattr(opp, "total_resources", 0))
                prod_total = sum(prod.get(opp.player_id, {}).values())
                share = (prod.get(opp.player_id, {}).get(wanted, 0.0) + 0.6) / (
                    prod_total + 3.0
                )
                expected_taken += min(2.0, total * share * 0.65)
            before = self._normalise_resource_map(p.resources)
            after = dict(before)
            after[wanted] += expected_taken
            utility = self._resource_bundle_value(
                after, goal
            ) - self._resource_bundle_value(before, goal)
            leader = self._leader_id()
            if leader is not None and prod.get(leader, {}).get(wanted, 0.0) > 0:
                utility += 0.20 * self.personality.aggression
            if utility < self.tuning.play_monopoly_min_utility:
                return -1e6
            return utility

        if ct == CharterType.PIONEER:
            hid = pars.get("hex_id")
            if hid not in self.game.map.hexes or self._hex_visible(int(hid)):
                return -1e6
            tile = self.game.map.hexes[int(hid)]
            # Score by topology and nearby expansion opportunities only; never
            # read hidden terrain/token for this unrevealed hex.
            adjacent_vertices = tile.vertex_ids
            own_network = self._reachable_vertices_known()
            near_network = sum(
                1
                for vid in adjacent_vertices
                if vid in own_network
                or any(
                    n in own_network
                    for n in self.game.map.vertices[vid].adjacent_vertices
                )
            )
            possible_sites = sum(
                1 for vid in adjacent_vertices if self._potential_settlement(vid)[0]
            )
            utility = (
                0.55
                + 0.42 * near_network
                + 0.35 * possible_sites * self.personality.fog_curiosity
            )
            utility *= 0.75 + 0.25 * self.personality.risk
            if utility < self.tuning.play_explorer_min_utility:
                return -1e6
            return utility

        return -1e6

    def _score_purify_hex(self, act: Action, goal: Goal) -> float:
        hid = act.p.get("hex_id")
        if hid not in self.game.map.hexes or not self._hex_visible(int(hid)):
            return -1e6
        tile = self.game.map.hexes[int(hid)]
        stage = int(tile.ext.get("blight_stage", 0))
        if stage <= 0:
            return -1e6
        own_buildings = 0
        own_production = 0.0
        for vid in tile.vertex_ids:
            if self._known_vertex_owner(vid) == self.pid:
                own_buildings += 1
                v = self.game.map.vertices[vid]
                own_production += (
                    2.0 if v.structure_type == StructureType.METROPOLIS else 1.0
                )
        if own_buildings <= 0:
            return -1e6
        pips = self.DICE_PIPS.get(tile.number_token, 0)
        # Benefit is a discounted expected production recovery; this is a
        # tactical action, not a default alternative to building every turn.
        benefit = (pips / 5.0) * own_production * (1.2 + 0.35 * min(stage, 2))
        cost = 0.75 * self._resource_weight(Resource.GRAIN, goal)
        score = benefit - cost
        if self._in_endgame() and self._my_vp() < int(self.game.points_to_win) - 1:
            score -= 0.65
        # Mod actions are useful tactical investments, but cannot automatically
        # beat a valuable VP build just because a productive hex has multiple
        # adjacent structures.
        return min(3.4, score)

    def _score_corrupt_hex(self, act: Action, goal: Goal) -> float:
        hid = act.p.get("hex_id")
        if hid not in self.game.map.hexes or not self._hex_visible(int(hid)):
            return -1e6
        tile = self.game.map.hexes[int(hid)]
        stage = int(tile.ext.get("blight_stage", 0))
        if stage >= 2:
            return -1e6
        own_buildings = 0
        enemy_buildings = 0
        leader_buildings = 0
        leader = self._leader_id()
        enemy_prod = 0.0
        pips = self.DICE_PIPS.get(tile.number_token, 0)
        for vid in tile.vertex_ids:
            owner = self._known_vertex_owner(vid)
            if owner is None:
                continue
            v = self.game.map.vertices[vid]
            amount = 2.0 if v.structure_type == StructureType.METROPOLIS else 1.0
            if owner == self.pid:
                own_buildings += 1
            else:
                enemy_buildings += 1
                enemy_prod += amount
                if owner == leader:
                    leader_buildings += 1
        if own_buildings or enemy_buildings == 0:
            return -1e6
        benefit = (pips / 5.0) * enemy_prod * (1.1 + 0.30 * self.personality.aggression)
        benefit += 0.18 * leader_buildings * self.personality.aggression
        # Avoid repeatedly spending on sabotage while own economy is behind.
        if self._my_vp() + 2 < self._leader_vp():
            benefit *= 0.85
        cost = 0.50 * self._resource_weight(Resource.GRAIN, goal)
        score = min(2.8, benefit - cost)

        # Не возобновляем борьбу за сектор сразу после окончания защиты.
        turn = int(getattr(self.game, "global_turn", 0))
        shield_until = int(tile.ext.get("shield_until", -1))

        if shield_until >= 0:
            turns_since_shield = turn - shield_until
            if 0 <= turns_since_shield < 12:
                return min(score, 0.10)

        # Если мы близки к победе и лидируем, приоритет — завершить игру,
        # а не тратить ресурсы на обычное осквернение чужих секторов.
        target_vp = int(getattr(self.game, "points_to_win", 12))
        if self._my_vp() >= target_vp - 2 and self._my_vp() >= self._leader_vp():
            return min(score, 0.10)

        return score

    def _score_activate_route(self, act: Action, goal: Goal) -> float:
        # The mod's exact parameters/benefits belong to mods_pack.py. Keep a
        # conservative generic valuation so a mod action cannot dominate a
        # concrete VP build merely because it exists.
        score = 0.10
        if goal and goal.needed:
            missing = self._goal_missing(self._player().resources, goal)
            if missing > 0:
                score += 0.70 + 0.20 * min(2, missing)
        if self._in_endgame():
            score -= 0.35
        return score

    def _score_extension_action(self, act: Action, goal: Goal) -> Optional[float]:
        """Ask installed mods to score their own actions through one generic API.

        A mod may provide ``score_ai_action(game, player_id, action, context)``
        and ``ai_gene_specs()``. Adding a new ordinary action mod then requires
        no action-specific branch in the base AI. Failures are recorded and the
        legacy fallback scorer remains available.
        """
        plugins = getattr(self.game, "plugins", None)
        if plugins is None:
            plugins = getattr(self.game, "_plugins", ())
        genes: dict[str, float] = {}
        for plugin in plugins or ():
            specs_fn = getattr(plugin, "ai_gene_specs", None)
            if not callable(specs_fn):
                continue
            try:
                specs = specs_fn()
            except Exception:
                continue
            if not isinstance(specs, dict):
                continue
            for name, spec in specs.items():
                if isinstance(spec, dict) and "default" in spec:
                    try:
                        genes[str(name)] = float(spec["default"])
                    except (TypeError, ValueError):
                        pass
        # Explicitly supplied/trained genes override module defaults.
        genes.update(self.mod_genes)
        context = {
            "goal": goal,
            "personality": self.personality,
            "tuning": self.tuning,
            "genes": genes,
            "resource_weight": self._resource_weight,
            "in_endgame": self._in_endgame(),
            "leader_id": self._leader_id(),
            "my_vp": self._my_vp(),
            "leader_vp": self._leader_vp(),
        }
        for plugin in plugins or ():
            scorer = getattr(plugin, "score_ai_action", None)
            if not callable(scorer):
                continue
            try:
                score = scorer(self.game, self.pid, act, context)
            except Exception as exc:
                errors = self.game.ext.setdefault("ai_plugin_score_errors", [])
                errors.append(
                    {
                        "plugin": getattr(plugin, "mod_id", type(plugin).__name__),
                        "action": act.type,
                        "error": repr(exc),
                    }
                )
                del errors[:-100]
                continue
            if score is not None:
                try:
                    numeric = float(score)
                except (TypeError, ValueError):
                    return -1e6
                return numeric if math.isfinite(numeric) else -1e6
        return None

    def _score_action(
        self, act: Action, goal: Goal, legal_actions: list[Action]
    ) -> float:
        pars = act.p
        p = self._player()
        current_vp = self._my_vp()
        target_vp = int(getattr(self.game, "points_to_win", 12))
        closeout = current_vp >= target_vp - 2

        if act.type == "end_turn":
            return 0.0

        # Считаем давление карт ДО совершения действия
        current_res = self._normalise_resource_map(p.resources)
        hp_before = self._hand_pressure_for(current_res)

        extension_score = self._score_extension_action(act, goal)
        if extension_score is not None:
            return extension_score

        if act.type == "build_metropolis":
            vid = int(pars.get("vertex_id", -1))
            score = (
                4.35 + 0.34 * self._vertex_production_score(vid)
            ) * self.personality.city_bias
            after_res = dict(current_res)
            for r, amount in COST_METROPOLIS.items():
                after_res[r] = max(0, after_res.get(r, 0) - amount)
            score += (hp_before - self._hand_pressure_for(after_res)) * 0.6
            if current_vp + 1 >= target_vp:
                return 10_000.0
            if closeout:
                score += self.tuning.closeout_city_bonus * self._comeback_factor()
            if current_vp >= self.tuning.endgame_vp_threshold:
                score += 0.45 * self.tuning.endgame_aggression_bonus
            return score if score >= self.tuning.build_city_min_score else -1e6

        if act.type == "build_outpost":
            vid = int(pars.get("vertex_id", -1))
            score = (
                2.90 + 0.88 * self._vertex_score(vid, setup_round=2)
            ) * self.personality.settlement_bias
            after_res = dict(current_res)
            for r, amount in COST_OUTPOST.items():
                after_res[r] = max(0, after_res.get(r, 0) - amount)
            score += (hp_before - self._hand_pressure_for(after_res)) * 0.6
            if current_vp + 1 >= target_vp:
                return 10_000.0
            if closeout:
                score += self.tuning.closeout_settlement_bonus * self._comeback_factor()
            if p.outposts_left <= 1:
                score -= 0.35
            return score if score >= self.tuning.build_settlement_min_score else -1e6

        if act.type == "build_route":
            eid = int(pars.get("edge_id", -1))
            if self._route_would_win(eid):
                return 10_000.0
            score = self._route_edge_score(eid)
            after_res = dict(current_res)
            for r, amount in COST_ROUTE.items():
                after_res[r] = max(0, after_res.get(r, 0) - amount)
            score += (hp_before - self._hand_pressure_for(after_res)) * 0.3
            return score if score >= self.tuning.build_road_min_score else -1e6

        if act.type == "trade_bank":
            return -1e6  # Scored once through _best_bank_trade, with per-turn cap.
        if act.type == "propose_trade":
            return -1e6  # Scored once through _best_player_trade, with cooldown.

        if act.type == "buy_charter":
            if len(getattr(self.game, "charter_deck", [])) <= 0:
                return -1e6
            if (
                current_vp < target_vp - 1
                and self._city_recovery_mode(goal, legal_actions)
                and self._goal_missing(p.resources, goal) > 0
            ):
                # A charter costs grain and ore, precisely the resources a
                # stranded city goal is trying to collect. Preserve them unless
                # the player is close enough that a Triumph can win immediately.
                return -1e6
            # The deck composition is not read. Purchase utility combines the
            # chance of a hidden Triumph VP with future tactical-card value.
            remaining = len(self.game.charter_deck)
            score = (
                1.8
                + 1.1 * self.personality.devcards
                + 0.25 * min(2.0, self._leader_vp() / max(1, target_vp))
            )
            after_res = dict(current_res)
            for r, amount in COST_CHARTER.items():
                after_res[r] = max(0, after_res.get(r, 0) - amount)
            score += (hp_before - self._hand_pressure_for(after_res)) * 0.5
            score += self.triumph_closeout_value(current_vp, target_vp)
            if current_vp + 1 >= target_vp:
                # A Triumph can win immediately; this is a probability-weighted
                # priority, not a claim that the top hidden card is known.
                score += 2.0
            return score if score >= self.tuning.buy_dev_card_min_score else -1e6

        if act.type == "play_charter":
            value = self._play_charter_score(act, goal)
            if value <= -1e5:
                return value
            return value * (0.75 + 0.25 * max(0.2, self.personality.devcards))

        if act.type == "activate_route":
            return self._score_activate_route(act, goal)
        if act.type == "purify_hex":
            return self._score_purify_hex(act, goal)
        if act.type == "corrupt_hex":
            return self._score_corrupt_hex(act, goal)
        return -1e6

    @staticmethod
    def triumph_closeout_value(current_vp: int, target_vp: int) -> float:
        if current_vp < target_vp - 2:
            return 0.0
        if current_vp == target_vp - 1:
            return 2.5 + 2.0 * BotBrain.TRIUMPH_PRIOR
        return 0.75 + BotBrain.TRIUMPH_PRIOR

    def _score_discard(self, act: Action, goal: Goal) -> float:
        discard = self._normalise_resource_map(act.p.get("resources", {}))
        hand = self._normalise_resource_map(self._player().resources)
        loss = 0.0
        for res, count in discard.items():
            for k in range(count):
                before = hand[res]
                scarcity = (
                    1.42
                    if before <= 1
                    else 1.18
                    if before == 2
                    else 1.0
                    if before <= 4
                    else 0.72
                )
                goal_cost = (
                    0.65
                    if goal and goal.needed.get(res, 0) > max(0, before - k - 1)
                    else 0.0
                )
                loss += self._resource_base(res) * scarcity + goal_cost
        return -loss

    def _weighted_choice(
        self, candidates: list[ScoredAction]
    ) -> Optional[ScoredAction]:
        """Choose with a meaningful skill profile, not just a tiny noise multiplier.

        ``easy`` deliberately considers a wider band of lower-scored actions;
        ``normal`` stays near the best plan; ``hard`` almost always picks the
        highest score, keeping only tiny near-ties for personality. The two
        stronger internal profiles remain useful for simulations.
        """
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.score, reverse=True)
        best_score = candidates[0].score
        profiles = {
            # (maximum regret below best score, max candidate count, temperature)
            "easy": (1.45, 6, 0.68),
            "normal": (0.62, 4, 0.20),
            "hard": (0.16, 3, 0.045),
            "tournament": (0.055, 2, 0.015),
            "skynet": (0.0, 1, 0.001),
        }
        difficulty = str(self.difficulty or "normal").strip().lower()
        regret, limit, base_temperature = profiles.get(difficulty, profiles["normal"])
        pool = [item for item in candidates if item.score >= best_score - regret][:limit]
        if not pool:
            return candidates[0]
        if len(pool) == 1:
            return pool[0]

        # Keep a modest archetype signature without allowing it to override the
        # selected difficulty tier. Easy bots make visibly less reliable choices;
        # hard bots remain close to greedy-best play.
        personality_noise = max(0.0, min(0.20, float(self.personality.randomness)))
        temperature = max(0.001, base_temperature + personality_noise * 0.30)
        weights = [
            math.exp(max(-40.0, (item.score - best_score) / temperature))
            for item in pool
        ]
        return self.rng.choices(pool, weights=weights, k=1)[0]

    def _difficulty_noise(self) -> float:
        """Compatibility helper for callers/diagnostics from older snapshots."""
        return {
            "easy": 1.8,
            "normal": 1.0,
            "hard": 0.35,
            "tournament": 0.12,
            "skynet": 0.0,
        }.get(str(self.difficulty or "normal").strip().lower(), 1.0)

    def _select_exact(
        self, legal_actions: list[Action], action_type: str, params: dict
    ) -> Optional[Action]:
        for act in legal_actions:
            if act.type == action_type and act.p == params:
                return act
        return None

    def _select_action(
        self, act: Action, score: float, reason: str, legal_actions: list[Action]
    ) -> Optional[Action]:
        # `act` is required to be the exact object from this legal list.
        for legal in legal_actions:
            if legal is act:
                goal = self.current_goal
                goal_snapshot = None
                if goal is not None:
                    goal_snapshot = {
                        "kind": goal.kind,
                        "target_id": goal.target_id,
                        "score": float(goal.score),
                        "needed": {
                            getattr(r, "value", str(r)): int(v)
                            for r, v in goal.needed.items()
                        },
                        "created_turn": int(goal.created_turn),
                        "locked_until_turn": int(goal.locked_until_turn),
                    }
                player = self._player()
                self.last_decision = {
                    "turn": int(self.game.global_turn),
                    "personal_turn": int(getattr(player, "personal_turns_taken", 0)),
                    "action": act.type,
                    "params": act.p,
                    "score": float(score),
                    "reason": reason,
                    "goal": goal_snapshot,
                    "goal_selection_debug": copy.deepcopy(self._last_goal_selection),
                    "bank_trade_search_debug": copy.deepcopy(
                        self._last_bank_trade_search
                    ),
                    "player_trade_search_debug": copy.deepcopy(
                        self._last_player_trade_search
                    ),
                    "vp": int(self._my_vp()),
                    "resources": {
                        getattr(r, "value", str(r)): int(v)
                        for r, v in player.resources.items()
                    },
                    "legal_action_types": sorted({item.type for item in legal_actions}),
                    "top_candidates": list(self._decision_candidates_snapshot),
                }
                return legal
        return None

    def choose_action(self, player_id: int, legal_actions: list[Action]) -> Action:
        self._decision_candidates_snapshot = []
        self._reset_turn_memory_if_needed()
        if not legal_actions:
            # The engine normally avoids calling a chooser with no legal moves.
            phase = getattr(self.game, "phase", None)
            if phase == GamePhase.ROLL:
                return Action.make("roll_dice")
            if phase == GamePhase.MAIN:
                return Action.make("end_turn")
            return Action.make("end_turn")

        phase = self.game.phase
        p = self._player()

        # Reactive trade acceptance. During trade resolution this player's list
        # should contain only respond_trade actions.
        trade_actions = [a for a in legal_actions if a.type == "respond_trade"]
        if trade_actions:
            offer = getattr(self.game, "active_trade_offer", None)
            accept = self.respond_to_trade(offer) if offer else False
            wanted = next(
                (a for a in trade_actions if bool(a.p.get("accept")) == accept),
                trade_actions[0],
            )
            return (
                self._select_action(wanted, 0.0, "respond to trade", legal_actions)
                or trade_actions[0]
            )

        if phase == GamePhase.ROLL:
            chosen = next(
                (a for a in legal_actions if a.type == "roll_dice"), legal_actions[0]
            )
            return (
                self._select_action(chosen, 0.0, "roll", legal_actions)
                or legal_actions[0]
            )

        if phase == GamePhase.DISCARD:
            goal = self._get_goal()
            scored = [
                ScoredAction(a, self._score_discard(a, goal), "minimise discard loss")
                for a in legal_actions
                if a.type == "discard"
            ]
            chosen = (
                self._weighted_choice(scored)
                if scored
                else ScoredAction(legal_actions[0], 0.0, "fallback discard")
            )
            return (
                self._select_action(
                    chosen.action, chosen.score, chosen.reason, legal_actions
                )
                or legal_actions[0]
            )

        if phase == GamePhase.RAIDER_MOVE:
            scored = []
            for act in legal_actions:
                if act.type == "move_raider":
                    hid = int(act.p.get("hex_id", -1))
                    scored.append(
                        ScoredAction(
                            act,
                            self._raider_target_score(hid),
                            "deny enemy production / protect own",
                        )
                    )
            chosen = self._weighted_choice(scored)
            if chosen:
                return (
                    self._select_action(
                        chosen.action, chosen.score, chosen.reason, legal_actions
                    )
                    or legal_actions[0]
                )
            return legal_actions[0]

        if phase == GamePhase.RAIDER_STEAL:
            candidates = [a for a in legal_actions if a.type == "choose_victim"]
            if candidates:

                def victim_score(act: Action) -> float:
                    vid = int(act.p.get("victim_id", -1))
                    vp = self._vp(vid, True)
                    total = int(getattr(self.game.players[vid], "total_resources", 0))
                    return (
                        0.8 * vp
                        + 0.25 * min(total, 8)
                        + (0.7 if vid == self._leader_id() else 0.0)
                    )

                scored_victims = [
                    ScoredAction(
                        act,
                        victim_score(act)
                        - 0.015 * self._player_order_distance(int(act.p.get("victim_id", -1))),
                        "choose public/high-value victim",
                    )
                    for act in candidates
                ]
                chosen = self._weighted_choice(scored_victims)
                best = chosen.action if chosen else candidates[0]
                return (
                    self._select_action(
                        best,
                        chosen.score if chosen else victim_score(best),
                        "choose public/high-value victim",
                        legal_actions,
                    )
                    or best
                )
            return legal_actions[0]

        if phase == GamePhase.SETUP:
            actions = [a for a in legal_actions if a.type == "setup_outpost"]
            if actions:
                is_second = getattr(self.game, "setup_index", 0) >= len(
                    self.game.players
                )
                scored = []
                for act in actions:
                    vid = int(act.p.get("vertex_id", -1))
                    score = self._vertex_score(vid, setup_round=2 if is_second else 1)
                    if is_second and self._setup_anchor_vertex is not None:
                        score = self._setup_second_vertex_score(
                            self._setup_anchor_vertex, vid
                        )
                    scored.append(
                        ScoredAction(
                            act, score, "setup settlement production/diversity"
                        )
                    )
                chosen = self._weighted_choice(scored)
                if chosen:
                    if not is_second:
                        self._setup_anchor_vertex = int(chosen.action.p["vertex_id"])
                    return (
                        self._select_action(
                            chosen.action, chosen.score, chosen.reason, legal_actions
                        )
                        or actions[0]
                    )

        if phase == GamePhase.SETUP_ROUTE:
            actions = [a for a in legal_actions if a.type == "setup_route"]
            if actions:
                scored = []
                for act in actions:
                    eid = int(act.p.get("edge_id", -1))
                    edge = self.game.map.edges[eid]
                    nxt = (
                        edge.vertices[1]
                        if edge.vertices[0] == self.game.last_setup_vertex
                        else edge.vertices[0]
                    )
                    score = 0.2 + self._vertex_production_score(nxt) * 0.35
                    # Prefer the edge that points toward unrevealed/new production
                    # potential; do not inspect hidden hex identities.
                    score += (
                        0.25
                        * sum(
                            1
                            for h in self.game.map.vertices[nxt].adjacent_hexes
                            if not self._hex_visible(h)
                        )
                        * self.personality.fog_curiosity
                    )
                    scored.append(ScoredAction(act, score, "setup road direction"))
                chosen = self._weighted_choice(scored)
                if chosen:
                    return (
                        self._select_action(
                            chosen.action, chosen.score, chosen.reason, legal_actions
                        )
                        or actions[0]
                    )

        if phase == GamePhase.MAIN:
            # Do not evaluate the offer as a proposer action; the engine handles
            # offer resolution before normal main-phase actions resume.
            self._last_bank_trade_search = {}
            self._last_player_trade_search = {}
            goal = self._get_goal(legal_actions)
            candidates: list[ScoredAction] = []

            # First: immediate legal win, including VP city/settlement or route
            # award. Scores of 10,000 intentionally dominate all other actions.
            for act in legal_actions:
                if act.type == "build_metropolis" and self._my_vp() + 1 >= int(
                    self.game.points_to_win
                ):
                    return (
                        self._select_action(
                            act,
                            10_000.0,
                            "immediate victory by metropolis",
                            legal_actions,
                        )
                        or act
                    )
                if act.type == "build_outpost" and self._my_vp() + 1 >= int(
                    self.game.points_to_win
                ):
                    return (
                        self._select_action(
                            act, 10_000.0, "immediate victory by outpost", legal_actions
                        )
                        or act
                    )
                if act.type == "build_route" and self._route_would_win(
                    int(act.p.get("edge_id", -1))
                ):
                    return (
                        self._select_action(
                            act,
                            10_000.0,
                            "immediate victory by longest route",
                            legal_actions,
                        )
                        or act
                    )

            evaluated: list[dict[str, Any]] = []
            for act in legal_actions:
                score = self._score_action(act, goal, legal_actions)
                if act.type == "end_turn":
                    continue
                eligible = score > 0.15
                evaluated.append(
                    {
                        "action": act.type,
                        "params": act.p,
                        "score": round(float(score), 4),
                        "considered": eligible,
                        "reason": act.type if eligible else "filtered: score <= 0.15",
                    }
                )
                if eligible:
                    # Separate trade actions are considered here so that a
                    # strong bank/P2P opportunity competes with builds, rather
                    # than receiving unconditional priority.
                    candidates.append(ScoredAction(act, score, act.type))

            bank = self._best_bank_trade(goal, legal_actions)
            if bank:
                candidates.append(bank)
                evaluated.append(
                    {
                        "action": bank.action.type,
                        "params": bank.action.p,
                        "score": round(float(bank.score), 4),
                        "considered": True,
                        "reason": bank.reason,
                    }
                )
            elif any(a.type == "trade_bank" for a in legal_actions):
                evaluated.append(
                    {
                        "action": "trade_bank_search",
                        "params": {},
                        "score": None,
                        "considered": False,
                        "reason": "no bank trade improved goal/resource utility enough",
                    }
                )

            p2p = self._best_player_trade(goal, legal_actions)
            if p2p:
                candidates.append(p2p)
                evaluated.append(
                    {
                        "action": p2p.action.type,
                        "params": p2p.action.p,
                        "score": round(float(p2p.score), 4),
                        "considered": True,
                        "reason": p2p.reason,
                    }
                )
            elif any(a.type == "propose_trade" for a in legal_actions):
                # Reuse the result recorded by _best_player_trade rather than
                # maintaining a second, potentially divergent copy of its rules.
                trade_search = self._last_player_trade_search or {}
                why = trade_search.get("reason") or (
                    "no candidate returned by _best_player_trade"
                )
                evaluated.append(
                    {
                        "action": "propose_trade_search",
                        "params": {},
                        "score": None,
                        "considered": False,
                        "reason": why,
                    }
                )

            self._decision_candidates_snapshot = sorted(
                evaluated,
                key=lambda item: (
                    -(
                        item["score"]
                        if isinstance(item["score"], (int, float))
                        else -1e9
                    ),
                    item["action"],
                    repr(item["params"]),
                ),
            )[:12]
            debug_trade = self._my_vp() >= 7 and any(
                a.type in ("trade_bank", "propose_trade") for a in legal_actions
            )

            chosen = self._weighted_choice(candidates)

            # If an action is below threshold or the only candidates are weak,
            # end the turn. Do not return fabricated/parameter-mismatched actions.
            if chosen:
                if chosen.action.type == "trade_bank":
                    self.bank_trades_this_turn += 1
                elif chosen.action.type == "propose_trade":
                    self.player_offers_this_turn += 1
                    self.last_offer_personal_turn = int(
                        getattr(p, "personal_turns_taken", 0)
                    )
                    current = self._normalise_resource_map(p.resources)
                    self.offer_resources_before = dict(current)
                    give = self._normalise_resource_map(chosen.action.p.get("give", {}))
                    want = self._normalise_resource_map(chosen.action.p.get("want", {}))
                    self.offer_expected_after = self._apply_trade_to_map(
                        current, want, give
                    )
                    self._pending_offer_pred = self._p2p_pred_cache().get(
                        self._trade_key(give, want)
                    )
                    self.our_offer_pending = True
                return self._select_action(
                    chosen.action, chosen.score, chosen.reason, legal_actions
                ) or next(
                    (a for a in legal_actions if a.type == "end_turn"), legal_actions[0]
                )

            end = next(
                (a for a in legal_actions if a.type == "end_turn"), legal_actions[0]
            )
            return (
                self._select_action(
                    end, 0.0, "no worthwhile legal action", legal_actions
                )
                or end
            )

        # Defensive fallback: prefer a supplied legal action; never invent a
        # guessed target for a malformed or unmatched action.
        return legal_actions[0]


# Compatibility helper for a few integrations that used the old method name.
def make_brain(player_id: int, game: HexGame, **kwargs) -> BotBrain:
    return BotBrain(player_id=player_id, game=game, **kwargs)
