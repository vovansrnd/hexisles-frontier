#!/usr/bin/env python3
"""HexIsles local play server — Engine v3 + Bots v6.

Uses isles_core.py, ai_brain.py and mods_pack.py from this directory. The HTTP
listener serves the tree selected by HEXISLES_STATIC (static_v4 by default;
run_local_v5.sh selects static_v5); gameplay uses WebSocket. The default bind
address is 0.0.0.0, so the server may be reachable from the local network. There
is no account-based authentication: do not expose it directly to the public Internet.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import secrets
import sqlite3
import string
import sys
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from isles_core import Action, ActionResult, GamePhase, HexGame
from ai_brain import BotBrain
from mods_pack import BlightMod, TradeRoutesMod
from match_archive import MatchArchive, MatchSession

try:
    from websockets.asyncio.server import serve as ws_serve
except ImportError:  # websockets < 13
    from websockets import serve as ws_serve

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / os.environ.get("HEXISLES_STATIC", "static_v4")
HTTP_HOST = os.environ.get("HEXISLES_HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HEXISLES_HTTP_PORT", "8080"))
WS_HOST = os.environ.get("HEXISLES_WS_HOST", "0.0.0.0")
WS_PORT = int(os.environ.get("HEXISLES_WS_PORT", "8765"))
BOT_STEP_DELAY = max(0.02, float(os.environ.get("HEXISLES_BOT_DELAY", "0.32")))
TRADE_STEP_DELAY = max(0.02, float(os.environ.get("HEXISLES_TRADE_DELAY", "0.35")))
ROOM_IDLE_TTL = max(30.0, float(os.environ.get("HEXISLES_ROOM_TTL", "900")))
DEBUG = os.environ.get("HEXISLES_DEBUG", "0") == "1"

# UI id -> name shown in the game -> strategy archetype in ai_brain.py
BOT_TYPES: dict[str, dict[str, str]] = {
    "c3po": {"name": "🤖 C3PO", "archetype": "human", "description": "Стратег"},
    "r2d2": {"name": "🤖 R2D2", "archetype": "merchant", "description": "Торговец"},
    "t800": {"name": "🤖 T-800", "archetype": "t800", "description": "Агрессор"},
    "walle": {"name": "🤖 Валл-И", "archetype": "chaos", "description": "Хаос"},
}

ROOMS: dict[str, HexGame] = {}
CONNECTIONS: dict[Any, tuple[str, int]] = {}
ROOM_CONNECTIONS: dict[str, dict[int, Any]] = {}
ROOM_TOKENS: dict[tuple[str, int], str] = {}
ROOM_LOCKS: dict[str, asyncio.Lock] = {}
DRIVE_TASKS: dict[str, asyncio.Task] = {}
CLEANUP_TASKS: dict[str, asyncio.Task] = {}
TRADE_TTL = max(5.0, float(os.environ.get("HEXISLES_TRADE_TTL", "25")))
TRADE_INVALID_TTL = 3.0
BOT_TRADE_MIN = 3.0
BOT_TRADE_MAX = 5.0
TRADE_ORDERS: dict[str, dict[str, Any]] = {}

# Completed-match archive. Sessions stay in memory during play and are written
# atomically only after the engine reaches FINISHED. Set
# HEXISLES_MATCH_ARCHIVE=0 to disable; HEXISLES_DB_PATH selects the SQLite path.
MATCH_ARCHIVE: MatchArchive | None = None
MATCH_ARCHIVE_INIT_ATTEMPTED = False
MATCH_SESSIONS: dict[str, MatchSession] = {}


def _get_match_archive() -> MatchArchive | None:
    global MATCH_ARCHIVE, MATCH_ARCHIVE_INIT_ATTEMPTED
    if os.environ.get("HEXISLES_MATCH_ARCHIVE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    if MATCH_ARCHIVE is not None:
        return MATCH_ARCHIVE
    if MATCH_ARCHIVE_INIT_ATTEMPTED:
        return None
    MATCH_ARCHIVE_INIT_ATTEMPTED = True
    configured = os.environ.get("HEXISLES_DB_PATH", "").strip()
    db_path = Path(configured).expanduser() if configured else ROOT / "data" / "hexisles.sqlite3"
    if not db_path.is_absolute():
        db_path = (ROOT / db_path).resolve()
    try:
        MATCH_ARCHIVE = MatchArchive(db_path)
        print(f"[archive] completed-match logging enabled: {MATCH_ARCHIVE.path}", flush=True)
        return MATCH_ARCHIVE
    except Exception as exc:
        # Archiving must not crash or stall a game if the filesystem is read-only.
        print(f"[archive] ERROR: cannot initialise match archive at {db_path}: {exc}", file=sys.stderr, flush=True)
        return None


def _start_match_recording(room_id: str, game: HexGame) -> None:
    """Begin a detailed in-memory journal after a match successfully starts."""
    if game.phase in (GamePhase.LOBBY, GamePhase.FINISHED) or _get_match_archive() is None:
        return
    previous = MATCH_SESSIONS.get(room_id)
    if previous is not None:
        _dump_abandoned_sessions("replaced by a new match in the same room")
        print(f"[archive] WARNING: replacing an unfinished recording for room {room_id}", file=sys.stderr, flush=True)
    try:
        session = MatchSession(game, game.ext.get("local_server_config", {}))
        session.live_game = game
        MATCH_SESSIONS[room_id] = session
        session.record(
            game,
            event_type="match_started",
            success=True,
            meta={"source": "server", "config": game.ext.get("local_server_config", {})},
        )
    except Exception as exc:
        MATCH_SESSIONS.pop(room_id, None)
        print(f"[archive] ERROR: cannot start recording room {room_id}: {exc}", file=sys.stderr, flush=True)


def _finish_match_recording(room_id: str, game: HexGame) -> None:
    """Persist a finished match once; a failed save remains available for retry."""
    session = MATCH_SESSIONS.get(room_id)
    archive = _get_match_archive()
    if session is None or archive is None or game.phase != GamePhase.FINISHED:
        return
    if not session.finalizing:
        session.finalizing = True
        try:
            session.record(
                game,
                event_type="match_finished",
                success=True,
                meta={"winner_player_id": game.winner_id},
            )
        except Exception as exc:
            session.finalizing = False
            print(f"[archive] ERROR: cannot close recording for room {room_id}: {exc}", file=sys.stderr, flush=True)
            return
    try:
        package = session.package(game)
        inserted = archive.save_completed_match(package)
        MATCH_SESSIONS.pop(room_id, None)
        if inserted:
            print(
                f"[archive] saved match {package['match_id'][:10]}: "
                f"{package['global_turns']} turns, {package['event_count']} events",
                flush=True,
            )
    except Exception as exc:
        print(f"[archive] ERROR: cannot persist finished room {room_id}: {exc}", file=sys.stderr, flush=True)


def _record_match_event(
    room_id: str,
    game: HexGame,
    *,
    event_type: str,
    actor_id: int | None = None,
    action_type: str | None = None,
    params: dict[str, Any] | None = None,
    success: bool | None = None,
    error: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Record a state-changing action without allowing telemetry to break play."""
    session = MATCH_SESSIONS.get(room_id)
    if session is None:
        return
    if session.finalizing:
        if game.phase == GamePhase.FINISHED:
            _finish_match_recording(room_id, game)
        return
    try:
        session.record(
            game,
            event_type=event_type,
            actor_id=actor_id,
            action_type=action_type,
            params=params,
            success=success,
            error=error,
            meta=meta,
        )
    except Exception as exc:
        print(f"[archive] ERROR: cannot record {event_type} in room {room_id}: {exc}", file=sys.stderr, flush=True)
        return
    if game.phase == GamePhase.FINISHED:
        _finish_match_recording(room_id, game)


