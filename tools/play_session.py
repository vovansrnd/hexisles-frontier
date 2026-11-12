"""play_session — драйвер headless-партий лабораторной копии (v3).

Два режима:

1. ``--auto`` — все места играют ботами (состав как в лобби v5: c3po,r2d2,t800).
   Полная трассировка решений через tools/ai_debugger.py.

2. Человек-в-контуре (без --auto): место 0 — человек. Драйвер останавливается
   на каждом решении человека и ждёт файл decision.json:

       {"actions": [{"type": "build_outpost", "p": {"vertex_id": 42}}, ...],
        "note": "почему"}

   Действия исполняются по порядку, пока они легальны. Состояние для решения
   пишется в turn.json — ТОЛЬКО информация игрока 0 (туман честно отфильтрован
   движком; внутренние данные в turn.json не попадают).

   Автоматически (без вопросов) исполняются только безальтернативные действия:
   roll_dice, когда это единственный легальный ход.

Запуск:
    python3 tools/play_session.py --auto --seed 1 --out sessions/bvb_s1
    python3 tools/play_session.py --seed 11 --me "Зодчий" --out sessions/my1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import server_local_v4 as S  # noqa: E402
from isles_core import GamePhase  # noqa: E402
from ai_debugger import AIDebugger  # noqa: E402

# Дефолт лобби клиента v5 (static_v5/js/ui/lobby.js) — «настройки старта как есть».
UI_DEFAULT_BOTS = ("c3po", "r2d2", "t800")


def make_session_game(room_id: str, seed: int, bots) -> "S.HexGame":  # type: ignore[name-defined]
    cfg = {
        "points_to_win": 12,
        "fog_of_war": True,
        "fast_start": False,
        "friendly_raider": True,
        "blight": True,
        "blight_mode": "factions",
        "cult_visibility": "open",
        "blight_spread_chance": 0.40,
        "routes": False,
        "bot_difficulty": "skynet",
    }
    g = S.make_game(room_id, cfg, seed=seed)
    return g


def hex_audit_context(g, hid) -> dict:
    """Аудит-контекст гекса ДЛЯ ТРАССЫ. Вызывается СТРОГО до apply_action.

    Внутренние данные движка (артефакт аудита, игрок их не видит; для ботов
    и принятия решений не используется — только пост-анализ трасс).
    """
    try:
        tile = g.map.hexes[int(hid)]
    except (KeyError, TypeError, ValueError):
        return {}
    blight_ext = (g.ext.get("blight") or {}).get("players") or {}
    owners = []
    for vid in tile.vertex_ids:
        v = g.map.vertices[vid]
        owner = v.structure_owner
        if owner is None:
            continue
        st = blight_ext.get(str(owner), {})
        owners.append({
            "pid": int(owner),
            "structure": str(v.structure_type).split(".")[-1],
            "vp": int(g.get_victory_points(owner)),
            "alignment": st.get("alignment"),
        })
    return {
        "hex_id": int(hid),
        "pips": {"2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "8": 5, "9": 4, "10": 3, "11": 2, "12": 1}.get(
            str(int(getattr(tile, "number_token", 0) or 0)), 0),
        "stage": int(tile.ext.get("blight_stage", 0) or 0),
        "shield_until": tile.ext.get("shield_until"),
        "owners": owners,
    }


def blight_alignment(g, pid: int):
    """Сторона игрока по внутреннему состоянию мода (аудит-артефакт)."""
    st = ((g.ext.get("blight") or {}).get("players") or {}).get(str(pid))
    return st.get("alignment") if st else None


def blight_score(g, pid: int):
    st = ((g.ext.get("blight") or {}).get("players") or {}).get(str(pid))
    return st.get("alignment_score") if st else None


def score_alternatives(g, brain, pid: int, phase, legal) -> list[dict]:
    """Оценки ВСЕХ легальных альтернатив ДО исполнения действия (п.6 поручения).

    Одна система оценок, одно состояние (до действия):
      MAIN  — brain._score_action (та же функция, которой бот выбирает;
              trade_bank/propose_trade в ней -1e6, т.к. считаются отдельно
              _best_bank_trade/_best_player_trade — их не дублируем);
      RAIDER_MOVE — brain._raider_target_score;
      RAIDER_STEAL — формула victim_score из choose_action (точная копия);
      DISCARD — brain._score_discard с текущей целью бота;
      SETUP — brain._vertex_score / _setup_second_vertex_score;
      SETUP_ROUTE — точная копия inline-формулы choose_action.
    Только чтение; игровой код не изменяется и не вызывается на запись.
    """
    alts: list[dict] = []
    if brain is None:
        return alts

    def _safe(fn, *args, **kwargs):
        try:
            v = fn(*args, **kwargs)
            return float(v) if v is not None else None
        except Exception:
            return None

    goal = getattr(brain, "current_goal", None)

    if phase == GamePhase.MAIN:
        for a in legal:
            if a.type in ("respond_trade",):
                continue
            if a.type in ("trade_bank", "propose_trade"):
                # отдельные подсистемы оценок; точный дубль здесь не считаем
                alts.append({"action": a.type, "params": {k: _jf2(v) for k, v in a.p.items()},
                             "score": None, "considered": True, "note": "scored by trade search"})
                continue
            sc = _safe(brain._score_action, a, goal, legal)
            item = {"action": a.type, "params": {k: _jf2(v) for k, v in a.p.items()},
                    "score": sc, "considered": bool(sc is not None and sc > 0.15)}
            if a.type in ("corrupt_hex", "purify_hex", "move_raider", "build_metropolis", "build_outpost"):
                hid = a.p.get("hex_id")
                if hid is not None:
                    item["hex_context"] = hex_audit_context(g, hid)
            alts.append(item)
        return alts
    if phase == GamePhase.RAIDER_MOVE:
        for a in legal:
            if a.type == "move_raider":
                hid = a.p.get("hex_id")
                alts.append({"action": "move_raider", "params": {"hex_id": hid},
                             "score": _safe(brain._raider_target_score, int(hid)),
                             "considered": True,
                             "hex_context": hex_audit_context(g, hid)})
    elif phase == GamePhase.RAIDER_STEAL:
        leader = None
        try:
            leader = brain._leader_id()
        except Exception:
            pass
        for a in legal:
            if a.type != "choose_victim":
                continue
            vid = int(a.p.get("victim_id", -1))
            if not 0 <= vid < len(g.players):
                continue
            try:
                vp = brain._vp(vid, True)
                total = int(g.players[vid].total_resources)
            except Exception:
                continue
            score = 0.8 * vp + 0.25 * min(total, 8) + (0.7 if vid == leader else 0.0)
            alts.append({"action": "choose_victim", "params": {"victim_id": vid},
                         "score": score, "considered": True})
    elif phase == GamePhase.DISCARD:
        for a in legal:
            if a.type == "discard":
                alts.append({"action": "discard", "params": {k: _jf2(v) for k, v in a.p.items()},
                             "score": _safe(brain._score_discard, a, goal),
                             "considered": True})
    elif phase == GamePhase.SETUP:
        is_second = getattr(g, "setup_index", 0) >= len(g.players)
        for a in legal:
            if a.type == "setup_outpost":
                vid = int(a.p.get("vertex_id", -1))
                if is_second and getattr(brain, "_setup_anchor_vertex", None) is not None:
                    score = _safe(brain._setup_second_vertex_score, brain._setup_anchor_vertex, vid)
                else:
                    score = _safe(brain._vertex_score, vid, 2 if is_second else 1)
                alts.append({"action": "setup_outpost", "params": a.p,
                             "score": score, "considered": True})
    elif phase == GamePhase.SETUP_ROUTE:
        for a in legal:
            if a.type != "setup_route":
                continue
            try:
                eid = int(a.p.get("edge_id", -1))
                edge = g.map.edges[eid]
                nxt = (edge.vertices[1] if edge.vertices[0] == g.last_setup_vertex
                       else edge.vertices[0])
                score = 0.2 + brain._vertex_production_score(nxt) * 0.35
                score += (0.25 * sum(1 for h in g.map.vertices[nxt].adjacent_hexes
                                     if not brain._hex_visible(h))
                          * brain.personality.fog_curiosity)
                alts.append({"action": "setup_route", "params": a.p,
                             "score": float(score), "considered": True})
            except Exception:
                alts.append({"action": "setup_route", "params": a.p,
                             "score": None, "considered": True})
    return alts


def _jf2(v):
    return getattr(v, "value", v)


def apply_cult_price(g, price_exit: float, price_step: float, dbg) -> None:
    """Runtime-обёртка A1 над модом (только лаборатория; игровые файлы не меняются).

    Цена вычитается из оценки purify_hex у культистов при рискованном остатке:
    score_after ≤ 0 → price_exit; score_after == 1 → price_step.
    Дополнительно инструментируются переходы стороны (в трассу).
    """
    from mods_pack import BlightMod
    mod = next((p for p in g._plugins if isinstance(p, BlightMod)), None)
    if mod is None:
        raise RuntimeError("cult-price requires the blight mod")
    orig_score = mod.score_ai_action
    orig_change = mod._change_alignment

    def priced(game_, pid, action, context=None):
        base = orig_score(game_, pid, action, context)
        if action.type != "purify_hex" or base is None:
            return base
        st = mod._player_blight_state(game_, pid)
        if str(st.get("alignment")) != "cult":
            return base
        after = int(st.get("alignment_score", 0)) - 1
        if after <= 0 and price_exit:
            dbg.emit_alignment_event("priced_exit", pid, int(game_.global_turn), after)
            return base - price_exit
        if after == 1 and price_step:
            dbg.emit_alignment_event("priced_step", pid, int(game_.global_turn), after)
            return base - price_step
        return base

    def tracked(game_, pid, delta):
        before = str(mod._player_blight_state(game_, pid).get("alignment", "neutral"))
        result = orig_change(game_, pid, delta)
        after = str(result)
        if before != after:
            dbg.emit_alignment_event("transition", pid, int(game_.global_turn), None,
                                     before=before, after=after, delta=int(delta))
        return result

    mod.score_ai_action = priced
    mod._change_alignment = tracked


def apply_bot_step(g) -> bool:
    """Один шаг бота/движка; False — ход человека или игра окончена."""
    if g.phase in (GamePhase.LOBBY, GamePhase.FINISHED):
        return False
    offer = g.active_trade_offer
    if offer is not None:
        pend = [p for p in g.bot_pids if p != offer.from_player and p not in offer.rejected_by]
        if pend:
            brain = g.bot_brains.get(pend[0])
            acc = bool(brain.respond_to_trade(offer)) if brain else False
            g.respond_trade(pend[0], acc)
            return True
        # боты закончили: либо ждём ответ человека, либо оффер уже разрешён
        return False
    if g.phase == GamePhase.DISCARD:
        pend = sorted(p for p in g.pending_discards if p in g.bot_pids)
        if not pend:
            return False
        pid = pend[0]
    else:
        pid = g.current_player_id
        if pid not in g.bot_pids:
            return False
    legal = g.enumerate_legal_actions(pid)
    if not legal:
        if g.phase == GamePhase.MAIN:
            g.end_turn(pid)
            return True
        return False
    brain = g.bot_brains.get(pid)
    a = brain.choose_action(pid, legal) if brain else legal[0]
    if not any(a is x for x in legal):
        a = next((x for x in legal if x.type == a.type and x.p == a.p), legal[0])
    # --- ВСЁ ДИАГНОСТИЧЕСКОЕ — ДО apply_action (п.1, 3, 5, 6 поручения) ---
    phase_before = g.phase
    leader_id = None
    standings_pre = {int(p.player_id): int(g.get_victory_points(p.player_id)) for p in g.players}
    align_before = blight_alignment(g, pid) if phase_before == GamePhase.MAIN else None
    score_before = blight_score(g, pid) if phase_before == GamePhase.MAIN else None
    raider_hex_before = getattr(g, "raider_hex", None)
    if brain is not None:
        try:
            leader_id = brain._leader_id()
        except Exception:
            pass
    alternatives_pre = score_alternatives(g, brain, pid, phase_before, legal)
    chosen_ctx = None
    if a is not None and a.p.get("hex_id") is not None:
        chosen_ctx = hex_audit_context(g, a.p["hex_id"])
    a_type = a.type if a is not None else None
    a_params = {k: _jf2(v) for k, v in (a.p or {}).items()} if a is not None else {}
    r = g.apply_action(pid, a)
    align_after = blight_alignment(g, pid) if a_type in ("purify_hex", "corrupt_hex") else None
    score_after = blight_score(g, pid) if a_type in ("purify_hex", "corrupt_hex") else None
    dbg: AIDebugger | None = getattr(g, "_lab_debugger", None)
    if dbg:
        dbg.record_bot_decision(
            pid, a, r,
            alternatives=alternatives_pre,
            leader_id=leader_id,
            standings_pre=standings_pre,
            chosen_hex_context=chosen_ctx,
            alignment_before=align_before,
            alignment_after=align_after,
            alignment_score_before=score_before,
            alignment_score_after=score_after,
            raider_hex_before=raider_hex_before,
        )
    if not r.success and g.phase == GamePhase.MAIN:
        g.end_turn(pid)
    return True


def run_bots_until_human(g, dbg, limit=20000) -> int:
    n = 0
    while n < limit:
        dbg.drain_journal()
        if not apply_bot_step(g):
            break
        n += 1
    dbg.drain_journal()
    return n


# ---------------------------------------------------------------------------
# Человек-в-контуре: chooser-протокол на движковом run_to_completion
# ---------------------------------------------------------------------------

def _match_action(legal, wanted: dict):
    wtype = wanted.get("type")
    wp = wanted.get("p") or {}
    for a in legal:
        if a.type != wtype:
            continue
        ap = {k: v for k, v in a.p.items()}
        if all(ap.get(k) == v for k, v in wp.items()):
            return a
    return None


class _HumanProtocol:
    """Файловый протокол решений человека для chooser-колбэка движка."""

    def __init__(self, g, dbg: AIDebugger, out: Path, human_pid: int):
        self.g = g
        self.dbg = dbg
        self.out = out
        self.human_pid = human_pid
        self.seq = 0
        self.plan: list[dict] = []

    def dump_turn(self, legal) -> None:
        payload = {
            "seq": self.seq,
            "phase": str(self.g.phase),
            "state": S.state_for(self.g, self.human_pid),
            "legal": [a.to_dict() for a in legal],
            "journal_tail": [e.get("msg", str(e)) for e in self.g.log[-30:]],
        }
        tmp = self.out / "turn.json.new"
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.out / "turn.json")
        dec = self.out / "decision.json"
        if dec.exists():
            dec.unlink()
        print(f"[human] waiting decision seq={self.seq} phase={self.g.phase} legal={len(legal)}", flush=True)

    def wait_decision(self, timeout_s: float = 21600) -> dict:
        dec = self.out / "decision.json"
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if dec.exists():
                try:
                    return json.loads(dec.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    time.sleep(0.3)
            time.sleep(0.3)
        raise TimeoutError("decision.json не появился")

    def choose(self, legal) -> Any:
        """Вернуть действие человека; ПРИМЕНЯЕТ ДВИЖОК (chooser не должен применять)."""
        if len(legal) == 1 and legal[0].type == "roll_dice":
            self.dbg.record_human_action(self.human_pid, legal[0], None, "auto: бросок")
            return legal[0]
        if not self.plan:
            self.dump_turn(legal)
            decision = self.wait_decision()
            self.seq += 1
            note = str(decision.get("note", ""))[:200]
            self.plan = decision.get("actions", [])
            self.dbg._emit("human_decision_note", seq=self.seq, note=note,
                           wanted=[w.get("type") for w in self.plan])
        while self.plan:
            wanted = self.plan.pop(0)
            act = _match_action(legal, wanted)
            if act is None and wanted.get("type") == "discard":
                from isles_core import Action
                cand = Action.make("discard", **(wanted.get("p") or {"resources": wanted.get("resources", {})}))
                act = next((a for a in legal if a.type == "discard" and a.p == cand.p), None)
            if act is None:
                self.dbg._emit("human_action", turn=self.g.global_turn, player_id=self.human_pid,
                               action=wanted.get("type"), params=wanted.get("p", {}),
                               applied_ok=False, error="not legal (plan item)", note="")
                continue
            hctx = hex_audit_context(self.g, act.p["hex_id"]) if act.p.get("hex_id") is not None else None
            al_before = blight_alignment(self.g, self.human_pid) if act.type in ("purify_hex", "corrupt_hex") else None
            sc_before = blight_score(self.g, self.human_pid) if act.type in ("purify_hex", "corrupt_hex") else None
            self.dbg.record_human_action(self.human_pid, act, None, "", hex_context=hctx,
                                         alignment_before=al_before, alignment_after=None)
            if act.type in ("purify_hex", "corrupt_hex"):
                self.dbg.emit_alignment_event("human_mod_action", self.human_pid,
                                              int(self.g.global_turn), None, before=al_before)
            self.dbg.drain_journal()
            return act
        end = next((a for a in legal if a.type == "end_turn"), None)
        if end is not None:
            self.dbg.record_human_action(self.human_pid, end, None, "фолбэк: конец хода")
            return end
        return legal[0]


def make_chooser(g, dbg: AIDebugger, out: Path, human_pid: Optional[int]):
    proto = _HumanProtocol(g, dbg, out, human_pid) if human_pid is not None else None

    def chooser(pid: int, legal_actions: list) -> Any:
        dbg.drain_journal()
        if proto is not None and pid == human_pid:
            return proto.choose(legal_actions)
        brain = g.bot_brains.get(pid)
        a = brain.choose_action(pid, legal_actions) if brain else legal_actions[0]
        if not any(x is a for x in legal_actions):
            a = next((x for x in legal_actions if x.type == a.type and x.p == a.p), legal_actions[0])
        alts = score_alternatives(g, brain, pid, g.phase, legal_actions)
        dbg.record_bot_decision(pid, a, None, alternatives=alts)
        return a

    return chooser


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--room", default=None)
    ap.add_argument("--auto", action="store_true", help="все места — боты")
    ap.add_argument("--me", default=None, help="имя человека")
    ap.add_argument("--human-seat", type=int, default=0, dest="human_seat",
                    help="место человека (0..3): до добавления человека ставится N ботов")
    ap.add_argument("--bots", default=",".join(UI_DEFAULT_BOTS))
    ap.add_argument("--cult-price", default=None, dest="cult_price",
                    help="условная цена 'exit,step' (runtime-обёртка, только лаборатория)")
    ap.add_argument("--max-steps", type=int, default=5000)
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--label", default=None)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    bots = [b.strip() for b in args.bots.split(",") if b.strip()]

    room = args.room or f"LAB{args.seed:04d}"
    g = make_session_game(room, args.seed, bots)
    human_pid = None
    human_seat = None
    if args.auto:
        for b in bots:
            ok, msg = S.add_bot(g, b)
            if not ok:
                print("bot not added:", b, msg)
    else:
        human_name = args.me or "Человек"
        seat = max(0, min(int(args.human_seat), 3))
        for b in bots[:seat]:
            ok, msg = S.add_bot(g, b)
            if not ok:
                print("bot not added:", b, msg)
        human_pid, _p = S.add_human(g, human_name)
        human_pid = int(human_pid)
        human_seat = seat
        for b in bots[seat:]:
            ok, msg = S.add_bot(g, b)
            if not ok:
                print("bot not added:", b, msg)

    label = args.label or f"{room}_{'auto' if args.auto else 'human@' + str(human_seat)}"
    dbg = AIDebugger(g, out, label=label, summary_only=args.summary_only)
    g._lab_debugger = dbg
    if args.cult_price:
        pe, ps = (float(x) for x in args.cult_price.split(","))
        apply_cult_price(g, pe, ps, dbg)
        dbg._emit("experiment_arm", price_exit=pe, price_step=ps, human_seat=human_seat)
        print(f"A-обёртка: exit={pe} step={ps}, человек на месте {human_seat} (pid {human_pid})", flush=True)
    g.start_game()
    dbg.drain_journal()

    chooser = make_chooser(g, dbg, out, human_pid)
    winner = g.run_to_completion(chooser, max_steps=args.max_steps)
    dbg.drain_journal()
    dbg.snapshot("final")
    summary = dbg.dump()
    print(json.dumps({
        "label": summary["label"],
        "turns": summary["turns"],
        "winner": summary["winner_name"],
        "vp": {p["name"]: p["vp"] for p in summary["players"]},
        "mod_vp": {p["name"]: summary["blight_final_internal"]["players"].get(p["player_id"], {}).get("alternate_vp") for p in summary["players"]},
        "out": str(out),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
