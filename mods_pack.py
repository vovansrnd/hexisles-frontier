"""Optional gameplay mods for HexIsles: Frontier.

The core owns the game rules and action loop. Mods register action handlers,
legal-action enumerators and event hooks through the public HexGame extension
API. They must not call undocumented ``mark_progress`` methods or partially
mutate game state before validation has completed.

Implemented rules
-----------------
BlightMod
  * On a 7, blight spreads with a configurable probability (default 40%).
  * ``environmental`` permits natural spreading and cleansing only; ``tactical``
    preserves the legacy actions available to every player; ``factions`` tracks
    a dynamic Cult/Order alignment and awards capped progress VP.
  * It targets productive hexes, preferring more productive/occupied hexes but
    tending to spread to a clean hex rather than repeatedly maxing one hex.
  * Stage 1 reduces each adjacent structure's yield by one; stage 2 reduces it
    by two, capped at that structure's normal yield.
  * Purify costs 1 grain and reduces the stage by one. It protects the hex
    against new spread/corruption for the configured number of table rounds.
  * Corrupt costs 1 wool and increases the stage by one (maximum stage 2); it
    is unavailable in environmental mode.
  * Environmental and faction modes seed two visible blight hotspots by default
    (pass ``initial_cultists=0`` to disable); tactical mode keeps the legacy clean
    start. New-mode hotspots are marked through fog without exposing terrain,
    number token, or resource.
  * In faction mode, Cult-aligned buildings are immune to Blight's production
    penalty and earn Tears on production rolls at infected sectors. Manual
    corruption cannot target a sector containing the acting player's own
    building; corruption targets must be visible, not connected to the player's
    infrastructure. An Order cleanse grants a three-table-round blessing to
    adjacent buildings belonging to the cleanser. Tears and Order cleanses have
    separate 3-step tracks, each capped at 3 bonus VP; a correct accusation can
    add one more VP, for a theoretical total of 7 from these rewards.
  * Hidden mode conceals opponents' dynamic alignment and progress counters
    until the Cult is revealed. An Order-aligned player may accuse once per game:
    a correct accusation reveals the target, removes up to two Tears and grants
    1 VP; a wrong accusation costs one random resource. Alignment remains
    dynamic rather than a fixed hidden role.
  * Under fog, the public log never prints the identity of a hidden target.

TradeRoutesMod
  * An eligible trade route is a simple path of 2..max_len player-owned roads
    connecting two of the player's buildings, without crossing an opponent's
    building.
  * At activation, the player chooses one commodity visible along the path.
    While the route is not suppressed, that commodity can be bank-traded at
    3:1 (or better if the player already has a port/pact).
  * Once the configured delay has passed, the route also supplies one unit of
    its commodity on an owner's turn. By default the player receives at most
    one route-income unit per own turn, even if several routes are active.
  * A route can only be activated once. The route limit counts unique routes
    registered during the game; suppression is temporary and does not erase it.
  * Moving the raider onto a hex bordering one or more path edges temporarily
    suppresses those routes for one table round.

Both mods expose namespaced AI gene specifications and an optional
``score_ai_action`` method. A generic AI adapter can consume these without
adding mod-specific branches to ai_brain.py. The adapter/trainer integration
is a separate layer; merely installing the mod does not train these genes.
"""

# ── ВЕРСИЯ ДВИЖКА ────────────────────────────────────────────────────────
# ЭТОТ ФАЙЛ СИНХРОНИЗИРУЕТСЯ ИЗ МАСТЕР-КОПИИ:
#   Мастер-копия: корень проекта (синхронизируется в интеграции платформы)
# Мастер-копия — ВСЕГДА новейшая версия. Не редактируйте этот файл напрямую!
# Дата последней синхронизации: см. git log.
# ─────────────────────────────────────────────────────────────────────────


from __future__ import annotations

import hashlib
import math
import random
import weakref
from collections import defaultdict, deque
from typing import Any, Optional

try:  # package import for Wagtail; top-level import for the local server
    from .isles_core import (
        RESOURCE_CHOICES, RESOURCE_NAMES_RU, Action, ActionResult, GamePhase,
        HexGame, Resource, StructureType, TerrainType,
    )
except ImportError:  # pragma: no cover - used by the local standalone server
    from isles_core import (
        RESOURCE_CHOICES, RESOURCE_NAMES_RU, Action, ActionResult, GamePhase,
        HexGame, Resource, StructureType, TerrainType,
    )


# Standard dice pips out of 36. Used for strategic scoring and weighted spread.
_DICE_PIPS: dict[int, int] = {
    2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 0,
    8: 5, 9: 4, 10: 3, 11: 2, 12: 1,
}


def _valid_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _resource(value: Any) -> Optional[Resource]:
    try:
        r = value if isinstance(value, Resource) else Resource(value)
    except (TypeError, ValueError):
        return None
    return r if r in RESOURCE_CHOICES else None


def _productive_tile(game: HexGame, hex_id: int) -> bool:
    tile = game.map.hexes.get(hex_id)
    if tile is None:
        return False
    if tile.terrain in (TerrainType.WASTELAND, TerrainType.RUINS):
        return False
    if int(getattr(tile, "number_token", 0) or 0) not in _DICE_PIPS:
        return False
    return getattr(tile, "resource", None) in RESOURCE_CHOICES and _DICE_PIPS.get(
        int(getattr(tile, "number_token", 0) or 0), 0
    ) > 0


def _blight_stage_sources(tile: Any, stage: int) -> list[Optional[int]]:
    """Return one attacker attribution per active stage of a blighted hex.

    A manual infection contributes the acting player's id; natural growth
    contributes ``None``.  This prevents a player who paid for stage I from
    receiving Tears for the additional stage later added by natural spread.
    The fallback supports older snapshots, which only stored the most recent
    manual attacker rather than per-stage sources.
    """
    stage = max(0, int(stage))
    raw = tile.ext.get("blight_stage_sources")
    if isinstance(raw, list):
        sources: list[Optional[int]] = [
            value if _valid_int(value) else None for value in raw[:stage]
        ]
    else:
        sources = []
    if len(sources) < stage:
        if not sources and stage:
            last = tile.ext.get("blight_last_corrupt_by")
            if _valid_int(last):
                sources = [None] * (stage - 1) + [last]
            else:
                sources = [None] * stage
        else:
            sources.extend([None] * (stage - len(sources)))
    return sources[:stage]


def _plural_tears(n: int) -> str:
    """Russian plural for «слеза» counts shown in feedback."""
    n = abs(int(n))
    last, tens = n % 10, n % 100
    if tens in (11, 12, 13, 14) or last in (0, 5, 6, 7, 8, 9):
        return "слёз"
    return "слезу" if last == 1 else "слезы"


def _telemetry(game: HexGame, mod_id: str) -> dict[str, Any]:
    """Create/retrieve JSON-serializable telemetry owned by the mod."""
    root = game.ext.setdefault("mod_telemetry", {})
    return root.setdefault(
        mod_id,
        {
            "counters": {},
            "by_player": {},
            "events": [],
        },
    )


def _inc(
    game: HexGame,
    mod_id: str,
    counter: str,
    amount: int = 1,
    player_id: Optional[int] = None,
) -> None:
    if amount == 0:
        return
    data = _telemetry(game, mod_id)
    counters = data["counters"]
    counters[counter] = int(counters.get(counter, 0)) + int(amount)
    if player_id is not None:
        key = str(player_id)
        by_player = data["by_player"].setdefault(key, {})
        by_player[counter] = int(by_player.get(counter, 0)) + int(amount)


def _append_event(
    game: HexGame, mod_id: str, event: dict[str, Any], max_events: int = 500
) -> None:
    events = _telemetry(game, mod_id)["events"]
    events.append(event)
    if len(events) > max_events:
        del events[:-max_events]


def _context_gene(context: dict[str, Any], name: str, default: float = 1.0) -> float:
    genes = context.get("genes", {})
    value = genes.get(name, default) if isinstance(genes, dict) else default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _visible_to(game: HexGame, player_id: int, hex_id: int) -> bool:
    return not game.fog_of_war or hex_id in game.players[player_id].revealed_hexes


def _structure_yield(structure_type: Any) -> int:
    return 2 if structure_type == StructureType.METROPOLIS else 1


def _current_route_rate_without_mod(
    game: HexGame, player_id: int, give_resource: Resource
) -> int:
    """Reconstruct the built-in bank rate without any route-modifier hooks."""
    rate = 4
    for vertex in game.map.vertices.values():
        if vertex.structure_owner != player_id or not vertex.is_port:
            continue
        if vertex.port_type == Resource.ANY:
            rate = min(rate, 3)
        elif vertex.port_type == give_resource:
            rate = min(rate, 2)
    player = game.players[player_id]
    if getattr(player, "trade_pact_turn", -1) == game.global_turn:
        rate = min(rate, 2)
    return rate