def dprint(*args: Any) -> None:
    if DEBUG:
        print("[debug]", *args, flush=True)


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def safe_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        number = int(value)
        return max(low, min(high, number))
    except (TypeError, ValueError, OverflowError):
        return default


def action_params_match(expected: Any, submitted: Any) -> bool:
    """Compare action params as they survive the JSON round trip.

    Engine actions can hold tuples (notably route edge paths) while JSON
    submissions return arrays. Canonical JSON makes tuple/list wire-equivalent
    without weakening scalar or nested-object validation.
    """
    try:
        return json.dumps(expected, sort_keys=True, ensure_ascii=False, separators=(",", ":")) == json.dumps(
            submitted, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
    except (TypeError, ValueError, OverflowError):
        return False


def get_lock(room_id: str) -> asyncio.Lock:
    if room_id not in ROOM_LOCKS:
        ROOM_LOCKS[room_id] = asyncio.Lock()
    return ROOM_LOCKS[room_id]


def new_room_id() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def bump_state(game: HexGame) -> None:
    game.state_version += 1
    cache = getattr(game, "_cache", None)
    if isinstance(cache, dict):
        cache.clear()


def result_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, ActionResult):
        data = dict(result.data or {})
        data["ok"] = bool(result.success)
        if not result.success:
            data["error"] = result.error or "Действие не выполнено"
        return data
    if isinstance(result, dict):
        return result
    return {"ok": bool(result)}


def make_brain(game: HexGame, player_id: int, bot_type: str) -> BotBrain:
    config = BOT_TYPES[bot_type]
    return BotBrain(
        player_id=player_id,
        game=game,
        archetype=config["archetype"],
        difficulty=getattr(game, "bot_difficulty", "normal"),
    )


def add_bot(game: HexGame, bot_type: str) -> tuple[bool, str]:
    if bot_type not in BOT_TYPES:
        return False, "Неизвестный тип бота"
    if len(game.players) >= game.max_players:
        return False, "В комнате уже четыре игрока"
    config = BOT_TYPES[bot_type]
    base_name = config["name"]
    used = {p.name for p in game.players}
    name = base_name
    suffix = 2
    while name in used:
        name = f"{base_name} {suffix}"
        suffix += 1
    player = game.add_bot(name)
    if player is None:
        return False, "Не удалось добавить бота"
    if not hasattr(game, "bot_brains"):
        game.bot_brains = {}
    game.bot_brains[player.player_id] = make_brain(game, player.player_id, bot_type)
    # Keep the archetype separately. Names with suffixes remain unambiguous.
    game.ext.setdefault("bot_types", {})[str(player.player_id)] = bot_type
    return True, name


def add_human(game: HexGame, name: str) -> tuple[int, Any]:
    # Engine v3 exposes the validated mutator as _add_player_internal; there is
    # intentionally no separate add_player() public method in this core version.
    result = game._add_player_internal(name, False)
    if not result.success:
        raise ValueError(result.error or "Не удалось добавить игрока")
    return int(result.data["player_id"]), result.data["player"]


def make_game(room_id: str, config: dict[str, Any], seed: int | None = None) -> HexGame:
    """Build a game from validated lobby options, without changing safe defaults."""
    plugins = []
    blight_mode = str(config.get("blight_mode", "tactical")).strip().lower()
    if blight_mode not in BlightMod.MODES:
        blight_mode = "tactical"
    cult_visibility = str(config.get("cult_visibility", "open")).strip().lower()
    if cult_visibility not in {"open", "hidden"}:
        cult_visibility = "open"
    routes_enabled = bool(config.get("routes", False))
    try:
        chance = float(config.get("blight_spread_chance", 0.40))
    except (TypeError, ValueError, OverflowError):
        chance = 0.40
    chance = max(0.0, min(1.0, chance))
    if config.get("blight", True):
        # cult_suppression_theft is a house rule (off in the shipped balance);
        # pass it through so archived configs and simulations reproduce games.
        plugins.append(BlightMod(
            spread_chance=chance,
            mode=blight_mode,
            cult_visibility=cult_visibility,
            cult_suppression_theft=bool(config.get("cult_suppression_theft", False)),
        ))
    if routes_enabled:
        plugins.append(TradeRoutesMod())

    game = HexGame(
        room_id=room_id,
        max_players=4,
        points_to_win=safe_int(config.get("points_to_win", 12), 12, 5, 20),
        seed=seed,
        fog_of_war=bool(config.get("fog_of_war", True)),
        fast_start=bool(config.get("fast_start", False)),
        friendly_raider=bool(config.get("friendly_raider", True)),
        silent=False,
        training_mode=False,
        plugins=plugins,
    )
    difficulty = str(config.get("bot_difficulty", "skynet")).strip().lower()
    if difficulty not in {"easy", "normal", "hard", "tournament", "skynet"}:
        difficulty = "skynet"
    game.bot_difficulty = difficulty
    game.bot_brains = {}
    normalized = dict(config)
    normalized.update({
        "points_to_win": safe_int(config.get("points_to_win", 12), 12, 5, 20),
        "fog_of_war": bool(config.get("fog_of_war", True)),
        "fast_start": bool(config.get("fast_start", False)),
        "friendly_raider": bool(config.get("friendly_raider", True)),
        "blight": bool(config.get("blight", True)),
        "blight_spread_chance": chance,
        "blight_mode": blight_mode,
        "cult_visibility": cult_visibility,
        "cult_suppression_theft": bool(config.get("cult_suppression_theft", False)),
        "routes": routes_enabled,
        "bot_difficulty": difficulty,
    })
    game.ext["local_server_config"] = normalized
    game.ext["bot_types"] = {}
    return game


