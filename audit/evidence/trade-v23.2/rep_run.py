#!/usr/bin/env python3
"""run_ab_trade_v25 — Этап 2.5: парный A/B, Legacy (A0) vs Trade Intelligence v23.2 (A1).

Изоляция: два git-worktree (A0 = main @ 3590371, A1 = ветка @ a879444);
каждый воркер — spawn-процесс со своим sys.path (импорты версий не смешиваются).
Shadow-оценки: в A0 модель v23.2 считается лабораторной спецификацией
(trade_features_spec, верифицирована 1:1 с веткой) ТОЛЬКО как тень; решение
принимает legacy. В A1 legacy-прогноз приходит из диагностики ветки как тень;
решает модель. Оба предсказателя оцениваются на одном наборе фактических
предложений внутри каждой руки.

Запуск:
  python3 tools/run_ab_trade_v25.py --smoke        # 2 партии (A0+A1, сид 63001)
  python3 tools/run_ab_trade_v25.py --arm A0 --workers 4
  python3 tools/run_ab_trade_v25.py --arm A1 --workers 4
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
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
WT = {"A0": "/home/claude/ev/hf-a0", "A1": "/home/claude/a2/hf-a2",
      "A2": "/home/claude/a2/hf-a2", "A0x": "/home/claude/a2/hf-a2"}
# Этап 2.6: A2 = ветка @ a879444, пороги допуска пересажены на шкалу модели
# (метод сохранения доли проходящих, калибровка на сидах 63001-63020 Этапа 2.5;
#  финальная оценка — на свежих сидах 64001-64020)
GATE_OVERRIDE_A2 = {"c3po": 0.142, "r2d2": 0.118, "t800": 0.151, "walle": 0.133}
BOTS = ("c3po", "r2d2", "t800", "walle")
PERMS = list(itertools.permutations(BOTS))
RES = ("wood", "clay", "grain", "wool", "ore")
GAIN_RE = re.compile(r"P2P gain=(-?[\d.]+)")


def _setup_paths(wt: str) -> None:
    # ветка/worktree ПЕРВЫМИ: игровой код берётся из своей версии
    sys.path.insert(0, LAB_TOOLS)
    sys.path.insert(0, LAB_STR)
    sys.path.insert(0, wt)


def _norm(d) -> dict[str, int]:
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


def _afford_flags(hand, costs) -> dict:
    h = _norm(hand)
    out = {}
    for name, cost in costs.items():
        c = _norm(cost)
        out[name] = all(h.get(r, 0) >= n for r, n in c.items())
    return out


def run_game(job: dict) -> dict:
    """Одна партия одной руки. Все игровые импорты — внутри (spawn-изоляция)."""
    arm = job["arm"]
    wt = WT[arm]
    # очистка кэша игровых модулей (воркер может обносить несколько партий)
    for mod in ("server_local_v4", "isles_core", "mods_pack", "ai_brain",
                "trade_intelligence", "play_session"):
        sys.modules.pop(mod, None)
    _setup_paths(wt)
    import server_local_v4 as S
    assert S.__file__.startswith(wt), f"изоляция: S из {S.__file__}"
    import ai_brain as _ab
    assert _ab.__file__.startswith(wt), f"изоляция: ai_brain из {_ab.__file__}"
    # ---- AUDIT INSTRUMENTATION (read-only: copies the brain's own diagnostics, no state/RNG access) ----
    import copy as _copy
    _orig_bpt = _ab.BotBrain._best_player_trade
    def _wrapped(self, goal, legal_actions, _o=_orig_bpt):
        res = _o(self, goal, legal_actions)
        try:
            d = getattr(self, "_last_player_trade_search", None)
            lg = getattr(self.game, "_audit_cand_log", None)
            if lg is not None and d is not None:
                cs = []
                for c in d.get("candidates") or []:
                    f = c.get("features") or {}
                    cs.append([c.get("give"), c.get("want"), c.get("gain"), c.get("pred_any_accept"),
                               c.get("legacy_pred_any_accept"), c.get("acceptance_gate"), c.get("accepted_by_gate"),
                               c.get("leader_help_penalty"), c.get("score"), bool(c.get("rejected_by_score")),
                               [f[k] for k in f] if f else None])
                lg.append({"turn": int(self.game.global_turn), "pid": int(self.pid), "status": d.get("status"),
                           "cands": cs, "chosen": res is not None,
                           "sel": d.get("selected_action"), "sel_score": d.get("selected_score")})
        except Exception:
            pass
        return res
    _ab.BotBrain._best_player_trade = _wrapped
    if arm == "A0x":
        import math as _m
        def _est(self, give, want, context):
            feats = _ab.trade_features_from_state(give=give, want=want, **context)
            others = [p.player_id for p in self.game.players if p.player_id != self.pid and p.connected]
            probs = [self._estimate_opponent_acceptance(o, give, want) for o in others]
            return 1.0 - _m.prod(1.0 - x for x in probs), feats
        _ab.BotBrain._estimate_any_opponent_acceptance = _est
    if arm == "A0":
        assert "trade_intelligence" not in sys.modules, "утечка модуля ветки в A0"
    from ai_debugger import AIDebugger
    from isles_core import COST_METROPOLIS, COST_OUTPOST, COST_ROUTE
    if arm == "A0":
        assert "trade_intelligence" not in sys.modules, "утечка модуля ветки в A0"
        import trade_features_spec as lab_spec
        artifact = json.load(open(Path(LAB_STR) / "sessions/noblight_baseline/"
                                           "trade_features_v22/logreg_taskA_model_v23.json",
                                  encoding="utf-8"))

        def model_pred(g, brain, pid, give, want):
            opps = []
            prod = brain._visible_production_by_player()
            standings = {int(p.player_id): int(g.get_victory_points(p.player_id, public=True))
                         for p in g.players}
            for oid in range(len(g.players)):
                if oid == pid:
                    continue
                pv = {getattr(k, "value", str(k)): float(v)
                      for k, v in (prod.get(oid) or {}).items()}
                opps.append({"total_res": int(getattr(g.players[oid], "total_resources", 0)),
                             "public_vp": int(standings.get(oid, 0)),
                             "visible_production": pv})
            me = g.players[pid]
            goal = getattr(brain, "current_goal", None)
            x = lab_spec.features_from_state(
                hand=me.resources, goal_kind=getattr(goal, "kind", None),
                goal_needed=getattr(goal, "needed", {}) or {},
                give=give, want=want, own_vp=int(g.get_victory_points(pid)),
                opponents=opps, global_turn=int(g.global_turn),
                personal_turn=int(getattr(me, "personal_turns_taken", 0)),
                revealed_hexes=len(getattr(me, "revealed_hexes", set())),
                points_to_win=int(g.points_to_win))
            return lab_spec.infer(artifact, x)
    seed = int(job["seed"]); perm = int(job["perm"])
    out_dir = Path(job["out_root"]) / f"{arm}_seed{seed}_p{perm:02d}"
    t0 = time.time()
    row = {"arm": arm, "seed": seed, "perm": perm, "out_dir": str(out_dir)}
    try:
        if out_dir.exists():
            for stale in ("trace.jsonl", "summary.json", "transcript.txt", "proposals.jsonl"):
                (out_dir / stale).unlink(missing_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        seats = tuple(job["seats"]) if job.get("seats") else PERMS[perm]
        g = S.make_game(f"AB{arm}{seed}P{perm:02d}", {
            "points_to_win": 12, "fog_of_war": True, "fast_start": False,
            "friendly_raider": True, "blight": False, "routes": False,
            "bot_difficulty": "skynet"}, seed=seed)
        row["map_md5"] = _map_fingerprint(g)
        row["config"] = {"blight": False, "routes": False,
                         "fog": True, "points_to_win": 12, "difficulty": "skynet"}
        row["code_head"] = {"A0": "3590371", "A1": "a879444", "A2": "a879444+gate", "A0x": "a879444+legacy_patch"}[arm]
        integrity_pre = _integrity(g)
        for b in seats:
            ok, msg = S.add_bot(g, b)
            if not ok:
                raise RuntimeError(f"bot {b}: {msg}")
        g._audit_cand_log = []
        if not globals().get("_ISO_PRINTED"):
            globals()["_ISO_PRINTED"] = True
            _b0 = g.bot_brains[0] if hasattr(g, "bot_brains") else None
            print("ISOLATION server_local_v4:", S.__file__, "| ai_brain:", _ab.__file__, "| S.BotBrain is ai_brain.BotBrain:", getattr(S, "BotBrain", None) is _ab.BotBrain,
                  "| brain type:", type(_b0).__module__ + "." + type(_b0).__name__, "| estimator patched (A0x):", "_est" in getattr(_ab.BotBrain._estimate_any_opponent_acceptance, "__qualname__", ""),
                  "| arm:", arm, "| nseats:", len(seats), flush=True)
        row["seats"] = {str(p.player_id): g.ext["bot_types"][str(p.player_id)] for p in g.players}
        if arm == "A2":
            for pid, brain in g.bot_brains.items():
                bt = g.ext["bot_types"][str(pid)]
                brain.tuning.trade_player_min_acceptance = GATE_OVERRIDE_A2[bt]
            row["gate_override"] = dict(GATE_OVERRIDE_A2)
        dbg = AIDebugger(g, out_dir, label=f"{arm}{seed}P{perm:02d}")
        g._lab_debugger = dbg
        proposals = []
        pending_apply = {}
        costs = {"route": COST_ROUTE, "outpost": COST_OUTPOST, "metropolis": COST_METROPOLIS}
        orig_apply = g.apply_action

        def apply_with_result(pid, action, *args, **kwargs):
            result = orig_apply(pid, action, *args, **kwargs)
            seq = pending_apply.pop(int(pid), None)
            if seq is not None and 0 < seq <= len(dbg.events) \
                    and dbg.events[seq - 1].get("kind") == "bot_decision":
                ev = dbg.events[seq - 1]
                ev["applied_ok"] = bool(getattr(result, "success", False))
                ev["apply_error"] = getattr(result, "error", None)
            return result

        g.apply_action = apply_with_result

        def chooser(pid: int, legal_actions):
            dbg.drain_journal()
            brain = g.bot_brains.get(pid)
            offer_pre = g.active_trade_offer
            a = brain.choose_action(pid, legal_actions) if brain else legal_actions[0]
            if not any(x is a for x in legal_actions):
                a = next((x for x in legal_actions
                          if x.type == a.type and x.p == a.p), legal_actions[0])
            standings_pre = {int(p.player_id): int(g.get_victory_points(p.player_id, public=True))
                             for p in g.players}
            chosen_ctx = None
            if a is not None and a.p.get("hex_id") is not None:
                from play_session import hex_audit_context
                chosen_ctx = hex_audit_context(g, a.p["hex_id"])
            from play_session import score_alternatives
            alts = score_alternatives(g, brain, pid, g.phase, legal_actions)
            if a.type == "respond_trade" and offer_pre is not None:
                dbg._emit("trade_response", turn=int(g.global_turn), responder=int(pid),
                          from_player=int(offer_pre.from_player),
                          give=_norm(offer_pre.give), want=_norm(offer_pre.want),
                          accepted=bool(a.p.get("accept")))
            if a.type == "propose_trade":
                give = _norm(a.p.get("give"))
                want = _norm(a.p.get("want"))
                rec = {"turn": int(g.global_turn), "proposer": int(pid),
                       "arch": row["seats"].get(str(pid), "?"),
                       "give": give, "want": want, "standings": standings_pre,
                       "n_addressees": len(g.players) - 1}
                key = brain._trade_key(a.p.get("give"), a.p.get("want")) \
                    if hasattr(brain, "_trade_key") else None
                rec["legacy_pred"] = brain._p2p_pred_cache().get(key) if key else None
                rec["gain"] = None
                m = GAIN_RE.search(str((getattr(brain, "last_decision", {}) or {}).get("reason")))
                if m:
                    rec["gain"] = float(m.group(1))
                if arm == "A0":
                    rec["model_pred"] = model_pred(g, brain, pid, a.p.get("give"), a.p.get("want"))
                elif arm == "A2":
                    diag = getattr(brain, "_last_player_trade_search", {}) or {}
                    cand = None
                    for c in diag.get("candidates") or []:
                        if c.get("give") == give and c.get("want") == want:
                            cand = c
                            break
                    rec["model_pred"] = cand.get("pred_any_accept") if cand else None
                    if cand and cand.get("legacy_pred_any_accept") is not None:
                        rec["legacy_pred"] = cand.get("legacy_pred_any_accept")
                    rec["gain"] = cand.get("gain") if cand else rec["gain"]
                    rec["accepted_by_gate"] = cand.get("accepted_by_gate") if cand else None
                else:
                    diag = getattr(brain, "_last_player_trade_search", {}) or {}
                    cand = None
                    for c in diag.get("candidates") or []:
                        if c.get("give") == give and c.get("want") == want:
                            cand = c
                            break
                    rec["model_pred"] = cand.get("pred_any_accept") if cand else None
                    if cand and cand.get("legacy_pred_any_accept") is not None:
                        rec["legacy_pred"] = cand.get("legacy_pred_any_accept")
                    rec["gain"] = cand.get("gain") if cand else rec["gain"]
                    rec["accepted_by_gate"] = cand.get("accepted_by_gate") if cand else None
                ob = getattr(brain, "offer_resources_before", None)
                ea = getattr(brain, "offer_expected_after", None)
                rec["afford_before"] = _afford_flags(ob, costs) if ob else None
                rec["afford_after"] = _afford_flags(ea, costs) if ea else None
                proposals.append(rec)
                dbg._emit("trade_proposal", turn=int(g.global_turn), proposer=int(pid),
                          give=give, want=want,
                          pred_any_accept=rec["model_pred"] if arm == "A1" else rec["legacy_pred"],
                          pred_model=rec["model_pred"], pred_legacy=rec["legacy_pred"],
                          gain=rec["gain"], arm=arm)
            dbg.record_bot_decision(pid, a, None, alternatives=alts,
                                    standings_pre=standings_pre,
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
        integrity["pre_start"] = {"blight_mod_present": integrity_pre["blight_mod_present"],
                                  "ext_blight_state": integrity_pre["ext_blight_state"]}
        with open(out_dir / "proposals.jsonl", "w", encoding="utf-8") as f:
            for rec in proposals:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        evc = []
        for e in dbg.events:
            k = e.get("kind")
            if k == "bot_decision":
                a_ = e.get("action")
                if a_ in ("build_route","build_outpost","build_metropolis","trade_bank","buy_charter","play_charter","propose_trade","setup_outpost"):
                    evc.append(["d", e["turn"], e["player_id"], a_, bool(e.get("applied_ok"))])
            elif k == "trade_proposal":
                evc.append(["p", e["turn"], e["proposer"]])
            elif k == "trade_response":
                evc.append(["r", e["turn"], e["responder"], e["from_player"], bool(e.get("accepted"))])
        row["ev"] = evc
        row["cands"] = g._audit_cand_log
        row["props"] = proposals
        row.update({
            "status": "completed",
            "winner_pid": summary.get("winner_id"),
            "winner_type": g.ext["bot_types"].get(str(summary.get("winner_id"))),
            "turns": summary.get("turns"),
            "stalled_out": bool(g.ext.get("stalled_out")),
            "decisions": summary.get("decisions_recorded"),
            "duration_s": round(time.time() - t0, 1),
            "vp": {p["name"]: p["vp"] for p in summary["players"]},
            "n_proposals": len(proposals),
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
    ap.add_argument("--arm", choices=["A0", "A1", "A2"], default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seeds", default="64001-64020")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    m = re.fullmatch(r"(\d+)-(\d+)", args.seeds)
    seeds = list(range(int(m.group(1)), int(m.group(2)) + 1))
    jobs = []
    arms = [args.arm] if args.arm else (["A0", "A1"] if not args.seeds.startswith("64")
                                        else ["A0", "A2"])
    if args.smoke:
        seeds = seeds[:1]
        perms = [0, 1]
    else:
        perms = list(range(24))
    for arm in arms:
        for s in seeds:
            for p in perms:
                series = ("ab_trade_v25" if arm == "A1"
                          else ("ab2_trade_v26" if args.seeds.startswith("64")
                                else "ab_trade_v25"))
                jobs.append({"arm": arm, "seed": s, "perm": p,
                             "out_root": str(LAB / "sessions" / series)})
    if args.limit:
        jobs = jobs[:args.limit]
    out_root = LAB / ("sessions/ab_trade_v25" if not args.seeds.startswith("64")
                      else "sessions/ab2_trade_v26")
    out_root.mkdir(parents=True, exist_ok=True)
    manifest = out_root / f"manifest_{args.arm or 'both'}.jsonl"
    print(f"партий: {len(jobs)} (arms={arms}) | воркеров: {args.workers} | spawn-изоляция",
          flush=True)
    t0 = time.time()
    ctx = mp.get_context("spawn")
    done = 0
    # ОТДЕЛЬНЫЙ пул на каждую руку: процессы не переиспользуют sys.modules
    with open(manifest, "a", encoding="utf-8") as mf:
        for arm in arms:
            arm_jobs = [j for j in jobs if j["arm"] == arm]
            if not arm_jobs:
                continue
            with ctx.Pool(args.workers) as pool:
                for row in pool.imap_unordered(_worker, arm_jobs, chunksize=1):
                    mf.write(json.dumps(row, ensure_ascii=False) + "\n")
                    mf.flush()
                    done += 1
                    if row["status"] == "completed":
                        print(f"[{done}/{len(jobs)}] {row['arm']} seed{row['seed']} p{row['perm']:02d}"
                              f" → {row.get('winner_type')} T{row.get('turns')} "
                              f"({row.get('duration_s')}s) предл. {row.get('n_proposals')}",
                              flush=True)
                    else:
                        print(f"[{done}/{len(jobs)}] {row['arm']} seed{row['seed']} p{row['perm']:02d}"
                              f" ERROR: {str(row.get('error'))[-200:]}", flush=True)
    print(f"готово за {(time.time() - t0) / 60:.1f} мин", flush=True)


if __name__ == "__main__":
    main()
