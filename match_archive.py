"""Detailed, completed-match-only telemetry for HexIsles.

While a match is running, events are kept in memory. Nothing about that match is
written to SQLite until the engine reaches GamePhase.FINISHED. At completion,
metadata, players, initial/final states, and all events are inserted in one
transaction. A crash or an abandoned match therefore leaves no partial match in
the archive.
"""

# ── ВЕРСИЯ ДВИЖКА ────────────────────────────────────────────────────────
# ЭТОТ ФАЙЛ СИНХРОНИЗИРУЕТСЯ ИЗ МАСТЕР-КОПИИ:
#   Мастер-копия: корень проекта (синхронизируется в интеграции платформы)
# Мастер-копия — ВСЕГДА новейшая версия. Не редактируйте этот файл напрямую!
# Дата последней синхронизации: см. git log.
# ─────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import math
import os
import sqlite3
import uuid
from collections import Counter
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Optional


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso_time(value: Optional[_dt.datetime] = None) -> str:
    return (value or utc_now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def json_safe(value: Any, *, _depth: int = 0) -> Any:
    """Convert common engine/AI structures to stable JSON-safe values."""
    if _depth > 30:
        return "<max-depth>"
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(k.value if isinstance(k, Enum) else k): json_safe(v, _depth=_depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [json_safe(item, _depth=_depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [json_safe(item, _depth=_depth + 1) for item in value]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return json_safe(value.to_dict(), _depth=_depth + 1)
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return json_safe(vars(value), _depth=_depth + 1)
        except Exception:
            pass
    return repr(value)


def stable_json(value: Any) -> str:
    return json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_fingerprints() -> dict[str, str]:
    """Identify engine, AI, mod, active server, archive and served frontend sources."""
    root = Path(__file__).resolve().parent
    # The shipped server in this tree is server_local_v4.py. Earlier versions
    # fingerprinted server_local_v3.py, which is absent from current snapshots
    # and silently recorded "unavailable" instead of identifying the runtime.
    names = ("isles_core.py", "ai_brain.py", "mods_pack.py", "server_local_v4.py", "match_archive.py")
    result: dict[str, str] = {}
    for name in names:
        path = root / name
        try:
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            result[name] = "unavailable"

    # UI behaviour affects a human playtest too. Hash all files in the active
    # static tree as a single deterministic manifest (path + bytes), using the
    # same HEXISLES_STATIC setting as server_local_v4.py.
    configured_static = os.environ.get("HEXISLES_STATIC", "static_v4").strip() or "static_v4"
    static_root = Path(configured_static).expanduser()
    if not static_root.is_absolute():
        static_root = root / static_root
    try:
        digest = hashlib.sha256()
        files = sorted(path for path in static_root.rglob("*") if path.is_file())
        if not static_root.is_dir() or not files:
            raise OSError(f"static tree missing or empty: {static_root}")
        for path in files:
            digest.update(path.relative_to(static_root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        result[f"frontend:{static_root.name}"] = digest.hexdigest()
    except OSError:
        result[f"frontend:{static_root.name}"] = "unavailable"
    return result


def _resource_map(resources: dict) -> dict[str, int]:
    return {str(getattr(r, "value", r)): int(amount) for r, amount in resources.items()}


def _mod_telemetry_without_event_history(game: Any) -> dict[str, Any]:
    root = getattr(game, "ext", {}).get("mod_telemetry", {})
    result: dict[str, Any] = {}
    if not isinstance(root, dict):
        return result
    for mod_id, raw in root.items():
        if not isinstance(raw, dict):
            result[str(mod_id)] = json_safe(raw)
            continue
        result[str(mod_id)] = {
            "counters": json_safe(raw.get("counters", {})),
            "by_player": json_safe(raw.get("by_player", {})),
            "event_count": len(raw.get("events", [])) if isinstance(raw.get("events", []), list) else 0,
        }
    return result


def capture_game_state(
    game: Any,
    map_signature: str | None = None,
    route_lengths: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Capture a complete server-only state, including fog-hidden information.

    This is an analysis archive, not a player observation. The full starting map
    plus subsequent deltas allows an analyst to replay the match exactly without
    storing a full board copy for every action.
    """
    players: dict[str, Any] = {}
    for player in game.players:
        pid = int(player.player_id)
        brain = getattr(game, "bot_brains", {}).get(pid)
        brain_personality = getattr(brain, "personality", None) if brain else None
        public_vp = int(game.get_victory_points(pid, public=True))
        full_vp = int(game.get_victory_points(pid, public=False))
        players[str(pid)] = {
            "player_id": pid,
            "name": str(player.name),
            "color": str(player.color),
            "is_bot": pid in game.bot_pids,
            "bot_type": game.ext.get("bot_types", {}).get(str(pid)) if pid in game.bot_pids else None,
            "bot_archetype": getattr(brain, "archetype", None) if brain else None,
            "bot_difficulty": getattr(brain, "difficulty", None) if brain else None,
            "bot_personality": json_safe(vars(brain_personality)) if brain_personality is not None and hasattr(brain_personality, "__dict__") else None,
            "resources": _resource_map(player.resources),
            "total_resources": int(player.total_resources),
            "victory_points_public": public_vp,
            "victory_points_total": full_vp,
            "hidden_vp": int(player.hidden_vp),
            "charters": [c.to_dict(player.personal_turns_taken) for c in player.charters],
            "vanguards_played": int(player.vanguards_played),
            "outposts_left": int(player.outposts_left),
            "metropolises_left": int(player.metropolises_left),
            "routes_left": int(player.routes_left),
            "routes_built": sum(1 for edge in game.map.edges.values() if edge.route_owner == pid),
            "longest_route_length": int((route_lengths or {}).get(pid, 0)),
            "connected": bool(player.connected),
            "free_routes_pending": int(player.free_routes_pending),
            "personal_turns_taken": int(player.personal_turns_taken),
            "trade_pact_turn": int(player.trade_pact_turn),
            "charter_played_this_turn": bool(player.charter_played_this_turn),
            "revealed_hexes": sorted(int(h) for h in player.revealed_hexes),
            "extension_state": json_safe(player.ext),
        }

    hexes: dict[str, Any] = {}
    for hid, tile in sorted(game.map.hexes.items()):
        hexes[str(hid)] = {
            "hex_id": int(hid), "q": int(tile.q), "r": int(tile.r),
            "terrain": str(tile.terrain.value),
            "resource": str(tile.resource.value),
            "number_token": int(tile.number_token),
            "has_raider": bool(tile.has_raider),
            "vertex_ids": [int(v) for v in tile.vertex_ids],
            "extension_state": json_safe(tile.ext),
        }

    vertices: dict[str, Any] = {}
    for vid, vertex in sorted(game.map.vertices.items()):
        vertices[str(vid)] = {
            "vertex_id": int(vid), "x": round(float(vertex.x), 3), "y": round(float(vertex.y), 3),
            "adjacent_hexes": sorted(int(v) for v in vertex.adjacent_hexes),
            "adjacent_vertices": sorted(int(v) for v in vertex.adjacent_vertices),
            "adjacent_edges": sorted(int(v) for v in vertex.adjacent_edges),
            "structure_type": vertex.structure_type.value if vertex.structure_type else None,
            "structure_owner": vertex.structure_owner,
            "is_port": bool(vertex.is_port),
            "port_type": vertex.port_type.value if vertex.port_type else None,
            "extension_state": json_safe(vertex.ext),
        }

    edges: dict[str, Any] = {}
    for eid, edge in sorted(game.map.edges.items()):
        edges[str(eid)] = {
            "edge_id": int(eid), "vertices": [int(v) for v in edge.vertices],
            "adjacent_hexes": sorted(int(v) for v in edge.adjacent_hexes),
            "route_owner": edge.route_owner,
            "is_port_edge": bool(edge.is_port_edge),
            "port_type": edge.port_type.value if edge.port_type else None,
            "extension_state": json_safe(edge.ext),
        }

    offer = game.active_trade_offer
    offer_state = None
    if offer is not None:
        offer_state = {
            "from_player": int(offer.from_player),
            "give": _resource_map(offer.give),
            "want": _resource_map(offer.want),
            "rejected_by": sorted(int(pid) for pid in offer.rejected_by),
        }

    extensions = {
        key: json_safe(value)
        for key, value in getattr(game, "ext", {}).items()
        if key not in ("local_server_config", "bot_types", "mod_telemetry")
    }

    return json_safe({
        "phase": game.phase.value,
        "turn": int(game.global_turn),
        "current_player_id": int(game.current_player_id),
        "turn_order": list(game.turn_order),
        "setup_order": list(game.setup_order),
        "setup_index": int(game.setup_index),
        "last_setup_vertex": game.last_setup_vertex,
        "dice_result": list(game.dice_result),
        "winner_id": game.winner_id,
        "points_to_win": int(game.points_to_win),
        "seed": str(game.seed),
        "map_signature": map_signature or game._map_signature(),
        "raider_hex": game.raider_hex,
        "longest_route_player": game.longest_route_player,
        "grand_garrison_player": game.grand_garrison_player,
        "pending_discards": {str(k): int(v) for k, v in game.pending_discards.items()},
        "pending_raider_victims": list(game.pending_raider_victims),
        "active_trade_offer": offer_state,
        "charter_deck": [c.value for c in game.charter_deck],
        "stall": {
            "last_vp_progress_turn": int(game.last_vp_progress_turn),
            "stall_limit_turns": int(game.stall_limit_turns),
            "stalled_out": bool(game.ext.get("stalled_out", False)),
        },
        "players": players,
        "board": {"hexes": hexes, "vertices": vertices, "edges": edges},
        "mod_telemetry": _mod_telemetry_without_event_history(game),
        "extensions": extensions,
    })


def state_summary(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": state.get("phase"),
        "turn": state.get("turn"),
        "current_player_id": state.get("current_player_id"),
        "dice_result": state.get("dice_result"),
        "winner_id": state.get("winner_id"),
        "raider_hex": state.get("raider_hex"),
        "awards": {
            "longest_route_player": state.get("longest_route_player"),
            "grand_garrison_player": state.get("grand_garrison_player"),
        },
        "players": {
            pid: {
                "name": p.get("name"),
                "is_bot": p.get("is_bot"),
                "resources": p.get("resources"),
                "vp_public": p.get("victory_points_public"),
                "vp_total": p.get("victory_points_total"),
                "routes_built": p.get("routes_built"),
                "longest_route_length": p.get("longest_route_length"),
                "outposts_left": p.get("outposts_left"),
                "metropolises_left": p.get("metropolises_left"),
                "routes_left": p.get("routes_left"),
            }
            for pid, p in state.get("players", {}).items()
        },
    }


def _diff(before: Any, after: Any, path: Optional[list[Any]] = None, out: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """A small JSON-patch-like change set; lists are replaced only when changed."""
    if path is None:
        path = []
    if out is None:
        out = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after), key=str):
            p = path + [key]
            if key not in after:
                out.append({"op": "remove", "path": p})
            elif key not in before:
                out.append({"op": "set", "path": p, "value": after[key]})
            else:
                _diff(before[key], after[key], p, out)
    elif isinstance(before, list) and isinstance(after, list):
        if before != after:
            out.append({"op": "set", "path": path, "value": after})
    elif before != after:
        out.append({"op": "set", "path": path, "value": after})
    return out


def _new_mod_events(game: Any, previous: dict[str, list[Any]]) -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
    telemetry = getattr(game, "ext", {}).get("mod_telemetry", {})
    current: dict[str, list[Any]] = {}
    new_events: dict[str, list[Any]] = {}
    if not isinstance(telemetry, dict):
        return current, new_events
    for mod_id, raw in telemetry.items():
        events = raw.get("events", []) if isinstance(raw, dict) else []
        if not isinstance(events, list):
            continue
        events = copy.deepcopy(events)
        old = previous.get(str(mod_id), [])
        if not old:
            fresh = events
        elif len(events) >= len(old) and events[:len(old)] == old:
            fresh = events[len(old):]
        else:
            overlap = min(len(old), len(events))
            while overlap > 0 and old[-overlap:] != events[:overlap]:
                overlap -= 1
            fresh = events[overlap:]
        if fresh:
            new_events[str(mod_id)] = json_safe(fresh)
        current[str(mod_id)] = events
    return current, new_events


def _game_logs_after(game: Any, after_id: int) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    max_id = int(after_id)
    for item in getattr(game, "log", []):
        try:
            entry_id = int(item.get("id", 0))
        except (TypeError, ValueError):
            continue
        max_id = max(max_id, entry_id)
        if entry_id > after_id:
            message = str(item.get("msg", ""))
            # Player chat is intentionally not persisted in the analysis archive.
            if message.startswith("💬 "):
                continue
            rows.append(json_safe(item))
    return rows, max_id


class MatchSession:
    """In-memory match journal. It is persisted only by MatchArchive on finish."""

    def __init__(self, game: Any, config: dict[str, Any]):
        self.match_id = uuid.uuid4().hex
        self.room_id = str(game.room_id)
        self.started_at = utc_now()
        self.started_at_iso = iso_time(self.started_at)
        self.started_monotonic = __import__("time").monotonic()
        self.config = json_safe(config)
        self.source_hashes = source_fingerprints()
        self.map_signature = game._map_signature()
        self.route_lengths = {int(p.player_id): int(game._calculate_longest_route(p.player_id)) for p in game.players}
        self.player_roster = self._player_roster(game)
        self.initial_state = capture_game_state(game, self.map_signature, self.route_lengths)
        self.last_state = copy.deepcopy(self.initial_state)
        self.last_log_id = 0
        all_logs, self.last_log_id = _game_logs_after(game, 0)
        self.initial_logs = all_logs
        self.last_mod_events, self.initial_mod_events = _new_mod_events(game, {})
        self.events: list[dict[str, Any]] = []
        self.finalizing = False
        self.saved = False

    @staticmethod
    def _player_roster(game: Any) -> list[dict[str, Any]]:
        bot_types = game.ext.get("bot_types", {})
        roster = []
        for player in game.players:
            pid = int(player.player_id)
            brain = getattr(game, "bot_brains", {}).get(pid)
            personality = getattr(brain, "personality", None) if brain else None
            roster.append({
                "player_id": pid,
                "name": str(player.name),
                "is_bot": pid in game.bot_pids,
                "bot_type": bot_types.get(str(pid)) if pid in game.bot_pids else None,
                "bot_archetype": getattr(brain, "archetype", None) if brain else None,
                "bot_difficulty": getattr(brain, "difficulty", None) if brain else None,
                "bot_personality": json_safe(vars(personality)) if personality is not None and hasattr(personality, "__dict__") else None,
            })
        return roster

    def record(
        self,
        game: Any,
        *,
        event_type: str,
        actor_id: Optional[int] = None,
        action_type: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        success: Optional[bool] = None,
        error: Optional[str] = None,
        legal_actions: Optional[list[Any]] = None,
        decision: Optional[dict[str, Any]] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        before = self.last_state
        after = capture_game_state(game, self.map_signature, self.route_lengths)
        # Longest-route calculation is comparatively expensive; recompute only
        # when a route or structure changes the graph (e.g. a blocking settlement).
        route_graph_changed = False
        for key in set(before["board"]["edges"]) | set(after["board"]["edges"]):
            b = before["board"]["edges"].get(key, {}).get("route_owner")
            a = after["board"]["edges"].get(key, {}).get("route_owner")
            if a != b:
                route_graph_changed = True
                break
        if not route_graph_changed:
            for key in set(before["board"]["vertices"]) | set(after["board"]["vertices"]):
                bv = before["board"]["vertices"].get(key, {})
                av = after["board"]["vertices"].get(key, {})
                if (bv.get("structure_owner"), bv.get("structure_type")) != (av.get("structure_owner"), av.get("structure_type")):
                    route_graph_changed = True
                    break
        if route_graph_changed:
            self.route_lengths = {int(p.player_id): int(game._calculate_longest_route(p.player_id)) for p in game.players}
            for pid, length in self.route_lengths.items():
                after["players"][str(pid)]["longest_route_length"] = length
        new_logs, self.last_log_id = _game_logs_after(game, self.last_log_id)
        self.last_mod_events, mod_events = _new_mod_events(game, self.last_mod_events)
        now = utc_now()
        phase_before = before.get("phase")
        phase_after = after.get("phase")
        actor_name = None
        if actor_id is not None and 0 <= int(actor_id) < len(game.players):
            actor_name = str(game.players[int(actor_id)].name)
        legal_payload = None
        if legal_actions is not None:
            legal_payload = [a.to_dict() if hasattr(a, "to_dict") else json_safe(a) for a in legal_actions]
        elapsed_ms = max(0, int((__import__("time").monotonic() - self.started_monotonic) * 1000))
        event = {
            "seq": len(self.events) + 1,
            "timestamp": iso_time(now),
            "elapsed_ms": elapsed_ms,
            "event_type": str(event_type),
            "actor_id": int(actor_id) if actor_id is not None else None,
            "actor_name": actor_name,
            "turn_before": before.get("turn"),
            "turn_after": after.get("turn"),
            "phase_before": phase_before,
            "phase_after": phase_after,
            "action_type": action_type,
            "params": json_safe(params or {}),
            "success": success,
            "error": error,
            "legal_actions": json_safe(legal_payload),
            "decision": json_safe(decision) if decision is not None else None,
            "state_before": state_summary(before),
            "state_after": state_summary(after),
            "delta": _diff(before, after),
            "logs": new_logs,
            "mod_events": mod_events,
            "meta": json_safe(meta or {}),
        }
        self.events.append(event)
        self.last_state = after
        return event

    def package(self, game: Any) -> dict[str, Any]:
        # Keep the ending snapshot authoritative even if a future plugin edits
        # the route graph without going through the usual placement actions.
        self.route_lengths = {int(p.player_id): int(game._calculate_longest_route(p.player_id)) for p in game.players}
        final_state = capture_game_state(game, self.map_signature, self.route_lengths)
        finished_at = utc_now()
        winner_id = game.winner_id
        winner = None
        if winner_id is not None and 0 <= int(winner_id) < len(game.players):
            winner = {"player_id": int(winner_id), "name": str(game.players[int(winner_id)].name)}
        config = dict(self.config)
        config["local_server_config"] = json_safe(game.ext.get("local_server_config", {}))
        config["bot_roster"] = self.player_roster
        config["source_fingerprints_sha256"] = self.source_hashes
        config["seed"] = str(game.seed)
        config["map_signature"] = self.map_signature
        if bool(game.ext.get("stalled_out")):
            finish_reason = "stall_limit"
        elif winner and int(final_state["players"][str(winner_id)]["victory_points_total"]) >= int(game.points_to_win):
            finish_reason = "victory_points"
        elif winner:
            finish_reason = "engine_finished"
        else:
            finish_reason = "finished_without_winner"
        player_rows = []
        board = final_state["board"]
        for pid_text, player in final_state["players"].items():
            pid = int(pid_text)
            owned_vertices = [v for v in board["vertices"].values() if v["structure_owner"] == pid]
            owned_edges = [e for e in board["edges"].values() if e["route_owner"] == pid]
            longest_route_length = int(self.route_lengths.get(pid, 0))
            final_state["players"][pid_text]["longest_route_length"] = longest_route_length
            player_rows.append({
                **{k: player.get(k) for k in ("player_id", "name", "is_bot", "bot_type", "bot_archetype", "bot_difficulty")},
                "normalized_name": " ".join(str(player.get("name", "")).casefold().split()),
                "is_winner": pid == winner_id,
                "rank": None,
                "victory_points_total": player["victory_points_total"],
                "victory_points_public": player["victory_points_public"],
                "resources": player["resources"],
                "total_resources": player["total_resources"],
                "routes_left": player["routes_left"],
                "outposts_left": player["outposts_left"],
                "metropolises_left": player["metropolises_left"],
                "routes_built": len(owned_edges),
                "settlements": sum(1 for v in owned_vertices if v["structure_type"] == "outpost"),
                "cities": sum(1 for v in owned_vertices if v["structure_type"] == "metropolis"),
                "charters_count": len(player["charters"]),
                "vanguards_played": player["vanguards_played"],
                "personal_turns_taken": player["personal_turns_taken"],
                "longest_route_length": longest_route_length,
                "has_longest_route": game.longest_route_player == pid,
                "has_grand_garrison": game.grand_garrison_player == pid,
            })
        sorted_players = sorted(player_rows, key=lambda p: (not p["is_winner"], -p["victory_points_total"], p["player_id"]))
        for rank, player in enumerate(sorted_players, start=1):
            player["rank"] = rank
        counts = Counter(str(e.get("action_type")) for e in self.events if e.get("action_type"))
        config.setdefault("archive_schema", 1)
        return {
            "match_id": self.match_id,
            "room_id": self.room_id,
            "started_at": self.started_at_iso,
            "finished_at": iso_time(finished_at),
            "duration_seconds": round(max(0.0, (finished_at - self.started_at).total_seconds()), 3),
            "seed": str(game.seed),
            "points_to_win": int(game.points_to_win),
            "finish_reason": finish_reason,
            "winner_player_id": winner_id,
            "winner_name": winner["name"] if winner else None,
            "global_turns": int(game.global_turn),
            "event_count": len(self.events),
            "action_count": sum(1 for e in self.events if e.get("action_type")),
            "action_counts": dict(counts),
            "config": config,
            "player_rows": sorted_players,
            "initial_state": self.initial_state,
            "initial_logs": self.initial_logs,
            "initial_mod_events": self.initial_mod_events,
            "final_state": final_state,
            "events": self.events,
            "final_mod_telemetry": json_safe(game.ext.get("mod_telemetry", {})),
        }


class MatchArchive:
    """SQLite storage for completed games only."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Transaction-aware connection scope that always closes the handle."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS matches (
                    match_id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status = 'completed'),
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    seed TEXT NOT NULL,
                    points_to_win INTEGER NOT NULL,
                    finish_reason TEXT NOT NULL,
                    winner_player_id INTEGER,
                    winner_name TEXT,
                    global_turns INTEGER NOT NULL,
                    event_count INTEGER NOT NULL,
                    action_count INTEGER NOT NULL,
                    action_counts_json TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    initial_state_json TEXT NOT NULL,
                    initial_logs_json TEXT NOT NULL,
                    initial_mod_events_json TEXT NOT NULL,
                    final_state_json TEXT NOT NULL,
                    final_mod_telemetry_json TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS match_players (
                    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
                    player_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    is_bot INTEGER NOT NULL,
                    bot_type TEXT,
                    bot_archetype TEXT,
                    bot_difficulty TEXT,
                    is_winner INTEGER NOT NULL,
                    rank INTEGER NOT NULL,
                    victory_points_total INTEGER NOT NULL,
                    victory_points_public INTEGER NOT NULL,
                    resources_json TEXT NOT NULL,
                    total_resources INTEGER NOT NULL,
                    routes_left INTEGER NOT NULL,
                    outposts_left INTEGER NOT NULL,
                    metropolises_left INTEGER NOT NULL,
                    routes_built INTEGER NOT NULL,
                    settlements INTEGER NOT NULL,
                    cities INTEGER NOT NULL,
                    charters_count INTEGER NOT NULL,
                    vanguards_played INTEGER NOT NULL,
                    personal_turns_taken INTEGER NOT NULL,
                    longest_route_length INTEGER NOT NULL,
                    has_longest_route INTEGER NOT NULL,
                    has_grand_garrison INTEGER NOT NULL,
                    PRIMARY KEY (match_id, player_id)
                );
                CREATE TABLE IF NOT EXISTS match_events (
                    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    elapsed_ms INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id INTEGER,
                    actor_name TEXT,
                    turn_before INTEGER,
                    turn_after INTEGER,
                    phase_before TEXT,
                    phase_after TEXT,
                    action_type TEXT,
                    params_json TEXT NOT NULL,
                    success INTEGER,
                    error TEXT,
                    legal_actions_json TEXT,
                    decision_json TEXT,
                    state_before_json TEXT NOT NULL,
                    state_after_json TEXT NOT NULL,
                    delta_json TEXT NOT NULL,
                    logs_json TEXT NOT NULL,
                    mod_events_json TEXT NOT NULL,
                    meta_json TEXT NOT NULL,
                    PRIMARY KEY (match_id, seq)
                );
                CREATE INDEX IF NOT EXISTS idx_matches_finished ON matches(finished_at DESC);
                CREATE INDEX IF NOT EXISTS idx_matches_winner ON matches(winner_name, finished_at DESC);
                CREATE INDEX IF NOT EXISTS idx_players_normalized_name ON match_players(normalized_name, is_bot);
                CREATE INDEX IF NOT EXISTS idx_events_action ON match_events(action_type, event_type);
                CREATE INDEX IF NOT EXISTS idx_events_actor ON match_events(actor_id, seq);
            """)

    def save_completed_match(self, package: dict[str, Any]) -> bool:
        """Atomically write one finished match. Returns False if already present."""
        match_id = str(package["match_id"])
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT 1 FROM matches WHERE match_id = ?", (match_id,)).fetchone()
            if existing:
                conn.rollback()
                return False
            conn.execute("""INSERT INTO matches VALUES (
                :match_id, :room_id, 'completed', :started_at, :finished_at,
                :duration_seconds, :seed, :points_to_win, :finish_reason,
                :winner_player_id, :winner_name, :global_turns, :event_count,
                :action_count, :action_counts_json, :config_json,
                :initial_state_json, :initial_logs_json, :initial_mod_events_json,
                :final_state_json, :final_mod_telemetry_json, :archived_at
            )""", {
                "match_id": match_id,
                "room_id": package["room_id"],
                "started_at": package["started_at"],
                "finished_at": package["finished_at"],
                "duration_seconds": package["duration_seconds"],
                "seed": str(package["seed"]),
                "points_to_win": package["points_to_win"],
                "finish_reason": package["finish_reason"],
                "winner_player_id": package["winner_player_id"],
                "winner_name": package["winner_name"],
                "global_turns": package["global_turns"],
                "event_count": package["event_count"],
                "action_count": package["action_count"],
                "action_counts_json": stable_json(package["action_counts"]),
                "config_json": stable_json(package["config"]),
                "initial_state_json": stable_json(package["initial_state"]),
                "initial_logs_json": stable_json(package["initial_logs"]),
                "initial_mod_events_json": stable_json(package["initial_mod_events"]),
                "final_state_json": stable_json(package["final_state"]),
                "final_mod_telemetry_json": stable_json(package["final_mod_telemetry"]),
                "archived_at": iso_time(),
            })
            for p in package["player_rows"]:
                conn.execute("""INSERT INTO match_players VALUES (
                    :match_id, :player_id, :name, :normalized_name, :is_bot,
                    :bot_type, :bot_archetype, :bot_difficulty, :is_winner, :rank,
                    :victory_points_total, :victory_points_public, :resources_json,
                    :total_resources, :routes_left, :outposts_left, :metropolises_left,
                    :routes_built, :settlements, :cities, :charters_count,
                    :vanguards_played, :personal_turns_taken, :longest_route_length,
                    :has_longest_route, :has_grand_garrison
                )""", {
                    **p,
                    "match_id": match_id,
                    "is_bot": int(bool(p["is_bot"])),
                    "is_winner": int(bool(p["is_winner"])),
                    "has_longest_route": int(bool(p["has_longest_route"])),
                    "has_grand_garrison": int(bool(p["has_grand_garrison"])),
                    "resources_json": stable_json(p["resources"]),
                })
            for event in package["events"]:
                conn.execute("""INSERT INTO match_events VALUES (
                    :match_id, :seq, :timestamp, :elapsed_ms, :event_type,
                    :actor_id, :actor_name, :turn_before, :turn_after,
                    :phase_before, :phase_after, :action_type, :params_json,
                    :success, :error, :legal_actions_json, :decision_json,
                    :state_before_json, :state_after_json, :delta_json,
                    :logs_json, :mod_events_json, :meta_json
                )""", {
                    "match_id": match_id,
                    "seq": event["seq"],
                    "timestamp": event["timestamp"],
                    "elapsed_ms": event["elapsed_ms"],
                    "event_type": event["event_type"],
                    "actor_id": event["actor_id"],
                    "actor_name": event["actor_name"],
                    "turn_before": event["turn_before"],
                    "turn_after": event["turn_after"],
                    "phase_before": event["phase_before"],
                    "phase_after": event["phase_after"],
                    "action_type": event["action_type"],
                    "params_json": stable_json(event["params"]),
                    "success": None if event["success"] is None else int(bool(event["success"])),
                    "error": event["error"],
                    "legal_actions_json": stable_json(event["legal_actions"]) if event["legal_actions"] is not None else None,
                    "decision_json": stable_json(event["decision"]) if event["decision"] is not None else None,
                    "state_before_json": stable_json(event["state_before"]),
                    "state_after_json": stable_json(event["state_after"]),
                    "delta_json": stable_json(event["delta"]),
                    "logs_json": stable_json(event["logs"]),
                    "mod_events_json": stable_json(event["mod_events"]),
                    "meta_json": stable_json(event["meta"]),
                })
            conn.commit()
        return True

    def list_matches(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("""SELECT match_id, room_id, started_at, finished_at,
                duration_seconds, winner_name, winner_player_id, points_to_win,
                global_turns, event_count, action_count, finish_reason
                FROM matches WHERE status='completed' ORDER BY finished_at DESC LIMIT ?""", (max(1, min(500, int(limit))),)).fetchall()
            return [dict(row) for row in rows]

    def leaderboard(self, limit: int = 30, min_games: int = 1, include_bots: bool = False) -> list[dict[str, Any]]:
        where = "" if include_bots else "WHERE mp.is_bot = 0"
        query = f"""SELECT mp.normalized_name AS player_key, MIN(mp.name) AS name,
            COUNT(*) AS games,
            SUM(mp.is_winner) AS wins,
            ROUND(100.0 * SUM(mp.is_winner) / COUNT(*), 1) AS win_rate,
            ROUND(AVG(mp.victory_points_total), 2) AS avg_vp,
            ROUND(AVG(m.global_turns), 2) AS avg_match_turns,
            MAX(m.finished_at) AS last_played
            FROM match_players mp JOIN matches m ON m.match_id=mp.match_id
            {where}
            GROUP BY mp.normalized_name
            HAVING COUNT(*) >= ?
            ORDER BY wins DESC, win_rate DESC, avg_vp DESC, games DESC
            LIMIT ?"""
        with self._connection() as conn:
            rows = conn.execute(query, (max(1, int(min_games)), max(1, min(500, int(limit))))).fetchall()
            return [dict(row) for row in rows]

    def match_summary(self, match_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM matches WHERE match_id=? AND status='completed'", (match_id,)).fetchone()
            if row is None:
                return None
            players = conn.execute("SELECT * FROM match_players WHERE match_id=? ORDER BY rank", (match_id,)).fetchall()
            action_rows = conn.execute("SELECT action_type, COUNT(*) AS n FROM match_events WHERE match_id=? AND action_type IS NOT NULL GROUP BY action_type ORDER BY n DESC", (match_id,)).fetchall()
            result = dict(row)
            result["config"] = json.loads(result.pop("config_json"))
            result["action_counts"] = json.loads(result.pop("action_counts_json"))
            for field in ("initial_state_json", "initial_logs_json", "initial_mod_events_json", "final_state_json", "final_mod_telemetry_json"):
                result.pop(field, None)
            result["players"] = [dict(p) for p in players]
            result["action_counts_by_database"] = [dict(a) for a in action_rows]
            return result

    def export_match(self, match_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            match = conn.execute("SELECT * FROM matches WHERE match_id=? AND status='completed'", (match_id,)).fetchone()
            if match is None:
                return None
            player_rows = conn.execute("SELECT * FROM match_players WHERE match_id=? ORDER BY rank", (match_id,)).fetchall()
            event_rows = conn.execute("SELECT * FROM match_events WHERE match_id=? ORDER BY seq", (match_id,)).fetchall()
        m = dict(match)
        for field in ("action_counts_json", "config_json", "initial_state_json", "initial_logs_json", "initial_mod_events_json", "final_state_json", "final_mod_telemetry_json"):
            m[field.removesuffix("_json")] = json.loads(m.pop(field))
        players = []
        for row in player_rows:
            p = dict(row)
            p["resources"] = json.loads(p.pop("resources_json"))
            for k in ("is_bot", "is_winner", "has_longest_route", "has_grand_garrison"):
                p[k] = bool(p[k])
            players.append(p)
        events = []
        for row in event_rows:
            e = dict(row)
            for field in ("params_json", "legal_actions_json", "decision_json", "state_before_json", "state_after_json", "delta_json", "logs_json", "mod_events_json", "meta_json"):
                target = field.removesuffix("_json")
                e[target] = json.loads(e.pop(field)) if e[field] is not None else None
            if e["success"] is not None:
                e["success"] = bool(e["success"])
            events.append(e)
        return {"match": m, "players": players, "events": events}

    def stats(self) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS matches, COALESCE(SUM(event_count),0) AS events, COALESCE(SUM(action_count),0) AS actions FROM matches WHERE status='completed'").fetchone()
            return dict(row)