def state_for(game: HexGame, player_id: int) -> dict[str, Any]:
    state = game.get_state_for_player(player_id, include_legal_actions=True)
    if "error" in state:
        return state

    state["state_version"] = int(getattr(game, "state_version", 0))
    state["bot_pids"] = sorted(game.bot_pids)
    state["bot_types"] = dict(game.ext.get("bot_types", {}))
    state["is_host"] = player_id == 0
    state["bank_rates"] = {
        resource.value: int(game._bank_rate(player_id, resource))
        for resource in game.players[player_id].resources
    }
    state["rules"] = dict(state.get("rules", {}))
    config = getattr(game, "ext", {}).get("local_server_config", {})
    blight_mode = str(config.get("blight_mode", "tactical"))
    cult_visibility = str(config.get("cult_visibility", "open"))
    state["rules"].update({
        "blight": bool(config.get("blight", True)),
        "routes": bool(config.get("routes", False)),
        "blight_spread_chance": float(config.get("blight_spread_chance", 0.40)),
        "blight_mode": blight_mode,
        "cult_visibility": cult_visibility,
    })
    if state["rules"]["routes"]:
        def route_summary(route: Any, *, include_path: bool = False) -> dict[str, Any]:
            if not isinstance(route, dict):
                return {}
            path = route.get("edge_path", ())
            summary = {
                "start_vid": route.get("start_vid"),
                "end_vid": route.get("end_vid"),
                "edge_count": len(path) if isinstance(path, (list, tuple)) else 0,
                "trade_resource": str(route.get("trade_resource", "")),
                "suppressed_until": int(route.get("suppressed_until", -1)),
                "activated_turn": int(route.get("activated_turn", 0)),
                "income_eligible_turn": int(route.get("income_eligible_turn", 0)),
                "last_income_turn": int(route.get("last_income_turn", -1)),
            }
            if include_path and isinstance(path, (list, tuple)):
                summary["edge_path"] = [int(eid) for eid in path]
            return summary
        for view, player in zip(state.get("players", []), game.players):
            # Route existence is public, but a hidden player's exact edge path
            # stays private under fog-of-war. The current player always gets theirs.
            view["active_routes"] = [
                route_summary(route, include_path=(not game.fog_of_war or player.player_id == player_id))
                for route in player.ext.get("active_routes", []) if isinstance(route, dict)
            ]
        state.setdefault("me", {})["active_routes"] = [
            route_summary(route, include_path=True)
            for route in game.players[player_id].ext.get("active_routes", []) if isinstance(route, dict)
        ]

    # Legal action lists can include boundary vertices/edges that the engine's
    # default observation omits because they touch only hidden hexes. Add only
    # the coordinates needed to click those legal targets; don't expose hidden
    # occupancy, port information, terrain, resource or blight state.
    visible_vertices = {int(v["vertex_id"]): v for v in state.get("vertices", [])}
    visible_edges = {int(e["edge_id"]): e for e in state.get("edges", [])}
    legal_actions = state.get("legal_actions") or []
    target_vertex_ids: set[int] = set()
    target_edge_ids: set[int] = set()
    for action in legal_actions:
        params = action.get("params") or {}
        if action.get("type") in ("setup_outpost", "build_outpost", "build_metropolis"):
            try:
                target_vertex_ids.add(int(params["vertex_id"]))
            except (KeyError, TypeError, ValueError):
                pass
        elif action.get("type") in ("setup_route", "build_route"):
            try:
                target_edge_ids.add(int(params["edge_id"]))
            except (KeyError, TypeError, ValueError):
                pass

    def add_hidden_vertex_placeholder(vertex_id: int) -> None:
        if vertex_id in visible_vertices or vertex_id not in game.map.vertices:
            return
        item = game.map.vertices[vertex_id].to_dict()
        # Preserve geometry only. The true structure/port data remains hidden.
        item.update({"structure_type": None, "structure_owner": None, "is_port": False, "port_type": None})
        visible_vertices[vertex_id] = item

    for vertex_id in target_vertex_ids:
        add_hidden_vertex_placeholder(vertex_id)
    for edge_id in target_edge_ids:
        raw_edge = game.map.edges.get(edge_id)
        if raw_edge is None:
            continue
        if edge_id not in visible_edges:
            visible_edges[edge_id] = {"edge_id": raw_edge.edge_id, "vertices": list(raw_edge.vertices), "route_owner": None}
        for vertex_id in raw_edge.vertices:
            add_hidden_vertex_placeholder(int(vertex_id))

    state["vertices"] = [visible_vertices[key] for key in sorted(visible_vertices)]
    state["edges"] = [visible_edges[key] for key in sorted(visible_edges)]

    # Keep trade expiry coherent between the server and client countdown.
    offer = game.active_trade_offer
    if offer is not None:
        meta = TRADE_ORDERS.get(game.room_id)
        created = float(meta.get("created", 0.0)) if meta else 0.0
        now_ts = time.monotonic()
        invalid_since = float(meta.get("invalid_since")) if meta and meta.get("invalid_since") is not None else None
        if invalid_since is not None:
            state["trade_ttl"] = round(max(0.0, TRADE_INVALID_TTL - (now_ts - invalid_since)), 1)
        else:
            state["trade_ttl"] = round(max(0.0, TRADE_TTL - (now_ts - created)), 1) if created else TRADE_TTL
        state["trade_invalid"] = invalid_since is not None

    # Keep public raider position visible even when the underlying hex is fogged;
    # do not reveal any additional terrain, token, resource or Blight details.
    # Blight stage itself is exposed only for sectors the observer already knows.
    revealed = set(game.players[player_id].revealed_hexes)
    for tile in state.get("hexes", []):
        hid = tile.get("hex_id")
        raw = game.map.hexes.get(hid)
        if raw is not None and raw.has_raider:
            tile["has_raider"] = True
        if tile.get("revealed") and hid in revealed:
            tile["blight_stage"] = int(raw.ext.get("blight_stage", 0)) if raw else 0
    for public_player in state.get("players", []):
        pid = int(public_player["player_id"])
        public_player["is_bot"] = pid in game.bot_pids
        public_player["bot_type"] = game.ext.get("bot_types", {}).get(str(pid))
    return state


