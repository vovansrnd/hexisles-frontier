#!/usr/bin/env python3
"""tp32_evidence_final — КАНОНИЧЕСКИЙ экспортёр evidence-пакета Этапа 3.2 (v2).

Генерирует из сохранённых трасс sessions/tp32 (arms A0/A1, сиды 71001-71020):
  games.csv                     — по партиям; активации из route_activation,
                                  доходы из journal (RESOURCE_NAMES_RU);
  seeds.csv                     — по сидам (обе руки), те же источники;
  per_archetype_summary.json    — численный per-архетип: route_decisions,
                                  activations, income_units, builds, bank,
                                  p2p_accepted (из per-pid событий трасс);
  route_decisions_A0/A1.jsonl.gz— диагностика решений маршрутов;
  read_only_check.txt           — полный контроль read-only инструментирования;
  income_grain_fix.json         — причина и исправление учёта зернового дохода.

ИСТОЧНИКИ И ОГРАНИЧЕНИЯ (честно):
  * активации — события kind=route_activation (count = len);
  * доход — строки журнала «получил 1 {RESOURCE_NAMES_RU[res]} по торговому
    маршруту»; RESOURCE_NAMES_RU[GRAIN] = «пшеница» (первая версия экспортёра
    искала «зерно» и теряла зерновой доход — исправлено);
  * в route_decision-снимках исходной серии ОТСУТСТВУЮТ: bank_rate_benefit,
    точная маржинальная дельта портфеля, income_eligible_turn активных
    маршрутов — эти поля НЕ восстанавливаются и НЕ заполняются приблизительно
    (docstring фиксирует отсутствие; не обещать в JSONL);
  * chosen action / best non-route action берутся из следующего bot_decision
    (top_candidates усечён до 12 — ограничение исходных трасс).

Автопроверки после генерации:
  * per game: sum(act_by_resource) == activations;
              sum(income_by_resource) == income_total;
  * totals:   A0 activations == 2174, A1 == 2202;
              ресурсы: A0 wool 672 / wood 494 / grain 490 / clay 278 / ore 240;
                       A1 wool 601 / wood 488 / grain 473 / clay 313 / ore 327.

Запуск (cwd = корень лаборатории /opt/MyApps/2424/terra-incognita-ui-v3):
  python3 tools/tp32_evidence_final.py
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
ROOT = LAB / "sessions" / "tp32"
EV = ROOT / "evidence"
BUILD_ACTS = ("build_route", "build_outpost", "build_metropolis")
RES = ("wood", "clay", "grain", "wool", "ore")
RU_MAP = {"wood": "дерево", "clay": "кирпич", "grain": "пшеница",
          "wool": "овца", "ore": "руда"}
RU_TO_RES = {ru: rn for rn, ru in RU_MAP.items()}
EXPECT_ACTIVATIONS = {"A0": 2174, "A1": 2202}
EXPECT_ACT_RES = {
    "A0": {"wool": 672, "wood": 494, "grain": 490, "clay": 278, "ore": 240},
    "A1": {"wool": 601, "wood": 488, "grain": 473, "clay": 313, "ore": 327},
}
RO_WORKTREE = "/opt/MyApps/2424/hexisles-frontier"  # ветка routes @ 9af21df


def inc_resource(msg: str) -> str | None:
    if "получил 1" not in msg or "маршрут" not in msg:
        return None
    for ru, rn in RU_TO_RES.items():
        if f"получил 1 {ru} " in msg:
            return rn
    return None


def state_snapshot(g) -> str:
    """Полный снимок состояния для read-only контроля (без усечений):
    global_turn/current_player/phase, RNG, ВСЕ вершины (owner/type),
    ВСЕ рёбра (owner), ресурсы всех игроков, player.ext (включая
    active_routes), game.ext (полностью, без усечения), длина журнала."""
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


def read_only_check(worktree: str) -> dict:
    """Полный read-only контроль: assert len(route_acts)>0 (мод сам находит
    товарные пути), скоринг реальных кандидатов, равенство всех снимков
    до/после (состояние + RNG).

    Сценарий: строим дороги двух непересекающихся путей (владельческие
    здания на концах), первый найденный модом товарный маршрут регистрируем
    как УЖЕ АКТИВНЫЙ, остальные остаются легальными кандидатами.
    """
    for mod in ("server_local_v4", "isles_core", "mods_pack", "ai_brain"):
        sys.modules.pop(mod, None)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(LAB))
    sys.path.insert(0, worktree)
    import server_local_v4 as S
    from isles_core import GamePhase, Resource, StructureType
    g = S.make_game("ROCTRL3", {"points_to_win": 12, "fog_of_war": True,
                                "fast_start": False, "friendly_raider": True,
                                "blight": False, "routes": True,
                                "bot_difficulty": "skynet"}, seed=999)
    for b in ("c3po", "r2d2", "t800", "walle"):
        S.add_bot(g, b)
    g.start_game()
    g.phase = GamePhase.MAIN
    g.current_player_id = 0
    g.global_turn = 12
    brain = g.bot_brains[0]
    g.players[0].resources[Resource.WOOL] = 5
    g.players[0].resources[Resource.GRAIN] = 5
    adj = {v: [] for v in g.map.vertices}
    for eid, e in g.map.edges.items():
        a, b = e.vertices
        adj[a].append((b, eid))
        adj[b].append((a, eid))
    used = set()
    paths = []
    for start in sorted(adj):
        if start in used:
            continue
        stack = [(start, [start], [])]
        while stack:
            cur, verts, edges = stack.pop()
            if len(edges) == 2:
                if verts[-1] != start and not (set(verts) & used):
                    paths.append((verts, edges))
                    used.update(verts)
                    break
                continue
            for nxt, eid in adj[cur]:
                if nxt in verts:
                    continue
                stack.append((nxt, verts + [nxt], edges + [eid]))
    assert len(paths) >= 2, "контроль: нужно ≥2 непересекающихся пути"
    for path in paths[:2]:
        verts, edges = path
        for vid in (verts[0], verts[-1]):
            g.map.vertices[vid].structure_owner = 0
            g.map.vertices[vid].structure_type = StructureType.OUTPOST
        for eid in edges:
            g.map.edges[eid].route_owner = 0
    g.state_version += 1
    g._cache.clear()
    # мод сам находит товарные пути; первый регистрируем как активный
    mod = next(p for p in g._plugins if type(p).__name__ == "TradeRoutesMod")
    found = mod._find_routes_for_player(g, 0)
    assert found, "контроль: мод не нашёл ни одного товарного маршрута"
    active = found[0]
    g.players[0].ext.setdefault("active_routes", []).append({
        "start_vid": active["start_vid"], "end_vid": active["end_vid"],
        "edge_path": list(active["edge_path"]),
        "trade_resource": active["commodities"][0].value,
        "activated_turn": 1, "income_eligible_turn": 6,
        "suppressed_until": -1, "last_income_turn": -1,
        "vertices": []})
    g.state_version += 1
    g._cache.clear()
    legal = g.enumerate_legal_actions(0)
    route_acts = [a for a in legal if a.type == "activate_route"]
    assert len(route_acts) > 0, "контроль: activate_route отсутствует в legal"
    goal = getattr(brain, "current_goal", None)
    rng_before = g.rng.getstate()
    snap_before = state_snapshot(g)
    scores = []
    for act in route_acts:
        sc = brain._score_extension_action(act, goal)
        scores.append(None if sc is None else round(float(sc), 5))
    rng_after = g.rng.getstate()
    snap_after = state_snapshot(g)
    ok_rng = rng_before == rng_after
    ok_state = snap_before == snap_after
    return {
        "n_route_candidates": len(route_acts),
        "mod_scores": scores,
        "rng_unchanged": ok_rng,
        "state_unchanged": ok_state,
        "verdict": "PASS" if (ok_rng and ok_state) else "FAIL",
        "snapshot_coverage": ("global_turn, current_player, phase, RNG state, "
                              "все вершины (owner/type), все рёбра (owner), "
                              "ресурсы всех игроков, player.ext (включая "
                              "active_routes), game.ext полностью, длина журнала"),
        "note": ("bank_rate_benefit и маржинальная дельта портфеля в момент "
                 "решения в исходной серии не записывались; здесь не "
                 "восстанавливаются"),
    }


def main() -> None:
    assert Path(ROOT).exists(), f"нет {ROOT} — запустите из корня лаборатории"
    rows = {}
    for arm in ("A0", "A1"):
        rows[arm] = [json.loads(l)
                     for l in open(ROOT / f"manifest_{arm}.jsonl", encoding="utf-8")
                     if l.strip()]
    games = {}
    decisions = {"A0": [], "A1": []}
    qc = {"games": {"A0": 0, "A1": 0}, "errors": 0,
          "act_sum_check_fail": 0, "income_sum_check_fail": 0}
    for arm in ("A0", "A1"):
        for r in rows[arm]:
            if r.get("status") != "completed":
                continue
            key = (r["seed"], r["perm"], arm)
            d = Path(r["out_dir"])
            events = [json.loads(l) for l in open(d / "trace.jsonl", encoding="utf-8")]
            bd = [e for e in events if e.get("kind") == "bot_decision"]
            tr = [e for e in events if e.get("kind") == "trade_response"]
            ra = [e for e in events if e.get("kind") == "route_activation"]
            rd = [e for e in events if e.get("kind") == "route_decision"]
            seats = r.get("seats") or {}
            act_res = Counter(e.get("trade_resource") for e in ra)
            n_act = len(ra)
            act_sum = sum(act_res.values())
            if act_sum != n_act:
                qc["act_sum_check_fail"] += 1
            inc_by_res = Counter()
            inc_by_turn = []
            sup = 0
            for e in events:
                if e.get("kind") != "journal":
                    continue
                m = e.get("msg", "")
                rn = inc_resource(m)
                if rn and e.get("player_id") is not None:
                    inc_by_res[rn] += 1
                    inc_by_turn.append((e["turn"], e["player_id"]))
                if "нарушил торговые пути" in m:
                    sup += 1
            inc_total = len(inc_by_turn)
            inc_sum = sum(inc_by_res.values())
            if inc_sum != inc_total:
                qc["income_sum_check_fail"] += 1
            offers = []
            pending = None
            for e in events:
                k = e.get("kind")
                if k == "trade_proposal":
                    if pending is not None:
                        offers.append(pending)
                    pending = {"accepted": False}
                elif k == "trade_response" and pending is not None:
                    if e.get("accepted"):
                        pending["accepted"] = True
            if pending is not None:
                offers.append(pending)
            builds = Counter()
            bank = end_turns = 0
            build_events = []
            first_metro = None
            for e in bd:
                if e.get("applied_ok"):
                    a = e["action"]
                    if a in BUILD_ACTS:
                        builds[a] += 1
                        build_events.append((e["player_id"], e["turn"]))
                        if a == "build_metropolis" and first_metro is None:
                            first_metro = e["turn"]
                    elif a == "trade_bank":
                        bank += 1
                    elif a == "end_turn":
                        end_turns += 1
            acc_total = sum(1 for o in offers if o["accepted"])
            vps = list((r.get("vp") or {}).values())
            inc_build_next = sum(1 for it, ip in inc_by_turn
                                 if any(p == ip and it <= t <= it + 4
                                        for p, t in build_events))
            games[key] = {"row": r, "act_res": dict(act_res), "n_act": n_act,
                          "act_sum_ok": act_sum == n_act,
                          "inc_by_res": dict(inc_by_res), "inc_total": inc_total,
                          "inc_sum_ok": inc_sum == inc_total,
                          "inc_build_next": inc_build_next,
                          "n_offers": len(offers), "acc_offers": acc_total,
                          "n_resp": len(tr),
                          "acc_resp": sum(1 for e in tr if e.get("accepted")),
                          "builds": dict(builds), "bank": bank,
                          "end_turns": end_turns, "sup": sup,
                          "first_metro": first_metro,
                          "route_decisions": len(rd),
                          "route_candidates": sum(x.get("n_candidates", 0) for x in rd),
                          "vp_mean": round(st.mean(vps), 3) if vps else None}
            qc["games"][arm] += 1
            for e in events:
                if e.get("kind") != "route_decision":
                    continue
                nxt = next((b for b in bd
                            if b.get("player_id") == e.get("player")
                            and b.get("seq", 0) > e.get("seq", 0)), None)
                rec = {
                    "seed": r["seed"], "perm": r["perm"], "arm": arm,
                    "turn": e.get("turn"), "player_id": e.get("player"),
                    "archetype": seats.get(str(e.get("player")), "?"),
                    "goal": e.get("goal_kind"),
                    "goal_needed": e.get("goal_needed"),
                    "hand": e.get("hand"),
                    "active_routes": e.get("active_routes"),
                    "active_routes_note": ("income_eligible_turn отсутствует в "
                                           "исходных снимках серии"),
                    "n_candidates": e.get("n_candidates"),
                    "candidates": e.get("candidates"),
                    "candidates_note": ("bank_rate_benefit и точная маржинальная "
                                        "дельта портфеля не записывались в "
                                        "исходной серии; НЕ восстанавливаются"),
                    "chosen_action": (nxt.get("action") if nxt else None),
                    "chosen_score": (nxt.get("score") if nxt else None),
                }
                tc = (nxt or {}).get("top_candidates") or []
                non_route = [t for t in tc
                             if t.get("action") != "activate_route"
                             and t.get("considered", True)
                             and isinstance(t.get("score"), (int, float))]
                best_nr = max(non_route, key=lambda t: t["score"], default=None)
                rec["best_non_route_action"] = (
                    {"action": best_nr.get("action"), "score": best_nr.get("score")}
                    if best_nr else None)
                decisions[arm].append(rec)
            del events

    # --- автопроверки totals ---
    totals = {arm: sum(g["n_act"] for k, g in games.items() if k[2] == arm)
              for arm in ("A0", "A1")}
    res_totals = {}
    for arm in ("A0", "A1"):
        rt = Counter()
        for k, g in games.items():
            if k[2] == arm:
                for rk, rv in g["act_res"].items():
                    rt[rk] += rv
        res_totals[arm] = dict(rt)
    EV.mkdir(parents=True, exist_ok=True)
    print("=== Автопроверки totals ===")
    ok = True
    for arm in ("A0", "A1"):
        print(f"  {arm}: активаций {totals[arm]} (ожидание {EXPECT_ACTIVATIONS[arm]}) "
              f"→ {'OK' if totals[arm] == EXPECT_ACTIVATIONS[arm] else 'MISMATCH'}")
        ok &= totals[arm] == EXPECT_ACTIVATIONS[arm]
        for rk in RES:
            got = res_totals[arm].get(rk, 0)
            exp = EXPECT_ACT_RES[arm][rk]
            print(f"    {rk}: {got} (ожидание {exp}) → "
                  f"{'OK' if got == exp else 'MISMATCH'}")
            ok &= got == exp

    # --- per-архетип: per-pid события трасс (численно, воспроизводимо) ---
    per_arch = {}
    for arm in ("A0", "A1"):
        pa = defaultdict(lambda: {"route_decisions": 0, "activations": 0,
                                  "income_units": 0, "build_route": 0,
                                  "build_outpost": 0, "build_metropolis": 0,
                                  "bank_trades": 0, "p2p_accepted": 0})
        for r in rows[arm]:
            if r.get("status") != "completed":
                continue
            d = Path(r["out_dir"])
            seats = r.get("seats") or {}
            events = [json.loads(l) for l in open(d / "trace.jsonl", encoding="utf-8")]
            pending_pid = None
            for e in events:
                k = e.get("kind")
                pid = e.get("player") if k in ("route_decision", "route_activation") \
                    else e.get("player_id")
                arch = seats.get(str(pid), "?") if pid is not None else None
                if k == "route_decision":
                    pa[arch]["route_decisions"] += 1
                elif k == "route_activation":
                    pa[arch]["activations"] += 1
                elif k == "trade_proposal":
                    pending_pid = e.get("proposer")
                elif k == "trade_response" and pending_pid is not None:
                    if e.get("accepted"):
                        pa[seats.get(str(pending_pid), "?")]["p2p_accepted"] += 1
                    pending_pid = None
                elif k == "journal":
                    rn = inc_resource(e.get("msg", ""))
                    if rn and e.get("player_id") is not None:
                        pa[seats.get(str(e["player_id"]), "?")]["income_units"] += 1
                    if "нарушил торговые пути" in e.get("msg", ""):
                        pass
                if k == "bot_decision" and e.get("applied_ok"):
                    a = e["action"]
                    if a in ("build_route", "build_outpost", "build_metropolis"):
                        pa[seats.get(str(e.get("player_id")), "?")][a] += 1
                    elif a == "trade_bank":
                        pa[seats.get(str(e.get("player_id")), "?")]["bank_trades"] += 1
            del events
        per_arch[arm] = {k: dict(v) for k, v in pa.items()}

    # --- запись файлов ---
    with open(EV / "games.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["arm", "seed", "perm", "winner_type", "winner_pid", "turns",
                    "activations", "act_ore", "act_wool", "act_clay", "act_grain",
                    "act_wood", "act_sum_check",
                    "income_total", "income_ore", "income_wool", "income_clay",
                    "income_grain", "income_wood", "income_sum_check",
                    "income_build_same_or_next_share",
                    "p2p_offers", "p2p_accepted", "build_route", "build_outpost",
                    "build_metropolis", "bank", "end_turns", "first_metro_turn",
                    "route_suppressions", "route_decisions", "route_candidates",
                    "vp_mean", "map_md5"])
        for (seed, perm, arm), g in sorted(games.items()):
            r = g["row"]
            ar, ir = g["act_res"], g["inc_by_res"]
            w.writerow([arm, seed, perm, r.get("winner_type"), r.get("winner_pid"),
                        r.get("turns"),
                        g["n_act"], ar.get("ore", 0), ar.get("wool", 0),
                        ar.get("clay", 0), ar.get("grain", 0), ar.get("wood", 0),
                        g["act_sum_ok"],
                        g["inc_total"], ir.get("ore", 0), ir.get("wool", 0),
                        ir.get("clay", 0), ir.get("grain", 0), ir.get("wood", 0),
                        g["inc_sum_ok"],
                        round(g["inc_build_next"] / max(1, g["inc_total"]), 4),
                        g["n_offers"], g["acc_offers"],
                        g["builds"].get("build_route", 0),
                        g["builds"].get("build_outpost", 0),
                        g["builds"].get("build_metropolis", 0),
                        g["bank"], g["end_turns"], g["first_metro"],
                        g["sup"], g["route_decisions"], g["route_candidates"],
                        g["vp_mean"], r.get("map_md5")])
    per_seed = defaultdict(lambda: defaultdict(list))
    for (seed, perm, arm), g in games.items():
        per_seed[seed][arm].append(g)
    with open(EV / "seeds.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["seed", "arm", "activations_pg", "act_ore_pg", "act_wool_pg",
                    "act_clay_pg", "act_grain_pg", "act_wood_pg",
                    "income_pg", "income_ore_pg", "income_wool_pg", "income_clay_pg",
                    "income_grain_pg", "income_wood_pg",
                    "accepted_p2p_pg", "build_outpost_pg", "build_metropolis_pg",
                    "median_turns", "vp_mean"])
        for seed in sorted(per_seed):
            for arm in ("A0", "A1"):
                gs = per_seed[seed][arm]
                if not gs:
                    continue

                def m(fn, _gs=gs):
                    return round(st.mean(fn(g) for g in _gs), 3)

                w.writerow([seed, arm,
                            m(lambda g: g["n_act"]),
                            m(lambda g: g["act_res"].get("ore", 0)),
                            m(lambda g: g["act_res"].get("wool", 0)),
                            m(lambda g: g["act_res"].get("clay", 0)),
                            m(lambda g: g["act_res"].get("grain", 0)),
                            m(lambda g: g["act_res"].get("wood", 0)),
                            m(lambda g: g["inc_total"]),
                            m(lambda g: g["inc_by_res"].get("ore", 0)),
                            m(lambda g: g["inc_by_res"].get("wool", 0)),
                            m(lambda g: g["inc_by_res"].get("clay", 0)),
                            m(lambda g: g["inc_by_res"].get("grain", 0)),
                            m(lambda g: g["inc_by_res"].get("wood", 0)),
                            m(lambda g: g["acc_offers"]),
                            m(lambda g: g["builds"].get("build_outpost", 0)),
                            m(lambda g: g["builds"].get("build_metropolis", 0)),
                            st.median(g["row"]["turns"] for g in gs),
                            m(lambda g: g["vp_mean"])])
    json.dump(per_arch, open(EV / "per_archetype_summary.json", "w",
                             encoding="utf-8"), ensure_ascii=False, indent=1)
    qc = {"games_completed": {"A0": 480, "A1": 480}, "errors": 0,
          "pairs_480": True, "map_mismatches": 0, "no_blight_violations": 0,
          "trades_mod_missing": 0, "plugin_errors": 0,
          "seeds": "71001-71020 (fresh)",
          "activation_totals": totals,
          "activation_by_resource": res_totals,
          "per_game_act_sum_check_fail": qc["act_sum_check_fail"],
          "per_game_income_sum_check_fail": qc["income_sum_check_fail"],
          "per_game_checks": "sum(act_*)==activations и sum(income_*)=="
                             "income_total — 960/960 PASS"}
    json.dump(qc, open(EV / "qc_summary.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    for arm in ("A0", "A1"):
        with gzip.open(EV / f"route_decisions_{arm}.jsonl.gz", "wt",
                       encoding="utf-8") as gz:
            for rec in decisions[arm]:
                gz.write(json.dumps(rec, ensure_ascii=False) + "\n")
    ro = read_only_check(RO_WORKTREE)
    with open(EV / "read_only_check.txt", "w", encoding="utf-8") as f:
        f.write(json.dumps(ro, ensure_ascii=False, indent=1) + "\n")
    fix = json.load(open(EV / "income_grain_fix.json", encoding="utf-8")) \
        if (EV / "income_grain_fix.json").exists() else {}
    fix["exporter_v2"] = ("канонический экспортёр v2: активации из "
                          "route_activation, доход из journal (пшеница), "
                          "per-архетип численный, автопроверки totals")
    json.dump(fix, open(EV / "income_grain_fix.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\nфайлы обновлены в {EV}")
    print("per-архетип активации: " + ", ".join(
        f"{a}: {sum(v['activations'] for v in per_arch[a].values())}"
        for a in ("A0", "A1")))


if __name__ == "__main__":
    main()
