"""
isles_core.py — Отказоустойчивое ядро «HexIsles: Frontier».
- VP-based Stall Watchdog: сторожевой таймер отслеживает реальное изменение очков победы.
- Гарантированный сброс стагнации, если за 160 ходов никто не получил VP.
"""

# ── ВЕРСИЯ ДВИЖКА ────────────────────────────────────────────────────────
# ЭТОТ ФАЙЛ СИНХРОНИЗИРУЕТСЯ ИЗ МАСТЕР-КОПИИ:
#   Мастер-копия: корень проекта (синхронизируется в интеграции платформы)
# Мастер-копия — ВСЕГДА новейшая версия. Не редактируйте этот файл напрямую!
# Дата последней синхронизации: см. git log.
# ─────────────────────────────────────────────────────────────────────────


from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import secrets
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional


class Resource(Enum):
    WOOD = "wood"
    CLAY = "clay"
    GRAIN = "grain"
    WOOL = "wool"
    ORE = "ore"
    NONE = "none"
    ANY = "any"


class TerrainType(Enum):
    FOREST = "forest"
    HILLS = "hills"
    FIELDS = "fields"
    PASTURE = "pasture"
    MOUNTAINS = "mountains"
    WASTELAND = "wasteland"
    RUINS = "ruins"


TERRAIN_TO_RESOURCE = {
    TerrainType.FOREST: Resource.WOOD,
    TerrainType.HILLS: Resource.CLAY,
    TerrainType.FIELDS: Resource.GRAIN,
    TerrainType.PASTURE: Resource.WOOL,
    TerrainType.MOUNTAINS: Resource.ORE,
    TerrainType.WASTELAND: Resource.NONE,
    TerrainType.RUINS: Resource.NONE,
}


class StructureType(Enum):
    OUTPOST = "outpost"        # Фактория (1 VP)
    METROPOLIS = "metropolis"  # Метрополия (2 VP)


class CharterType(Enum):
    VANGUARD = "vanguard"                # Страж (Рыцарь)
    TRIUMPH = "triumph"                  # Патент Триумфа (+1 VP)
    EXPANSION_ACT = "expansion_act"      # Бесплатные тракты
    ABUNDANCE = "abundance"              # Рог Изобилия
    MONOPOLY = "monopoly"                # Монополия
    PIONEER = "pioneer"                  # Первопроходец (разведка)
    RAIDER_HUNTER = "raider_hunter"      # Охотник на разбойника


RESOURCE_CHOICES = (
    Resource.WOOD,
    Resource.CLAY,
    Resource.GRAIN,
    Resource.WOOL,
    Resource.ORE,
)

RESOURCE_NAMES_RU = {
    Resource.WOOD: "дерево",
    Resource.CLAY: "кирпич",
    Resource.GRAIN: "пшеница",
    Resource.WOOL: "овца",
    Resource.ORE: "руда",
}

COST_ROUTE = {Resource.WOOD: 1, Resource.CLAY: 1}
COST_OUTPOST = {Resource.WOOD: 1, Resource.CLAY: 1, Resource.GRAIN: 1, Resource.WOOL: 1}
COST_METROPOLIS = {Resource.GRAIN: 2, Resource.ORE: 3}
COST_CHARTER = {Resource.GRAIN: 1, Resource.WOOL: 1, Resource.ORE: 1}

HEX_SIZE = 50.0


class GamePhase(Enum):
    LOBBY = "lobby"
    SETUP = "setup"
    SETUP_ROUTE = "setup_route"
    ROLL = "roll"
    RAIDER_MOVE = "raider_move"
    RAIDER_STEAL = "raider_steal"
    DISCARD = "discard"
    MAIN = "main"
    FINISHED = "finished"


_FREEZE_DICT = "__dict__"
_FREEZE_LIST = "__list__"
_FREEZE_TUPLE = "__tuple__"
_FREEZE_SET = "__set__"


def _freeze(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        items = [(_freeze(k), _freeze(v)) for k, v in value.items()]
        items.sort(key=lambda kv: str(kv[0]))
        return (_FREEZE_DICT, tuple(items))
    if isinstance(value, list):
        return (_FREEZE_LIST, tuple(_freeze(v) for v in value))
    if isinstance(value, tuple):
        return (_FREEZE_TUPLE, tuple(_freeze(v) for v in value))
    if isinstance(value, set):
        return (_FREEZE_SET, tuple(sorted((_freeze(v) for v in value), key=str)))
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, (tuple, list)) and value:
        tag = value[0]
        if tag == _FREEZE_DICT and len(value) == 2:
            return {_thaw(k): _thaw(v) for k, v in value[1]}
        if tag == _FREEZE_LIST and len(value) == 2:
            return [_thaw(v) for v in value[1]]
        if tag == _FREEZE_TUPLE and len(value) == 2:
            return tuple(_thaw(v) for v in value[1])
        if tag == _FREEZE_SET and len(value) == 2:
            return set(_thaw(v) for v in value[1])
    if isinstance(value, list):
        return [_thaw(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_thaw(v) for v in value)
    return value


@dataclass(frozen=True)
class Action:
    type: str
    params: tuple = ()

    @staticmethod
    def make(action_type: str, **kwargs) -> "Action":
        frozen = tuple(sorted(((k, _freeze(v)) for k, v in kwargs.items()), key=lambda x: str(x[0])))
        return Action(type=action_type, params=frozen)

    @property
    def p(self) -> dict[str, Any]:
        return {k: _thaw(v) for k, v in self.params}

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "params": self.p}


@dataclass
class ActionResult:
    success: bool
    error: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class HexTile:
    hex_id: int
    q: int
    r: int
    terrain: TerrainType
    number_token: int = 0
    has_raider: bool = False
    vertex_ids: list[int] = field(default_factory=list)
    ext: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def resource(self) -> Resource:
        return TERRAIN_TO_RESOURCE[self.terrain]

    def to_dict(self, revealed: bool = True) -> dict[str, Any]:
        if not revealed:
            return {
                "hex_id": self.hex_id, "q": self.q, "r": self.r,
                "terrain": "unknown", "number_token": 0,
                "has_raider": False, "revealed": False,
            }
        return {
            "hex_id": self.hex_id, "q": self.q, "r": self.r,
            "terrain": self.terrain.value, "number_token": self.number_token,
            "has_raider": self.has_raider,
            "is_ruins": self.terrain == TerrainType.RUINS,
            "revealed": True,
        }


@dataclass
class Vertex:
    vertex_id: int
    x: float
    y: float
    adjacent_hexes: list[int] = field(default_factory=list)
    adjacent_vertices: list[int] = field(default_factory=list)
    adjacent_edges: list[int] = field(default_factory=list)
    structure_type: Optional[StructureType] = None
    structure_owner: Optional[int] = None
    is_port: bool = False
    port_type: Optional[Resource] = None
    ext: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_empty(self) -> bool:
        return self.structure_type is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "vertex_id": self.vertex_id,
            "x": round(self.x, 2), "y": round(self.y, 2),
            "structure_type": self.structure_type.value if self.structure_type else None,
            "structure_owner": self.structure_owner,
            "is_port": self.is_port,
            "port_type": self.port_type.value if self.port_type else None,
        }


@dataclass
class Edge:
    edge_id: int
    vertices: tuple[int, int]
    adjacent_hexes: list[int] = field(default_factory=list)
    route_owner: Optional[int] = None
    is_port_edge: bool = False
    port_type: Optional[Resource] = None
    ext: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_empty(self) -> bool:
        return self.route_owner is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "vertices": list(self.vertices),
            "route_owner": self.route_owner,
        }


@dataclass
class CharterInstance:
    charter_type: CharterType
    bought_turn: int

    def is_playable(self, personal_turn: int) -> bool:
        return personal_turn > self.bought_turn and self.charter_type != CharterType.TRIUMPH

    def to_dict(self, personal_turn: int) -> dict[str, Any]:
        return {
            "type": self.charter_type.value,
            "playable": self.is_playable(personal_turn),
            "bought_turn": self.bought_turn,
        }


@dataclass
class Player:
    player_id: int
    name: str
    color: str
    resources: dict[Resource, int] = field(default_factory=lambda: {r: 0 for r in RESOURCE_CHOICES})
    charters: list[CharterInstance] = field(default_factory=list)
    vanguards_played: int = 0
    hidden_vp: int = 0
    outposts_left: int = 5
    metropolises_left: int = 4
    routes_left: int = 15
    revealed_hexes: set[int] = field(default_factory=set)
    charter_played_this_turn: bool = False
    connected: bool = True
    free_routes_pending: int = 0
    personal_turns_taken: int = 0
    trade_pact_turn: int = -1
    ext: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def total_resources(self) -> int:
        return sum(self.resources.values())

    def has_resources(self, cost: dict[Resource, int]) -> bool:
        return all(self.resources.get(r, 0) >= amt for r, amt in cost.items())

    def pay_resources(self, cost: dict[Resource, int]) -> bool:
        if not self.has_resources(cost):
            return False
        for r, amt in cost.items():
            self.resources[r] -= amt
        return True

    def add_resource(self, resource: Resource, amount: int = 1):
        if resource in RESOURCE_CHOICES and amount > 0:
            self.resources[resource] = self.resources.get(resource, 0) + amount

    def to_dict_private(self) -> dict[str, Any]:
        pt = self.personal_turns_taken
        return {
            "player_id": self.player_id, "name": self.name, "color": self.color,
            "resources": {r.value: amt for r, amt in self.resources.items()},
            "charters": [c.to_dict(pt) for c in self.charters],
            "vanguards_played": self.vanguards_played,
            "outposts_left": self.outposts_left,
            "metropolises_left": self.metropolises_left,
            "routes_left": self.routes_left,
            "total_resources": self.total_resources,
            "connected": self.connected,
            "free_routes_pending": self.free_routes_pending,
            "personal_turns_taken": pt,
            "trade_pact_turn": self.trade_pact_turn,
            "charter_played_this_turn": self.charter_played_this_turn,
        }

    def to_dict_public(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id, "name": self.name, "color": self.color,
            "charter_count": len(self.charters),
            "vanguards_played": self.vanguards_played,
            "total_resources": self.total_resources,
            "outposts_left": self.outposts_left,
            "metropolises_left": self.metropolises_left,
            "routes_left": self.routes_left,
            "connected": self.connected,
        }


@dataclass
class TradeOffer:
    from_player: int
    give: dict[Resource, int]
    want: dict[Resource, int]
    rejected_by: set[int] = field(default_factory=set)