async def send_json(ws: Any, payload: dict[str, Any]) -> None:
    try:
        await ws.send(dumps(payload))
    except Exception:
        pass


async def send_error(ws: Any, message: str) -> None:
    await send_json(ws, {"type": "error", "message": message})


async def broadcast_room(room_id: str) -> None:
    async with get_lock(room_id):
        game = ROOMS.get(room_id)
        if game is None:
            return
        targets = list(ROOM_CONNECTIONS.get(room_id, {}).items())
        snapshots = []
        for pid, ws in targets:
            try:
                snapshots.append((pid, ws, state_for(game, pid)))
            except Exception as exc:
                dprint("state build failed", room_id, pid, repr(exc))
    dead: list[tuple[int, Any]] = []
    for pid, ws, state in snapshots:
        try:
            await ws.send(dumps({"type": "state", "data": state}))
        except Exception:
            dead.append((pid, ws))
    for pid, ws in dead:
        if ROOM_CONNECTIONS.get(room_id, {}).get(pid) is ws:
            ROOM_CONNECTIONS[room_id].pop(pid, None)
            CONNECTIONS.pop(ws, None)
            game = ROOMS.get(room_id)
            if game and pid < len(game.players):
                game.players[pid].connected = False
                bump_state(game)
    if ROOM_CONNECTIONS.get(room_id):
        schedule_drive(room_id)
    else:
        schedule_cleanup(room_id)


def schedule_drive(room_id: str) -> None:
    current = DRIVE_TASKS.get(room_id)
    if current and not current.done():
        return
    DRIVE_TASKS[room_id] = asyncio.create_task(drive_room(room_id), name=f"bots:{room_id}")