class BlightMod:
    """A tunable, deterministic-under-seed blight/cleansing module.

    ``cultists`` is retained as a compatibility alias and means the number of
    productive hexes seeded at stage 1 at game start. ``initial_cultists`` is
    the clearer keyword and overrides it when supplied. To preserve old games,
    ``tactical`` defaults to a clean start; the ``environmental`` and ``factions``
    rulesets seed two hotspots unless the caller explicitly supplies a count.
    """

    mod_id = "blight"
    MODES = {"environmental", "tactical", "factions"}
    VP_STEP = 3
    VP_CAP = 3
    MOD_AI_GENE_SPECS: dict[str, dict[str, float]] = {
        "blight.purify_bias": {"default": 1.0, "min": 0.25, "max": 2.5, "sigma": 0.10},
        "blight.corrupt_bias": {"default": 1.0, "min": 0.25, "max": 2.5, "sigma": 0.10},
        "blight.anti_leader_bias": {"default": 1.0, "min": 0.0, "max": 2.0, "sigma": 0.10},
    }

    def __init__(
        self,
        cultists: int = 0,
        purified_shield_rounds: int = 1,
        *,
        initial_cultists: Optional[int] = None,
        spread_chance: float = 0.40,
        max_stage: int = 2,
        clean_hex_weight: float = 1.0,
        infected_hex_weight: float = 0.35,
        purify_resource: Any = "grain",
        mode: str = "tactical",
        vp_step: int = VP_STEP,
        vp_cap: int = VP_CAP,
        cult_visibility: str = "open",
        tears_prealignment: bool = False,
        blessing_per_building: bool = True,
        tears_vp_step: Optional[int] = None,
        purify_vp_step: Optional[int] = None,
        cult_corrupt_tear: bool = False,
        cult_suppression_theft: bool = False,
        ai_corrupt_aggression: float = 1.0,
        ai_hidden_suspicion: bool = True,
        accusation_enabled: bool = True,
        open_alignment_aware: bool = True,
        shared_vp_cap: Optional[int] = None,
    ):
        explicit_seed_count = initial_cultists is not None
        if initial_cultists is not None:
            cultists = initial_cultists
        if not _valid_int(cultists) or cultists < 0:
            raise ValueError("cultists/initial_cultists must be a non-negative integer")
        if not _valid_int(purified_shield_rounds) or purified_shield_rounds < 1:
            raise ValueError("purified_shield_rounds must be a positive integer")
        if isinstance(spread_chance, bool) or not isinstance(spread_chance, (int, float)):
            raise ValueError("spread_chance must be numeric")
        if not math.isfinite(float(spread_chance)) or not 0.0 <= float(spread_chance) <= 1.0:
            raise ValueError("spread_chance must be in [0, 1]")
        if max_stage != 2:
            raise ValueError("This ruleset currently supports exactly two blight stages")
        mode = str(mode).strip().lower()
        if mode in {"nature", "eco"}:
            mode = "environmental"
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {', '.join(sorted(self.MODES))}")
        # New rulesets need visible pressure from the start, otherwise their
        # gameplay can look indistinguishable from legacy tactical blight for
        # many turns. Keep tactical mode backward-compatible; explicit
        # initial_cultists=0 remains available for a clean experimental map.
        if not explicit_seed_count and mode in {"environmental", "factions"} and cultists == 0:
            cultists = 2
        if not _valid_int(vp_step) or vp_step < 1:
            raise ValueError("vp_step must be a positive integer")
        if not _valid_int(vp_cap) or vp_cap < 0:
            raise ValueError("vp_cap must be a non-negative integer")
        # Faction-specific milestone pacing: tears (Cult) and cleanses (Order)
        # may progress at different speeds. Both default to the shared vp_step.
        if tears_vp_step is None:
            tears_vp_step = vp_step
        if purify_vp_step is None:
            purify_vp_step = vp_step
        if not _valid_int(tears_vp_step) or tears_vp_step < 1:
            raise ValueError("tears_vp_step must be a positive integer")
        if not _valid_int(purify_vp_step) or purify_vp_step < 1:
            raise ValueError("purify_vp_step must be a positive integer")
        if (not math.isfinite(float(clean_hex_weight)) or not math.isfinite(float(infected_hex_weight))
                or clean_hex_weight <= 0 or infected_hex_weight < 0):
            raise ValueError("spread weights must be finite and positive/non-negative")
        if not isinstance(tears_prealignment, bool):
            raise ValueError("tears_prealignment must be a boolean")
        if not isinstance(blessing_per_building, bool):
            raise ValueError("blessing_per_building must be a boolean")
        if not isinstance(cult_corrupt_tear, bool):
            raise ValueError("cult_corrupt_tear must be a boolean")
        if not isinstance(cult_suppression_theft, bool):
            raise ValueError("cult_suppression_theft must be a boolean")
        if (isinstance(ai_corrupt_aggression, bool) or not isinstance(ai_corrupt_aggression, (int, float))
                or not math.isfinite(float(ai_corrupt_aggression)) or ai_corrupt_aggression <= 0):
            raise ValueError("ai_corrupt_aggression must be a positive finite number")
        if not isinstance(ai_hidden_suspicion, bool):
            raise ValueError("ai_hidden_suspicion must be a boolean")
        if not isinstance(accusation_enabled, bool):
            raise ValueError("accusation_enabled must be a boolean")
        if not isinstance(open_alignment_aware, bool):
            raise ValueError("open_alignment_aware must be a boolean")
        if shared_vp_cap is not None and (isinstance(shared_vp_cap, bool) or not _valid_int(shared_vp_cap) or shared_vp_cap < 0):
            raise ValueError("shared_vp_cap must be a non-negative integer or None")

        self.initial_cultists = int(cultists)
        self.purified_shield_rounds = int(purified_shield_rounds)
        self.spread_chance = float(spread_chance)
        self.max_stage = 2
        self.clean_hex_weight = float(clean_hex_weight)
        self.infected_hex_weight = float(infected_hex_weight)
        self.mode = mode
        cult_visibility = str(cult_visibility).strip().lower()
        if cult_visibility not in {"open", "hidden"}:
            raise ValueError("cult_visibility must be 'open' or 'hidden'")
        self.cult_visibility = cult_visibility
        self.vp_step = int(vp_step)
        self.vp_cap = int(vp_cap)
        # Balance-tuning knobs for the factions ruleset, calibrated on paired
        # 5000-game bot simulations with identical seeds per arm
        # (tools/sim_blight_variants.py). The single shipped balance change is
        # in the AI scoring: corrupt_hex no longer values strikes that only
        # feed a Cult-aligned victim (they are immune and even earn Tears).
        # With that fix the original rules measure near even (pairwise final-VP
        # diff −0.05, per-player winrates 24.7% vs 27.6%). The optional
        # cult_suppression_theft — the Cult pockets the units its corruption
        # suppresses — overcorrects once the feeding hole is closed (pairwise
        # −0.78, cult 52% of wins) and therefore defaults to False; it stays
        # available as a house rule. Other knobs: tears_prealignment banks
        # tears before joining the Cult, blessing_per_building=False pays a
        # flat +1 blessing per roll, cult_corrupt_tear pays one tear per
        # corrupt, and ai_corrupt_aggression is a TEMPORARY manual lever for
        # quick experiments — keep it at 1.0 while training bot genes so the
        # trained corrupt_bias stays interpretable on its own (it never
        # changes the rules, only the generic AI's valuation of corrupt_hex).
        self.tears_prealignment = bool(tears_prealignment)
        self.blessing_per_building = bool(blessing_per_building)
        self.tears_vp_step = int(tears_vp_step)
        self.purify_vp_step = int(purify_vp_step)
        self.cult_corrupt_tear = bool(cult_corrupt_tear)
        self.cult_suppression_theft = bool(cult_suppression_theft)
        self.ai_corrupt_aggression = float(ai_corrupt_aggression)
        # DIAGNOSTIC-ONLY switches for the hidden-mode ablation experiment
        # (docs/blight_hidden_diagnosis.md). Defaults equal the shipped rules:
        # the suspicion heuristic is part of the AI scoring, and the one-shot
        # accusation is part of hidden factions. Flipping either is an
        # experimental ablation, never a rule change.
        self.ai_hidden_suspicion = bool(ai_hidden_suspicion)
        self.accusation_enabled = bool(accusation_enabled)
        # DIAGNOSTIC-ONLY switch for the open-vs-hidden awareness experiment
        # (O0/O1): when False, the open-mode corrupt scoring stops using the
        # target's REAL alignment (no skip of Cult-owned sectors, no feeding
        # penalty). This ablates exactly the "don't feed the Cult" knowledge
        # that hidden bots lack; rules, costs and rewards are untouched, and
        # the shipped default is True.
        self.open_alignment_aware = bool(open_alignment_aware)
        # DIAGNOSTIC shared cap across both faction tracks (proposal in
        # docs/blight_shared_cap_proposal.md): alternate_vp = tears_vp +
        # purify_vp may not exceed it. None = off (shipped rules); individual
        # per-track caps stay at vp_cap; accusation_vp is outside the limit.
        # Milestones after the cap keep being tracked and counted in
        # milestones_after_cap, but stop minting VP.
        self.shared_vp_cap = None if shared_vp_cap is None else int(shared_vp_cap)
        # Standard 1-card cleanse payment (default grain = shipped rules). With no
        # such card a player may pay 2x of another resource (deadlock escape).
        try:
            self.purify_resource = (
                purify_resource if isinstance(purify_resource, Resource) else Resource(str(purify_resource))
            )
        except ValueError:
            raise ValueError(f"unknown purify_resource: {purify_resource!r}")
        self._emergency_resources = tuple(
            r for r in (Resource.WOOD, Resource.CLAY, Resource.WOOL, Resource.ORE, Resource.GRAIN)
            if r != self.purify_resource
        )
        self._access_cache: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._suspicion_cache: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    def install(self, game: HexGame) -> None:
        game.on("game_started", self.on_game_started)
        game.on("modify_resource_gains", self.modify_resource_gains)
        game.on("after_dice_rolled", self.on_after_dice_rolled)
        game.on("after_resource_distribution", self.on_after_resource_distribution)
        game.on("modify_observation", self.modify_observation)
        game.on("modify_victory_points", self.modify_victory_points)
        game.register_action("purify_hex", self.handle_purify_hex)
        game.register_action("corrupt_hex", self.handle_corrupt_hex)
        game.register_action("accuse_cultist", self.handle_accuse_cultist)
        game.register_enumerator(self.enumerate_actions)

    def ai_gene_specs(self) -> dict[str, dict[str, float]]:
        """Namespaced, trainer-discoverable genes for this mod's AI behaviour."""
        return {k: dict(v) for k, v in self.MOD_AI_GENE_SPECS.items()}

    def on_game_started(self, game: HexGame) -> None:
        state = game.ext.setdefault("blight", {})
        state.clear()
        state.update(
            {
                "schema_version": 1,
                "spread_chance": self.spread_chance,
                "initial_cultists": self.initial_cultists,
                "mode": self.mode,
                "cult_visibility": self.cult_visibility,
                "vp_step": self.vp_step,
                "tears_vp_step": self.tears_vp_step,
                "purify_vp_step": self.purify_vp_step,
                "vp_cap": self.vp_cap,
                "players": {
                    str(player.player_id): {
                        "alignment_score": 0,
                        "alignment": "neutral",
                        "tears": 0,
                        "purified_count": 0,
                        "corrupt_count": 0,
                        "alternate_vp": 0,
                        "tears_vp": 0,
                        "purify_vp": 0,
                        "tears_steps_seen": 0,
                        "purify_steps_seen": 0,
                        # Observability: tears booked by source (never
                        # reconstructed post-hoc) + personal feedback queue.
                        "tears_from_immunity": 0,
                        "tears_from_suppression": 0,
                        "tears_from_direct": 0,
                        "feedback": [],
                        "accusation_used": False,
                        "cult_revealed": False,
                        "accusation_vp": 0,
                    }
                    for player in game.players
                },
                "pending_victory_checks": [],
            }
        )
        for tile in game.map.hexes.values():
            tile.ext.pop("blight_stage", None)
            tile.ext.pop("shield_until", None)
            tile.ext.pop("blight_last_purify_turn", None)
            tile.ext.pop("blight_last_purify_by", None)
            tile.ext.pop("blight_last_corrupt_turn", None)
            tile.ext.pop("blight_last_corrupt_by", None)
            tile.ext.pop("blight_stage_sources", None)

        candidates = [hid for hid in sorted(game.map.hexes) if _productive_tile(game, hid)]
        seed_count = min(self.initial_cultists, len(candidates))
        if seed_count:
            rng = self._local_rng(game)
            weighted = [(hid, self._spread_weight(game, hid, stage=0)) for hid in candidates]
            chosen: list[int] = []
            pool = list(weighted)
            for _ in range(seed_count):
                pick = self._weighted_pick(game, pool, rng=rng)
                if pick is None:
                    break
                chosen.append(pick)
                pool = [(hid, w) for hid, w in pool if hid != pick]
            self._save_local_rng(game, rng)
            for hid in chosen:
                game.map.hexes[hid].ext["blight_stage"] = 1
                game.map.hexes[hid].ext["blight_stage_sources"] = [None]
                _inc(game, self.mod_id, "initial_infestations")

    @staticmethod
    def _alignment_for_score(score: int) -> str:
        """Return the faction reached by the score, with a two-step threshold."""
        if score >= 2:
            return "cult"
        if score <= -2:
            return "order"
        return "neutral"

    @classmethod
    def _alignment_after_change(cls, score: int, previous: str) -> str:
        """Use a small hysteresis band so one action does not flip a faction.

        A player becomes Cult at +2 or Order at -2. Once aligned, they keep that
        faction until their score returns to zero; only then do they become
        neutral. Crossing to the opposite faction therefore takes deliberate,
        repeated actions rather than one accidental move.
        """
        reached = cls._alignment_for_score(score)
        if reached != "neutral":
            return reached
        if previous == "cult" and score > 0:
            return "cult"
        if previous == "order" and score < 0:
            return "order"
        return "neutral"

    def _player_blight_state(self, game: HexGame, player_id: int) -> dict[str, Any]:
        state = game.ext.setdefault("blight", {})
        players = state.setdefault("players", {})
        key = str(player_id)
        result = players.get(key)
        if not isinstance(result, dict):
            result = {
                "alignment_score": 0,
                "alignment": "neutral",
                "tears": 0,
                "purified_count": 0,
                "corrupt_count": 0,
                "alternate_vp": 0,
                "tears_vp": 0,
                "purify_vp": 0,
                "tears_steps_seen": 0,
                "purify_steps_seen": 0,
                "tears_from_immunity": 0,
                "tears_from_suppression": 0,
                "tears_from_direct": 0,
                "feedback": [],
                "accusation_used": False,
                "cult_revealed": False,
                "accusation_vp": 0,
            }
            players[key] = result
        return result

    def _change_alignment(self, game: HexGame, player_id: int, delta: int) -> str:
        """Move the alignment track and log only a genuine faction transition."""
        if self.mode != "factions":
            return "neutral"
        data = self._player_blight_state(game, player_id)
        old_alignment = str(data.get("alignment", "neutral"))
        score = int(data.get("alignment_score", 0)) + int(delta)
        new_alignment = self._alignment_after_change(score, old_alignment)
        data["alignment_score"] = score
        data["alignment"] = new_alignment
        if new_alignment != old_alignment:
            _inc(game, self.mod_id, "alignment_changes", player_id=player_id)
        if new_alignment != old_alignment and not (self.mode == "factions" and self.cult_visibility == "hidden"):
            labels = {
                "cult": "Культ Скверны",
                "order": "Орден очищения",
                "neutral": "нейтралитет",
            }
            game._add_log(
                f"☯ {game.players[player_id].name}: {labels[old_alignment]} → "
                f"{labels[new_alignment]} (баланс {score:+d})",
                player_id,
            )
        return new_alignment

    # ------------------------------------------------------------------
    # Personal feedback queue (treated as telemetry/UX, never as rules).
    #
    # Each entry {id, turn, kind, text} is stored per player and exposed ONLY
    # in that player's own observation, so hidden-mode information (who holds
    # which faction, who corrupted what) never reaches other clients. Public
    # journal lines are added only in the open mode, mirroring the existing
    # theft announcement.
    # ------------------------------------------------------------------
    def _feedback(self, game: HexGame, player_id: int, kind: str, text: str) -> None:
        data = self._player_blight_state(game, player_id)
        queue = data.setdefault("feedback", [])
        state = game.ext.setdefault("blight", {})
        seq = int(state.get("feedback_seq", 0)) + 1
        state["feedback_seq"] = seq
        queue.append({"id": seq, "turn": int(game.global_turn), "kind": kind, "text": text})
        del queue[:-20]

    def _tears_tail(self, game: HexGame, player_id: int) -> str:
        """Progress phrase after a tears award: steps to next VP or the cap."""
        data = self._player_blight_state(game, player_id)
        if int(data.get("tears_vp", 0)) >= self.vp_cap:
            return (f"предел +{self.vp_cap} ОП достигнут — очки за слёзы больше "
                    f"не начисляются (слёзы продолжают учитываться)")
        left = self.tears_vp_step - (int(data.get("tears", 0)) % self.tears_vp_step)
        return f"до +1 ОП осталось {left} слёз"

    def _feedback_tears(self, game: HexGame, player_id: int, kind: str, amount: int) -> None:
        tail = self._tears_tail(game, player_id)
        word = _plural_tears(amount)
        if kind == "immunity":
            text = f"☠ Заражённые сектора дали вам {amount} {word}; {tail}."
        elif kind == "suppression":
            text = f"🌑 Скверна подавила добычу соперника — вам {amount} {word}; {tail}."
        elif kind == "direct":
            text = f"🌑 Осквернение принесло вам {amount} {word}; {tail}."
        else:
            text = f"Вам начислено {amount} {word}; {tail}."
        self._feedback(game, player_id, kind, text)

    def _feedback_suppression(self, game: HexGame, player_id: int, victim_id: int,
                              resource: Any, suppressed: int) -> None:
        """Tell the attributed attacker about real damage their stage caused.

        Personal queue always; the public journal line is open-mode only — in
        hidden mode it would reveal the attacker's Cult alignment.
        """
        self._feedback_tears(game, player_id, kind="suppression", amount=suppressed)
        if self.mode == "factions" and self.cult_visibility == "hidden":
            return
        name = RESOURCE_NAMES_RU.get(resource, getattr(resource, "value", resource))
        tail = self._tears_tail(game, player_id)
        game._add_log(
            f"😈 Скверна подавила {suppressed}×{name} у {self._log_victim(game, victim_id)} — "
            f"{self._log_actor(game, player_id)} получает {suppressed} {_plural_tears(suppressed)} ({tail})",
            self._log_author(game, player_id),
        )

    def _award_progress_vp(self, game: HexGame, player_id: int, source: str) -> int:
        """Award separate, capped Cult-tear and Order-cleansing milestones."""
        if self.mode != "factions":
            return 0
        data = self._player_blight_state(game, player_id)
        if source == "tears":
            total = max(0, int(data.get("tears", 0)))
            seen_key, vp_key = "tears_steps_seen", "tears_vp"
            step = self.tears_vp_step
        elif source == "purify":
            total = max(0, int(data.get("purified_count", 0)))
            seen_key, vp_key = "purify_steps_seen", "purify_vp"
            step = self.purify_vp_step
        else:
            return 0

        earned_steps = total // step
        seen = max(0, int(data.get(seen_key, 0)))
        gained = 0
        for milestone in range(seen + 1, earned_steps + 1):
            data[seen_key] = milestone
            # Cult and Order have independent tracks/caps: one faction cannot
            # consume the other faction's reward budget by switching alignment.
            if int(data.get(vp_key, 0)) >= self.vp_cap:
                continue
            # DIAGNOSTIC shared cap: alternate_vp (tears_vp + purify_vp) may be
            # limited across BOTH tracks, making the second track worth at
            # most (shared_vp_cap - 3) extra. Milestones keep being tracked
            # (steps_seen grows) but stop minting VP; nothing is retroactively
            # stripped. accusation_vp is outside this limit. Counter
            # milestones_after_cap shows how often the cap actually binds.
            if self.shared_vp_cap is not None and int(data.get("alternate_vp", 0)) >= self.shared_vp_cap:
                _inc(game, self.mod_id, "milestones_after_cap", player_id=player_id)
                continue
            data["alternate_vp"] = int(data.get("alternate_vp", 0)) + 1
            data[vp_key] = int(data.get(vp_key, 0)) + 1
            gained += 1
            if self.mode == "factions" and self.cult_visibility == "hidden" and source == "tears":
                message = f"🏅 {game.players[player_id].name} получает +1 бонусное очко за прогресс Скверны (предел {self.vp_cap})"
            else:
                label = "слёз Скверны" if source == "tears" else "очищения"
                message = (f"🏅 {game.players[player_id].name} получает +1 бонусное очко: {label} "
                           f"({total}/{step}, предел {self.vp_cap})")
            game._add_log(message, player_id)
        data[seen_key] = max(int(data.get(seen_key, 0)), earned_steps)
        if gained:
            pending = game.ext.setdefault("blight", {}).setdefault("pending_victory_checks", [])
            if player_id not in pending:
                pending.append(player_id)
        return gained

    def modify_victory_points(self, game: HexGame, points: int, player_id: int, public: bool) -> int:
        """Feed capped faction rewards into the engine's ordinary VP accounting."""
        if self.mode != "factions":
            return points
        data = self._player_blight_state(game, player_id)
        return (int(points)
                + max(0, int(data.get("alternate_vp", 0)))
                + max(0, int(data.get("accusation_vp", 0))))

    def modify_observation(self, game: HexGame, observation: dict[str, Any], player_id: int) -> dict[str, Any]:
        """Expose faction progress and hotspot signals without hidden sector details."""
        rules = observation.setdefault("rules", {})
        rules["blight"] = True
        rules["blight_mode"] = self.mode
        rules["blight_spread_chance"] = self.spread_chance
        rules["cult_visibility"] = self.cult_visibility
        if self.mode in {"environmental", "factions"}:
            # A blight hotspot remains visible as a warning under fog. The
            # frontend receives only a boolean signal for hidden hexes, never
            # hidden terrain, number token, resource or infestation stage.
            for hex_view in observation.get("hexes", []):
                try:
                    hid = int(hex_view["hex_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                tile = game.map.hexes.get(hid)
                if tile is not None and int(tile.ext.get("blight_stage", 0)) > 0:
                    hex_view["blight_detected"] = True
        if self.mode != "factions":
            return observation

        for item in observation.get("players", []):
            try:
                pid = int(item["player_id"])
            except (KeyError, TypeError, ValueError):
                continue
            data = self._player_blight_state(game, pid)
            hidden = (self.cult_visibility == "hidden" and pid != player_id
                      and not bool(data.get("cult_revealed", False)))
            item["blight"] = {
                "alignment": "hidden" if hidden else data["alignment"],
                "alignment_score": None if hidden else int(data["alignment_score"]),
                "tears": None if hidden else int(data["tears"]),
                "purified_count": None if hidden else int(data["purified_count"]),
                # Faction progress for the player cards. In open mode faction
                # accounting is public anyway; in hidden mode the None-ed tears
                # above already conceal these numbers, so they stay absent.
                "tears_vp": None if hidden else int(data["tears_vp"]),
                "tears_vp_step": None if hidden else int(self.tears_vp_step),
                "purify_vp": None if hidden else int(data["purify_vp"]),
                "purify_vp_step": None if hidden else int(self.purify_vp_step),
                "tears_from_immunity": None if hidden else int(data.get("tears_from_immunity", 0)),
                "tears_from_suppression": None if hidden else int(data.get("tears_from_suppression", 0)),
                "tears_from_direct": None if hidden else int(data.get("tears_from_direct", 0)),
                "bonus_vp": None if hidden else int(data["alternate_vp"]) + int(data.get("accusation_vp", 0)),
                "cult_revealed": bool(data.get("cult_revealed", False)),
                "vp_step": self.vp_step,
                "vp_cap": self.vp_cap,
            }
        me = observation.get("me")
        if isinstance(me, dict):
            mine = self._player_blight_state(game, player_id)
            me["blight"] = {
                "alignment": mine["alignment"],
                "alignment_score": int(mine["alignment_score"]),
                "tears": int(mine["tears"]),
                "purified_count": int(mine["purified_count"]),
                "bonus_vp": int(mine["alternate_vp"]) + int(mine.get("accusation_vp", 0)),
                "tears_vp": int(mine["tears_vp"]),
                "purify_vp": int(mine["purify_vp"]),
                "tears_from_immunity": int(mine.get("tears_from_immunity", 0)),
                "tears_from_suppression": int(mine.get("tears_from_suppression", 0)),
                "tears_from_direct": int(mine.get("tears_from_direct", 0)),
                "feedback": list(mine.get("feedback", []))[-10:],
                "accusation_used": bool(mine.get("accusation_used", False)),
                "cult_revealed": bool(mine.get("cult_revealed", False)),
                "accusation_vp": int(mine.get("accusation_vp", 0)),
                "cult_visibility": self.cult_visibility,
                "vp_step": self.vp_step,
                "tears_vp_step": self.tears_vp_step,
                "purify_vp_step": self.purify_vp_step,
                "vp_cap": self.vp_cap,
                "shared_vp_cap": self.shared_vp_cap,
                "alternate_vp": int(mine.get("alternate_vp", 0)),
            }
        return observation

    def on_after_resource_distribution(self, game: HexGame, roller_player_id: int, rolled_number: int) -> None:
        """Resolve bonus-VP victory checks only after the engine finished production."""
        # Diagnostic timeline: every 10 global turns record the map's blight
        # load and the cumulative suppressed production, so the blight's
        # dynamics are measurable instead of only its end state.
        telemetry = _telemetry(game, self.mod_id)
        snapshots = telemetry.setdefault("blight_timeline", [])
        if not snapshots or int(game.global_turn) - int(snapshots[-1]["turn"]) >= 10:
            stages = {0: 0, 1: 0, 2: 0}
            for tile in game.map.hexes.values():
                if not _productive_tile(game, tile.hex_id):
                    continue
                stages[min(2, int(tile.ext.get("blight_stage", 0)))] += 1
            counters = telemetry.setdefault("counters", {})
            snapshots.append({
                "turn": int(game.global_turn),
                "productive_stage0": stages[0],
                "productive_stage1": stages[1],
                "productive_stage2": stages[2],
                "suppressed_total": int(counters.get("resource_units_suppressed", 0)),
                "purify_actions": int(counters.get("purify_actions", 0)),
                "corrupt_actions": int(counters.get("corrupt_actions", 0)),
            })
        state = game.ext.setdefault("blight", {})
        pending = list(dict.fromkeys(state.get("pending_victory_checks", [])))
        state["pending_victory_checks"] = []
        for pid in pending:
            if game.phase == GamePhase.FINISHED:
                break
            if _valid_int(pid) and 0 <= pid < len(game.players):
                game._check_victory(pid)

    def _accessible_hexes(self, game: HexGame, player_id: int) -> frozenset[int]:
        cache = self._access_cache.get(game)
        if cache is not None and cache[0] == game.state_version:
            cached = cache[1].get(player_id)
            if cached is not None:
                return cached
        else:
            cache = (game.state_version, {})
            self._access_cache[game] = cache

        out: set[int] = set()
        for vertex in game.map.vertices.values():
            if vertex.structure_owner == player_id:
                out.update(vertex.adjacent_hexes)
        for edge in game.map.edges.values():
            if edge.route_owner == player_id:
                out.update(edge.adjacent_hexes)
        if game.fog_of_war:
            out.intersection_update(game.players[player_id].revealed_hexes)
        result = frozenset(out)
        cache[1][player_id] = result
        return result

    def _spread_weight(self, game: HexGame, hex_id: int, stage: int) -> float:
        tile = game.map.hexes[hex_id]
        pips = _DICE_PIPS.get(int(tile.number_token), 0)
        buildings = 0.0
        for vid in tile.vertex_ids:
            vertex = game.map.vertices[vid]
            if vertex.structure_owner is not None:
                buildings += _structure_yield(vertex.structure_type) * 0.75
        # Productive sites are more strategically meaningful targets. Slightly
        # prefer clean sites to avoid concentrating stage 2 too early.
        stage_weight = self.clean_hex_weight if stage <= 0 else self.infected_hex_weight
        return max(0.05, (0.6 + pips) * (1.0 + buildings) * stage_weight)

    def _spread_candidates(self, game: HexGame) -> list[tuple[int, float]]:
        """Return eligible spread targets; new modes spread along a local frontier.

        The legacy tactical mode keeps its original map-wide weighted targeting
        so existing players can reproduce the shipped rules. Environmental and
        faction modes spread from WASTELAND/RUINS sources and already infected
        productive hexes to productive neighbours only.
        """
        eligible: dict[int, float] = {}
        frontier: Optional[set[int]] = None
        if self.mode in {"environmental", "factions"}:
            sources = {
                hid
                for hid, tile in game.map.hexes.items()
                if tile.terrain in (TerrainType.WASTELAND, TerrainType.RUINS)
                or int(tile.ext.get("blight_stage", 0)) > 0
            }
            frontier = set()
            for edge in game.map.edges.values():
                adjacent = set(edge.adjacent_hexes)
                if len(adjacent) == 2 and adjacent.intersection(sources):
                    frontier.update(adjacent - sources)

        for hid, tile in sorted(game.map.hexes.items()):
            if frontier is not None and hid not in frontier:
                continue
            if not _productive_tile(game, hid):
                continue
            stage = int(tile.ext.get("blight_stage", 0))
            shield_until = int(tile.ext.get("shield_until", -1))
            if stage >= self.max_stage or shield_until > game.global_turn:
                continue
            eligible[hid] = self._spread_weight(game, hid, stage)
        return list(eligible.items())

    def _local_rng(self, game: HexGame, *, stream: Optional[str] = None) -> random.Random:
        """Get a deterministic mod RNG stream without consuming engine RNG.

        The default stream (``blight``) is retained for initial infestation and
        target selection. The ``spread_check`` stream is independent, so a
        successful target draw cannot shift future probability checks. Each
        stream state lives in game.ext, making snapshots/replays deterministic.
        """
        states = game.ext.setdefault("plugin_rng_states", {})
        state_key = self.mod_id if stream is None else f"{self.mod_id}:{stream}"
        state = states.get(state_key)
        rng = random.Random()
        if isinstance(state, dict) and "internal" in state:
            rng.setstate((int(state["version"]), tuple(int(x) for x in state["internal"]), state.get("gauss")))
        else:
            # Preserve the established seed for the original target stream.
            # New named streams use domain-separated seeds.
            if stream is None:
                seed_text = f"HexIsles:{self.mod_id}:{game.seed}:{game.room_id}"
            else:
                seed_text = f"HexIsles:{self.mod_id}:{stream}:{game.seed}:{game.room_id}"
            seed = int.from_bytes(hashlib.sha256(seed_text.encode("utf-8")).digest()[:8], "big")
            rng.seed(seed)
        return rng

    def _save_local_rng(self, game: HexGame, rng: random.Random, *, stream: Optional[str] = None) -> None:
        version, internal, gauss = rng.getstate()
        states = game.ext.setdefault("plugin_rng_states", {})
        state_key = self.mod_id if stream is None else f"{self.mod_id}:{stream}"
        states[state_key] = {"version": version, "internal": list(internal), "gauss": gauss}

    @staticmethod
    def _weighted_pick(game: HexGame, items: list[tuple[int, float]], rng: Optional[random.Random] = None) -> Optional[int]:
        rng = rng or game.rng
        if not items:
            return None
        total = sum(max(0.0, weight) for _, weight in items)
        if total <= 0:
            return rng.choice([item for item, _ in items])
        point = rng.random() * total
        acc = 0.0
        for item, weight in items:
            acc += max(0.0, weight)
            if point <= acc:
                return item
        return items[-1][0]

    def on_after_dice_rolled(
        self, game: HexGame, player_id: int, d1: int, d2: int, total: int
    ) -> None:
        if total != 7:
            return
        # Always consume exactly one value from the spread-check stream per 7.
        # Target selection uses a separate stream: successful spreads therefore
        # cannot perturb the random sequence used by later chance checks.
        chance_rng = self._local_rng(game, stream="spread_check")
        should_spread = chance_rng.random() < self.spread_chance
        self._save_local_rng(game, chance_rng, stream="spread_check")
        if not should_spread:
            return
        target_rng = self._local_rng(game)
        candidates = self._spread_candidates(game)
        target = self._weighted_pick(game, candidates, rng=target_rng)
        self._save_local_rng(game, target_rng)
        if target is None:
            return

        tile = game.map.hexes[target]
        previous_stage = min(self.max_stage, int(tile.ext.get("blight_stage", 0)))
        stage = min(self.max_stage, previous_stage + 1)
        sources = _blight_stage_sources(tile, previous_stage)
        if stage > previous_stage:
            # The new stage came from natural spread, not from the player who
            # may have manually infected the tile at an earlier time.
            sources.extend([None] * (stage - previous_stage))
            tile.ext["blight_stage_sources"] = sources
        tile.ext["blight_stage"] = stage
        _inc(game, self.mod_id, "spread_events")
        _inc(game, self.mod_id, "stages_added")
        _append_event(game, self.mod_id, {"turn": game.global_turn, "kind": "spread", "hex_id": target, "stage": stage})
        if game.fog_of_war:
            game._add_log(f"☠️ Скверна поразила неизвестный сектор (Стадия {stage})")
        else:
            game._add_log(f"☠️ Скверна поразила сектор {target} (Стадия {stage})")

    def modify_resource_gains(
        self,
        game: HexGame,
        gains: dict[int, dict[Resource, int]],
        rolled_number: int,
    ) -> dict[int, dict[Resource, int]]:
        """Apply blight losses, Cult immunity/tears, and temporary Order blessings."""
        losses: dict[int, dict[Resource, int]] = defaultdict(lambda: defaultdict(int))
        loss_sources: dict[int, dict[Resource, list[tuple[int, Optional[int]]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        cult_tears: dict[int, int] = defaultdict(int)
        blessed_gains: dict[int, dict[Resource, int]] = defaultdict(lambda: defaultdict(int))

        for hid in game.map.number_to_hexes.get(rolled_number, ()):
            tile = game.map.hexes[hid]
            if tile.has_raider or not _productive_tile(game, hid):
                continue
            stage = min(self.max_stage, int(tile.ext.get("blight_stage", 0)))
            res = tile.resource
            # A Cult-aligned player's own buildings are immune. The stages on
            # their producing sectors instead generate Tears when that number rolls.
            if stage > 0:
                stage_sources = _blight_stage_sources(tile, stage)
                for vid in tile.vertex_ids:
                    vertex = game.map.vertices[vid]
                    pid = vertex.structure_owner
                    if pid is None:
                        continue
                    normal = _structure_yield(vertex.structure_type)
                    if self.mode == "factions" and self._player_blight_state(game, pid).get("alignment") == "cult":
                        cult_tears[pid] += min(normal, stage)
                        continue
                    lost = min(normal, stage)
                    if lost > 0:
                        losses[pid][res] += lost
                        for raw_attacker in stage_sources[:lost]:
                            attacker_id = (
                                int(raw_attacker)
                                if _valid_int(raw_attacker) and 0 <= raw_attacker < len(game.players)
                                else None
                            )
                            loss_sources[pid][res].append((1, attacker_id))

            # A cleansing blessing lasts three full table rounds. It is tied to
            # the cleanser's buildings adjacent to this hex and adds one unit
            # whenever this hex's number produces, even if the hex is now clean.
            blessings = tile.ext.get("blight_order_blessings", [])
            if isinstance(blessings, list):
                live = []
                for blessing in blessings:
                    if not isinstance(blessing, dict):
                        continue
                    try:
                        owner = int(blessing.get("player_id", -1))
                        until = int(blessing.get("until_turn", -1))
                    except (TypeError, ValueError):
                        continue
                    if until < game.global_turn:
                        continue
                    live.append(blessing)
                    if 0 <= owner < len(game.players):
                        if self.blessing_per_building:
                            count = sum(1 for vid in tile.vertex_ids if game.map.vertices[vid].structure_owner == owner)
                            if count:
                                blessed_gains[owner][res] += count
                        elif any(game.map.vertices[vid].structure_owner == owner for vid in tile.vertex_ids):
                            blessed_gains[owner][res] += 1
                tile.ext["blight_order_blessings"] = live

        result: dict[int, dict[Resource, int]] = {pid: dict(current) for pid, current in gains.items()}
        theft_events: dict[tuple[int, int, Resource], int] = {}
        for pid, per_resource in losses.items():
            result.setdefault(pid, {})
            for res, lost in per_resource.items():
                before = int(result[pid].get(res, 0))
                after = max(0, before - int(lost))
                actually_lost = before - after
                if after:
                    result[pid][res] = after
                else:
                    result[pid].pop(res, None)
                if actually_lost:
                    _inc(game, self.mod_id, "resource_units_suppressed", actually_lost, player_id=pid)
                    # Diagnostic split: WHO bears the economic damage, by the
                    # victim's alignment AT THE MOMENT of suppression. Internal
                    # telemetry only — never exposed to clients.
                    victim_alignment = str(self._player_blight_state(game, pid).get("alignment", "neutral"))
                    _inc(game, self.mod_id, f"suppressed_on_{victim_alignment}", actually_lost)
                    # Suppression event with the exact stage-source breakdown:
                    # one record per (victim, resource, roll), losses never
                    # duplicated across the stages that caused them.
                    by_player: dict[int, int] = {}
                    natural = 0
                    leftover = actually_lost
                    for source_loss, attacker_id in loss_sources[pid][res]:
                        if leftover <= 0:
                            break
                        take = min(int(source_loss), leftover)
                        leftover -= take
                        if attacker_id is None or not (_valid_int(attacker_id) and 0 <= attacker_id < len(game.players)) or attacker_id == pid:
                            natural += take
                        else:
                            by_player[int(attacker_id)] = by_player.get(int(attacker_id), 0) + take
                    _append_event(game, self.mod_id, {
                        "turn": int(game.global_turn), "kind": "suppress",
                        "victim_id": int(pid), "resource": getattr(res, "value", str(res)),
                        "amount": int(actually_lost), "by_player": by_player, "natural": natural,
                    })
                    self._feedback(game, pid, "loss",
                                   f"🌑 Скверна отняла {actually_lost}×{RESOURCE_NAMES_RU.get(res, res)}")
                    if self.mode == "factions":
                        remaining = actually_lost
                        for source_loss, attacker_id in loss_sources[pid][res]:
                            if remaining <= 0:
                                break
                            attributed = min(int(source_loss), remaining)
                            remaining -= attributed
                            if attributed <= 0 or attacker_id is None or attacker_id == pid or not 0 <= attacker_id < len(game.players):
                                continue
                            attacker_state = self._player_blight_state(game, attacker_id)
                            attacker_is_cult = str(attacker_state.get("alignment")) == "cult"
                            if not self.tears_prealignment and not attacker_is_cult:
                                continue
                            attacker_state["tears"] = int(attacker_state.get("tears", 0)) + attributed
                            attacker_state["tears_from_suppression"] = int(attacker_state.get("tears_from_suppression", 0)) + attributed
                            _inc(game, self.mod_id, "cult_suppression_tears", attributed, player_id=attacker_id)
                            # Feedback: open mode logs it publicly (alignment is
                            # public there); hidden mode keeps it personal so
                            # the cultist's faction is not revealed.
                            self._feedback_suppression(game, attacker_id, victim_id=pid, resource=res, suppressed=attributed)
                            if attacker_is_cult:
                                # The Cult feeds on the suppressed work: the
                                # corruptor receives the units the victim lost.
                                if self.cult_suppression_theft:
                                    result.setdefault(attacker_id, {})
                                    result[attacker_id][res] = int(result[attacker_id].get(res, 0)) + attributed
                                    _inc(game, self.mod_id, "cult_stolen_units", attributed, player_id=attacker_id)
                                    key = (attacker_id, pid, res)
                                    theft_events[key] = theft_events.get(key, 0) + attributed
                                self._award_progress_vp(game, attacker_id, "tears")

        # Announce every theft of this production roll so the Cult player feels
        # the engine working, instead of discovering a silent total at the end.
        if theft_events:
            # Resource is a plain Enum and cannot be ordered; sort by its value.
            ordered = sorted(
                theft_events.items(),
                key=lambda item: (item[0][0], item[0][1], item[0][2].value),
            )
            for (thief_id, victim_id, resource), amount in ordered:
                game._add_log(
                    f"😈 {self._log_actor(game, thief_id)} высосал {amount}×"
                    f"{RESOURCE_NAMES_RU[resource]} у {self._log_victim(game, victim_id)}",
                    self._log_author(game, thief_id),
                )

        if self.mode == "factions":
            for pid, amount in cult_tears.items():
                if amount <= 0:
                    continue
                data = self._player_blight_state(game, pid)
                data["tears"] = int(data.get("tears", 0)) + amount
                data["tears_from_immunity"] = int(data.get("tears_from_immunity", 0)) + amount
                _inc(game, self.mod_id, "cult_immunity_tears", amount, player_id=pid)
                self._award_progress_vp(game, pid, "tears")
                self._feedback_tears(game, pid, kind="immunity", amount=amount)

        # Apply blessings after blight suppression so the bonus is not itself
        # erased by the infected sector's production penalty.
        for pid, per_resource in blessed_gains.items():
            result.setdefault(pid, {})
            for resource, amount in per_resource.items():
                result[pid][resource] = int(result[pid].get(resource, 0)) + int(amount)
                _inc(game, self.mod_id, "order_blessing_resource_units", int(amount), player_id=pid)
        return {pid: values for pid, values in result.items() if values}

    def enumerate_actions(self, game: HexGame, player_id: int) -> list[Action]:
        if game.phase != GamePhase.MAIN or player_id != game.current_player_id:
            return []
        player = game.players[player_id]
        actions: list[Action] = []
        accessible = self._accessible_hexes(game, player_id)

        # Cleansing remains local: the player must have infrastructure access
        # to the affected sector.
        for hid in sorted(accessible):
            tile = game.map.hexes.get(hid)
            if tile is None or not _productive_tile(game, hid):
                continue
            stage = int(tile.ext.get("blight_stage", 0))
            if stage <= 0:
                continue
            # Standard cleanse: one grain. Keep the old action shape for
            # compatibility with saved/replayed actions and integrations.
            if player.has_resources({self.purify_resource: 1}):
                actions.append(Action.make("purify_hex", hex_id=hid))
            # Deadlock escape only: if the player has no grain at all,
            # permit an expensive cleanse by paying two matching cards of
            # another resource. This is not a cheap alternative to normal
            # grain payment; it exists solely to recover from a grain lock.
            if not player.has_resources({self.purify_resource: 1}):
                for payment_resource in self._emergency_resources:
                    if player.has_resources({payment_resource: 2}):
                        actions.append(Action.make(
                            "purify_hex", hex_id=hid,
                            pay_resource=payment_resource.value,
                        ))

        # Corruption is a ranged action against a visible productive sector,
        # not an action constrained by the player's own roads/buildings.
        # Do not allow players to manually infect their own buildings: Cult
        # immunity plus Tears would turn that into a cheap passive-income farm.
        if self.mode != "environmental" and player.has_resources({Resource.WOOL: 1}):
            for hid in sorted(game.map.hexes):
                if not _visible_to(game, player_id, hid):
                    continue
                tile = game.map.hexes[hid]
                if not _productive_tile(game, hid):
                    continue
                stage = int(tile.ext.get("blight_stage", 0))
                shield_until = int(tile.ext.get("shield_until", -1))
                if stage >= self.max_stage or shield_until > game.global_turn:
                    continue
                if any(
                    game.map.vertices[vid].structure_owner == player_id
                    for vid in tile.vertex_ids
                ):
                    continue
                actions.append(Action.make("corrupt_hex", hex_id=hid))
        if (self.mode == "factions" and self.cult_visibility == "hidden"
                and self.accusation_enabled):
            own = self._player_blight_state(game, player_id)
            if own.get("alignment") == "order" and not own.get("accusation_used"):
                has_penalty_card = any(int(player.resources.get(resource, 0)) > 0 for resource in Resource)
                if has_penalty_card:
                    for target in game.players:
                        if target.player_id == player_id:
                            continue
                        target_state = self._player_blight_state(game, target.player_id)
                        if not target_state.get("cult_revealed"):
                            actions.append(Action.make("accuse_cultist", target_player_id=target.player_id))
        return actions

    def handle_accuse_cultist(self, game: HexGame, player_id: int, action: Action) -> ActionResult:
        """One-shot Order accusation for hidden-faction games."""
        if self.mode != "factions" or self.cult_visibility != "hidden":
            return ActionResult(False, "Обличение доступно только при скрытом Культе")
        if not self.accusation_enabled:
            return ActionResult(False, "Обличение отключено конфигурацией (диагностический прогон)")
        if game.phase != GamePhase.MAIN or player_id != game.current_player_id:
            return ActionResult(False, "Неверная фаза или не ваш ход")
        if not _valid_int(player_id) or not 0 <= player_id < len(game.players):
            return ActionResult(False, "Неверный игрок")
        raw_target = action.p.get("target_player_id")
        if not _valid_int(raw_target) or not 0 <= raw_target < len(game.players) or raw_target == player_id:
            return ActionResult(False, "Неверная цель обличения")
        accuser = self._player_blight_state(game, player_id)
        target = self._player_blight_state(game, raw_target)
        if accuser.get("alignment") != "order":
            return ActionResult(False, "Обличать может только игрок Ордена")
        if accuser.get("accusation_used"):
            return ActionResult(False, "Вы уже использовали обличение")
        if target.get("cult_revealed"):
            return ActionResult(False, "Эта сторона уже раскрыта")
        if not any(int(game.players[player_id].resources.get(resource, 0)) > 0 for resource in Resource):
            return ActionResult(False, "Для ошибочного обличения нужна хотя бы одна карта ресурса")

        accuser["accusation_used"] = True
        _inc(game, self.mod_id, "accusations", player_id=player_id)
        # Diagnostic payload: the fair-guess baseline needs the number of
        # LEGAL targets and the number of actual Cult members among them AT
        # THE MOMENT of the accusation (final membership is not a baseline).
        legal_targets = [
            other.player_id for other in game.players
            if other.player_id != player_id
            and not self._player_blight_state(game, other.player_id).get("cult_revealed")
        ]
        cult_among = sum(
            1 for tid in legal_targets
            if self._player_blight_state(game, tid).get("alignment") == "cult"
        )
        correct = bool(target.get("alignment") == "cult")
        _append_event(game, self.mod_id, {
            "turn": int(game.global_turn), "kind": "accuse",
            "player_id": int(player_id), "target_player_id": int(raw_target),
            "correct": correct,
            "legal_targets": len(legal_targets), "cult_among_legal": cult_among,
        })
        if target.get("alignment") == "cult":
            target["cult_revealed"] = True
            removed = min(2, max(0, int(target.get("tears", 0))))
            target["tears"] = max(0, int(target.get("tears", 0)) - removed)
            accuser["accusation_vp"] = int(accuser.get("accusation_vp", 0)) + 1
            _inc(game, self.mod_id, "correct_accusations", player_id=player_id)
            game._add_log(f"🔎 {game.players[player_id].name} разоблачил Культ: {game.players[raw_target].name}. +1 ОП; потеряно слёз: {removed}.", player_id)
            # This action runs inside the engine mutation wrapper; clear cached VP
            # before the immediate victory check, then the wrapper bumps state_version.
            game._cache.clear()
            game._check_victory(player_id)
            return ActionResult(True, data={"correct": True, "target_player_id": raw_target, "tears_removed": removed})

        resources = [resource for resource in Resource if int(game.players[player_id].resources.get(resource, 0)) > 0]
        accusation_rng = self._local_rng(game, stream="accusation")
        resource = accusation_rng.choice(resources)
        self._save_local_rng(game, accusation_rng, stream="accusation")
        game.players[player_id].resources[resource] -= 1
        _inc(game, self.mod_id, "wrong_accusations", player_id=player_id)
        game._add_log(f"🔎 {game.players[player_id].name} ошибся при обличении и потерял 1 ресурс.", player_id)
        return ActionResult(True, data={"correct": False, "lost_resource": resource.value})

    def handle_purify_hex(
        self, game: HexGame, player_id: int, action: Action
    ) -> ActionResult:
        if game.phase != GamePhase.MAIN or player_id != game.current_player_id:
            return ActionResult(False, "Неверная фаза или не ваш ход")
        if not _valid_int(player_id) or not 0 <= player_id < len(game.players):
            return ActionResult(False, "Неверный player_id")
        raw_hid = action.p.get("hex_id", -1)
        if not _valid_int(raw_hid):
            return ActionResult(False, "Неверный идентификатор сектора")
        hid = raw_hid
        tile = game.map.hexes.get(hid)
        if tile is None or hid not in self._accessible_hexes(game, player_id):
            return ActionResult(False, "Сектор недоступен")
        if not _productive_tile(game, hid):
            return ActionResult(False, "Сектор не производит ресурсы")
        stage = int(tile.ext.get("blight_stage", 0))
        if stage <= 0:
            return ActionResult(False, "Здесь нет скверны")
        player = game.players[player_id]
        raw_payment = action.p.get("pay_resource", self.purify_resource.value)
        try:
            payment_resource = raw_payment if isinstance(raw_payment, Resource) else Resource(str(raw_payment))
        except (TypeError, ValueError):
            return ActionResult(False, "Неизвестный ресурс оплаты очистки")

        if payment_resource == self.purify_resource:
            payment = {self.purify_resource: 1}
            payment_label = "1 зерно" if self.purify_resource == Resource.GRAIN else f"1×{self.purify_resource.value}"
        elif payment_resource in self._emergency_resources:
            if player.has_resources({self.purify_resource: 1}):
                return ActionResult(False, "Аварийная очистка доступна только при отсутствии зерна")
            payment = {payment_resource: 2}
            payment_label = f"2×{payment_resource.value} (аварийная очистка)"
        else:
            return ActionResult(False, "Недопустимый ресурс оплаты очистки")
        if not player.has_resources(payment):
            return ActionResult(False, f"Недостаточно ресурсов: {payment_label}")

        # All validation occurs before either resource or tile state is changed.
        if not player.pay_resources(payment):
            return ActionResult(False, f"Недостаточно ресурсов: {payment_label}")
        tile.ext["blight_stage_sources"] = _blight_stage_sources(tile, stage)[: stage - 1]
        tile.ext["blight_stage"] = stage - 1
        tile.ext["shield_until"] = game.global_turn + self.purified_shield_rounds * max(1, len(game.players))
        tile.ext["blight_last_purify_turn"] = int(game.global_turn)
        tile.ext["blight_last_purify_by"] = int(player_id)
        _inc(game, self.mod_id, "purify_actions", player_id=player_id)
        _inc(game, self.mod_id, "stages_removed")
        _append_event(game, self.mod_id, {"turn": game.global_turn, "kind": "purify", "player_id": player_id, "hex_id": hid, "stage_before": stage, "stage_after": stage - 1, "payment": payment_label})
        alternate_vp_gained = 0
        if self.mode == "factions":
            alignment = self._change_alignment(game, player_id, -1)
            blight_player = self._player_blight_state(game, player_id)
            # Count actual successful cleans even while a player is neutral or
            # Cult-aligned. The Order's VP track becomes claimable on reaching
            # Order, so early cleans toward that allegiance are not discarded.
            blight_player["purified_count"] = int(blight_player.get("purified_count", 0)) + 1
            if alignment == "order":
                alternate_vp_gained = self._award_progress_vp(game, player_id, "purify")
                blessings = tile.ext.get("blight_order_blessings", [])
                if not isinstance(blessings, list):
                    blessings = []
                blessings = [item for item in blessings if not (
                    isinstance(item, dict) and str(item.get("player_id", -1)).lstrip("-").isdigit()
                    and int(item.get("player_id", -1)) == player_id
                )]
                blessings.append({
                    "player_id": int(player_id),
                    "until_turn": int(game.global_turn) + 3 * max(1, len(game.players)),
                })
                tile.ext["blight_order_blessings"] = blessings
                _inc(game, self.mod_id, "order_blessings_granted", player_id=player_id)
        if payment_resource == self.purify_resource:
            game._add_log(f"✨ {self._log_actor(game, player_id)} очистил сектор {hid}", self._log_author(game, player_id))
        else:
            game._add_log(f"✨ {self._log_actor(game, player_id)} очистил сектор {hid} ({payment_label})", self._log_author(game, player_id))
        if alternate_vp_gained:
            state = game.ext.setdefault("blight", {})
            pending = state.setdefault("pending_victory_checks", [])
            state["pending_victory_checks"] = [pid for pid in pending if pid != player_id]
            game._check_victory(player_id)
        return ActionResult(True)

    def handle_corrupt_hex(
        self, game: HexGame, player_id: int, action: Action
    ) -> ActionResult:
        if game.phase != GamePhase.MAIN or player_id != game.current_player_id:
            return ActionResult(False, "Неверная фаза или не ваш ход")
        if self.mode == "environmental":
            return ActionResult(False, "В природном режиме нельзя вручную распространять скверну")
        if not _valid_int(player_id) or not 0 <= player_id < len(game.players):
            return ActionResult(False, "Неверный player_id")
        raw_hid = action.p.get("hex_id", -1)
        if not _valid_int(raw_hid):
            return ActionResult(False, "Неверный идентификатор сектора")
        hid = raw_hid
        tile = game.map.hexes.get(hid)
        if tile is None or not _visible_to(game, player_id, hid):
            return ActionResult(False, "Сектор не виден или недоступен")
        if not _productive_tile(game, hid):
            return ActionResult(False, "Непроизводящий сектор нельзя осквернить")

        # Validation must be side-effect free. Telemetry lives in game state,
        # so do not mutate counters/events until the action has passed every
        # rule/resource check and the payment has succeeded.
        target_owners = {
            game.map.vertices[vid].structure_owner for vid in tile.vertex_ids
            if game.map.vertices[vid].structure_owner is not None
        }
        if player_id in target_owners:
            return ActionResult(False, "Нельзя осквернять сектор со своей постройкой")

        stage = int(tile.ext.get("blight_stage", 0))
        shield_until = int(tile.ext.get("shield_until", -1))
        if stage >= self.max_stage or shield_until > game.global_turn:
            return ActionResult(False, "Сектор защищен или максимально заражен")
        player = game.players[player_id]
        if not player.has_resources({Resource.WOOL: 1}):
            return ActionResult(False, "Необходима 1 шерсть")

        if not player.pay_resources({Resource.WOOL: 1}):
            return ActionResult(False, "Необходима 1 шерсть")

        actor_alignment_before = None
        target_alignments: dict[str, str] = {}
        if self.mode == "factions":
            actor_alignment_before = str(
                self._player_blight_state(game, player_id).get("alignment", "neutral")
            )
            target_alignments = {
                str(target_id): str(
                    self._player_blight_state(game, target_id).get("alignment", "neutral")
                )
                for target_id in sorted(target_owners)
            }

        sources = _blight_stage_sources(tile, stage)
        sources.append(int(player_id))
        tile.ext["blight_stage_sources"] = sources
        tile.ext["blight_stage"] = stage + 1
        tile.ext["blight_last_corrupt_turn"] = int(game.global_turn)
        tile.ext["blight_last_corrupt_by"] = int(player_id)
        _inc(game, self.mod_id, "corrupt_actions", player_id=player_id)
        _inc(game, self.mod_id, "stages_added_by_players")
        actor_state = self._player_blight_state(game, player_id)
        actor_state["corrupt_count"] = int(actor_state.get("corrupt_count", 0)) + 1

        # Aggregate target-alignment counts and preserve actor -> target
        # relationships for balance telemetry. An occupied hex can touch more
        # than one player, so relationship counts are per distinct owner.
        if not target_owners:
            _inc(game, self.mod_id, "corrupt_targets_unoccupied", player_id=player_id)
        else:
            telemetry = _telemetry(game, self.mod_id)
            relationships = telemetry.setdefault("corruption_relationships", {})
            actor_key = str(player_id)
            for target_id in sorted(target_owners):
                target_alignment = target_alignments.get(str(target_id), "not_applicable")
                if self.mode == "factions":
                    category = target_alignment if target_alignment in ("cult", "order", "neutral") else "neutral"
                    _inc(game, self.mod_id, f"corrupt_targets_{category}_aligned", player_id=player_id)
                pair_key = f"{player_id}->{target_id}"
                pair = relationships.setdefault(pair_key, {
                    "actor_id": int(player_id),
                    "target_owner_id": int(target_id),
                    "actions": 0,
                    "actor_alignment_before": {},
                    "target_alignment": {},
                })
                pair["actions"] = int(pair.get("actions", 0)) + 1
                actor_alignments = pair.setdefault("actor_alignment_before", {})
                actor_label = actor_alignment_before or "not_applicable"
                actor_alignments[actor_label] = int(actor_alignments.get(actor_label, 0)) + 1
                target_counts = pair.setdefault("target_alignment", {})
                target_counts[target_alignment] = int(target_counts.get(target_alignment, 0)) + 1

        event = {
            "turn": int(game.global_turn), "kind": "corrupt",
            "player_id": int(player_id), "hex_id": int(hid),
            "stage_before": int(stage), "stage_after": int(stage + 1),
            "target_owner_ids": sorted(int(owner_id) for owner_id in target_owners),
            "actor_alignment_before": actor_alignment_before,
            "target_alignments": target_alignments,
        }
        _append_event(game, self.mod_id, event)
        cult_vp_gained = 0
        if self.mode == "factions":
            alignment = self._change_alignment(game, player_id, +1)
            if alignment == "cult":
                blight_state = self._player_blight_state(game, player_id)
                if self.cult_corrupt_tear:
                    blight_state["tears"] = int(blight_state.get("tears", 0)) + 1
                    blight_state["tears_from_direct"] = int(blight_state.get("tears_from_direct", 0)) + 1
                    _inc(game, self.mod_id, "cult_corrupt_tears", player_id=player_id)
                    # The bonus tear can cross a milestone even for a player
                    # who is already Cult — claim it right here.
                    cult_vp_gained = self._award_progress_vp(game, player_id, "tears")
                    self._feedback_tears(game, player_id, kind="direct", amount=1)
                elif actor_alignment_before != "cult":
                    # Joining the Cult claims tear milestones banked earlier
                    # (relevant only with tears_prealignment).
                    cult_vp_gained = self._award_progress_vp(game, player_id, "tears")
                if cult_vp_gained:
                    state = game.ext.setdefault("blight", {})
                    pending = state.setdefault("pending_victory_checks", [])
                    state["pending_victory_checks"] = [pid for pid in pending if pid != player_id]
                    game._cache.clear()
                    game._check_victory(player_id)
        game._add_log(f"🌑 {self._log_actor(game, player_id)} осквернил сектор {hid}", self._log_author(game, player_id))
        return ActionResult(True)

    def _log_actor(self, game: HexGame, player_id: int) -> str:
        """Journal display name of an actor, anonymous in hidden mode.

        The game log is public (every client receives it with the author id),
        and alignment is a deterministic sum of corrupt (+1) / purify (−1)
        actions. Naming the actor of a corrupt or a cleanse therefore lets any
        player reconstruct the hidden alignment score by arithmetic. In hidden
        factions those actions stay anonymous until a lawful accusation reveals
        a Cult member; the open mode keeps names.
        """
        if self.mode == "factions" and self.cult_visibility == "hidden":
            return "кто-то"
        return game.players[player_id].name

    def _log_victim(self, game: HexGame, player_id: int) -> str:
        if self.mode == "factions" and self.cult_visibility == "hidden":
            return "другого искателя"
        return game.players[player_id].name

    def _log_author(self, game: HexGame, player_id: int) -> Optional[int]:
        """The log author id is public metadata; drop it in hidden mode too."""
        if self.mode == "factions" and self.cult_visibility == "hidden":
            return None
        return int(player_id)

    def _cult_suspicion(self, game: HexGame, owner_id: int) -> int:
        """How much map-wide corruption is attributed to ``owner_id``.

        Used only when faction alignment is hidden: heavy corrupting activity
        is circumstantial evidence of Cult sympathy, so the AI discounts the
        expected damage of striking that player's sectors without ever reading
        the hidden alignment itself. Mirrors the accusation scorer's evidence.
        """
        cache = self._suspicion_cache.get(game)
        if cache is None or cache[0] != game.state_version:
            counts: dict[int, int] = {}
            for tile in game.map.hexes.values():
                stage = max(0, min(self.max_stage, int(tile.ext.get("blight_stage", 0))))
                for source in _blight_stage_sources(tile, stage):
                    if _valid_int(source) and 0 <= source < len(game.players):
                        counts[source] = counts.get(source, 0) + 1
            cache = (game.state_version, counts)
            self._suspicion_cache[game] = cache
        return cache[1].get(owner_id, 0)

    def score_ai_action(
        self, game: HexGame, player_id: int, action: Action, context: Optional[dict[str, Any]] = None
    ) -> Optional[float]:
        """Optional generic-AI scoring hook; returns None for unrelated actions."""
        if action.type == "accuse_cultist":
            context = context or {}
            try:
                target_id = int(action.p.get("target_player_id", -1))
            except (TypeError, ValueError):
                return -1e6
            if not self.accusation_enabled:
                return -1e6
            if self.cult_visibility != "hidden" or self.mode != "factions" or not 0 <= target_id < len(game.players):
                return -1e6
            target_state = self._player_blight_state(game, target_id)
            # Bots should act on visible evidence, not get a free peek at the hidden alignment.
            if target_id == player_id:
                return -1e6
            evidence = 0
            for tile in game.map.hexes.values():
                stage = max(0, min(self.max_stage, int(tile.ext.get("blight_stage", 0))))
                evidence += sum(
                    1 for source in _blight_stage_sources(tile, stage)
                    if source == target_id
                )
            return 0.35 + min(1.5, evidence * 0.25) if evidence else -0.15
        if action.type not in ("purify_hex", "corrupt_hex"):
            return None
        context = context or {}
        genes = context.get("genes", {})
        raw_hid = action.p.get("hex_id", -1)
        if not _valid_int(raw_hid) or raw_hid not in game.map.hexes or not _visible_to(game, player_id, raw_hid):
            return -1e6
        tile = game.map.hexes[raw_hid]
        if not _productive_tile(game, raw_hid):
            return -1e6
        pips = _DICE_PIPS.get(int(tile.number_token), 0)
        own_yield = 0.0
        enemy_yield = 0.0
        enemy_leader_yield = 0.0
        own_count = 0
        enemy_count = 0
        cult_feed = 0.0
        leader_id = context.get("leader_id")
        stage = min(self.max_stage, int(tile.ext.get("blight_stage", 0)))
        hidden = self.mode == "factions" and self.cult_visibility == "hidden"

        for vid in tile.vertex_ids:
            vertex = game.map.vertices[vid]
            owner = vertex.structure_owner
            if owner is None:
                continue
            normal = _structure_yield(vertex.structure_type)
            if owner == player_id:
                own_count += 1
                before = max(0, normal - min(normal, stage))
                after = max(0, normal - min(normal, max(0, stage - 1)))
                own_yield += max(0, after - before)
            else:
                before = max(0, normal - min(normal, stage))
                after = max(0, normal - min(normal, min(self.max_stage, stage + 1)))
                loss = max(0, before - after)
                enemy_count += 1
                # A Cult-aligned victim is immune to suppression and even earns
                # Tears on an infected sector. Open mode knows the alignment, so
                # such a strike yields nothing and actively feeds the opponent.
                # Hidden mode must not peek: discount by the same corruption
                # evidence the accusation scorer uses instead — unless the
                # suspicion heuristic is ablated for a diagnostic run.
                if self.mode == "factions":
                    if hidden and self.ai_hidden_suspicion:
                        # Suspicion from corruption evidence, not the hidden
                        # alignment itself: each attributed blight stage is a
                        # clue. Four clues is conviction — treat the target as
                        # immune and skip it, matching open-mode behaviour.
                        suspicion = self._cult_suspicion(game, owner)
                        if suspicion >= 4:
                            cult_feed += min(normal, stage + 1)
                            continue
                        loss *= max(0.35, 1.0 - 0.22 * suspicion)
                    elif (not hidden and self.open_alignment_aware
                          and str(self._player_blight_state(game, owner).get("alignment")) == "cult"):
                        cult_feed += min(normal, stage + 1)
                        continue
                enemy_yield += loss
                if owner == leader_id:
                    enemy_leader_yield += loss

        weight_fn = context.get("resource_weight")
        goal = context.get("goal")
        if callable(weight_fn):
            try:
                grain_cost = float(weight_fn(self.purify_resource, goal))
                wool_cost = float(weight_fn(Resource.WOOL, goal))
            except Exception:
                grain_cost = wool_cost = 1.0
        else:
            grain_cost = wool_cost = 1.0

        if action.type == "purify_hex":
            if stage <= 0 or own_count == 0:
                return -1e6
            player = game.players[player_id]
            raw_payment = action.p.get("pay_resource", self.purify_resource.value)
            try:
                payment_resource = raw_payment if isinstance(raw_payment, Resource) else Resource(str(raw_payment))
            except (TypeError, ValueError):
                return -1e6
            if payment_resource != self.purify_resource:
                # Emergency cleansing is for an actual strategic resource lock,
                # not a cheap way to clean any convenient tile. Require that
                # this tile yields a resource missing from the active build goal.
                goal_needed = getattr(goal, "needed", {}) if goal else {}
                if not goal_needed or player.resources.get(tile.resource, 0) >= goal_needed.get(tile.resource, 0):
                    return -1e6
            if payment_resource == self.purify_resource:
                payment_count = 1
                payment_weight = grain_cost
            elif payment_resource in self._emergency_resources:
                payment_count = 2
                # Emergency payment must not become a generally cheaper
                # alternative to the standard payment: only when it is absent.
                if player.has_resources({self.purify_resource: 1}):
                    return -1e6
                if not player.has_resources({payment_resource: payment_count}):
                    return -1e6
                try:
                    payment_weight = float(weight_fn(payment_resource, goal)) if callable(weight_fn) else 1.0
                except Exception:
                    payment_weight = 1.0
            else:
                return -1e6
            if not player.has_resources({payment_resource: payment_count}):
                return -1e6
            # A stage-2 tile covered only by settlements yields no immediate
            # recovery when reduced to stage 1; avoid blindly cleaning it.
            benefit = (pips / 5.0) * own_yield * 3.0
            if stage == 2 and own_yield <= 0:
                benefit = 0.35 * (pips / 5.0) * own_count
            score = min(3.5, benefit * _context_gene(context, "blight.purify_bias") - 0.65 * payment_count * payment_weight)
            # Extra value for restoring a resource that the player cannot
            # currently obtain from hand but needs for the active goal.
            holdings = player.resources
            target_resource = tile.resource
            if holdings.get(target_resource, 0) <= 0:
                score += 0.60
            if goal and getattr(goal, "needed", None):
                if holdings.get(target_resource, 0) < goal.needed.get(target_resource, 0):
                    score += 0.45
            # In the city-recovery endgame, only clean a tile that returns a
            # resource missing from the active city plan when all expansion
            # pieces are exhausted. This now also permits emergency payment.
            if (
                getattr(goal, "kind", None) == "city"
                and player.metropolises_left > 0
                and player.outposts_left <= 0
                and player.routes_left <= 0
                and Resource.GRAIN in getattr(goal, "needed", {})
                and player.resources.get(Resource.GRAIN, 0) < goal.needed.get(Resource.GRAIN, 0)
            ):
                missing_goal_resources = {
                    resource for resource, amount in goal.needed.items()
                    if player.resources.get(resource, 0) < amount
                }
                if tile.resource not in missing_goal_resources:
                    return -1e6
            if self.mode == "factions":
                player_state = self._player_blight_state(game, player_id)
                shared_room = (self.shared_vp_cap is None
                               or int(player_state.get("alternate_vp", 0)) < self.shared_vp_cap)
                next_alignment_score = int(player_state.get("alignment_score", 0)) - 1
                if self._alignment_after_change(
                    next_alignment_score, str(player_state.get("alignment", "neutral"))
                ) == "order":
                    if shared_room and int(player_state.get("purify_vp", 0)) < self.vp_cap:
                        progress = int(player_state.get("purified_count", 0)) % self.purify_vp_step
                        score += 0.18 + 0.30 * (progress / max(1, self.purify_vp_step - 1))
            return score

        if self.mode == "environmental":
            return -1e6
        if stage >= self.max_stage or int(tile.ext.get("shield_until", -1)) > game.global_turn:
            return -1e6
        if not game.players[player_id].has_resources({Resource.WOOL: 1}):
            return -1e6
        # Mirror action legality: never corrupt a sector with our own building.
        has_own_building = any(game.map.vertices[vid].structure_owner == player_id for vid in tile.vertex_ids)
        if has_own_building or enemy_count == 0:
            return -1e6
        benefit = (pips / 5.0) * enemy_yield * 2.8
        benefit += (pips / 5.0) * enemy_leader_yield * 0.35 * _context_gene(context, "blight.anti_leader_bias")
        # In open mode every Cult-owned vertex on the target is a gift to the
        # opponent (immunity plus Tears), priced as an explicit penalty.
        benefit -= 0.45 * cult_feed
        if self.mode == "factions" and self.cult_suppression_theft:
            # The Cult pockets the suppressed units, so corruption is income,
            # not just sabotage — value it like a productive investment.
            state_now = self._player_blight_state(game, player_id)
            cult_now = str(state_now.get("alignment")) == "cult"
            cult_after = self._alignment_after_change(
                int(state_now.get("alignment_score", 0)) + 1,
                str(state_now.get("alignment", "neutral")),
            ) == "cult"
            if cult_now or cult_after:
                benefit += (pips / 5.0) * enemy_yield * 0.9
        behind = int(context.get("my_vp", 0)) + 2 < int(context.get("leader_vp", 0))
        if behind:
            benefit *= 0.82
        score = min(3.5, benefit * self.ai_corrupt_aggression * _context_gene(context, "blight.corrupt_bias") - 0.55 * wool_cost)

        # Avoid the costly purge/re-infect loop seen in timeout matches.
        # These are AI scoring penalties, not legality restrictions: a highly
        # valuable strike against a leader can still win the comparison.
        now = int(game.global_turn)
        player_count = max(1, len(game.players))
        last_purify = tile.ext.get("blight_last_purify_turn")
        if isinstance(last_purify, int):
            age = now - last_purify
            if 0 <= age <= 2 * player_count:
                score -= 1.35
            elif 0 <= age <= 4 * player_count:
                score -= 0.65
        last_corrupt = tile.ext.get("blight_last_corrupt_turn")
        last_corrupt_by = tile.ext.get("blight_last_corrupt_by")
        if last_corrupt_by == player_id and isinstance(last_corrupt, int):
            age = now - last_corrupt
            if 0 <= age <= 2 * player_count:
                score -= 0.55
            elif 0 <= age <= 4 * player_count:
                score -= 0.25
        if self.mode == "factions":
            player_state = self._player_blight_state(game, player_id)
            # With the diagnostic shared cap, exhausted total VP room means
            # neither track can mint more faction VP — the progress bonuses
            # below must not treat it as available reward.
            shared_room = (self.shared_vp_cap is None
                           or int(player_state.get("alternate_vp", 0)) < self.shared_vp_cap)
            if self._alignment_after_change(
                int(player_state.get("alignment_score", 0)) + 1,
                str(player_state.get("alignment", "neutral")),
            ) == "cult":
                # Joining the Cult claims past tear milestones at once; value
                # the flip by how close the accumulated tears are to a step.
                if shared_room and int(player_state.get("tears_vp", 0)) < self.vp_cap:
                    progress = int(player_state.get("tears", 0)) % self.tears_vp_step
                    score += 0.18 + 0.30 * (progress / max(1, self.tears_vp_step - 1))
            elif shared_room and int(player_state.get("tears_vp", 0)) < self.vp_cap:
                score += 0.12 * min(1.0, int(player_state.get("tears", 0)) / max(1, self.tears_vp_step - 1))
        # BEGIN blight closeout/post-shield guard
        # This code is inside BlightMod.score_ai_action(), the active scorer
        # for corrupt_hex when the blight plugin is installed.
        global_turn_now = int(getattr(game, "global_turn", 0))
        shield_until_val = int(tile.ext.get("shield_until", -1))
        if shield_until_val >= 0:
            turns_since_shield = global_turn_now - shield_until_val
            if 0 <= turns_since_shield < 12:
                return min(score, 0.10)

        target_vp = int(getattr(game, "points_to_win", 10))
        my_vp = int(context.get("my_vp", 0))
        leader_vp = int(context.get("leader_vp", 0))
        if my_vp >= target_vp - 2 and my_vp >= leader_vp:
            return min(score, 0.10)
        # END blight closeout/post-shield guard
        return score


class TradeRoutesMod:
    """A commodity-specialized, pirate-disruptable trade-route module."""

    mod_id = "routes"
    MOD_AI_GENE_SPECS: dict[str, dict[str, float]] = {
        "routes.activation_bias": {"default": 1.0, "min": 0.20, "max": 2.5, "sigma": 0.10},
        "routes.route_length_bias": {"default": 0.8, "min": 0.0, "max": 2.0, "sigma": 0.10},
        "routes.redundancy_bias": {"default": 0.5, "min": 0.0, "max": 2.0, "sigma": 0.10},
    }

    def __init__(
        self,
        routes_limit: int = 3,
        min_len: int = 2,
        max_len: int = 6,
        income_delay_rounds: int = 1,
        one_income_per_turn: bool = True,
        suppress_on_pirate: bool = True,
        bank_trade_rate: int = 3,
        suppression_rounds: Optional[int] = None,
    ):
        for name, value in (("routes_limit", routes_limit), ("min_len", min_len), ("max_len", max_len)):
            if not _valid_int(value) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if max_len < min_len:
            raise ValueError("max_len must be >= min_len")
        if not _valid_int(income_delay_rounds) or income_delay_rounds < 0:
            raise ValueError("income_delay_rounds must be a non-negative integer")
        if not _valid_int(bank_trade_rate) or not 2 <= bank_trade_rate <= 4:
            raise ValueError("bank_trade_rate must be an integer in [2, 4]")
        if suppression_rounds is not None and (not _valid_int(suppression_rounds) or suppression_rounds < 1):
            raise ValueError("suppression_rounds must be a positive integer or None")

        self.routes_limit = routes_limit
        self.min_len = min_len
        self.max_len = max_len
        self.income_delay_rounds = income_delay_rounds
        self.one_income_per_turn = bool(one_income_per_turn)
        self.suppress_on_pirate = bool(suppress_on_pirate)
        self.bank_trade_rate = bank_trade_rate
        self.suppression_rounds = suppression_rounds
        self._route_cache: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    def install(self, game: HexGame) -> None:
        game.register_action("activate_route", self.handle_activate_route)
        game.register_enumerator(self.enumerate_actions)
        game.on("turn_started", self.on_turn_started)
        game.on("after_raider_moved", self.on_after_raider_moved)
        game.on("modify_bank_trade_rate", self.modify_bank_rate)

    def ai_gene_specs(self) -> dict[str, dict[str, float]]:
        return {k: dict(v) for k, v in self.MOD_AI_GENE_SPECS.items()}

    def _route_commodities(self, game: HexGame, player_id: int, edge_path: tuple[int, ...]) -> tuple[Resource, ...]:
        commodities: set[Resource] = set()
        for eid in edge_path:
            edge = game.map.edges[eid]
            for hid in edge.adjacent_hexes:
                if not _visible_to(game, player_id, hid) or not _productive_tile(game, hid):
                    continue
                commodities.add(game.map.hexes[hid].resource)
        return tuple(r for r in RESOURCE_CHOICES if r in commodities)

    def _find_routes_for_player(self, game: HexGame, player_id: int) -> list[dict[str, Any]]:
        cache = self._route_cache.get(game)
        if cache is not None and cache[0] == game.state_version:
            player_cache = cache[1].get(player_id)
            if player_cache is not None:
                return player_cache
        else:
            cache = (game.state_version, {})
            self._route_cache[game] = cache

        owned_vertices = {
            vid for vid, vertex in game.map.vertices.items() if vertex.structure_owner == player_id
        }
        if len(owned_vertices) < 2:
            cache[1][player_id] = []
            return []

        # Graph contains only player-owned roads and cannot pass through a
        # vertex occupied by an opponent. Sort adjacency to make enumeration
        # deterministic independent of hash iteration order.
        graph: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for eid in sorted(game.map.edges):
            edge = game.map.edges[eid]
            if edge.route_owner != player_id:
                continue
            v1, v2 = edge.vertices
            for start, end in ((v1, v2), (v2, v1)):
                if game.map.vertices[start].structure_owner not in (None, player_id):
                    continue
                if game.map.vertices[end].structure_owner not in (None, player_id):
                    continue
                graph[start].append((end, eid))
        for vid in graph:
            graph[vid].sort(key=lambda pair: (pair[0], pair[1]))

        active_keys: set[frozenset[int]] = {
            frozenset(int(eid) for eid in route.get("edge_path", ()))
            for player in game.players
            for route in player.ext.get("active_routes", [])
            if isinstance(route, dict)
        }
        routes: list[dict[str, Any]] = []
        seen: set[tuple[int, int, tuple[int, ...]]] = set()

        for start_vid in sorted(owned_vertices):
            stack: list[tuple[int, tuple[int, ...], frozenset[int]]] = [
                (start_vid, (), frozenset({start_vid}))
            ]
            while stack:
                cur, path_edges, visited_vertices = stack.pop()
                if cur != start_vid and cur in owned_vertices and len(path_edges) >= self.min_len:
                    # Canonical direction prevents duplicate reverse routes.
                    if start_vid < cur and len(path_edges) <= self.max_len:
                        key = (start_vid, cur, path_edges)
                        if key not in seen and frozenset(path_edges) not in active_keys:
                            commodities = self._route_commodities(game, player_id, path_edges)
                            if commodities:
                                seen.add(key)
                                routes.append(
                                    {
                                        "start_vid": start_vid,
                                        "end_vid": cur,
                                        "edge_path": path_edges,
                                        "commodities": commodities,
                                    }
                                )
                    # An own building may be an endpoint but can also be a
                    # transit vertex if the route is otherwise a simple path.
                if len(path_edges) >= self.max_len:
                    continue
                neighbours = graph.get(cur, ())
                # Stack is LIFO: reverse keeps output traversal stable.
                for nxt, eid in reversed(neighbours):
                    if nxt in visited_vertices:
                        continue
                    stack.append((nxt, path_edges + (eid,), visited_vertices | {nxt}))

        routes.sort(key=lambda r: (r["start_vid"], r["end_vid"], r["edge_path"]))
        cache[1][player_id] = routes
        return routes

    def enumerate_actions(self, game: HexGame, player_id: int) -> list[Action]:
        if game.phase != GamePhase.MAIN or player_id != game.current_player_id:
            return []
        player = game.players[player_id]
        active = player.ext.get("active_routes", [])
        if not isinstance(active, list) or len(active) >= self.routes_limit:
            return []
        if not player.has_resources({Resource.WOOL: 1, Resource.GRAIN: 1}):
            return []

        actions: list[Action] = []
        for route in self._find_routes_for_player(game, player_id):
            for commodity in route["commodities"]:
                actions.append(
                    Action.make(
                        "activate_route",
                        start_vid=route["start_vid"],
                        end_vid=route["end_vid"],
                        edge_path=route["edge_path"],
                        trade_resource=commodity.value,
                    )
                )
        return actions

    def handle_activate_route(self, game: HexGame, player_id: int, action: Action) -> ActionResult:
        if game.phase != GamePhase.MAIN or player_id != game.current_player_id:
            return ActionResult(False, "Не ваша основная фаза")
        if not _valid_int(player_id) or not 0 <= player_id < len(game.players):
            return ActionResult(False, "Неверный player_id")
        player = game.players[player_id]
        routes = player.ext.setdefault("active_routes", [])
        if not isinstance(routes, list):
            return ActionResult(False, "Повреждено состояние торговых маршрутов")
        if len(routes) >= self.routes_limit:
            return ActionResult(False, "Достигнут лимит торговых маршрутов")
        if not player.has_resources({Resource.WOOL: 1, Resource.GRAIN: 1}):
            return ActionResult(False, "Требуется 1 шерсть и 1 зерно")

        params = action.p
        start_vid = params.get("start_vid", -1)
        end_vid = params.get("end_vid", -1)
        if not _valid_int(start_vid) or not _valid_int(end_vid):
            return ActionResult(False, "Неверные вершины маршрута")
        raw_path = params.get("edge_path", ())
        if not isinstance(raw_path, (tuple, list)) or not all(_valid_int(eid) for eid in raw_path):
            return ActionResult(False, "Неверный путь маршрута")
        edge_path = tuple(raw_path)
        commodity = _resource(params.get("trade_resource"))
        if commodity is None:
            return ActionResult(False, "Неверный торговый товар")

        candidate = next(
            (
                route for route in self._find_routes_for_player(game, player_id)
                if route["start_vid"] == start_vid
                and route["end_vid"] == end_vid
                and route["edge_path"] == edge_path
                and commodity in route["commodities"]
            ),
            None,
        )
        if candidate is None:
            return ActionResult(False, "Маршрут недействителен, уже активирован или товар неизвестен")

        # The pay method is the only mutating validation left; everything else
        # was checked first. Do not call optional/nonexistent game.mark_progress.
        if not player.pay_resources({Resource.WOOL: 1, Resource.GRAIN: 1}):
            return ActionResult(False, "Не хватает ресурсов")
        route_state = {
            "start_vid": start_vid,
            "end_vid": end_vid,
            "edge_path": list(edge_path),
            "trade_resource": commodity.value,
            "activated_turn": game.global_turn,
            "income_eligible_turn": game.global_turn + self.income_delay_rounds * max(1, len(game.players)),
            "suppressed_until": -1,
            "last_income_turn": -1,
        }
        routes.append(route_state)
        _inc(game, self.mod_id, "route_activations", player_id=player_id)
        _append_event(
            game,
            self.mod_id,
            {
                "turn": game.global_turn,
                "kind": "activate",
                "player_id": player_id,
                "start_vid": start_vid,
                "end_vid": end_vid,
                "edge_path": list(edge_path),
                "trade_resource": commodity.value,
            },
        )
        game._add_log(f"🚚 {player.name} активировал торговый маршрут!", player_id)
        from isles_core import RESOURCE_NAMES_RU
        game._add_log(f"   📦 Товар маршрута: {RESOURCE_NAMES_RU[commodity]}", player_id)
        return ActionResult(True)

    def on_turn_started(self, game: HexGame, player_id: int) -> None:
        """Pay route income at most once on an owner's turn, if unblocked.

        The default is one unit per player turn across all active routes. With
        ``one_income_per_turn=False``, each ready unsuppressed route pays one
        unit on each owner turn. Suppression suspends both income and bank-rate
        benefit but does not delete or reset the route.
        """
        if not _valid_int(player_id) or not 0 <= player_id < len(game.players):
            return
        player = game.players[player_id]
        routes = player.ext.get("active_routes", [])
        if not isinstance(routes, list):
            return
        ready: list[tuple[dict[str, Any], Resource]] = []
        delay = self.income_delay_rounds * max(1, len(game.players))
        for route in routes:
            if not isinstance(route, dict):
                continue
            commodity = _resource(route.get("trade_resource"))
            if commodity is None:
                continue
            activated_turn = int(route.get("activated_turn", game.global_turn))
            eligible_turn = int(route.get("income_eligible_turn", activated_turn + delay))
            if game.global_turn < eligible_turn or game.global_turn <= activated_turn:
                continue
            if int(route.get("suppressed_until", -1)) > game.global_turn:
                continue
            if int(route.get("last_income_turn", -1)) == game.global_turn:
                continue
            ready.append((route, commodity))
        if self.one_income_per_turn and ready:
            # Do not let the first route in active_routes monopolize income
            # forever. Rotate through all currently-ready routes, skipping
            # routes temporarily suppressed by the raider.
            def route_key(item: tuple[dict[str, Any], Resource]) -> str:
                route, commodity = item
                path = route.get("edge_path", ())
                normalized_path = ",".join(str(int(eid)) for eid in path) if isinstance(path, (list, tuple)) else ""
                return f"{normalized_path}:{commodity.value}"

            ready.sort(key=lambda item: (tuple(item[0].get("edge_path", ())), item[1].value))
            last_key = str(player.ext.get("route_income_last_key", ""))
            start_index = 0
            if last_key:
                previous_indices = [i for i, item in enumerate(ready) if route_key(item) == last_key]
                if previous_indices:
                    start_index = (previous_indices[0] + 1) % len(ready)
            ready = [ready[start_index]]
            player.ext["route_income_last_key"] = route_key(ready[0])
        for route, commodity in ready:
            player.add_resource(commodity, 1)
            route["last_income_turn"] = game.global_turn
            _inc(game, self.mod_id, "route_income_units", player_id=player_id)
            _inc(game, self.mod_id, f"income_{commodity.value}", player_id=player_id)
            _append_event(
                game,
                self.mod_id,
                {
                    "turn": game.global_turn,
                    "kind": "income",
                    "player_id": player_id,
                    "trade_resource": commodity.value,
                    "amount": 1,
                },
            )
            from isles_core import RESOURCE_NAMES_RU
            game._add_log(
                f"💱 {player.name} получил 1 {RESOURCE_NAMES_RU[commodity]} по торговому маршруту",
                player_id,
            )

    def on_after_raider_moved(
        self, game: HexGame, player_id: int, hex_id: int, victims: list[int]
    ) -> None:
        if not self.suppress_on_pirate:
            return
        if hex_id not in game.map.hexes:
            return
        duration = self.suppression_rounds or max(1, len(game.players))
        affected_edges = {
            eid for eid, edge in game.map.edges.items() if hex_id in edge.adjacent_hexes
        }
        newly_suppressed = 0
        for owner in game.players:
            for route in owner.ext.get("active_routes", []):
                if not isinstance(route, dict):
                    continue
                path = route.get("edge_path", ())
                if not set(path).intersection(affected_edges):
                    continue
                old_until = int(route.get("suppressed_until", -1))
                if old_until > game.global_turn:
                    continue
                route["suppressed_until"] = game.global_turn + duration
                newly_suppressed += 1
                _inc(game, self.mod_id, "route_suppressions", player_id=owner.player_id)
                _append_event(
                    game,
                    self.mod_id,
                    {
                        "turn": game.global_turn,
                        "kind": "suppress",
                        "player_id": owner.player_id,
                        "hex_id": hex_id,
                        "suppressed_until": route["suppressed_until"],
                        "trade_resource": route.get("trade_resource"),
                    },
                )
        if newly_suppressed:
            _inc(game, self.mod_id, "suppression_events")
            # Keep the existing one-line message for log parsers.
            game._add_log("🏴‍☠️ Пират нарушил торговые пути", player_id)

    def modify_bank_rate(
        self, game: HexGame, rate: int, player_id: int, give_r: Resource
    ) -> int:
        try:
            commodity = _resource(give_r)
        except Exception:
            return rate
        if commodity is None or not 0 <= player_id < len(game.players):
            return rate
        routes = game.players[player_id].ext.get("active_routes", [])
        if not isinstance(routes, list):
            return rate
        route_is_active = any(
            isinstance(route, dict)
            and route.get("trade_resource") == commodity.value
            and int(route.get("suppressed_until", -1)) <= game.global_turn
            for route in routes
        )
        if route_is_active:
            return min(int(rate), self.bank_trade_rate)
        return rate

    def score_ai_action(
        self, game: HexGame, player_id: int, action: Action, context: Optional[dict[str, Any]] = None
    ) -> Optional[float]:
        """Optional generic-AI score for route actions, in the shared utility scale."""
        if action.type != "activate_route":
            return None
        context = context or {}
        params = action.p
        commodity = _resource(params.get("trade_resource"))
        if commodity is None:
            return -1e6
        player = game.players[player_id]
        if not player.has_resources({Resource.WOOL: 1, Resource.GRAIN: 1}):
            return -1e6
        active_routes = player.ext.get("active_routes", [])
        if not isinstance(active_routes, list) or len(active_routes) >= self.routes_limit:
            return -1e6

        base_rate = _current_route_rate_without_mod(game, player_id, commodity)
        existing_routes_same_commodity = [
            route for route in active_routes
            if isinstance(route, dict) and route.get("trade_resource") == commodity.value
        ]
        existing_live = any(
            int(route.get("suppressed_until", -1)) <= game.global_turn
            for route in existing_routes_same_commodity
        )
        if existing_live:
            return -1e6

        holdings = player.resources
        source_count = int(holdings.get(commodity, 0))
        goal = context.get("goal")
        needed = getattr(goal, "needed", {}) or {}
        needed_from_commodity = int(needed.get(commodity, needed.get(commodity.value, 0)) or 0)
        missing_this = max(0, needed_from_commodity - source_count)
        surplus = max(0, source_count - needed_from_commodity)
        missing_other = sum(
            max(0, int(amt) - int(holdings.get(r, 0)))
            for r, amt in (needed.items() if isinstance(needed, dict) else [])
            if (r if isinstance(r, Resource) else _resource(r)) != commodity
        )

        # A route has two independent benefits: a commodity-specific bank-rate
        # upgrade and delayed resource income. A 3:1 or 2:1 port removes the
        # bank-rate benefit, but must not incorrectly erase the income benefit.
        all_active_routes = [r for r in active_routes if isinstance(r, dict)]
        any_unsuppressed_route = any(
            int(r.get("suppressed_until", -1)) <= game.global_turn
            for r in all_active_routes
        )
        income_benefit = not self.one_income_per_turn or not any_unsuppressed_route
        bank_rate_benefit = base_rate > self.bank_trade_rate
        if not bank_rate_benefit and not income_benefit:
            return -1e6

        route_len = len(params.get("edge_path", ()))
        benefit = 0.35 + 0.42 * min(4, surplus) + 0.40 * min(2, missing_other)
        # Selected commodity should align with the current plan, especially in
        # closeout where grain/ore shortages can otherwise strand a 9-VP leader.
        benefit += 0.62 * min(2, missing_this)
        benefit += 0.12 * min(self.max_len, route_len) * _context_gene(context, "routes.route_length_bias", 0.8)
        if income_benefit:
            benefit += 0.85
        # Redundant route to the same resource can insure against suppression,
        # but gets no automatic bonus unless its previous route is suppressed.
        if existing_routes_same_commodity and not existing_live:
            benefit += 0.45 * _context_gene(context, "routes.redundancy_bias", 0.5)
        cost_fn = context.get("resource_weight")
        if callable(cost_fn):
            try:
                benefit -= 0.45 * (float(cost_fn(Resource.WOOL, goal)) + float(cost_fn(Resource.GRAIN, goal)))
            except Exception:
                benefit -= 0.9
        else:
            benefit -= 0.9
        return max(-2.0, min(3.0, benefit * _context_gene(context, "routes.activation_bias")))



# A single mod registry is the only central point that needs updating when a
# new built-in mod is added. The engine and AI discover the mod's actions and
# scoring interface through the plugin contract, not by action-type branches.
MOD_REGISTRY = {
    "blight": BlightMod,
    "routes": TradeRoutesMod,
}


def normalize_mod_names(names: Any) -> list[str]:
    """Normalize a comma-separated string or iterable of mod names."""
    if names is None:
        return []
    if isinstance(names, str):
        raw_names = names.split(",")
    else:
        try:
            raw_names = list(names)
        except TypeError as exc:
            raise TypeError("Mod names must be a comma-separated string or iterable") from exc
    output: list[str] = []
    seen: set[str] = set()
    for raw in raw_names:
        name = str(raw).strip().lower()
        if not name or name in seen:
            continue
        if name not in MOD_REGISTRY:
            choices = ", ".join(sorted(MOD_REGISTRY))
            raise ValueError(f"Unknown mod '{name}'. Available built-in mods: {choices}")
        seen.add(name)
        output.append(name)
    return output


def create_mods(names: Any = None, *, blight_spread_chance: Optional[float] = None,
                blight_purify_resource: Optional[str] = None,
                blight_mode: Optional[str] = None) -> list[Any]:
    """Instantiate named mods with validated defaults and optional test overrides."""
    output = []
    for name in normalize_mod_names(names):
        if name == "blight" and any(value is not None for value in (
            blight_spread_chance, blight_purify_resource, blight_mode
        )):
            kwargs: dict[str, Any] = {}
            if blight_spread_chance is not None:
                kwargs["spread_chance"] = blight_spread_chance
            if blight_purify_resource is not None:
                kwargs["purify_resource"] = blight_purify_resource
            if blight_mode is not None:
                kwargs["mode"] = blight_mode
            output.append(BlightMod(**kwargs))
        else:
            output.append(MOD_REGISTRY[name]())
    return output


def discover_ai_genes(names: Any = None) -> dict[str, dict[str, float]]:
    """Discover namespaced AI genes from selected modules for a trainer."""
    specs: dict[str, dict[str, float]] = {}
    for mod in create_mods(names):
        getter = getattr(mod, "ai_gene_specs", None)
        if not callable(getter):
            continue
        declared = getter()
        if not isinstance(declared, dict):
            raise TypeError(f"{type(mod).__name__}.ai_gene_specs() must return a dict")
        for name, raw in declared.items():
            if not isinstance(raw, dict):
                raise TypeError(f"Gene '{name}' specification must be a dict")
            required = {"default", "min", "max"}
            if not required.issubset(raw):
                raise ValueError(f"Gene '{name}' must declare default, min, max")
            default, low, high = (float(raw[k]) for k in ("default", "min", "max"))
            if not all(math.isfinite(v) for v in (default, low, high)) or low > high or not low <= default <= high:
                raise ValueError(f"Invalid bounds/default for gene '{name}'")
            if name in specs:
                raise ValueError(f"Duplicate mod AI gene name: '{name}'")
            specs[str(name)] = {
                "default": default,
                "min": low,
                "max": high,
                "sigma": float(raw.get("sigma", 0.10)),
            }
    return specs
