"""AIDebugger — трассировщик решений ИИ для лабораторной копии (v3).

Собирает по ходу партии:
  * каждое решение каждого бота: тип, параметры, итоговая оценка, причина,
    топ-кандидаты, активная цель (из brain.last_decision);
  * публичный журнал партии (game.log) инкрементально, без потери из-за cap 500;
  * внутреннюю телеметрию Скверны: выравнивание, слёзы, благословения, ОП-дорожки;
  * снимки полной карты (внутренний вид, для пост-анализа).

Выгрузка: trace.jsonl (сырые события), transcript.txt (читаемая хроника),
summary.json (итоги партии + статистика действий).
Внутренние (скрытые туманом) данные пишутся ТОЛЬКО в trace.jsonl/summary.json
и помечены "internal"; принимая решения, человек-игрок их не читает.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional


def _jf(value: Any) -> Any:
    """JSON-safe conversion (enums -> str)."""
    return getattr(value, "value", value)


class AIDebugger:
    def __init__(self, game: Any, out_dir: str | Path, label: str = "run",
                 summary_only: bool = False) -> None:
        self.game = game
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.label = label
        self.summary_only = summary_only
        self.t0 = time.time()
        self.events: list[dict[str, Any]] = []
        self._log_consumed = 0
        self._seq = 0
        self.snapshots: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def _emit(self, kind: str, **payload: Any) -> None:
        self._seq += 1
        rec = {"seq": self._seq, "t": round(time.time() - self.t0, 3), "kind": kind, **payload}
        self.events.append(rec)

    # ------------------------------------------------------------------
    def drain_journal(self) -> None:
        log = getattr(self.game, "log", [])
        while self._log_consumed < len(log):
            entry = log[self._log_consumed]
            self._log_consumed += 1
            self._emit(
                "journal",
                turn=int(getattr(self.game, "global_turn", 0)),
                msg=entry.get("msg") or entry.get("message") or str(entry),
                player_id=entry.get("player_id"),
            )

    # ------------------------------------------------------------------
    def blight_state(self) -> dict[str, Any]:
        """Internal blight view: alignments, tears, VP tracks, infected hexes."""
        g = self.game
        ext = getattr(g, "ext", {}).get("blight", {})
        players = {}
        for pid, data in (ext.get("players") or {}).items():
            players[int(pid)] = {
                "alignment": data.get("alignment"),
                "alignment_score": data.get("alignment_score"),
                "tears": data.get("tears"),
                "tears_vp": data.get("tears_vp"),
                "purify_vp": data.get("purify_vp"),
                "alternate_vp": data.get("alternate_vp"),
                "purified_count": data.get("purified_count"),
                "corrupt_count": data.get("corrupt_count"),
                "accusation_used": data.get("accusation_used"),
                "cult_revealed": data.get("cult_revealed"),
            }
        hexes = {}
        for hid, tile in g.map.hexes.items():
            stage = int(tile.ext.get("blight_stage", 0) or 0)
            if stage > 0:
                hexes[hid] = {
                    "stage": stage,
                    "sources": [_jf(s) for s in tile.ext.get("blight_stage_sources", [])],
                    "shield_until": tile.ext.get("shield_until"),
                }
        return {"players": players, "infected_hexes": hexes}

    # ------------------------------------------------------------------
    def record_bot_decision(self, pid: int, action: Any, result: Any,
                            alternatives: Optional[list[dict]] = None,
                            leader_id: Optional[int] = None,
                            standings_pre: Optional[dict] = None,
                            chosen_hex_context: Optional[dict] = None,
                            alignment_before: Optional[str] = None,
                            alignment_after: Optional[str] = None,
                            alignment_score_before: Optional[int] = None,
                            alignment_score_after: Optional[int] = None,
                            raider_hex_before: Optional[int] = None) -> None:
        brain = (getattr(self.game, "bot_brains", {}) or {}).get(pid)
        if brain is None:
            return
        dec = getattr(brain, "last_decision", None) or {}
        alts = [a for a in (alternatives or []) if a.get("score") is not None]
        chosen_score = dec.get("score")
        if chosen_score is None and alts:
            # для фаз без top_candidates берём оценку выбранного из alternatives
            for a in alts:
                if a.get("action") == action.type and (a.get("params") or {}) == (action.p or {}):
                    chosen_score = a.get("score")
                    break
        best_alt: Optional[dict] = None
        delta: Optional[float] = None
        if chosen_score is not None:
            cands = [a for a in alts
                     if not (a.get("action") == action.type and (a.get("params") or {}) == (action.p or {}))
                     and a.get("considered", True)]
            if cands:
                best_alt = max(cands, key=lambda x: float(x["score"]))
                delta = round(float(chosen_score) - float(best_alt["score"]), 4)
        self._emit(
            "bot_decision",
            turn=int(getattr(self.game, "global_turn", 0)),
            player_id=int(pid),
            player_name=getattr(self.game.players[pid], "name", str(pid)),
            archetype=getattr(brain, "archetype", None),
            action=action.type,
            params={k: _jf(v) for k, v in (action.p or {}).items()},
            applied_ok=(None if result is None else bool(getattr(result, "success", False))),
            error=(getattr(result, "error", None) if result is not None else None),
            standings_pre=standings_pre,
            leader_id=leader_id,
            chosen_hex_context=chosen_hex_context,
            alignment_before=alignment_before,
            alignment_after=alignment_after,
            alignment_score_before=alignment_score_before,
            alignment_score_after=alignment_score_after,
            raider_hex_before=raider_hex_before,
            score=chosen_score,
            reason=dec.get("reason"),
            goal=dec.get("goal"),
            best_alternative=best_alt,
            score_delta=delta,
            alternatives=alts,
            top_candidates=(dec.get("top_candidates") or [])[:12],
            vp=dec.get("vp"),
            resources=dec.get("resources"),
        )

    def emit_alignment_event(self, kind: str, pid: int, turn: int, score_after: Optional[int] = None,
                             before: Optional[str] = None, after: Optional[str] = None,
                             delta: Optional[int] = None) -> None:
        """Событие стороны: переход фракции или случай срабатывания цены."""
        self._emit("alignment_event", detail=kind, player_id=int(pid), turn=int(turn),
                   before=before, after=after, score_after=score_after, delta=delta)

    def record_human_action(self, pid: int, action: Any, result: Any, note: str = "",
                            hex_context: Optional[dict] = None,
                            alignment_before: Optional[str] = None,
                            alignment_after: Optional[str] = None) -> None:
        self._emit(
            "human_action",
            turn=int(getattr(self.game, "global_turn", 0)),
            player_id=int(pid),
            action=action.type,
            params={k: _jf(v) for k, v in (action.p or {}).items()},
            applied_ok=(None if result is None else bool(getattr(result, "success", False))),
            error=(getattr(result, "error", None) if result is not None else None),
            note=note,
            hex_context=hex_context,
            alignment_before=alignment_before,
            alignment_after=alignment_after,
        )

    # ------------------------------------------------------------------
    def snapshot(self, tag: str) -> None:
        g = self.game
        players = {}
        for p in g.players:
            players[p.player_id] = {
                "name": p.name,
                "vp": int(g.get_victory_points(p.player_id)),
                "resources": {str(r): int(n) for r, n in p.resources.items()},
            }
        snap = {
            "tag": tag,
            "turn": int(getattr(g, "global_turn", 0)),
            "phase": _jf(getattr(g, "phase", None)),
            "players": players,
            "blight_internal": self.blight_state(),
        }
        self.snapshots.append(snap)
        self._emit("snapshot", **snap)

    # ------------------------------------------------------------------
    def dump(self) -> dict[str, Any]:
        g = self.game
        self.drain_journal()

        # per-player action statistics from the trace
        by_player: dict[int, Counter] = defaultdict(Counter)
        decisions = 0
        for ev in self.events:
            if ev["kind"] in ("bot_decision", "human_action") and ev.get("applied_ok"):
                by_player[ev["player_id"]][ev["action"]] += 1
                if ev["kind"] == "bot_decision":
                    decisions += 1

        winner = getattr(g, "winner_id", None)
        summary = {
            "label": self.label,
            "room_id": getattr(g, "room_id", None),
            "seed": getattr(g, "_seed", getattr(g, "seed", None)),
            "duration_s": round(time.time() - self.t0, 1),
            "turns": int(getattr(g, "global_turn", 0)),
            "phase": _jf(getattr(g, "phase", None)),
            "winner_id": winner,
            "winner_name": getattr(g.players[winner], "name", None) if winner is not None and winner < len(g.players) else None,
            "points_to_win": getattr(g, "points_to_win", None),
            "config": getattr(g, "ext", {}).get("local_server_config"),
            "bot_types": getattr(g, "ext", {}).get("bot_types"),
            "players": [
                {
                    "player_id": p.player_id,
                    "name": p.name,
                    "is_bot": p.player_id in (getattr(g, "bot_pids", set()) or set()),
                    "vp": int(g.get_victory_points(p.player_id)),
                    "actions": dict(by_player.get(p.player_id, Counter())),
                }
                for p in g.players
            ],
            "blight_final_internal": self.blight_state(),
            "journal": [ev["msg"] for ev in self.events if ev["kind"] == "journal"],
            "decisions_recorded": decisions,
            "plugin_errors": getattr(g, "ext", {}).get("ai_plugin_score_errors", []),
        }

        if self.summary_only:
            with open(self.out_dir / "summary.json", "w", encoding="utf-8") as fh:
                json.dump(summary, fh, ensure_ascii=False, indent=1, default=_jf)
            return summary

        with open(self.out_dir / "trace.jsonl", "w", encoding="utf-8") as fh:
            for ev in self.events:
                fh.write(json.dumps(ev, ensure_ascii=False, default=_jf) + "\n")
        with open(self.out_dir / "summary.json", "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1, default=_jf)

        # human-readable transcript
        lines = [f"# Партия «{self.label}» — {summary.get('winner_name') or 'без победителя'}",
                 f"Ходов: {summary['turns']}, время: {summary['duration_s']} c", "", "## Журнал"]
        for ev in self.events:
            if ev["kind"] == "journal":
                lines.append(f"[T{ev['turn']:>3}] {ev['msg']}")
        lines += ["", "## Решения ботов (итоговый выбор; score — итоговая оценка шкалы)"]
        for ev in self.events:
            if ev["kind"] != "bot_decision":
                continue
            top = ev.get("top_candidates") or []
            top_s = "; ".join(
                f"{c.get('action')}={c.get('score')}" for c in top[:3] if c.get("considered")
            )
            lines.append(
                f"[T{ev['turn']:>3}] {ev['player_name']} ({ev.get('archetype')}): "
                f"{ev['action']} {ev['params'] or ''} score={ev.get('score')} «{ev.get('reason')}»"
                + (f" | альт.best={ev['best_alternative'].get('action')}:{ev['best_alternative'].get('score')} Δ={ev.get('score_delta')}" if ev.get("best_alternative") else "")
            )
        (self.out_dir / "transcript.txt").write_text("\n".join(lines), encoding="utf-8")
        return summary