async def drive_room(room_id: str) -> None:
    this_task = asyncio.current_task()
    try:
        while True:
            game = ROOMS.get(room_id)
            if game is None:
                return
            await asyncio.sleep(TRADE_STEP_DELAY if game.active_trade_offer else BOT_STEP_DELAY)
            did_action = False
            async with get_lock(room_id):
                game = ROOMS.get(room_id)
                if game is None or game.phase in (GamePhase.LOBBY, GamePhase.FINISHED):
                    return

                offer = game.active_trade_offer
                if offer is None:
                    TRADE_ORDERS.pop(room_id, None)
                if offer is not None:
                    now_ts = time.monotonic()
                    meta = TRADE_ORDERS.get(room_id)
                    if meta is None or meta.get("offer") is not offer:
                        pending = [pid for pid in game.bot_pids if pid != offer.from_player]
                        game.rng.shuffle(pending)
                        first_due = now_ts + BOT_TRADE_MIN + game.rng.random() * (BOT_TRADE_MAX - BOT_TRADE_MIN)
                        schedule = {
                            pid: first_due + index * (0.35 + game.rng.random() * 0.45)
                            for index, pid in enumerate(pending)
                        }
                        meta = {"offer": offer, "created": now_ts, "schedule": schedule, "invalid_since": None}
                        TRADE_ORDERS[room_id] = meta

                    sender = game.players[offer.from_player]
                    if not sender.has_resources(offer.give):
                        if meta["invalid_since"] is None:
                            meta["invalid_since"] = now_ts
                            game._add_log(f"⚠️ Предложение обмена от {sender.name} потеряло актуальность")
                            bump_state(game)
                            _record_match_event(
                                room_id, game, event_type="system", actor_id=offer.from_player,
                                success=True, meta={"reason": "trade_offer_invalidated"},
                            )
                            did_action = True
                        elif now_ts - float(meta["invalid_since"]) >= TRADE_INVALID_TTL:
                            game.cancel_trade(offer.from_player)
                            game._add_log("⌛ Предложение снято: у отправителя больше нет нужных ресурсов")
                            TRADE_ORDERS.pop(room_id, None)
                            _record_match_event(
                                room_id, game, event_type="system", actor_id=offer.from_player,
                                action_type="cancel_trade", params={"reason": "sender_lost_resources"},
                                success=True, meta={"source": "timer"},
                            )
                            did_action = True
                    elif now_ts - float(meta["created"]) >= TRADE_TTL:
                        game.cancel_trade(offer.from_player)
                        game._add_log("⌛ Время на ответ вышло — предложение обмена снято")
                        TRADE_ORDERS.pop(room_id, None)
                        _record_match_event(
                            room_id, game, event_type="system", actor_id=offer.from_player,
                            action_type="cancel_trade", params={"reason": "offer_expired"},
                            success=True, meta={"source": "timer"},
                        )
                        did_action = True
                    else:
                        schedule = meta["schedule"]
                        for pid in list(schedule):
                            if pid in offer.rejected_by or pid == offer.from_player:
                                schedule.pop(pid, None)
                        due = [(schedule[pid], pid) for pid in schedule if now_ts >= schedule[pid]]
                        if due:
                            _, bot_pid = min(due)
                            schedule.pop(bot_pid, None)
                            brain = game.bot_brains.get(bot_pid)
                            try:
                                accept = bool(brain.respond_to_trade(offer)) if brain else False
                            except Exception as exc:
                                dprint("bot trade response failed", room_id, bot_pid, repr(exc))
                                accept = False
                            result = game.respond_trade(bot_pid, accept)
                            if not result.success:
                                game._add_log(f"⚠️ Ответ бота на обмен не принят: {result.error}")
                            _record_match_event(
                                room_id, game, event_type="action", actor_id=bot_pid,
                                action_type="respond_trade", params={"accept": accept},
                                success=result.success, error=result.error,
                                meta={"source": "bot", "bot_type": game.ext.get("bot_types", {}).get(str(bot_pid)),
                                      "offer_from": offer.from_player},
                            )
                            did_action = True
                        else:
                            pending_humans = [
                                p.player_id for p in game.players
                                if p.player_id not in game.bot_pids
                                and p.player_id != offer.from_player
                                and p.connected
                                and p.player_id not in offer.rejected_by
                            ]
                            bots_left = any(pid not in offer.rejected_by for pid in meta["schedule"])
                            if not pending_humans and not bots_left:
                                game.cancel_trade(offer.from_player)
                                TRADE_ORDERS.pop(room_id, None)
                                _record_match_event(
                                    room_id, game, event_type="system", actor_id=offer.from_player,
                                    action_type="cancel_trade", params={"reason": "no_responders"},
                                    success=True, meta={"source": "server"},
                                )
                                did_action = True
                            # Otherwise leave the driver alive while awaiting a
                            # scheduled bot response or an actual human response.
                elif game.phase == GamePhase.DISCARD:
                    pending_bots = [pid for pid in game.pending_discards if pid in game.bot_pids]
                    if not pending_bots:
                        return
                    pending_bots.sort()
                    bot_pid = pending_bots[0]
                    legal = game.enumerate_legal_actions(bot_pid)
                    brain = game.bot_brains.get(bot_pid)
                    if not legal:
                        return
                    try:
                        action = brain.choose_action(bot_pid, legal) if brain else legal[0]
                    except Exception as exc:
                        dprint("bot discard decision failed", room_id, bot_pid, repr(exc))
                        action = legal[0]
                    if not any(action is item for item in legal):
                        action = next((item for item in legal if item.type == action.type and item.p == action.p), legal[0])
                    result = game.apply_action(bot_pid, action)
                    if not result.success:
                        dprint("bot discard action failed", room_id, bot_pid, result.error)
                    _record_match_event(
                        room_id, game, event_type="action", actor_id=bot_pid,
                        action_type=action.type, params=action.p,
                        success=result.success, error=result.error,
                        meta={"source": "bot", "bot_type": game.ext.get("bot_types", {}).get(str(bot_pid)),
                              "phase": "discard", "legal_action_count": len(legal)},
                    )
                    did_action = True
                else:
                    pid = game.current_player_id
                    if pid not in game.bot_pids:
                        return  # pause until the human acts
                    legal = game.enumerate_legal_actions(pid)
                    if not legal:
                        if game.phase == GamePhase.MAIN:
                            result = game.end_turn(pid)
                            _record_match_event(
                                room_id, game, event_type="action", actor_id=pid,
                                action_type="end_turn", params={},
                                success=result.success, error=result.error,
                                meta={"source": "bot", "reason": "no_legal_actions"},
                            )
                            did_action = bool(result.success)
                        else:
                            dprint("no legal actions", room_id, game.phase, pid)
                            return
                    else:
                        brain = game.bot_brains.get(pid)
                        try:
                            action = brain.choose_action(pid, legal) if brain else legal[0]
                        except Exception as exc:
                            dprint("bot choose_action failed", room_id, pid, repr(exc))
                            action = legal[0]
                        if not any(action is item for item in legal):
                            action = next((item for item in legal if item.type == action.type and item.p == action.p), legal[0])
                        phase_before_action = game.phase.value
                        result = game.apply_action(pid, action)
                        if not result.success:
                            dprint("bot action rejected", room_id, pid, action.to_dict(), result.error)
                            game._add_log(f"⚠️ Действие {action.type} не выполнено: {result.error or 'ошибка'}", pid)
                        _record_match_event(
                            room_id, game, event_type="action", actor_id=pid,
                            action_type=action.type, params=action.p,
                            success=result.success, error=result.error,
                            meta={"source": "bot", "bot_type": game.ext.get("bot_types", {}).get(str(pid)),
                                  "phase": phase_before_action, "legal_action_count": len(legal)},
                        )
                        if not result.success:
                            # Avoid a hot loop if a bot selected an invalid action.
                            if game.phase == GamePhase.MAIN:
                                fallback = next((item for item in legal if item.type == "end_turn"), None)
                                if fallback is not None:
                                    fallback_result = game.apply_action(pid, fallback)
                                    _record_match_event(
                                        room_id, game, event_type="action", actor_id=pid,
                                        action_type=fallback.type, params=fallback.p,
                                        success=fallback_result.success, error=fallback_result.error,
                                        meta={"source": "server_fallback", "reason": "invalid_bot_action"},
                                    )
                        did_action = True
            if did_action:
                await broadcast_room(room_id)
            else:
                # Trade offers are timer-driven even when no bot is due on this
                # tick; keep the loop alive without broadcasting unchanged state.
                live_game = ROOMS.get(room_id)
                if live_game is not None and live_game.active_trade_offer is not None:
                    continue
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        traceback.print_exc()
    finally:
        if DRIVE_TASKS.get(room_id) is this_task:
            DRIVE_TASKS.pop(room_id, None)


async def delayed_cleanup(room_id: str) -> None:
    try:
        await asyncio.sleep(ROOM_IDLE_TTL)
        if not ROOM_CONNECTIONS.get(room_id):
            ROOMS.pop(room_id, None)
            # Only completed matches are persisted. An abandoned unfinished match
            # is deliberately discarded when its room expires.
            MATCH_SESSIONS.pop(room_id, None)
            ROOM_CONNECTIONS.pop(room_id, None)
            ROOM_LOCKS.pop(room_id, None)
            DRIVE_TASKS.pop(room_id, None)
            TRADE_ORDERS.pop(room_id, None)
            for key in [key for key in ROOM_TOKENS if key[0] == room_id]:
                ROOM_TOKENS.pop(key, None)
            print(f"[room] {room_id} removed after idle timeout", flush=True)
    finally:
        CLEANUP_TASKS.pop(room_id, None)


def schedule_cleanup(room_id: str) -> None:
    old = CLEANUP_TASKS.pop(room_id, None)
    if old and not old.done():
        old.cancel()
    CLEANUP_TASKS[room_id] = asyncio.create_task(delayed_cleanup(room_id))


def cancel_cleanup(room_id: str) -> None:
    old = CLEANUP_TASKS.pop(room_id, None)
    if old and not old.done():
        old.cancel()


async def attach_connection(ws: Any, room_id: str, player_id: int, token: str | None = None) -> str:
    token = token or secrets.token_urlsafe(24)
    old = ROOM_CONNECTIONS.setdefault(room_id, {}).get(player_id)
    if old is not None and old is not ws:
        CONNECTIONS.pop(old, None)
        try:
            await old.close(code=4001, reason="Reconnected from another tab")
        except Exception:
            pass
    CONNECTIONS[ws] = (room_id, player_id)
    ROOM_CONNECTIONS[room_id][player_id] = ws
    ROOM_TOKENS[(room_id, player_id)] = token
    game = ROOMS[room_id]
    game.players[player_id].connected = True
    bump_state(game)
    cancel_cleanup(room_id)
    return token