class HexMap:
    def __init__(self, rng: random.Random, radius: int = 2, *, legacy_number_pool: bool = False):
        self.rng = rng
        self.radius = radius
        # Reproduces pre-fix maps (replays of old timeouts). Never use for new games.
        self.legacy_number_pool = bool(legacy_number_pool)
        self.hexes: dict[int, HexTile] = {}
        self.vertices: dict[int, Vertex] = {}
        self.edges: dict[int, Edge] = {}
        self._hex_by_coord: dict[tuple[int, int], int] = {}
        self._next_hex_id = 0
        self._next_vertex_id = 0
        self._next_edge_id = 0
        self.number_to_hexes: dict[int, list[int]] = defaultdict(list)
        self._generate()

    def _generate(self):
        pool = self._create_terrain_pool()
        self.rng.shuffle(pool)

        prod_count = len([t for t in pool if t not in (TerrainType.WASTELAND, TerrainType.RUINS)])
        tokens = self._create_number_pool(prod_count)

        coords = [
            (q, r)
            for q in range(-self.radius, self.radius + 1)
            for r in range(-self.radius, self.radius + 1)
            if abs(q + r) <= self.radius
        ]
        self.rng.shuffle(coords)

        for q, r in coords:
            hid = self._next_hex_id
            self._next_hex_id += 1
            terrain = pool.pop() if pool else TerrainType.WASTELAND
            num = tokens.pop() if terrain not in (TerrainType.WASTELAND, TerrainType.RUINS) and tokens else 0
            self.hexes[hid] = HexTile(hex_id=hid, q=q, r=r, terrain=terrain, number_token=num)
            self._hex_by_coord[(q, r)] = hid

        self._guarantee_no_adjacent_reds()
        self._generate_graph()
        self._place_ports_with_gap()
        self._reindex_numbers()

    def _reindex_numbers(self):
        self.number_to_hexes.clear()
        for hid, tile in self.hexes.items():
            if tile.number_token > 0:
                self.number_to_hexes[tile.number_token].append(hid)

    def _has_adjacent_reds(self) -> bool:
        reds = {6, 8}
        dirs = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
        for tile in self.hexes.values():
            if tile.number_token not in reds: continue
            for dq, dr in dirs:
                nhid = self._hex_by_coord.get((tile.q + dq, tile.r + dr))
                if nhid is not None and self.hexes[nhid].number_token in reds:
                    return True
        return False

    def _guarantee_no_adjacent_reds(self):
        """Place all 6/8 tokens on a non-adjacent set of productive hexes.

        Unlike repeated random swaps, this searches for a valid placement. It
        preserves the exact number-token multiset. If the current productive
        terrain positions cannot accommodate all red tokens, it uses a valid
        independent set on the full board and swaps terrain with non-selected
        productive hexes as a deterministic structural fallback.
        """
        reds = {6, 8}
        dirs = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]

        original_tokens = [t.number_token for t in self.hexes.values() if t.number_token > 0]
        red_tokens = [n for n in original_tokens if n in reds]
        other_tokens = [n for n in original_tokens if n not in reds]
        if not red_tokens:
            return

        neighbors = {}
        for hid, tile in self.hexes.items():
            near = set()
            for dq, dr in dirs:
                nhid = self._hex_by_coord.get((tile.q + dq, tile.r + dr))
                if nhid is not None:
                    near.add(nhid)
            neighbors[hid] = near

        def find_independent_set(candidates, needed):
            # Randomized candidate order keeps layouts varied while the search
            # itself backtracks instead of relying on a fixed retry count.
            if needed == 0:
                return []
            if len(candidates) < needed:
                return None
            for i, hid in enumerate(candidates):
                tail = [other for other in candidates[i + 1:] if other not in neighbors[hid]]
                if len(tail) < needed - 1:
                    continue
                rest = find_independent_set(tail, needed - 1)
                if rest is not None:
                    return [hid, *rest]
            return None

        productive = [
            hid for hid, tile in self.hexes.items()
            if tile.terrain not in (TerrainType.WASTELAND, TerrainType.RUINS)
        ]
        self.rng.shuffle(productive)
        red_positions = find_independent_set(productive, len(red_tokens))

        if red_positions is None:
            # Extremely unusual terrain arrangement: find positions anywhere
            # on the board, then move productive terrain onto any selected
            # unproductive positions without changing terrain counts.
            all_ids = list(self.hexes)
            self.rng.shuffle(all_ids)
            red_positions = find_independent_set(all_ids, len(red_tokens))
            if red_positions is None:
                raise RuntimeError(
                    f"Не удалось разместить {len(red_tokens)} красных жетонов без соседства"
                )
            selected = set(red_positions)
            for hid in red_positions:
                tile = self.hexes[hid]
                if tile.terrain in (TerrainType.WASTELAND, TerrainType.RUINS):
                    donors = [
                        other_id for other_id in productive
                        if other_id not in selected
                        and self.hexes[other_id].terrain not in (TerrainType.WASTELAND, TerrainType.RUINS)
                    ]
                    if not donors:
                        raise RuntimeError(
                            "Не удалось перераспределить продуктивную местность для красных жетонов"
                        )
                    donor_id = self.rng.choice(donors)
                    tile.terrain, self.hexes[donor_id].terrain = (
                        self.hexes[donor_id].terrain, tile.terrain
                    )
                    productive.remove(donor_id)
                    productive.append(hid)

        red_set = set(red_positions)
        productive_now = [
            hid for hid, tile in self.hexes.items()
            if tile.terrain not in (TerrainType.WASTELAND, TerrainType.RUINS)
        ]
        if len(productive_now) != len(original_tokens):
            raise RuntimeError(
                "Несогласованность генератора: число продуктивных секторов изменилось"
            )

        self.rng.shuffle(red_tokens)
        self.rng.shuffle(other_tokens)
        for tile in self.hexes.values():
            tile.number_token = 0
        for hid, token in zip(red_positions, red_tokens):
            self.hexes[hid].number_token = token
        remaining_positions = [hid for hid in productive_now if hid not in red_set]
        if len(remaining_positions) != len(other_tokens):
            raise RuntimeError(
                "Несогласованность генератора: количество нерегулярных жетонов не совпало"
            )
        for hid, token in zip(remaining_positions, other_tokens):
            self.hexes[hid].number_token = token

        if self._has_adjacent_reds():
            raise RuntimeError("Внутренняя ошибка: найдены соседние шестерки/восьмерки")

    def _create_terrain_pool(self) -> list[TerrainType]:
        if self.radius == 2:
            return (
                [TerrainType.FOREST] * 4 + [TerrainType.HILLS] * 3 +
                [TerrainType.FIELDS] * 4 + [TerrainType.PASTURE] * 4 +
                [TerrainType.MOUNTAINS] * 3 + [TerrainType.WASTELAND] * 1
            )
        return (
            [TerrainType.FOREST] * 7 + [TerrainType.HILLS] * 6 +
            [TerrainType.FIELDS] * 7 + [TerrainType.PASTURE] * 7 +
            [TerrainType.MOUNTAINS] * 6 + [TerrainType.WASTELAND] * 1 +
            [TerrainType.RUINS] * 3
        )

    def _create_number_pool(self, count: int) -> list[int]:
        base = [2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12]
        if getattr(self, "legacy_number_pool", False):
            # Historical behaviour: truncate the unshuffled repeated base list
            # (always drops 11, 11, 12 for count=33), then shuffle.
            pool = []
            while len(pool) < count:
                pool.extend(base)
            pool = pool[:count]
            self.rng.shuffle(pool)
            return pool
        if count <= 0:
            return []
        copies = (count + len(base) - 1) // len(base)
        pool = base * copies
        # Randomly choose which tokens are omitted when count is not a multiple
        # of the base-pool length; never systematically truncate high numbers.
        return self.rng.sample(pool, count)

    def _generate_graph(self):
        v_coords: list[tuple[float, float, int]] = []
        e_map: dict[tuple[int, int], int] = {}

        def get_vid(vx: float, vy: float) -> int:
            for cx, cy, vid in v_coords:
                if math.hypot(vx - cx, vy - cy) < 5.0: return vid
            vid = self._next_vertex_id
            self._next_vertex_id += 1
            v_coords.append((vx, vy, vid))
            self.vertices[vid] = Vertex(vertex_id=vid, x=vx, y=vy)
            return vid

        for hid, tile in self.hexes.items():
            cx = HEX_SIZE * (math.sqrt(3) * tile.q + math.sqrt(3) / 2 * tile.r)
            cy = HEX_SIZE * (1.5 * tile.r)
            hverts = []
            for i in range(6):
                ang = math.pi / 3 * i - math.pi / 6
                vx = cx + HEX_SIZE * math.cos(ang)
                vy = cy + HEX_SIZE * math.sin(ang)
                vid = get_vid(vx, vy)
                hverts.append(vid)
                if hid not in self.vertices[vid].adjacent_hexes:
                    self.vertices[vid].adjacent_hexes.append(hid)
            tile.vertex_ids = hverts

            for i in range(6):
                v1, v2 = hverts[i], hverts[(i + 1) % 6]
                ekey = (min(v1, v2), max(v1, v2))
                if ekey not in e_map:
                    eid = self._next_edge_id
                    self._next_edge_id += 1
                    e_map[ekey] = eid
                    self.edges[eid] = Edge(edge_id=eid, vertices=ekey)
                eid = e_map[ekey]
                if hid not in self.edges[eid].adjacent_hexes:
                    self.edges[eid].adjacent_hexes.append(hid)
                for va, vb in ((v1, v2), (v2, v1)):
                    if vb not in self.vertices[va].adjacent_vertices:
                        self.vertices[va].adjacent_vertices.append(vb)
                    if eid not in self.vertices[va].adjacent_edges:
                        self.vertices[va].adjacent_edges.append(eid)

    def _place_ports_with_gap(self):
        port_types = [Resource.ANY] * 4 + [Resource.WOOD, Resource.CLAY, Resource.GRAIN, Resource.WOOL, Resource.ORE]
        self.rng.shuffle(port_types)
        coast_edges = [e for e in self.edges.values() if len(e.adjacent_hexes) == 1]
        coast_edges.sort(key=lambda e: math.atan2(self.vertices[e.vertices[0]].y, self.vertices[e.vertices[0]].x))
        assigned = set()

        for ptype in port_types:
            for edge in coast_edges:
                if edge.is_port_edge: continue
                v1, v2 = edge.vertices
                if v1 in assigned or v2 in assigned: continue
                v1_adj = set(self.vertices[v1].adjacent_vertices)
                v2_adj = set(self.vertices[v2].adjacent_vertices)
                if (v1_adj | v2_adj) & assigned: continue

                edge.is_port_edge = True
                edge.port_type = ptype
                for vid in (v1, v2):
                    self.vertices[vid].is_port = True
                    self.vertices[vid].port_type = ptype
                    assigned.add(vid)
                break


