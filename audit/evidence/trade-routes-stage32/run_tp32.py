#!/usr/bin/env python3
"""run_tp32 — Этап 3.2: A0 (9af21df, прежняя оценка) vs A1 (f1fff48, портфельная
оценка Trade Routes), routes ON, 20 свежих сидов × 24 рассадки.

Read-only диагностика решений маршрутов: в каждом вызове chooser, где среди
легальных действий есть activate_trade_route, фиксируются кандидаты (ресурс,
длина пути, оценка мода через brain._score_extension_action — тот же контекст,
что у бота), активные маршруты с их ресурсами; после choose_action — выбранное
действие. Состояние, RNG и порядок кандидатов не затрагиваются.

Запуск: python3 tools/run_tp32.py --arm A0|A1 --seeds 71001-71020 --workers 4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import re
import sys
import time
import traceback
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
LAB_STR = str(LAB)
LAB_TOOLS = str(LAB / "tools")
WT = {"A0": "/opt/MyApps/2424/hf-tr0", "A1": "/opt/MyApps/2424/hf-tr1"}
CODE_HEAD = {"A0": "9af21df", "A1": "f1fff48"}


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
            for stale in ("trace.jsonl", "summary.json", "transcript.txt", "proposals.jsonl"):
                (out_dir / stale).unlink(missing_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        seats_expr = job["seats"]
        seats = tuple(seats_expr.split(","))
        g = S.make_game(f"TP32{arm}{seed}P{perm:02d}", {
            "points_to_win": 12, "fog_of_war": True, "fast_start": False,
            "friendly_raider": True, "blight": False, "routes": True,
            "bot_difficulty": "skynet"}, seed=seed)
        row["map_md5"] = _map_fingerprint(g)
        row["config"] = {"blight": False, "routes": True, "fog": True,
                         "points_to_win": 12, "difficulty": "skynet",
                         "one_income_per_turn": "standard"}
        for b in seats:
            ok, msg = S.add_bot(g, b)
            if not ok:
                raise RuntimeError(f"bot {b}: {msg}")
        row["seats"] = {str(p.player_id): g.ext["bot_types"][str(p.player_id)] for p in g.players}
        dbg = AIDebugger(g, out_dir, label=f"TP32{arm}{seed}P{perm:02d}")
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

        def chooser(pid: int, legal_actions):
            dbg.drain_journal()
            brain = g.bot_brains.get(pid)
            offer_pre = g.active_trade_offer
            # --- read-only диагностика решений маршрутов ---
            route_acts = [x for x in legal_actions if x.type == "activate_route"]
            if route_acts and brain is not None:
                goal = getattr(brain, "current_goal", None)
                active = [{"resource": str(r.get("trade_resource")),
                           "suppressed": int(r.get("suppressed_until", -1)) > g.global_turn}
                          for r in (g.players[pid].ext.get("active_routes") or [])
                          if isinstance(r, dict)]
                cands = []
                for act in route_acts:
                    p = act.p or {}
                    cands.append({
                        "trade_resource": str(p.get("trade_resource")),
                        "path_len": len(p.get("edge_path") or ()),
                    })
                    try:
                        sc = brain._score_extension_action(act, goal)
                        cands[-1]["mod_score"] = None if sc is None else round(float(sc), 5)
                    except Exception as exc:
                        cands[-1]["mod_score_error"] = repr(exc)[:120]
                dbg._emit("route_decision",
                          turn=int(g.global_turn), player=int(pid),
                          goal_kind=getattr(goal, "kind", None),
                          goal_needed=_norm(getattr(goal, "needed", {}) or {}) or None,
                          hand=_norm(g.players[pid].resources),
                          active_routes=active,
                          n_candidates=len(cands), candidates=cands)
            a = brain.choose_action(pid, legal_actions) if brain else legal_actions[0]
            if not any(x is a for x in legal_actions):
                a = next((x for x in legal_actions
                          if x.type == a.type and x.p == a.p), legal_actions[0])
            if a.type == "activate_route":
                dbg._emit("route_activation", turn=int(g.global_turn), player=int(pid),
                          trade_resource=str(a.p.get("trade_resource")),
                          path_len=len(a.p.get("edge_path") or ()))
            if a.type == "propose_trade":
                dbg._emit("trade_proposal", turn=int(g.global_turn), proposer=int(pid),
                          give=_norm(a.p.get("give")), want=_norm(a.p.get("want")))
            if a.type == "respond_trade" and offer_pre is not None:
                dbg._emit("trade_response", turn=int(g.global_turn), responder=int(pid),
                          from_player=int(offer_pre.from_player),
                          give=_norm(offer_pre.give), want=_norm(offer_pre.want),
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
    ap.add_argument("--arm", choices=["A0", "A1"], required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seeds", default="71001-71020")
    ap.add_argument("--out-root", default=None)
    args = ap.parse_args()
    import re
    m = re.fullmatch(r"(\d+)-(\d+)", args.seeds)
    seeds = list(range(int(m.group(1)), int(m.group(2)) + 1))
    seats_exprs = []
    import itertools
    for p in itertools.permutations(("c3po", "r2d2", "t800", "walle")):
        seats_exprs.append(",".join(p))
    out_root = Path(args.out_root) if args.out_root else LAB / "sessions" / "tp32"
    out_root.mkdir(parents=True, exist_ok=True)
    jobs = [{"arm": args.arm, "worktree": WT[args.arm], "seed": s, "perm": i,
             "seats": expr, "out_root": str(out_root)}
            for s in seeds for i, expr in enumerate(seats_exprs)]
    manifest = out_root / f"manifest_{args.arm}.jsonl"
    print(f"{args.arm}: партий {len(jobs)} | воркеров {args.workers} | worktree {WT[args.arm]}",
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
    print(f"{args.arm}: готово за {(time.time() - t0) / 60:.1f} мин", flush=True)


if __name__ == "__main__":
    main()