async def handle_message(ws: Any, raw: str) -> None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        await send_error(ws, "Некорректный JSON")
        return
    if not isinstance(data, dict):
        await send_error(ws, "Ожидался объект JSON")
        return

    action = str(data.get("action", ""))
    try:
        if action == "create_room":
            name = str(data.get("name", "Игрок")).strip()[:24] or "Игрок"
            room_id = new_room_id()
            while room_id in ROOMS:
                room_id = new_room_id()
            config = {
                "points_to_win": safe_int(data.get("points_to_win", 12), 12, 5, 20),
                "fog_of_war": bool(data.get("fog_of_war", True)),
                "fast_start": bool(data.get("fast_start", False)),
                "friendly_raider": bool(data.get("friendly_raider", True)),
                "blight": bool(data.get("blight", True)),
                "blight_spread_chance": data.get("blight_spread_chance", 0.40),
                "blight_mode": str(data.get("blight_mode", "tactical")).strip().lower(),
                "cult_visibility": str(data.get("cult_visibility", "open")).strip().lower(),
                "routes": bool(data.get("routes", False)),
                "bot_difficulty": str(data.get("bot_difficulty", "skynet")).strip().lower()[:20],
            }
            game = make_game(room_id, config)
            human_pid, _ = add_human(game, name)
            ROOMS[room_id] = game
            ROOM_LOCKS[room_id] = asyncio.Lock()
            ROOM_CONNECTIONS[room_id] = {}
            for raw_bot_type in data.get("bot_types", ["c3po", "r2d2", "t800", "walle"]):
                bot_type = str(raw_bot_type).strip().lower()
                ok, message = add_bot(game, bot_type)
                if not ok:
                    dprint("bot not added", bot_type, message)
            token = await attach_connection(ws, room_id, human_pid)
            should_start = bool(data.get("start_immediately", True))
            if should_start:
                if len(game.players) < 2:
                    await send_error(ws, "Для начала игры нужен хотя бы один бот или второй игрок")
                    should_start = False
                else:
                    result = game.start_game()
                    if not result.success:
                        await send_error(ws, result.error or "Не удалось начать игру")
                        should_start = False
                    else:
                        _start_match_recording(room_id, game)
            await send_json(ws, {
                "type": "room_created",
                "room_id": room_id,
                "player_id": human_pid,
                "reconnect_token": token,
                "started": should_start,
                "bots": [p.name for p in game.players if p.player_id in game.bot_pids],
            })
            await broadcast_room(room_id)
            if should_start:
                schedule_drive(room_id)

        elif action == "join_room":
            room_id = str(data.get("room_id", "")).strip().upper()
            name = str(data.get("name", "Игрок")).strip()[:24] or "Игрок"
            game = ROOMS.get(room_id)
            if game is None:
                await send_error(ws, "Комната не найдена или уже удалена")
                return
            async with get_lock(room_id):
                if game.phase != GamePhase.LOBBY:
                    await send_error(ws, "Игра уже началась; подключение новых игроков закрыто")
                    return
                pid, _ = add_human(game, name)
                token = await attach_connection(ws, room_id, pid)
            await send_json(ws, {"type": "joined", "room_id": room_id, "player_id": pid, "reconnect_token": token})
            await broadcast_room(room_id)

        elif action == "reconnect":
            room_id = str(data.get("room_id", "")).strip().upper()
            pid = safe_int(data.get("player_id"), -1, 0, 3)
            token = str(data.get("reconnect_token", ""))
            game = ROOMS.get(room_id)
            expected = ROOM_TOKENS.get((room_id, pid))
            if game is None or pid >= len(game.players) or not token or not expected or not secrets.compare_digest(token, expected):
                await send_error(ws, "Сохранённая сессия больше не действует. Создай новую игру.")
                return
            await attach_connection(ws, room_id, pid, token)
            await send_json(ws, {"type": "reconnected", "room_id": room_id, "player_id": pid})
            await broadcast_room(room_id)
            schedule_drive(room_id)

        elif action == "add_bot":
            info = CONNECTIONS.get(ws)
            if not info:
                await send_error(ws, "Сначала создай комнату")
                return
            room_id, pid = info
            game = ROOMS.get(room_id)
            if game is None or pid != 0 or game.phase != GamePhase.LOBBY:
                await send_error(ws, "Бота можно добавить только в лобби хозяином комнаты")
                return
            async with get_lock(room_id):
                ok, message = add_bot(game, str(data.get("bot_type", "c3po")).lower())
            if not ok:
                await send_error(ws, message)
            await broadcast_room(room_id)

        elif action == "start_game":
            info = CONNECTIONS.get(ws)
            if not info:
                return
            room_id, pid = info
            game = ROOMS.get(room_id)
            if game is None or pid != 0:
                await send_error(ws, "Начать игру может хозяин комнаты")
                return
            async with get_lock(room_id):
                result = game.start_game()
            if not result.success:
                await send_error(ws, result.error or "Не удалось начать игру")
                return
            _start_match_recording(room_id, game)
            await broadcast_room(room_id)
            schedule_drive(room_id)

        elif action == "restart_game":
            info = CONNECTIONS.get(ws)
            if not info:
                return
            room_id, requester_pid = info
            old = ROOMS.get(room_id)
            if old is None or requester_pid != 0 or old.phase != GamePhase.FINISHED:
                await send_error(ws, "Перезапустить игру может хозяин после окончания партии")
                return
            config = dict(old.ext.get("local_server_config", {}))
            new_game = make_game(room_id, config)
            old_players = list(old.players)
            old_bot_types = dict(old.ext.get("bot_types", {}))
            for p in old_players:
                if p.player_id in old.bot_pids:
                    bot_type = old_bot_types.get(str(p.player_id), "c3po")
                    add_bot(new_game, bot_type)
                else:
                    add_human(new_game, p.name)
                    new_game.players[-1].connected = p.connected
            # Retain existing reconnect tokens for human participants.
            new_game.start_game()
            async with get_lock(room_id):
                ROOMS[room_id] = new_game
                for p in new_game.players:
                    if p.player_id in new_game.bot_pids:
                        bot_type = new_game.ext.get("bot_types", {}).get(str(p.player_id), "c3po")
                        new_game.bot_brains[p.player_id] = make_brain(new_game, p.player_id, bot_type)
            _start_match_recording(room_id, new_game)
            await broadcast_room(room_id)
            schedule_drive(room_id)

        elif action == "game_action":
            info = CONNECTIONS.get(ws)
            if not info:
                await send_error(ws, "Соединение не привязано к игроку")
                return
            room_id, pid = info
            game = ROOMS.get(room_id)
            if game is None:
                await send_error(ws, "Комната не найдена")
                return
            action_type = str(data.get("game_action", ""))
            params = data.get("params", {})
            if not isinstance(params, dict):
                await send_error(ws, "Параметры действия должны быть объектом")
                return
            async with get_lock(room_id):
                game = ROOMS.get(room_id)
                if game is None:
                    return
                legal = game.enumerate_legal_actions(pid)
                chosen = next((a for a in legal if a.type == action_type and action_params_match(a.p, params)), None)
                direct_discard = chosen is None and action_type == "discard"
                direct_propose = chosen is None and action_type == "propose_trade"
                if direct_discard:
                    # The engine lists at most 250 discard combinations, so a big hand cannot pick every
                    # split. discard_resources() validates any split itself (counts, ownership, phase).
                    result = game.discard_resources(pid, params.get("resources", {}))
                elif direct_propose:
                    # The enumerator lists only 1:1 offers as UI hints, while the engine accepts any
                    # resource multisets (phase, turn, offer slot and hand are validated inside).
                    # Compose dialogs must not be limited to the enumerated hints.
                    result = game.propose_trade(pid, params.get("give", {}), params.get("want", {}))
                elif chosen is None:
                    result = ActionResult(False, "Это действие сейчас недоступно или его параметры изменились")
                else:
                    result = game.apply_action(pid, chosen)
                _record_match_event(
                    room_id, game, event_type="action", actor_id=pid,
                    action_type=action_type, params=params,
                    success=result.success, error=result.error,
                    meta={"source": "human", "direct_discard_fallback": direct_discard,
                          "direct_propose_fallback": direct_propose},
                )
            await send_json(ws, {"type": "action_result", "result": result_payload(result)})
            if result.success:
                await broadcast_room(room_id)
                schedule_drive(room_id)

        elif action == "chat":
            info = CONNECTIONS.get(ws)
            if not info:
                return
            room_id, pid = info
            game = ROOMS.get(room_id)
            message = str(data.get("msg", "")).strip()[:240]
            if game and message:
                game._add_log(f"💬 {game.players[pid].name}: {message}", pid)
                bump_state(game)
                await broadcast_room(room_id)

        elif action == "ping":
            await send_json(ws, {"type": "pong"})
        else:
            await send_error(ws, f"Неизвестная команда: {action or '(пусто)'}")
    except Exception as exc:
        traceback.print_exc()
        await send_error(ws, f"Ошибка сервера: {exc}")