def mutator(fn):
    @wraps(fn)
    def wrapper(self: "HexGame", *args, **kwargs):
        depth = getattr(self, "_mutation_depth", 0)
        self._mutation_depth = depth + 1
        try:
            res = fn(self, *args, **kwargs)
            return res
        finally:
            self._mutation_depth = depth
            if depth == 0:
                self.state_version += 1
                self._cache.clear()
                # АВТОМАТИЧЕСКАЯ ПРОВЕРКА ПРОГРЕССА VP
                self._sync_vp_progress()
    return wrapper


def cached(fn):
    @wraps(fn)
    def wrapper(self: "HexGame", *args, **kwargs):
        if getattr(self, "_mutation_depth", 0) > 0:
            return fn(self, *args, **kwargs)

        key = (fn.__name__, self.state_version, args, tuple(sorted(kwargs.items())))
        if key not in self._cache:
            self._cache[key] = fn(self, *args, **kwargs)
        return self._cache[key]
    return wrapper


class HexGame:
    def __init__(
        self,
        room_id: str,
        max_players: int = 4,
        points_to_win: int = 12,
        seed: Optional[int] = None,
        *,
        fog_of_war: bool = False,
        fast_start: bool = False,
        friendly_raider: bool = True,
        silent: bool = False,
        training_mode: bool = False,
        plugins: Optional[list[Any]] = None,
        legacy_number_pool: bool = False,
    ):
        self.room_id = room_id
        self.max_players = max_players
        self.points_to_win = points_to_win

        self.seed = seed if seed is not None else secrets.randbits(64)
        self._seed = self.seed
        self.rng = random.Random(self.seed)

        self.fog_of_war = fog_of_war
        self.fast_start = fast_start
        self.friendly_raider = friendly_raider
        self.silent = silent
        self.training_mode = training_mode

        self.map = HexMap(rng=self.rng, radius=3 if fog_of_war else 2, legacy_number_pool=legacy_number_pool)
        self.phase = GamePhase.LOBBY
        self.global_turn: int = 0
        self.current_player_id: int = 0
        self.dice_result: tuple[int, int] = (0, 0)

        # СТРОГИЙ VP-БАЗИРОВАННЫЙ СТОРОЖЕВОЙ ТАЙМЕР
        self.last_vp_progress_turn: int = 0
        self._last_vp_signature: tuple[int, ...] = ()
        self.stall_limit_turns: int = 160  # Лимит 160 ходов без прироста VP!

        self.players: list[Player] = []
        self.colors = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12"]
        self.bot_pids: set[int] = set()
        self.raider_hex: Optional[int] = None
        self.longest_route_player: Optional[int] = None
        self.grand_garrison_player: Optional[int] = None

        self.charter_deck = (
            [CharterType.TRIUMPH] * 5 +
            [CharterType.VANGUARD] * 14 +
            [CharterType.EXPANSION_ACT] * 2 +
            [CharterType.ABUNDANCE] * 2 +
            [CharterType.MONOPOLY] * 2 +
            [CharterType.PIONEER] * 3 +
            [CharterType.RAIDER_HUNTER] * 2
        )
        self.rng.shuffle(self.charter_deck)

        self.log: list[dict[str, Any]] = []
        self.log_counter: int = 0
        self.pending_discards: dict[int, int] = {}
        self.last_setup_vertex: Optional[int] = None
        self.pending_raider_victims: list[int] = []
        self.setup_order: list[int] = []
        self.setup_index: int = 0
        self.turn_order: list[int] = []

        self.active_trade_offer: Optional[TradeOffer] = None

        self.state_version: int = 0
        self._cache: dict = {}
        self._mutation_depth: int = 0
        self.winner_id: Optional[int] = None

        self.ext: dict[str, Any] = {}
        self._hooks: dict[str, list[Callable]] = defaultdict(list)
        self._action_handlers: dict[str, Callable] = {}
        self._action_enumerators: list[Callable] = []
        self._plugins: list[Any] = []

        self._place_initial_raider()
        for p in plugins or []:
            self.install_plugin(p)

    def _place_initial_raider(self):
        for hid, tile in self.map.hexes.items():
            if tile.terrain == TerrainType.WASTELAND:
                tile.has_raider = True
                self.raider_hex = hid
                return
        if self.map.hexes:
            hid = next(iter(self.map.hexes))
            self.map.hexes[hid].has_raider = True
            self.raider_hex = hid

    def _vp_signature(self) -> tuple[int, ...]:
        return tuple(self.get_victory_points(p.player_id, public=False) for p in self.players)

    def _sync_vp_progress(self):
        sig = self._vp_signature()
        if sig != self._last_vp_signature:
            self._last_vp_signature = sig
            self.last_vp_progress_turn = self.global_turn

    def is_stalled(self, limit: Optional[int] = None) -> bool:
        if self.phase in (GamePhase.LOBBY, GamePhase.SETUP, GamePhase.SETUP_ROUTE, GamePhase.FINISHED):
            return False
        hard_limit = self.stall_limit_turns if limit is None else max(1, int(limit))
        if self.global_turn < max(30, len(self.players) * 8):
            return False
        return (self.global_turn - self.last_vp_progress_turn) >= hard_limit

    def _current_leader_id(self) -> Optional[int]:
        if not self.players: return None
        return max((p.player_id for p in self.players), key=lambda pid: self.get_victory_points(pid, public=False))

    def install_plugin(self, plugin: Any) -> Any:
        if plugin is None: return None
        install = getattr(plugin, "install", None)
        if callable(install): install(self)
        elif callable(plugin): plugin(self)
        self._plugins.append(plugin)
        return plugin

    def on(self, event_name: str, fn: Callable) -> Callable:
        self._hooks[event_name].append(fn)
        return fn

    def trigger(self, event_name: str, *args, **kwargs) -> None:
        for fn in self._hooks.get(event_name, ()):
            try: fn(self, *args, **kwargs)
            except Exception as exc: self._plugin_error(f"hook:{event_name}", exc)

    def pipe(self, event_name: str, value: Any, *args, **kwargs) -> Any:
        for fn in self._hooks.get(event_name, ()):
            try:
                res = fn(self, value, *args, **kwargs)
                if res is not None: value = res
            except Exception as exc: self._plugin_error(f"pipe:{event_name}", exc)
        return value

    def _plugin_error(self, where: str, exc: BaseException):
        errors = self.ext.setdefault("plugin_errors", [])
        errors.append({"where": where, "error": repr(exc)})
        del errors[:-100]
        self._add_log(f"⚠️ Ошибка плагина в {where}: {exc}")

    def register_action(self, action_name: str, handler: Callable):
        self._action_handlers[action_name] = handler

    def register_enumerator(self, fn: Callable):
        self._action_enumerators.append(fn)

    def _valid_pid(self, pid: int) -> bool:
        return isinstance(pid, int) and not isinstance(pid, bool) and 0 <= pid < len(self.players)

    def _err_pid(self, pid: int) -> Optional[ActionResult]:
        if not self._valid_pid(pid): return ActionResult(False, "Неверный player_id")
        return None

    def _err_finished(self) -> Optional[ActionResult]:
        if self.phase == GamePhase.FINISHED: return ActionResult(False, "Игра уже завершена")
        return None

    def _add_log(self, msg: str, player_id: Optional[int] = None):
        if self.silent: return
        self.log_counter += 1
        self.log.append({
            "id": self.log_counter,
            "turn": self.global_turn,
            "msg": msg,
            "player_id": player_id
        })
        if len(self.log) > 500:
            self.log = self.log[-500:]

    @property
    def current_player(self) -> Optional[Player]:
        if not self.players or not (0 <= self.current_player_id < len(self.players)):
            return None
        return self.players[self.current_player_id]

    def _initial_revealed(self) -> set[int]:
        if not self.fog_of_war:
            return set(self.map.hexes.keys())
        return {hid for hid, t in self.map.hexes.items() if max(abs(t.q), abs(t.r), abs(t.q + t.r)) <= 2}

    @mutator
    def _add_player_internal(self, name: str, is_bot: bool) -> ActionResult:
        if len(self.players) >= self.max_players or self.phase != GamePhase.LOBBY:
            return ActionResult(False, "Нельзя добавить игрока")
        pid = len(self.players)
        player = Player(
            player_id=pid,
            name=name,
            color=self.colors[pid % len(self.colors)],
            connected=True,
            revealed_hexes=self._initial_revealed(),
        )
        self.players.append(player)
        if is_bot: self.bot_pids.add(pid)
        return ActionResult(True, data={"player_id": pid, "player": player})

    def add_bot(self, name: str) -> Optional[Player]:
        r = self._add_player_internal(name, True)
        return r.data.get("player") if r.success else None

    @mutator
    def start_game(self) -> ActionResult:
        if self.phase != GamePhase.LOBBY: return ActionResult(False, "Игра уже началась")
        if len(self.players) < 2: return ActionResult(False, "Требуется минимум 2 игрока")

        order = list(range(len(self.players)))
        self.rng.shuffle(order)

        self.turn_order = order
        self.setup_order = order + list(reversed(order))
        self.setup_index = 0
        self.last_setup_vertex = None
        self.pending_discards.clear()
        self.pending_raider_victims.clear()
        self.active_trade_offer = None
        self.winner_id = None
        self.dice_result = (0, 0)
        self.last_vp_progress_turn = 0
        self._last_vp_signature = ()
        self.ext.pop("stalled_out", None)

        self.phase = GamePhase.SETUP
        self.current_player_id = self.setup_order[0]
        self.global_turn = 0
        self._add_log("🎮 Игра началась! Фаза расстановки.")
        self.trigger("game_started")
        return ActionResult(True)

    def _bank_rate(self, player_id: int, give_r: Resource) -> int:
        rate = 4
        for v in self.map.vertices.values():
            if v.structure_owner == player_id and v.is_port:
                if v.port_type == Resource.ANY: rate = min(rate, 3)
                elif v.port_type == give_r: rate = min(rate, 2)
        p = self.players[player_id]
        if p.trade_pact_turn == self.global_turn: rate = min(rate, 2)
        mod_rate = self.pipe("modify_bank_trade_rate", rate, player_id, give_r)
        if isinstance(mod_rate, bool) or not isinstance(mod_rate, int) or mod_rate < 1:
            return 4
        return mod_rate

    @cached
    def _reachable_vertices(self, player_id: int) -> frozenset[int]:
        blocked = frozenset(
            vid for vid, v in self.map.vertices.items()
            if v.structure_owner is not None and v.structure_owner != player_id
        )
        reachable = set()
        q = deque([vid for vid, v in self.map.vertices.items() if v.structure_owner == player_id])
        reachable.update(q)
        while q:
            vid = q.popleft()
            for eid in self.map.vertices[vid].adjacent_edges:
                edge = self.map.edges[eid]
                if edge.route_owner == player_id:
                    v1, v2 = edge.vertices
                    nxt = v2 if v1 == vid else v1
                    if nxt not in blocked and nxt not in reachable:
                        reachable.add(nxt)
                        q.append(nxt)
        return frozenset(reachable)

    @cached
    def _legal_route_edges(self, player_id: int) -> tuple[int, ...]:
        if self.players[player_id].routes_left <= 0: return ()
        reachable = self._reachable_vertices(player_id)
        res = []
        for eid, e in self.map.edges.items():
            if e.is_empty and (e.vertices[0] in reachable or e.vertices[1] in reachable):
                res.append(eid)
        return tuple(res)

    @cached
    def _legal_outpost_vertices(self, player_id: int) -> tuple[int, ...]:
        if self.players[player_id].outposts_left <= 0: return ()
        reachable = self._reachable_vertices(player_id)
        res = []
        for vid in reachable:
            v = self.map.vertices[vid]
            if v.is_empty and all(self.map.vertices[adj].is_empty for adj in v.adjacent_vertices):
                res.append(vid)
        return tuple(res)

    @cached
    def _legal_metropolis_vertices(self, player_id: int) -> tuple[int, ...]:
        if self.players[player_id].metropolises_left <= 0: return ()
        return tuple(
            vid for vid, v in self.map.vertices.items()
            if v.structure_owner == player_id and v.structure_type == StructureType.OUTPOST
        )

    def _calculate_longest_route(self, player_id: int) -> int:
        player_edges = {eid for eid, e in self.map.edges.items() if e.route_owner == player_id}
        if not player_edges: return 0

        graph = defaultdict(set)
        for eid in player_edges:
            v1, v2 = self.map.edges[eid].vertices
            for va, vb in ((v1, v2), (v2, v1)):
                if self.map.vertices[va].structure_owner not in (None, player_id): continue
                graph[va].add((vb, eid))

        max_len = 0
        def dfs(node: int, visited_edges: set[int], length: int):
            nonlocal max_len
            if length > max_len: max_len = length
            for neighbor, eid in graph.get(node, ()):
                if eid not in visited_edges:
                    visited_edges.add(eid)
                    dfs(neighbor, visited_edges, length + 1)
                    visited_edges.remove(eid)

        all_verts = {v for eid in player_edges for v in self.map.edges[eid].vertices}
        for sv in all_verts: dfs(sv, set(), 0)
        return max_len

    def _check_longest_route(self):
        lengths = {p.player_id: self._calculate_longest_route(p.player_id) for p in self.players}
        max_l = max(lengths.values(), default=0)
        incumbent = self.longest_route_player

        if incumbent is not None and max_l >= 5 and lengths.get(incumbent, 0) == max_l:
            return

        leaders = [pid for pid, length in lengths.items() if length == max_l and length >= 5]
        new_owner = leaders[0] if len(leaders) == 1 else None

        if new_owner != incumbent:
            self.longest_route_player = new_owner
            if new_owner is not None:
                self._add_log(f"🛤️ {self.players[new_owner].name} захватывает Великий тракт! ({lengths[new_owner]} дорог)", new_owner)
                self._check_victory(new_owner)

    def _check_grand_garrison(self):
        counts = {p.player_id: p.vanguards_played for p in self.players}
        max_c = max(counts.values(), default=0)
        incumbent = self.grand_garrison_player

        if incumbent is not None and max_c >= 3 and counts.get(incumbent, 0) == max_c:
            return

        leaders = [pid for pid, count in counts.items() if count == max_c and count >= 3]
        new_owner = leaders[0] if len(leaders) == 1 else None

        if new_owner != incumbent:
            self.grand_garrison_player = new_owner
            if new_owner is not None:
                self._add_log(f"⚔️ {self.players[new_owner].name} собирает Великую стражу! ({max_c} стражей)", new_owner)
                self._check_victory(new_owner)

    @cached
    def get_victory_points(self, player_id: int, public: bool = False) -> int:
        vp = sum(
            (1 if v.structure_type == StructureType.OUTPOST else 2)
            for v in self.map.vertices.values()
            if v.structure_owner == player_id
        )
        if self.longest_route_player == player_id: vp += 2
        if self.grand_garrison_player == player_id: vp += 2
        if not public: vp += self.players[player_id].hidden_vp
        return self.pipe("modify_victory_points", vp, player_id, public)

    def _check_victory(self, player_id: int):
        if self.phase == GamePhase.FINISHED: return
        if self.get_victory_points(player_id, public=False) >= self.points_to_win:
            self.phase = GamePhase.FINISHED
            self.winner_id = player_id
            self._add_log(f"🏆 {self.players[player_id].name} ПОБЕДИЛ с {self.get_victory_points(player_id)} очками! 🏆")

    # ── МУТАЦИОННЫЕ ДЕЙСТВИЯ ─────────────────────────────────────

    @mutator
    def setup_place_outpost(self, player_id: int, vertex_id: int) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.SETUP or self.setup_order[self.setup_index] != player_id:
            return ActionResult(False, "Не ваш ход расстановки")
        vertex = self.map.vertices.get(vertex_id)
        if not vertex or not vertex.is_empty or any(not self.map.vertices[a].is_empty for a in vertex.adjacent_vertices):
            return ActionResult(False, "Недопустимая позиция")

        player = self.players[player_id]
        is_second = self.setup_index >= len(self.players)

        if self.fast_start and is_second and player.metropolises_left > 0:
            vertex.structure_type = StructureType.METROPOLIS
            player.metropolises_left -= 1
            player.outposts_left -= 1
            self._add_log(f"🏙️ {player.name} основал Столицу!", player_id)
        else:
            vertex.structure_type = StructureType.OUTPOST
            player.outposts_left -= 1
            self._add_log(f"🏠 {player.name} построил поселение", player_id)

        vertex.structure_owner = player_id
        self.last_setup_vertex = vertex_id

        for hid in vertex.adjacent_hexes:
            player.revealed_hexes.add(hid)
            tile = self.map.hexes[hid]
            if is_second and tile.resource != Resource.NONE:
                player.add_resource(tile.resource, 1)

        self.phase = GamePhase.SETUP_ROUTE
        return ActionResult(True)

    @mutator
    def setup_place_route(self, player_id: int, edge_id: int) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.SETUP_ROUTE or self.setup_order[self.setup_index] != player_id:
            return ActionResult(False, "Не фаза расстановки дороги")
        edge = self.map.edges.get(edge_id)
        if not edge or not edge.is_empty or self.last_setup_vertex not in edge.vertices:
            return ActionResult(False, "Дорога должна примыкать к поселению")

        player = self.players[player_id]
        edge.route_owner = player_id
        player.routes_left -= 1
        self._add_log(f"🛤️ {player.name} построил дорогу", player_id)

        self.setup_index += 1
        if self.setup_index >= len(self.setup_order):
            self.phase = GamePhase.ROLL
            self.current_player_id = self.turn_order[0] if self.turn_order else self.setup_order[0]
            self.global_turn = 1
            self.players[self.current_player_id].personal_turns_taken += 1
            self._add_log("✅ Расстановка завершена! Начинается игра.")
            self.trigger("turn_started", self.current_player_id)
        else:
            self.current_player_id = self.setup_order[self.setup_index]
            self.phase = GamePhase.SETUP

        return ActionResult(True)

    @mutator
    def roll_dice(self, player_id: int) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.ROLL or player_id != self.current_player_id:
            return ActionResult(False, "Сейчас нельзя бросать кубики")

        self.active_trade_offer = None
        self.pending_raider_victims.clear()

        d1, d2 = self.rng.randint(1, 6), self.rng.randint(1, 6)
        self.dice_result = (d1, d2)
        total = d1 + d2
        self._add_log(f"🎲 {self.current_player.name}: {d1}+{d2}={total}", player_id)
        self.trigger("after_dice_rolled", player_id, d1, d2, total)

        if total == 7:
            has_discards = False
            for p in self.players:
                if p.total_resources > 7:
                    self.pending_discards[p.player_id] = p.total_resources // 2
                    has_discards = True
            if has_discards:
                self.phase = GamePhase.DISCARD
                self._add_log("💀 Выпало 7! Игроки сбрасывают лишние ресурсы.")
            else:
                self.phase = GamePhase.RAIDER_MOVE
                self._add_log("🏴‍☠️ Переместите разбойника!")
        else:
            self._distribute_production(total)
            self.phase = GamePhase.MAIN
            # Extension callbacks that can award production-linked VP must run
            # after resource distribution and the phase transition. Firing a
            # victory check from inside modify_resource_gains would let this
            # method overwrite FINISHED with MAIN immediately afterwards.
            self.trigger("after_resource_distribution", player_id, total)

        return ActionResult(True, data={"dice": [d1, d2], "total": total})

    def _distribute_production(self, number: int):
        gains: dict[int, dict[Resource, int]] = defaultdict(lambda: defaultdict(int))
        for hid in self.map.number_to_hexes.get(number, []):
            tile = self.map.hexes[hid]
            if tile.has_raider or tile.terrain in (TerrainType.WASTELAND, TerrainType.RUINS):
                continue
            for vid in tile.vertex_ids:
                v = self.map.vertices[vid]
                if v.structure_owner is not None:
                    amt = 2 if v.structure_type == StructureType.METROPOLIS else 1
                    gains[v.structure_owner][tile.resource] += amt

        gains = self.pipe("modify_resource_gains", gains, number)
        for pid, res_dict in gains.items():
            player = self.players[pid]
            parts = []
            for r, count in res_dict.items():
                player.add_resource(r, count)
                parts.append(f"{count} {RESOURCE_NAMES_RU[r]}")
            if parts:
                self._add_log(f"📦 {player.name} получил: {', '.join(parts)}", pid)

    @mutator
    def discard_resources(self, player_id: int, discards: dict) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.DISCARD: return ActionResult(False, "Не фаза сброса")
        if player_id not in self.pending_discards: return ActionResult(False, "Вам не нужно сбрасывать")
        if not isinstance(discards, dict): return ActionResult(False, "Ожидался словарь ресурсов")

        clean_discards = defaultdict(int)
        for raw_k, amount in discards.items():
            if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                return ActionResult(False, "Некорректное количество")
            try:
                r = raw_k if isinstance(raw_k, Resource) else Resource(raw_k)
            except Exception:
                return ActionResult(False, "Неизвестный ресурс")
            if r not in RESOURCE_CHOICES:
                return ActionResult(False, "Ресурс нельзя сбрасывать")
            clean_discards[r] += amount

        needed = self.pending_discards[player_id]
        if sum(clean_discards.values()) != needed:
            return ActionResult(False, f"Нужно сбросить ровно {needed}")

        p = self.players[player_id]
        for r, amt in clean_discards.items():
            if p.resources.get(r, 0) < amt:
                return ActionResult(False, f"Недостаточно {r.value}")

        for r, amt in clean_discards.items():
            p.resources[r] -= amt

        del self.pending_discards[player_id]
        self._add_log(f"♻️ {p.name} сбросил ресурсы", player_id)
        if not self.pending_discards:
            self.phase = GamePhase.RAIDER_MOVE
            self._add_log("🏴‍☠️ Переместите разбойника!")
        return ActionResult(True)

    @mutator
    def move_raider(self, player_id: int, hex_id: int) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.RAIDER_MOVE or player_id != self.current_player_id:
            return ActionResult(False, "Не фаза перемещения разбойника")
        tile = self.map.hexes.get(hex_id)
        if not tile or hex_id == self.raider_hex:
            return ActionResult(False, "Неверный тайл")

        if self.raider_hex is not None and self.raider_hex in self.map.hexes:
            self.map.hexes[self.raider_hex].has_raider = False
        tile.has_raider = True
        self.raider_hex = hex_id
        self.players[player_id].revealed_hexes.add(hex_id)

        victims = set()
        for vid in tile.vertex_ids:
            v = self.map.vertices[vid]
            if v.structure_owner not in (None, player_id):
                victim = self.players[v.structure_owner]
                if victim.total_resources > 0:
                    if self.friendly_raider and self.get_victory_points(v.structure_owner, True) <= 2:
                        continue
                    victims.add(v.structure_owner)

        if victims:
            self.pending_raider_victims = sorted(victims)
            self.phase = GamePhase.RAIDER_STEAL
            self._add_log(f"🏴‍☠️ {self.players[player_id].name} переместил разбойника. Выберите жертву.", player_id)
        else:
            self.pending_raider_victims = []
            self.phase = GamePhase.MAIN
            self._add_log(f"🏴‍☠️ {self.players[player_id].name} переместил разбойника (грабить некого)", player_id)

        self.trigger("after_raider_moved", player_id, hex_id, self.pending_raider_victims)
        return ActionResult(True)

    @mutator
    def choose_raider_victim(self, player_id: int, victim_id: int) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if not self._valid_pid(victim_id): return ActionResult(False, "Неверная жертва")
        if self.phase != GamePhase.RAIDER_STEAL or player_id != self.current_player_id:
            return ActionResult(False, "Не фаза выбора жертвы")
        if victim_id not in self.pending_raider_victims:
            return ActionResult(False, "Неверная жертва")

        victim = self.players[victim_id]
        pool = [r for r, count in victim.resources.items() for _ in range(count)]
        if pool:
            stolen = self.rng.choice(pool)
            victim.resources[stolen] -= 1
            self.players[player_id].add_resource(stolen, 1)
            self._add_log(f"🏴‍☠️ {self.players[player_id].name} украл {RESOURCE_NAMES_RU[stolen]} у {victim.name}", player_id)

        self.pending_raider_victims = []
        self.phase = GamePhase.MAIN
        return ActionResult(True)

    @mutator
    def propose_trade(self, player_id: int, give: dict, want: dict) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.MAIN or player_id != self.current_player_id:
            return ActionResult(False, "Нельзя предложить обмен")
        if self.active_trade_offer is not None:
            return ActionResult(False, "Другой обмен уже активен")

        def parse_map(d):
            if not isinstance(d, dict) or not d: return None
            res = {}
            for k, v in d.items():
                if isinstance(v, bool) or not isinstance(v, int) or v <= 0: return None
                try:
                    r = k if isinstance(k, Resource) else Resource(k)
                    if r not in RESOURCE_CHOICES: return None
                    res[r] = v
                except Exception: return None
            return res

        give_map = parse_map(give)
        want_map = parse_map(want)
        if not give_map or not want_map:
            return ActionResult(False, "Некорректные параметры обмена")

        player = self.players[player_id]
        if not player.has_resources(give_map):
            return ActionResult(False, "Нет ресурсов для предложения")

        self.active_trade_offer = TradeOffer(from_player=player_id, give=give_map, want=want_map)
        self._add_log(f"📣 {player.name} предложил обмен!", player_id)
        return ActionResult(True)

    @mutator
    def respond_trade(self, player_id: int, accept: bool) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.MAIN: return ActionResult(False, "Вне основной фазы")
        offer = self.active_trade_offer
        if not offer or offer.from_player == player_id or player_id in offer.rejected_by:
            return ActionResult(False, "Нет активного предложения для вас")

        if accept:
            p_from = self.players[offer.from_player]
            p_to = self.players[player_id]
            if not p_to.has_resources(offer.want) or not p_from.has_resources(offer.give):
                self.active_trade_offer = None
                return ActionResult(False, "Недостаточно ресурсов")

            p_from.pay_resources(offer.give)
            p_to.pay_resources(offer.want)
            for r, count in offer.want.items(): p_from.add_resource(r, count)
            for r, count in offer.give.items(): p_to.add_resource(r, count)

            self.active_trade_offer = None
            self._add_log(f"🤝 {p_from.name} и {p_to.name} совершили обмен!")
        else:
            offer.rejected_by.add(player_id)
            active_opponents = {p.player_id for p in self.players if p.connected and p.player_id != offer.from_player}
            if active_opponents.issubset(offer.rejected_by):
                self.active_trade_offer = None
                self._add_log("🚫 Предложение обмена отклонено всеми")

        return ActionResult(True)

    @mutator
    def cancel_trade(self, player_id: int) -> ActionResult:
        e = self._err_pid(player_id)
        if e: return e
        if self.active_trade_offer and self.active_trade_offer.from_player == player_id:
            self.active_trade_offer = None
            return ActionResult(True)
        return ActionResult(False, "Невозможно отменить")

    @mutator
    def trade_bank(self, player_id: int, give: str, receive: str) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.MAIN or player_id != self.current_player_id:
            return ActionResult(False, "Нельзя торговать сейчас")
        try:
            give_r = give if isinstance(give, Resource) else Resource(give)
            recv_r = receive if isinstance(receive, Resource) else Resource(receive)
        except Exception:
            return ActionResult(False, "Неверный ресурс")

        if give_r not in RESOURCE_CHOICES or recv_r not in RESOURCE_CHOICES or give_r == recv_r:
            return ActionResult(False, "Недопустимый ресурс для обмена")

        rate = self._bank_rate(player_id, give_r)
        player = self.players[player_id]
        if player.resources.get(give_r, 0) < rate:
            return ActionResult(False, f"Нужно {rate} {give_r.value}")

        player.resources[give_r] -= rate
        player.add_resource(recv_r, 1)
        self._add_log(f"🔄 {player.name}: {rate}×{RESOURCE_NAMES_RU[give_r]} → {RESOURCE_NAMES_RU[recv_r]}", player_id)
        return ActionResult(True)

    @mutator
    def buy_charter(self, player_id: int) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.MAIN or player_id != self.current_player_id:
            return ActionResult(False, "Нельзя купить карту сейчас")
        if not self.charter_deck:
            return ActionResult(False, "Колода пуста")
        player = self.players[player_id]
        if not player.pay_resources(COST_CHARTER):
            return ActionResult(False, "Не хватает ресурсов")

        c = self.charter_deck.pop()
        player.charters.append(CharterInstance(charter_type=c, bought_turn=player.personal_turns_taken))
        self._add_log(f"🃏 {player.name} купил карту развития", player_id)

        if c == CharterType.TRIUMPH:
            player.hidden_vp += 1
            self._check_victory(player_id)

        return ActionResult(True, data={"charter": c.value})

    @mutator
    def play_charter(self, player_id: int, charter_type: str, params: dict = None) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.MAIN or player_id != self.current_player_id:
            return ActionResult(False, "Нельзя играть карту сейчас")
        player = self.players[player_id]
        if player.charter_played_this_turn:
            return ActionResult(False, "Уже сыграна карта в этом ходу")

        try:
            ct = charter_type if isinstance(charter_type, CharterType) else CharterType(charter_type)
        except Exception:
            return ActionResult(False, "Неизвестный тип карты")

        instance = next((c for c in player.charters if c.charter_type == ct and c.is_playable(player.personal_turns_taken)), None)
        if not instance:
            return ActionResult(False, "Карта недоступна")

        params = params or {}

        if ct == CharterType.ABUNDANCE:
            r1 = Resource(params.get("resource1", "")) if params.get("resource1") in [r.value for r in RESOURCE_CHOICES] else None
            r2 = Resource(params.get("resource2", "")) if params.get("resource2") in [r.value for r in RESOURCE_CHOICES] else None
            if not r1 or not r2:
                return ActionResult(False, "Укажите 2 корректных ресурса")

        elif ct == CharterType.MONOPOLY:
            target = Resource(params.get("resource", "")) if params.get("resource") in [r.value for r in RESOURCE_CHOICES] else None
            if not target:
                return ActionResult(False, "Укажите корректный ресурс")

        elif ct == CharterType.PIONEER:
            hid = params.get("hex_id")
            if hid not in self.map.hexes:
                return ActionResult(False, "Неверный сектор для разведки")
            if hid in player.revealed_hexes:
                return ActionResult(False, "Сектор уже исследован")

        player.charters.remove(instance)
        player.charter_played_this_turn = True

        if ct == CharterType.VANGUARD:
            player.vanguards_played += 1
            self._check_grand_garrison()
            self._check_victory(player_id)
            if self.phase != GamePhase.FINISHED:
                self.phase = GamePhase.RAIDER_MOVE
            self._add_log(f"⚔️ {player.name} сыграл Рыцаря!", player_id)

        elif ct == CharterType.EXPANSION_ACT:
            player.free_routes_pending = min(2, player.routes_left)
            self._add_log(f"🛤️ {player.name} сыграл Строительство дорог", player_id)

        elif ct == CharterType.ABUNDANCE:
            player.add_resource(r1, 1)
            player.add_resource(r2, 1)
            self._add_log(f"🌽 {player.name}: Год изобилия", player_id)

        elif ct == CharterType.MONOPOLY:
            total = 0
            for opp in self.players:
                if opp.player_id != player_id:
                    amt = opp.resources.get(target, 0)
                    opp.resources[target] = 0
                    total += amt
            player.add_resource(target, total)
            self._add_log(f"💰 {player.name}: Монополия на {target.value} (+{total})", player_id)

        elif ct == CharterType.PIONEER:
            player.revealed_hexes.add(hid)
            self._add_log(f"🧭 {player.name} исследовал сектор {hid}", player_id)

        elif ct == CharterType.RAIDER_HUNTER:
            if self.raider_hex is not None and self.raider_hex in self.map.hexes:
                self.map.hexes[self.raider_hex].has_raider = False
            desert_hex = next((h for h, t in self.map.hexes.items() if t.terrain == TerrainType.WASTELAND), None)
            if desert_hex is not None:
                self.raider_hex = desert_hex
                self.map.hexes[desert_hex].has_raider = True
            self._add_log(f"🏹 {player.name} прогнал разбойника!", player_id)

        return ActionResult(True)

    @mutator
    def build_route(self, player_id: int, edge_id: int) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.MAIN or player_id != self.current_player_id:
            return ActionResult(False, "Нельзя строить сейчас")
        if edge_id not in self._legal_route_edges(player_id):
            return ActionResult(False, "Нельзя строить здесь")

        player = self.players[player_id]
        if player.free_routes_pending > 0:
            player.free_routes_pending -= 1
            self._add_log(f"🛤️ {player.name} построил бесплатную дорогу", player_id)
        else:
            if not player.pay_resources(COST_ROUTE):
                return ActionResult(False, "Не хватает ресурсов")
            self._add_log(f"🛤️ {player.name} построил дорогу", player_id)

        self.map.edges[edge_id].route_owner = player_id
        player.routes_left -= 1
        self.trigger("after_build_route", player_id, edge_id)
        self._check_longest_route()
        self._check_victory(player_id)
        return ActionResult(True)

    @mutator
    def build_outpost(self, player_id: int, vertex_id: int) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.MAIN or player_id != self.current_player_id:
            return ActionResult(False, "Нельзя строить сейчас")
        if vertex_id not in self._legal_outpost_vertices(player_id):
            return ActionResult(False, "Нельзя строить здесь")
        player = self.players[player_id]
        if not player.pay_resources(COST_OUTPOST):
            return ActionResult(False, "Не хватает ресурсов")

        v = self.map.vertices[vertex_id]
        v.structure_type = StructureType.OUTPOST
        v.structure_owner = player_id
        player.outposts_left -= 1

        for hid in v.adjacent_hexes:
            player.revealed_hexes.add(hid)

        self._add_log(f"🏠 {player.name} построил поселение", player_id)
        self.trigger("after_build_outpost", player_id, vertex_id)
        self._check_longest_route()
        self._check_victory(player_id)
        return ActionResult(True)

    @mutator
    def build_metropolis(self, player_id: int, vertex_id: int) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.MAIN or player_id != self.current_player_id:
            return ActionResult(False, "Нельзя строить сейчас")
        if vertex_id not in self._legal_metropolis_vertices(player_id):
            return ActionResult(False, "Нельзя улучшить здесь")
        player = self.players[player_id]
        if not player.pay_resources(COST_METROPOLIS):
            return ActionResult(False, "Не хватает ресурсов")

        self.map.vertices[vertex_id].structure_type = StructureType.METROPOLIS
        player.outposts_left += 1
        player.metropolises_left -= 1
        self._add_log(f"🏙️ {player.name} построил город", player_id)
        self.trigger("after_build_metropolis", player_id, vertex_id)
        self._check_victory(player_id)
        return ActionResult(True)

    @mutator
    def end_turn(self, player_id: int) -> ActionResult:
        e = self._err_pid(player_id) or self._err_finished()
        if e: return e
        if self.phase != GamePhase.MAIN or player_id != self.current_player_id:
            return ActionResult(False, "Нельзя завершить ход")

        player = self.players[player_id]
        if player.free_routes_pending > 0 and self._legal_route_edges(player_id):
            return ActionResult(False, "Вы обязаны построить бесплатные дороги")

        self.active_trade_offer = None
        player.charter_played_this_turn = False
        player.free_routes_pending = 0

        self._check_victory(player_id)
        if self.phase == GamePhase.FINISHED:
            return ActionResult(True)

        self.trigger("turn_ended", player_id)

        if self.turn_order and self.current_player_id in self.turn_order:
            current_idx = self.turn_order.index(self.current_player_id)
            self.current_player_id = self.turn_order[(current_idx + 1) % len(self.turn_order)]
        else:
            self.current_player_id = (self.current_player_id + 1) % len(self.players)

        self.global_turn += 1
        self.players[self.current_player_id].personal_turns_taken += 1
        self.phase = GamePhase.ROLL
        self._add_log(f"⏭️ Ход переходит к {self.current_player.name}")
        self.trigger("turn_started", self.current_player_id)
        return ActionResult(True)

    def _enumerate_discard_combos(self, player_id: int, needed: int, max_variants: int = 250) -> list[dict[Resource, int]]:
        player = self.players[player_id]
        limits = [player.resources.get(r, 0) for r in RESOURCE_CHOICES]
        out: list[dict[Resource, int]] = []
        cur = [0] * len(RESOURCE_CHOICES)

        def dfs(i: int, remaining: int):
            if len(out) >= max_variants: return
            if i == len(RESOURCE_CHOICES) - 1:
                if remaining <= limits[i]:
                    cur[i] = remaining
                    combo = {RESOURCE_CHOICES[j]: cur[j] for j in range(len(RESOURCE_CHOICES)) if cur[j] > 0}
                    out.append(combo)
                return
            max_take = min(limits[i], remaining)
            for take in range(max_take, -1, -1):
                cur[i] = take
                dfs(i + 1, remaining - take)
                if len(out) >= max_variants: break
            cur[i] = 0

        dfs(0, needed)
        return out

    def enumerate_legal_actions(self, player_id: int) -> list[Action]:
        if not self._valid_pid(player_id) or self.phase == GamePhase.FINISHED:
            return []

        actions: list[Action] = []
        is_my_turn = (player_id == self.current_player_id)
        p = self.players[player_id]

        if self.phase == GamePhase.SETUP and self.setup_order and self.setup_order[self.setup_index] == player_id:
            for vid, v in self.map.vertices.items():
                if v.is_empty and all(self.map.vertices[adj].is_empty for adj in v.adjacent_vertices):
                    actions.append(Action.make("setup_outpost", vertex_id=vid))
            return actions

        if self.phase == GamePhase.SETUP_ROUTE and self.last_setup_vertex is not None and self.setup_order and self.setup_order[self.setup_index] == player_id:
            for eid in self.map.vertices[self.last_setup_vertex].adjacent_edges:
                if self.map.edges[eid].is_empty:
                    actions.append(Action.make("setup_route", edge_id=eid))
            return actions

        if self.phase == GamePhase.ROLL and is_my_turn:
            actions.append(Action.make("roll_dice"))
            return actions

        if self.phase == GamePhase.DISCARD and player_id in self.pending_discards:
            needed = self.pending_discards[player_id]
            for combo in self._enumerate_discard_combos(player_id, needed):
                actions.append(Action.make("discard", resources={r.value: amt for r, amt in combo.items()}))
            return actions

        if self.phase == GamePhase.RAIDER_MOVE and is_my_turn:
            for hid in self.map.hexes:
                if hid != self.raider_hex:
                    actions.append(Action.make("move_raider", hex_id=hid))
            return actions

        if self.phase == GamePhase.RAIDER_STEAL and is_my_turn:
            for vid in self.pending_raider_victims:
                actions.append(Action.make("choose_victim", victim_id=vid))
            return actions

        if self.phase != GamePhase.MAIN:
            return actions

        offer = self.active_trade_offer
        if offer:
            if player_id == offer.from_player:
                actions.append(Action.make("cancel_trade"))
            elif player_id not in offer.rejected_by:
                can_accept = p.has_resources(offer.want) and self.players[offer.from_player].has_resources(offer.give)
                if can_accept:
                    actions.append(Action.make("respond_trade", accept=True))
                actions.append(Action.make("respond_trade", accept=False))

        if not is_my_turn:
            return actions

        route_edges = self._legal_route_edges(player_id)
        if p.free_routes_pending > 0 and route_edges:
            for eid in route_edges:
                actions.append(Action.make("build_route", edge_id=eid))
            return actions

        actions.append(Action.make("end_turn"))

        if p.has_resources(COST_ROUTE) and p.routes_left > 0:
            for eid in route_edges:
                actions.append(Action.make("build_route", edge_id=eid))

        if p.has_resources(COST_OUTPOST) and p.outposts_left > 0:
            for vid in self._legal_outpost_vertices(player_id):
                actions.append(Action.make("build_outpost", vertex_id=vid))

        if p.has_resources(COST_METROPOLIS) and p.metropolises_left > 0:
            for vid in self._legal_metropolis_vertices(player_id):
                actions.append(Action.make("build_metropolis", vertex_id=vid))

        if p.has_resources(COST_CHARTER) and len(self.charter_deck) > 0:
            actions.append(Action.make("buy_charter"))

        if not p.charter_played_this_turn:
            playable_types = set()
            for c in p.charters:
                if c.is_playable(p.personal_turns_taken) and c.charter_type not in playable_types:
                    playable_types.add(c.charter_type)
            for ct in sorted(playable_types, key=lambda item: item.value):
                if ct in (CharterType.VANGUARD, CharterType.RAIDER_HUNTER):
                    actions.append(Action.make("play_charter", charter_type=ct.value))
                elif ct == CharterType.EXPANSION_ACT and p.routes_left > 0 and route_edges:
                    actions.append(Action.make("play_charter", charter_type=ct.value))
                elif ct == CharterType.ABUNDANCE:
                    for r1 in RESOURCE_CHOICES:
                        for r2 in RESOURCE_CHOICES:
                            if r1.value <= r2.value:
                                actions.append(Action.make("play_charter", charter_type=ct.value, resource1=r1.value, resource2=r2.value))
                elif ct == CharterType.MONOPOLY:
                    for r in RESOURCE_CHOICES:
                        actions.append(Action.make("play_charter", charter_type=ct.value, resource=r.value))
                elif ct == CharterType.PIONEER:
                    for hid in [h for h in self.map.hexes if h not in p.revealed_hexes]:
                        actions.append(Action.make("play_charter", charter_type=ct.value, hex_id=hid))

        for give_r in RESOURCE_CHOICES:
            rate = self._bank_rate(player_id, give_r)
            if p.resources.get(give_r, 0) >= rate:
                for recv_r in RESOURCE_CHOICES:
                    if recv_r != give_r:
                        actions.append(Action.make("trade_bank", give=give_r.value, receive=recv_r.value))

        if p.total_resources > 0 and self.active_trade_offer is None:
            for give_r in RESOURCE_CHOICES:
                if p.resources.get(give_r, 0) >= 1:
                    for want_r in RESOURCE_CHOICES:
                        if give_r != want_r:
                            actions.append(Action.make("propose_trade", give={give_r.value: 1}, want={want_r.value: 1}))

        for fn in self._action_enumerators:
            try:
                extra = fn(self, player_id)
                if extra: actions.extend(extra)
            except Exception as exc:
                self._plugin_error(f"enumerator:{getattr(fn, '__name__', type(fn).__name__)}", exc)

        return actions

    def apply_action(self, player_id: int, action: Action) -> ActionResult:
        handler = self._action_handlers.get(action.type)
        if handler:
            depth = getattr(self, "_mutation_depth", 0)
            self._mutation_depth = depth + 1
            try:
                res = handler(self, player_id, action)
                return res if isinstance(res, ActionResult) else ActionResult(True)
            except Exception as exc:
                self._plugin_error(f"action:{action.type}", exc)
                return ActionResult(False, f"Ошибка плагина в действии {action.type}")
            finally:
                self._mutation_depth = depth
                if depth == 0:
                    self.state_version += 1
                    self._cache.clear()
                    self._sync_vp_progress()

        p = action.p
        t = action.type
        if t == "setup_outpost": return self.setup_place_outpost(player_id, p["vertex_id"])
        elif t == "setup_route": return self.setup_place_route(player_id, p["edge_id"])
        elif t == "roll_dice": return self.roll_dice(player_id)
        elif t == "discard": return self.discard_resources(player_id, p.get("resources", {}))
        elif t == "move_raider": return self.move_raider(player_id, p["hex_id"])
        elif t == "choose_victim": return self.choose_raider_victim(player_id, p["victim_id"])
        elif t == "propose_trade": return self.propose_trade(player_id, p.get("give", {}), p.get("want", {}))
        elif t == "respond_trade": return self.respond_trade(player_id, p.get("accept", False))
        elif t == "cancel_trade": return self.cancel_trade(player_id)
        elif t == "trade_bank": return self.trade_bank(player_id, p["give"], p["receive"])
        elif t == "buy_charter": return self.buy_charter(player_id)
        elif t == "play_charter": return self.play_charter(player_id, p.get("charter_type", ""), p)
        elif t == "build_route": return self.build_route(player_id, p["edge_id"])
        elif t == "build_outpost": return self.build_outpost(player_id, p["vertex_id"])
        elif t == "build_metropolis": return self.build_metropolis(player_id, p["vertex_id"])
        elif t == "end_turn": return self.end_turn(player_id)

        return ActionResult(False, f"Неизвестное действие: {t}")

    def get_observation(self, player_id: int, include_legal_actions: bool = False) -> dict[str, Any]:
        return self.get_state_for_player(player_id, include_legal_actions=include_legal_actions)

    def get_state_for_player(self, player_id: int, include_legal_actions: bool = False) -> dict[str, Any]:
        if not self._valid_pid(player_id): return {"error": "Неверный player_id"}
        player = self.players[player_id]

        def vertex_visible(v: Vertex) -> bool:
            if not self.fog_of_war: return True
            return v.structure_owner == player_id or any(h in player.revealed_hexes for h in v.adjacent_hexes)

        def edge_visible(e: Edge) -> bool:
            if not self.fog_of_war: return True
            return e.route_owner == player_id or any(h in player.revealed_hexes for h in e.adjacent_hexes)

        players_public = []
        for p in self.players:
            d = p.to_dict_public()
            if p.player_id == player_id or self.phase == GamePhase.FINISHED:
                # Hidden triumph cards are secret during play, but final
                # standings should show the actual result to every participant.
                d["victory_points"] = self.get_victory_points(p.player_id, public=False)
                d["longest_route"] = self._calculate_longest_route(p.player_id)
            else:
                d["victory_points"] = self.get_victory_points(p.player_id, public=True)
                d["longest_route"] = None if self.fog_of_war else self._calculate_longest_route(p.player_id)
            d["has_longest_route"] = self.longest_route_player == p.player_id
            d["has_grand_garrison"] = self.grand_garrison_player == p.player_id
            players_public.append(d)

        my_data = player.to_dict_private()
        my_data["victory_points"] = self.get_victory_points(player_id, public=False)

        trade_data = None
        if self.active_trade_offer:
            o = self.active_trade_offer
            can_accept = (
                player_id != o.from_player
                and player.has_resources(o.want)
                and self.players[o.from_player].has_resources(o.give)
            )
            trade_data = {
                "from_player": o.from_player,
                "from_name": self.players[o.from_player].name,
                "give": {r.value: amt for r, amt in o.give.items()},
                "want": {r.value: amt for r, amt in o.want.items()},
                "rejected_by_me": player_id in o.rejected_by,
                "can_accept": can_accept,
            }

        legal_targets = self._build_legal_targets(player_id)
        legal_actions_raw = self.enumerate_legal_actions(player_id)
        available_action_types = sorted({a.type for a in legal_actions_raw})
        legal_actions_payload = [a.to_dict() for a in legal_actions_raw] if include_legal_actions else None

        state = {
            "room_id": self.room_id,
            "phase": self.phase.value,
            "turn": self.global_turn,
            "current_player": self.current_player_id,
            "dice": list(self.dice_result),
            "hexes": [
                self.map.hexes[hid].to_dict(revealed=(hid in player.revealed_hexes))
                for hid in sorted(self.map.hexes)
            ],
            "vertices": [
                self.map.vertices[vid].to_dict()
                for vid in sorted(self.map.vertices)
                if vertex_visible(self.map.vertices[vid])
            ],
            "edges": [
                self.map.edges[eid].to_dict()
                for eid in sorted(self.map.edges)
                if edge_visible(self.map.edges[eid])
            ],
            "players": players_public,
            "me": my_data,
            "my_id": player_id,
            "log": self.log[-20:],
            "available_actions": available_action_types,
            "legal_targets": legal_targets,
            "legal_actions": legal_actions_payload,
            "pending_raider_victims": (
                self.pending_raider_victims
                if self.phase == GamePhase.RAIDER_STEAL and player_id == self.current_player_id
                else []
            ),
            "points_to_win": self.points_to_win,
            "pending_discards": self.pending_discards.get(player_id, 0) if self.phase == GamePhase.DISCARD else 0,
            "trade_offer": trade_data,
            "winner_id": self.winner_id,
            "charters_remaining": len(self.charter_deck),
            "rules": {
                "fog_of_war": self.fog_of_war,
                "fast_start": self.fast_start,
                "friendly_raider": self.friendly_raider,
            },
        }
        return self.pipe("modify_observation", state, player_id)

    def _build_legal_targets(self, player_id: int) -> dict[str, list[int]]:
        out = {
            "setup_outpost_vertices": [],
            "setup_route_edges": [],
            "build_route_edges": [],
            "build_outpost_vertices": [],
            "build_metropolis_vertices": [],
            "move_raider_hexes": [],
        }
        if self.phase == GamePhase.SETUP and self.setup_order and self.setup_order[self.setup_index] == player_id:
            out["setup_outpost_vertices"] = [
                vid for vid, v in self.map.vertices.items()
                if v.is_empty and all(self.map.vertices[adj].is_empty for adj in v.adjacent_vertices)
            ]
        elif (self.phase == GamePhase.SETUP_ROUTE and self.last_setup_vertex is not None
              and self.setup_order and self.setup_order[self.setup_index] == player_id):
            out["setup_route_edges"] = [
                eid for eid in self.map.vertices[self.last_setup_vertex].adjacent_edges
                if self.map.edges[eid].is_empty
            ]
        elif self.phase == GamePhase.MAIN and player_id == self.current_player_id:
            out["build_route_edges"] = list(self._legal_route_edges(player_id))
            out["build_outpost_vertices"] = list(self._legal_outpost_vertices(player_id))
            out["build_metropolis_vertices"] = list(self._legal_metropolis_vertices(player_id))
        elif self.phase == GamePhase.RAIDER_MOVE and player_id == self.current_player_id:
            out["move_raider_hexes"] = [hid for hid in self.map.hexes if hid != self.raider_hex]
        return out

    def _serialize_rng_state(self) -> dict[str, Any]:
        version, internal, gauss = self.rng.getstate()
        return {"version": version, "internal": list(internal), "gauss": gauss}

    @staticmethod
    def _deserialize_rng_state(data: dict[str, Any]) -> tuple:
        return (int(data["version"]), tuple(int(x) for x in data["internal"]), data.get("gauss"))

    def _map_signature(self) -> str:
        payload = {
            "radius": self.map.radius,
            "hexes": [
                (hid, t.q, t.r, t.terrain.value, t.number_token)
                for hid, t in sorted(self.map.hexes.items())
            ],
            "vertices": [
                (vid, tuple(sorted(v.adjacent_hexes)), tuple(sorted(v.adjacent_vertices)),
                 tuple(sorted(v.adjacent_edges)), v.is_port,
                 v.port_type.value if v.port_type else None)
                for vid, v in sorted(self.map.vertices.items())
            ],
            "edges": [
                (eid, tuple(e.vertices), tuple(sorted(e.adjacent_hexes)),
                 e.is_port_edge, e.port_type.value if e.port_type else None)
                for eid, e in sorted(self.map.edges.items())
            ],
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def clone(self) -> "HexGame":
        return self.__class__.from_snapshot(self.to_snapshot(), plugins=list(self._plugins))

    def to_snapshot(self) -> dict[str, Any]:
        snap: dict[str, Any] = {
            "room_id": self.room_id,
            "seed": self.seed,
            "max_players": self.max_players,
            "points_to_win": self.points_to_win,
            "training_mode": self.training_mode,
            "silent": self.silent,
            "mods": {
                "fog_of_war": self.fog_of_war,
                "fast_start": self.fast_start,
                "friendly_raider": self.friendly_raider,
            },
            "map_radius": self.map.radius,
            "map_signature": self._map_signature(),
            "map_static_hexes": [
                {"id": hid, "terrain": t.terrain.value, "number": t.number_token}
                for hid, t in sorted(self.map.hexes.items())
            ],
            "rng_state": self._serialize_rng_state(),
            "phase": self.phase.value,
            "global_turn": self.global_turn,
            "current_player_id": self.current_player_id,
            "dice_result": list(self.dice_result),
            "winner_id": self.winner_id,
            "raider_hex": self.raider_hex,
            "longest_route_player": self.longest_route_player,
            "grand_garrison_player": self.grand_garrison_player,
            "setup_index": self.setup_index,
            "setup_order": list(self.setup_order),
            "turn_order": list(self.turn_order),
            "last_setup_vertex": self.last_setup_vertex,
            "last_vp_progress_turn": self.last_vp_progress_turn,
            "pending_discards": dict(self.pending_discards),
            "pending_raider_victims": list(self.pending_raider_victims),
            "charter_deck": [c.value for c in self.charter_deck],
            "log": copy.deepcopy(self.log),
            "log_counter": self.log_counter,
            "active_trade_offer": (
                {
                    "from_player": self.active_trade_offer.from_player,
                    "give": {r.value: a for r, a in self.active_trade_offer.give.items()},
                    "want": {r.value: a for r, a in self.active_trade_offer.want.items()},
                    "rejected_by": sorted(self.active_trade_offer.rejected_by),
                } if self.active_trade_offer is not None else None
            ),
            "players": [
                {
                    "player_id": p.player_id, "name": p.name, "color": p.color,
                    "is_bot": p.player_id in self.bot_pids,
                    "connected": p.connected,
                    "resources": {r.value: a for r, a in p.resources.items()},
                    "charters": [
                        {"type": c.charter_type.value, "bought_turn": c.bought_turn}
                        for c in p.charters
                    ],
                    "vanguards_played": p.vanguards_played,
                    "hidden_vp": p.hidden_vp,
                    "outposts_left": p.outposts_left,
                    "metropolises_left": p.metropolises_left,
                    "routes_left": p.routes_left,
                    "revealed_hexes": sorted(p.revealed_hexes),
                    "charter_played_this_turn": p.charter_played_this_turn,
                    "free_routes_pending": p.free_routes_pending,
                    "personal_turns_taken": p.personal_turns_taken,
                    "trade_pact_turn": p.trade_pact_turn,
                }
                for p in self.players
            ],
            "structures": [
                {"vertex_id": vid, "type": v.structure_type.value, "owner": v.structure_owner}
                for vid, v in sorted(self.map.vertices.items())
                if v.structure_type is not None
            ],
            "routes": [
                {"edge_id": eid, "owner": e.route_owner}
                for eid, e in sorted(self.map.edges.items())
                if e.route_owner is not None
            ],
            "hex_raider": [hid for hid, t in sorted(self.map.hexes.items()) if t.has_raider],
        }

        if self.ext: snap["game_ext"] = _freeze(self.ext)
        if any(p.ext for p in self.players): snap["player_ext"] = [_freeze(p.ext) if p.ext else None for p in self.players]
        hex_ext = {hid: _freeze(t.ext) for hid, t in self.map.hexes.items() if t.ext}
        if hex_ext: snap["hex_ext"] = hex_ext
        vertex_ext = {vid: _freeze(v.ext) for vid, v in self.map.vertices.items() if v.ext}
        if vertex_ext: snap["vertex_ext"] = vertex_ext
        edge_ext = {eid: _freeze(e.ext) for eid, e in self.map.edges.items() if e.ext}
        if edge_ext: snap["edge_ext"] = edge_ext

        return snap

    def restore_from_snapshot(self, snap: dict[str, Any]) -> None:
        expected_radius = snap.get("map_radius", self.map.radius)
        if expected_radius != self.map.radius:
            raise ValueError(f"Снапшот несовместим: radius {expected_radius} != {self.map.radius}")

        expected_sig = snap.get("map_signature")
        if expected_sig is not None and expected_sig != self._map_signature():
            raise ValueError("Снапшот несовместим: map_signature не совпадает")

        for item in snap.get("map_static_hexes", []):
            hid = int(item["id"])
            if hid in self.map.hexes:
                self.map.hexes[hid].terrain = TerrainType(item["terrain"])
                self.map.hexes[hid].number_token = int(item["number"])
        self.map._reindex_numbers()

        self.room_id = snap.get("room_id", self.room_id)
        self.max_players = snap.get("max_players", self.max_players)
        self.points_to_win = snap.get("points_to_win", self.points_to_win)
        self.training_mode = snap.get("training_mode", self.training_mode)
        self.silent = snap.get("silent", self.silent)

        rng_state = snap.get("rng_state")
        if rng_state is not None:
            self.rng.setstate(self._deserialize_rng_state(rng_state))

        mods = snap.get("mods", {})
        self.fog_of_war = mods.get("fog_of_war", self.fog_of_war)
        self.fast_start = mods.get("fast_start", self.fast_start)
        self.friendly_raider = mods.get("friendly_raider", self.friendly_raider)

        self.phase = GamePhase(snap["phase"])
        self.global_turn = snap["global_turn"]
        self.current_player_id = snap["current_player_id"]
        self.dice_result = tuple(snap.get("dice_result", [0, 0]))
        self.winner_id = snap.get("winner_id")
        self.raider_hex = snap.get("raider_hex")
        self.longest_route_player = snap.get("longest_route_player")
        self.grand_garrison_player = snap.get("grand_garrison_player")
        self.setup_index = snap.get("setup_index", 0)
        self.setup_order = list(snap.get("setup_order", []))
        self.turn_order = list(snap.get("turn_order", []))
        self.last_setup_vertex = snap.get("last_setup_vertex")
        self.last_vp_progress_turn = int(snap.get("last_vp_progress_turn", 0))
        self._last_vp_signature = ()
        self.pending_discards = {int(k): int(v) for k, v in snap.get("pending_discards", {}).items()}
        self.pending_raider_victims = list(snap.get("pending_raider_victims", []))
        self.charter_deck = [CharterType(v) for v in snap.get("charter_deck", [])]

        self.log = copy.deepcopy(snap.get("log", []))
        self.log_counter = int(snap.get("log_counter", len(self.log)))

        offer_data = snap.get("active_trade_offer")
        if offer_data:
            self.active_trade_offer = TradeOffer(
                from_player=int(offer_data["from_player"]),
                give={Resource(r): int(a) for r, a in offer_data["give"].items()},
                want={Resource(r): int(a) for r, a in offer_data["want"].items()},
            )
            self.active_trade_offer.rejected_by = set(int(x) for x in offer_data.get("rejected_by", []))
        else:
            self.active_trade_offer = None

        self.players.clear()
        self.bot_pids.clear()

        player_ext = snap.get("player_ext")
        for idx, pd in enumerate(snap["players"]):
            p = Player(
                player_id=int(pd["player_id"]),
                name=pd["name"],
                color=pd.get("color", self.colors[int(pd["player_id"]) % len(self.colors)]),
                resources={Resource(r): int(a) for r, a in pd["resources"].items()},
                vanguards_played=int(pd["vanguards_played"]),
                hidden_vp=int(pd["hidden_vp"]),
                outposts_left=int(pd["outposts_left"]),
                metropolises_left=int(pd["metropolises_left"]),
                routes_left=int(pd["routes_left"]),
                revealed_hexes=set(int(h) for h in pd.get("revealed_hexes", [])),
                charter_played_this_turn=bool(pd.get("charter_played_this_turn", False)),
                connected=bool(pd.get("connected", True)),
                free_routes_pending=int(pd.get("free_routes_pending", 0)),
                personal_turns_taken=int(pd.get("personal_turns_taken", 0)),
                trade_pact_turn=int(pd.get("trade_pact_turn", -1)),
            )
            if player_ext and idx < len(player_ext) and player_ext[idx] is not None:
                p.ext = _thaw(player_ext[idx])
            for cd in pd.get("charters", []):
                p.charters.append(CharterInstance(
                    charter_type=CharterType(cd["type"]), bought_turn=int(cd["bought_turn"]),
                ))
            self.players.append(p)
            if pd.get("is_bot", False):
                self.bot_pids.add(p.player_id)

        for v in self.map.vertices.values():
            v.structure_type = None
            v.structure_owner = None
            v.ext = {}
        for e in self.map.edges.values():
            e.route_owner = None
            e.ext = {}
        for t in self.map.hexes.values():
            t.has_raider = False
            t.ext = {}

        for s in snap.get("structures", []):
            vid = int(s["vertex_id"])
            if vid in self.map.vertices:
                self.map.vertices[vid].structure_type = StructureType(s["type"])
                self.map.vertices[vid].structure_owner = int(s["owner"])

        for r in snap.get("routes", []):
            eid = int(r["edge_id"])
            if eid in self.map.edges:
                self.map.edges[eid].route_owner = int(r["owner"])

        for hid in snap.get("hex_raider", []):
            hid = int(hid)
            if hid in self.map.hexes:
                self.map.hexes[hid].has_raider = True

        for vid, raw in snap.get("vertex_ext", {}).items():
            vid = int(vid)
            if vid in self.map.vertices: self.map.vertices[vid].ext = _thaw(raw)
        for eid, raw in snap.get("edge_ext", {}).items():
            eid = int(eid)
            if eid in self.map.edges: self.map.edges[eid].ext = _thaw(raw)
        for hid, raw in snap.get("hex_ext", {}).items():
            hid = int(hid)
            if hid in self.map.hexes: self.map.hexes[hid].ext = _thaw(raw)

        self.ext = _thaw(snap["game_ext"]) if "game_ext" in snap else {}
        self.seed = snap.get("seed", self.seed)
        self.state_version = 0
        self._cache.clear()
        self._mutation_depth = 0

    @classmethod
    def from_snapshot(cls, snap: dict[str, Any], plugins: Optional[list[Any]] = None) -> "HexGame":
        mods = dict(snap.get("mods", {}))
        game = cls(
            room_id=snap["room_id"],
            max_players=snap.get("max_players", 4),
            points_to_win=snap.get("points_to_win", 12),
            seed=snap.get("seed"),
            training_mode=snap.get("training_mode", False),
            silent=snap.get("silent", False),
            plugins=plugins,
            **mods,
        )
        game.restore_from_snapshot(snap)
        return game

    def state_hash(self) -> str:
        raw = json.dumps(self.to_snapshot(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    # ИГРОВОЙ ЦИКЛ С АВТОМАТИЧЕСКИМ РАЗРЕШЕНИЕМ СТАГНАЦИИ ПО VP
    def run_to_completion(self, choose_action_fn: Callable[[int, list[Action]], Action], max_steps: int = 5000) -> Optional[int]:
        for _ in range(max_steps):
            if self.phase == GamePhase.FINISHED:
                return self.winner_id

            # СРАБАТЫВАНИЕ СТОРОЖЕВОГО ТАЙМЕРА ПО ПРИРОСТУ VP (160 ХОДОВ)
            if self.is_stalled():
                leader = self._current_leader_id()
                if leader is not None:
                    self.phase = GamePhase.FINISHED
                    self.winner_id = leader
                    self.ext["stalled_out"] = True
                    self._add_log(f"⏳ Партия завершена по лимиту стагнации очков (160 ходов без VP): {self.players[leader].name} лидирует.", leader)
                    return self.winner_id

            if self.phase == GamePhase.DISCARD:
                for pid in list(self.pending_discards.keys()):
                    if self.phase != GamePhase.DISCARD: break
                    legal = self.enumerate_legal_actions(pid)
                    if not legal: continue
                    act = choose_action_fn(pid, legal)
                    self.apply_action(pid, act)
                continue

            if self.phase == GamePhase.MAIN and self.active_trade_offer is not None:
                offer = self.active_trade_offer
                responders = [p for p in self.players if p.player_id != offer.from_player and p.player_id not in offer.rejected_by]
                self.rng.shuffle(responders)
                for p in responders:
                    if self.active_trade_offer is None: break
                    legal = self.enumerate_legal_actions(p.player_id)
                    trade_acts = [a for a in legal if a.type == "respond_trade"]
                    if trade_acts:
                        act = choose_action_fn(p.player_id, trade_acts)
                        self.apply_action(p.player_id, act)
                if self.active_trade_offer is not None:
                    self.cancel_trade(offer.from_player)
                continue

            pid = self.current_player_id
            legal = self.enumerate_legal_actions(pid)
            if not legal:
                if self.phase == GamePhase.MAIN:
                    self.end_turn(pid)
                else:
                    break
                continue

            act = choose_action_fn(pid, legal)
            self.apply_action(pid, act)

        return self.winner_id if self.phase == GamePhase.FINISHED else None