#!/usr/bin/env python3
"""Corrective Stage 4.2 replay: capture the actual chooser's binary64 scores.

Scores are tapped from the real BotBrain._score_action return values during
choose_action(), after choose_action() resolves _get_goal(legal_actions).
No scorer is called a second time for logging. Candidate records retain
candidate_order, chooser_goal, and score_observed_by_chooser. The six-decimal
mod_score remains for Stage 4.1 compatibility.

Партии идентичны Stage 4.1: seeds 72001-72020, 24 рассадки, A0 (main@3590371)
и A1 (f84f978), та же конфигурация. Новые файлы: sessions/tp42/.

Запуск: python3 tools/run_tp42.py --arm both --workers 4 --seeds 72001-72020
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import random
import re
import sys
import time
import traceback
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
LAB_STR = str(LAB)
LAB_TOOLS = str(LAB / "tools")
WT = {"A0": "/opt/MyApps/2424/hf-b0", "A1": "/opt/MyApps/2424/hf-b1"}
CODE_HEAD = {"A0": "3590371", "A1": "f84f978"}
BOTS = ("c3po", "r2d2", "t800", "walle")
CONFIG = {"points_to_win": 12, "fog_of_war": True, "fast_start": False,
          "friendly_raider": True, "blight": True, "blight_mode": "factions",
          "cult_visibility": "open", "blight_spread_chance": 0.40,
          "routes": False, "bot_difficulty": "skynet",
          "cult_suppression_theft": False, "cult_corrupt_tear": False}
DICE_PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}


def _goal_snapshot(goal):
    if goal is None:
        return None
    return {
        "kind": getattr(goal, "kind", None),
        "target_id": getattr(goal, "target_id", None),
        "score": float(getattr(goal, "score", 0.0)),
        "needed": {str(getattr(k, "value", k)): int(v)
                   for k, v in (getattr(goal, "needed", {}) or {}).items()},
        "created_turn": int(getattr(goal, "created_turn", 0)),
        "locked_until_turn": int(getattr(goal, "locked_until_turn", 0)),
    }


def choose_action_with_actual_score_capture(brain, player_id, legal_actions):
    """Capture _score_action values from the real choose_action pass."""
    captured_scores, captured_goals = {}, {}
    original_method = brain._score_action
    namespace = getattr(brain, "__dict__", {})
    had_override = "_score_action" in namespace
    previous_override = namespace.get("_score_action")

    def capture_score(action, goal, actions):
        value = original_method(action, goal, actions)
        if action.type == "corrupt_hex":
            key = id(action)
            if key in captured_scores:
                raise RuntimeError("duplicate _score_action capture for corrupt_hex")
            captured_scores[key] = float(value)
            captured_goals[key] = _goal_snapshot(goal)
        return value

    brain._score_action = capture_score
    try:
        chosen = brain.choose_action(player_id, legal_actions)
    finally:
        if had_override:
            brain._score_action = previous_override
        else:
            delattr(brain, "_score_action")
    return chosen, captured_scores, captured_goals


def _norm(d):
    return {str(getattr(k, "value", k)): int(v) for k, v in (d or {}).items()}


def _map_fingerprint(g) -> str:
    payload = {
        "hexes": sorted((int(h), str(getattr(t, "terrain", "")),
                         int(getattr(t, "number_token", 0) or 0))
                        for h, t in g.map.hexes.items()),
        "ports": sorted((int(p), str(getattr(pt, "kind", "")))
                        for p, pt in getattr(g.map, "ports", {}).items()),
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _integrity(g) -> dict:
    plugins = [type(p).__name__ for p in getattr(g, "_plugins", [])]
    journal_bad = sum(1 for e in g.log
                      if any(m in str(e.get("msg") or e.get("message") or e).lower()
                             for m in ("скверн", "осквернил", "очистил", "культ", "слёз")))
    return {"plugins": plugins, "blight_mod_present": "BlightMod" in plugins,
            "ext_blight_state": bool(g.ext.get("blight")),
            "blight_hexes": sum(1 for t in g.map.hexes.values()
                                if int(t.ext.get("blight_stage", 0) or 0) > 0),
            "journal_blight_count": journal_bad}


def state_hash(g) -> str:
    """Полный снимок состояния (без усечений) для read-only контроля."""
    return hashlib.md5(json.dumps({
        "global_turn": int(g.global_turn),
        "current_player": int(g.current_player_id),
        "phase": str(g.phase),
        "rng": [str(x) for x in g.rng.getstate()],
        "vertices": {v: {"owner": g.map.vertices[v].structure_owner,
                         "type": str(g.map.vertices[v].structure_type)}
                     for v in sorted(g.map.vertices)},
        "edges": {e: {"owner": g.map.edges[e].route_owner}
                  for e in sorted(g.map.edges)},
        "resources": {p.player_id: {getattr(k, "value", str(k)): int(v)
                                    for k, v in p.resources.items()}
                      for p in g.players},
        "player_ext": {p.player_id: json.dumps(p.ext, sort_keys=True, default=str)
                       for p in g.players},
        "game_ext": json.dumps(g.ext, sort_keys=True, default=str),
        "log_len": len(g.log),
    }, sort_keys=True, default=str).encode()).hexdigest()


def run_game(job: dict) -> dict:
    arm = job["arm"]
    wt = job["worktree"]
    sys.path.insert(0, LAB_TOOLS)
    sys.path.insert(0, LAB_STR)
    sys.path.insert(0, wt)
    for mod in ("server_local_v4", "isles_core", "mods_pack", "ai_brain",
                "trade_intelligence", "trade_policy_experiments", "play_session"):
        sys.modules.pop(mod, None)
    import server_local_v4 as S
    assert S.__file__.startswith(wt), f"изоляция: S из {S.__file__}"
    import ai_brain as _ab
    assert _ab.__file__.startswith(wt), f"изоляция: ai_brain из {_ab.__file__}"
    from ai_debugger import AIDebugger
    seed, perm = int(job["seed"]), int(job["perm"])
    out_dir = Path(job["out_root"]) / f"{arm}_seed{seed}_p{perm:02d}"
    t0 = time.time()
    row = {"arm": arm, "seed": seed, "perm": perm, "out_dir": str(out_dir),
           "code_head": CODE_HEAD[arm]}
    try:
        if out_dir.exists():
            for stale in ("trace.jsonl", "summary.json", "transcript.txt",
                          "corrupt_decisions.jsonl"):
                (out_dir / stale).unlink(missing_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        seats = job["seats"].split(",")
        # room_id ОБЯЗАН совпадать со Stage 4.1 байт-в-байт: он входит в
        # пер-ботовые RNG (ai_brain: f"{room_id}:{pid}:{seed}:{archetype}")
        # и в blight RNG-стримы (mods_pack: f"HexIsles:...:{seed}:{room_id}").
        # Смена префикса TP41->TP42 ломает воспроизводимость партий.
        g = S.make_game(f"TP41{arm}{seed}P{perm:02d}", dict(CONFIG), seed=seed)
        row["map_md5"] = _map_fingerprint(g)
        row["config"] = {k: (bool(v) if isinstance(v, bool) else v)
                         for k, v in CONFIG.items()}
        for b in seats:
            ok, msg = S.add_bot(g, b)
            if not ok:
                raise RuntimeError(f"bot {b}: {msg}")
        row["seats"] = {str(p.player_id): g.ext["bot_types"][str(p.player_id)]
                        for p in g.players}
        seats_map = {str(p.player_id): g.ext["bot_types"][str(p.player_id)]
                     for p in g.players}
        dbg = AIDebugger(g, out_dir, label=f"TP41{arm}{seed}P{perm:02d}")
        g._lab_debugger = dbg
        pending_apply = {}
        orig_apply = g.apply_action

        def apply_with_result(pid, action, *args, **kwargs):
            result = orig_apply(pid, action, *args, **kwargs)
            seq = pending_apply.pop(int(pid), None)
            if seq is not None and 0 < seq <= len(dbg.events) \
                    and dbg.events[seq - 1].get("kind") == "bot_decision":
                dbg.events[seq - 1]["applied_ok"] = bool(getattr(result, "success", False))
            return result

        g.apply_action = apply_with_result
        mod = next(p for p in g._plugins if type(p).__name__ == "BlightMod")
        max_stage = mod.max_stage

        def chooser(pid: int, legal_actions):
            dbg.drain_journal()
            brain = g.bot_brains.get(pid)
            corrupt_acts = [x for x in legal_actions if x.type == "corrupt_hex"]
            if corrupt_acts and brain is not None:
                # --- READ-ONLY диагностика corrupt_hex ---
                snap_before = state_hash(g)
                rng_before = g.rng.getstate()
                current_goal_before = _goal_snapshot(getattr(brain, "current_goal", None))
                leader = brain._leader_id()
                my_vp = int(g.get_victory_points(pid))
                leader_vp = (int(g.get_victory_points(leader, public=True))
                             if leader is not None else 0)
                align = str((mod._player_blight_state(g, pid) or {})
                            .get("alignment", "neutral"))
                res0 = {getattr(k, "value", str(k)): int(v)
                        for k, v in g.players[pid].resources.items()}
                yield_fn = sys.modules["mods_pack"]._structure_yield
                cands = []
                for order, act in enumerate(corrupt_acts):
                    hid = int(act.p.get("hex_id"))
                    tile = g.map.hexes[hid]
                    stage = int(tile.ext.get("blight_stage", 0))
                    pips = DICE_PIPS.get(int(tile.number_token or 0), 0)
                    enemy_loss = leader_loss = 0.0
                    enemy_n = leader_n = cult_n = 0
                    for vid in tile.vertex_ids:
                        v = g.map.vertices[vid]
                        owner = v.structure_owner
                        if owner is None or owner == pid:
                            continue
                        normal = yield_fn(v.structure_type)
                        before = max(0, normal - min(normal, stage))
                        after = max(0, normal - min(normal,
                                                    min(max_stage, stage + 1)))
                        loss = max(0, before - after)
                        align_o = str((mod._player_blight_state(g, owner) or {})
                                      .get("alignment", "neutral"))
                        if align_o == "cult":
                            cult_n += 1  # иммунитет в factions/open
                            continue
                        enemy_loss += loss
                        enemy_n += 1
                        if owner == leader:
                            leader_loss += loss
                            leader_n += 1
                    impact = enemy_loss * pips
                    cands.append({
                        "candidate_order": order,
                        "hex_id": hid,
                        "resource": getattr(tile.resource, "value",
                                            str(tile.resource)),
                        "pips": pips, "stage": stage,
                        "enemy_buildings": enemy_n,
                        "leader_buildings": leader_n,
                        "cult_buildings": cult_n,
                        "expected_production_loss": round(enemy_loss, 4),
                        "impact": round(impact, 4),
                        "leader_loss_share": (round(leader_loss / enemy_loss, 4)
                                              if enemy_loss else 0.0),
                        "score_observed_by_chooser": False,
                        "mod_score": None,
                        "mod_score_full": None,
                        "mod_score_hex": None,
                        "chooser_goal": None,
                    })
                decision_event_index = len(dbg.events)
                dbg._emit("corrupt_decision", turn=int(g.global_turn),
                          player=int(pid), archetype=seats_map.get(str(pid), "?"),
                          my_vp=my_vp, leader=leader, leader_vp=leader_vp,
                          lead_margin=my_vp - leader_vp, alignment=align,
                          resources=res0, current_goal_before=current_goal_before,
                          n_candidates=len(cands), candidates=cands)
                # контроль read-only: снимки строго вокруг диагностического чтения
                snap_after_diag = state_hash(g)
                rng_ok_diag = rng_before == g.rng.getstate()
                diag_ok = (snap_before == snap_after_diag) and rng_ok_diag
                dbg._emit("corrupt_readonly", state_unchanged=snap_before == snap_after_diag,
                          rng_unchanged=rng_ok_diag, n_candidates=len(cands))
                if not diag_ok:
                    raise RuntimeError("диагностика изменила состояние/RNG")
                a, chooser_scores, chooser_goals = choose_action_with_actual_score_capture(
                    brain, pid, legal_actions
                )
                for candidate, action in zip(cands, corrupt_acts):
                    key = id(action)
                    if key in chooser_scores:
                        value = chooser_scores[key]
                        candidate["score_observed_by_chooser"] = True
                        candidate["mod_score"] = round(value, 6)
                        candidate["mod_score_full"] = value
                        candidate["mod_score_hex"] = value.hex()
                        candidate["chooser_goal"] = chooser_goals.get(key)
                dbg.events[decision_event_index]["candidates"] = cands
                if a.type == "corrupt_hex" and not all(
                    candidate["score_observed_by_chooser"] for candidate in cands
                ):
                    raise RuntimeError(
                        "chooser selected corrupt_hex but some candidates lacked "
                        "scores from the real _score_action pass"
                    )
                if not any(x is a for x in legal_actions):
                    a = next((x for x in legal_actions
                              if x.type == a.type and x.p == a.p), legal_actions[0])
                if a.type == "corrupt_hex":
                    chid = int(a.p.get("hex_id"))
                    ch = next((c for c in cands if c["hex_id"] == chid), None)
                    dbg._emit("corrupt_choice", turn=int(g.global_turn),
                              player=int(pid), hex_id=chid,
                              impact=(ch or {}).get("impact"),
                              candidate_order=(ch or {}).get("candidate_order"),
                              mod_score=(ch or {}).get("mod_score"),
                              mod_score_full=(ch or {}).get("mod_score_full"),
                              mod_score_hex=(ch or {}).get("mod_score_hex"))
                dbg.record_bot_decision(pid, a, None)
                if dbg.events and dbg.events[-1].get("kind") == "bot_decision":
                    pending_apply[int(pid)] = len(dbg.events)
                return a
            # обычный путь (без corrupt-кандидатов)
            a = brain.choose_action(pid, legal_actions) if brain else legal_actions[0]
            if not any(x is a for x in legal_actions):
                a = next((x for x in legal_actions
                          if x.type == a.type and x.p == a.p), legal_actions[0])
            if a.type == "respond_trade" and getattr(g, "active_trade_offer", None) is not None:
                o = g.active_trade_offer
                dbg._emit("trade_response", turn=int(g.global_turn), responder=int(pid),
                          from_player=int(o.from_player),
                          give=_norm(o.give), want=_norm(o.want),
                          accepted=bool(a.p.get("accept")))
            from play_session import score_alternatives, hex_audit_context
            alts = score_alternatives(g, brain, pid, g.phase, legal_actions)
            chosen_ctx = None
            if a is not None and a.p.get("hex_id") is not None:
                chosen_ctx = hex_audit_context(g, a.p["hex_id"])
            dbg.record_bot_decision(pid, a, None, alternatives=alts,
                                    chosen_hex_context=chosen_ctx)
            if dbg.events and dbg.events[-1].get("kind") == "bot_decision":
                pending_apply[int(pid)] = len(dbg.events)
            return a

        g.start_game()
        dbg.drain_journal()
        winner = g.run_to_completion(chooser, max_steps=8000)
        dbg.drain_journal()
        dbg.snapshot("final")
        summary = dbg.dump()
        integrity = _integrity(g)
        row.update({
            "status": "completed",
            "winner_pid": summary.get("winner_id"),
            "winner_type": g.ext["bot_types"].get(str(summary.get("winner_id"))),
            "turns": summary.get("turns"),
            "decisions": summary.get("decisions_recorded"),
            "duration_s": round(time.time() - t0, 1),
            "vp": {p["name"]: p["vp"] for p in summary["players"]},
            "plugin_errors": len(summary.get("plugin_errors") or []),
            "integrity": integrity,
        })
    except Exception:
        row.update({"status": "error", "error": traceback.format_exc()[-1500:],
                    "duration_s": round(time.time() - t0, 1)})
    return row


def _worker(job):
    return run_game(job)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["A0", "A1", "both"], default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seeds", default="72001-72020")
    ap.add_argument("--out-root", default=None)
    args = ap.parse_args()
    m = re.fullmatch(r"(\d+)-(\d+)", args.seeds)
    seeds = list(range(int(m.group(1)), int(m.group(2)) + 1))
    arms = [args.arm] if args.arm in ("A0", "A1") else ["A0", "A1"]
    import itertools
    seats_exprs = [",".join(p) for p in itertools.permutations(BOTS)]
    out_root = Path(args.out_root) if args.out_root else LAB / "sessions" / "tp42"
    out_root.mkdir(parents=True, exist_ok=True)
    jobs = [{"arm": a, "worktree": WT[a], "seed": s, "perm": i,
             "seats": expr, "out_root": str(out_root)}
            for a in arms for s in seeds for i, expr in enumerate(seats_exprs)]
    random.Random(0x41).shuffle(jobs)  # детерминированное перемешивание A0/A1
    manifest = out_root / ("manifest_both.jsonl" if len(arms) == 2
                           else f"manifest_{args.arm}.jsonl")
    print(f"руки {arms}: партий {len(jobs)} | воркеров {args.workers}",
          flush=True)
    t0 = time.time()
    ctx = mp.get_context("spawn")
    done = 0
    with open(manifest, "a", encoding="utf-8") as mf:
        with ctx.Pool(args.workers) as pool:
            for row in pool.imap_unordered(_worker, jobs, chunksize=1):
                mf.write(json.dumps(row, ensure_ascii=False) + "\n")
                mf.flush()
                done += 1
                if done % 60 == 0 or done == len(jobs):
                    print(f"[{done}/{len(jobs)}] {row.get('status')} "
                          f"seed{row.get('seed')} p{row.get('perm'):02d}", flush=True)
    print(f"руки {arms}: готово за {(time.time() - t0) / 60:.1f} мин", flush=True)


if __name__ == "__main__":
    main()