async def ws_handler(ws: Any, path: str | None = None) -> None:
    try:
        async for raw in ws:
            await handle_message(ws, raw)
    except Exception as exc:
        # Normal disconnects differ between websockets package versions.
        dprint("websocket closed", repr(exc))
    finally:
        info = CONNECTIONS.pop(ws, None)
        if info:
            room_id, pid = info
            conns = ROOM_CONNECTIONS.get(room_id, {})
            if conns.get(pid) is ws:
                conns.pop(pid, None)
                game = ROOMS.get(room_id)
                if game is not None and pid < len(game.players):
                    game.players[pid].connected = False
                    offer = game.active_trade_offer
                    if offer and offer.from_player == pid:
                        game.active_trade_offer = None
                        TRADE_ORDERS.pop(room_id, None)
                    elif offer and pid != offer.from_player:
                        offer.rejected_by.add(pid)
                    bump_state(game)
                if conns:
                    await broadcast_room(room_id)
                else:
                    schedule_cleanup(room_id)


async def http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        raw_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not raw_line:
            return
        parts = raw_line.decode("latin-1", errors="ignore").strip().split()
        if len(parts) < 2:
            await _http_response(writer, 400, "Bad Request", b"Bad Request", "text/plain; charset=utf-8")
            return
        method, raw_target = parts[0].upper(), parts[1]
        if method not in ("GET", "HEAD"):
            await _http_response(writer, 405, "Method Not Allowed", b"Method Not Allowed", "text/plain; charset=utf-8", extra="Allow: GET, HEAD\r\n")
            return
        # Consume request headers; no cookies or client state are required.
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if line in (b"\r\n", b"\n", b""):
                break

        path = unquote(urlsplit(raw_target).path)
        if path == "/api/leaderboard":
            body = json.dumps(_leaderboard_payload(), ensure_ascii=False).encode("utf-8")
            await _http_response(writer, 200, "OK", body, "application/json; charset=utf-8", content_length=len(body))
            return
        if path == "/":
            path = "/index.html"
        candidate = (STATIC_DIR / path.lstrip("/")).resolve()
        base = STATIC_DIR.resolve()
        if candidate != base and base not in candidate.parents:
            await _http_response(writer, 404, "Not Found", b"Not Found", "text/plain; charset=utf-8")
            return
        if not candidate.is_file():
            await _http_response(writer, 404, "Not Found", b"Not Found", "text/plain; charset=utf-8")
            return
        data = await asyncio.to_thread(candidate.read_bytes)
        if candidate == (STATIC_DIR / "index.html").resolve():
            data = data.replace(b"__HEXISLES_WS_PORT__", str(WS_PORT).encode("ascii"))
            data = data.replace(b"__HEXISLES_BUILD__", _static_build_stamp().encode("utf-8"))
        mime, _ = mimetypes.guess_type(str(candidate))
        mime = mime or "application/octet-stream"
        if mime.startswith("text/") or mime in ("application/javascript", "application/json", "image/svg+xml"):
            mime += "; charset=utf-8"
        await _http_response(writer, 200, "OK", b"" if method == "HEAD" else data, mime, content_length=len(data))
    except asyncio.TimeoutError:
        try:
            await _http_response(writer, 408, "Request Timeout", b"Request Timeout", "text/plain; charset=utf-8")
        except Exception:
            pass
    except Exception as exc:
        dprint("http error", repr(exc))
        try:
            await _http_response(writer, 500, "Internal Server Error", b"Internal Server Error", "text/plain; charset=utf-8")
        except Exception:
            pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _static_build_stamp() -> str:
    """Visible build marker: the newest mtime among the served static files.

    Injected into index.html so a player can always SEE which client build the
    page was loaded from (a kept-open tab survives server restarts with old
    modules in memory — the stamp makes that immediately obvious).
    """
    try:
        newest = max(
            (p.stat().st_mtime for p in STATIC_DIR.rglob("*") if p.is_file()),
            default=0.0,
        )
        return time.strftime("%d.%m %H:%M", time.localtime(newest))
    except OSError:
        return "—"


def _dump_abandoned_sessions(reason: str) -> None:
    """Write unfinished match journals to data/abandoned/ as JSON files.

    The SQLite archive intentionally stores only completed matches, so an
    abandoned playtest would vanish. Before a recording is replaced or on
    server shutdown, dump whatever was collected — enough for analysis.
    """
    archive = _get_match_archive()
    if archive is None or not MATCH_SESSIONS:
        return
    out_dir = archive.path.parent / "abandoned"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    for room_id, session in list(MATCH_SESSIONS.items()):
        game = getattr(session, "live_game", None)
        if game is None:
            continue
        try:
            package = session.package(game)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = out_dir / f"abandoned-{room_id}-{stamp}.json"
            path.write_text(json.dumps({
                "reason": reason,
                "package": package,
            }, ensure_ascii=False), encoding="utf-8")
            print(f"[archive] unfinished match in room {room_id} dumped to {path.name} ({reason})", flush=True)
        except Exception as exc:
            print(f"[archive] ERROR: cannot dump unfinished match for room {room_id}: {exc}", file=sys.stderr, flush=True)


def _leaderboard_payload(limit: int = 30) -> dict[str, Any]:
    """Aggregate finished archived matches into the Hall of Fame feed.

    Read-only view over the archive DB; when archiving is disabled the feed is
    simply empty. Human and bot names are grouped by their normalized name.
    """
    archive = _get_match_archive()
    if archive is None:
        return {"players": []}
    try:
        conn = sqlite3.connect(f"file:{archive.path}?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute(
                """
                SELECT MAX(name) AS name, MAX(is_bot) AS is_bot, COUNT(*) AS games,
                       SUM(is_winner) AS wins, AVG(victory_points_total) AS avg_vp
                FROM match_players
                GROUP BY normalized_name
                ORDER BY wins DESC, avg_vp DESC, games DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {"players": []}
    players = []
    for name, is_bot, games, wins, avg_vp in rows:
        games_n = int(games or 0)
        wins_n = int(wins or 0)
        players.append({
            "name": name or "—",
            "is_bot": bool(is_bot),
            "games": games_n,
            "wins": wins_n,
            "win_rate": round(100.0 * wins_n / games_n, 1) if games_n else 0.0,
            "avg_vp": round(float(avg_vp or 0.0), 2),
        })
    return {"players": players}


async def _http_response(
    writer: asyncio.StreamWriter,
    code: int,
    reason: str,
    body: bytes,
    content_type: str,
    extra: str = "",
    content_length: int | None = None,
) -> None:
    n = len(body) if content_length is None else content_length
    headers = (
        f"HTTP/1.1 {code} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {n}\r\n"
        "Cache-Control: no-store\r\n"
        "X-Content-Type-Options: nosniff\r\n"
        "Referrer-Policy: no-referrer\r\n"
        "Connection: close\r\n"
        + extra
        + "\r\n"
    )
    writer.write(headers.encode("latin-1") + body)
    await writer.drain()


async def main() -> None:
    for required in (ROOT / "isles_core.py", ROOT / "ai_brain.py", ROOT / "mods_pack.py"):
        if not required.is_file():
            print(f"ОШИБКА: рядом с server_local_v4.py не найден {required.name}", file=sys.stderr)
            raise SystemExit(2)
    if not (STATIC_DIR / "index.html").is_file():
        print(f"ОШИБКА: не найден клиент {STATIC_DIR / 'index.html'}", file=sys.stderr)
        raise SystemExit(2)

    # Initialise only after validating the runnable source/client snapshot.
    # The archive is optional diagnostics and must not create an empty DB when
    # startup is going to fail because the configured frontend is missing.
    _get_match_archive()

    try:
        http_server = await asyncio.start_server(http_handler, HTTP_HOST, HTTP_PORT)
    except OSError as exc:
        print(f"Не удалось открыть HTTP-порт {HTTP_PORT} на {HTTP_HOST}: {exc}", file=sys.stderr)
        print("Если порт занят, попробуй: HEXISLES_HTTP_PORT=8081 ./run_local_v5.sh", file=sys.stderr)
        raise SystemExit(2)

    try:
        try:
            async with ws_serve(
                ws_handler,
                WS_HOST,
                WS_PORT,
                max_size=256 * 1024,
                ping_interval=20,
                ping_timeout=20,
            ):
                print("HexIsles — локальная игра", flush=True)
                print("Версии: Engine v3 · Bots v6", flush=True)
                display_host = "127.0.0.1" if HTTP_HOST in {"0.0.0.0", "::"} else HTTP_HOST
                print(f"HTTP bind: {HTTP_HOST}:{HTTP_PORT}", flush=True)
                print(f"WebSocket bind: {WS_HOST}:{WS_PORT}", flush=True)
                print(f"Локальный адрес клиента: http://{display_host}:{HTTP_PORT}/", flush=True)
                print(f"Статика: {STATIC_DIR} · сборка клиента от {_static_build_stamp()}", flush=True)
                if HTTP_HOST in {"0.0.0.0", "::"} or WS_HOST in {"0.0.0.0", "::"}:
                    print("Для устройства в локальной сети используйте IP этого компьютера; не открывайте сервер напрямую в публичный Интернет.", flush=True)
                print("Нажми Ctrl+C для остановки.", flush=True)
                async with http_server:
                    await http_server.serve_forever()
        except OSError as exc:
            print(f"Не удалось открыть WebSocket-порт {WS_PORT} на {WS_HOST}: {exc}", file=sys.stderr)
            print("Если порт занят, задай другой: HEXISLES_WS_PORT=8766 ./run_local_v5.sh", file=sys.stderr)
            raise SystemExit(2)
    finally:
        http_server.close()
        await http_server.wait_closed()
        tasks = list(DRIVE_TASKS.values()) + list(CLEANUP_TASKS.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        _dump_abandoned_sessions("server shutdown")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nСервер остановлен.", flush=True)
