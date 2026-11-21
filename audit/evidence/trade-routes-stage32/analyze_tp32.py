#!/usr/bin/env python3
"""analyze_tp32 — Этап 3.2: A0 (9af21df) vs A1 (f1fff48) — портфельная оценка
Trade Routes.

Метрики: активации по ресурсам; доля активаций ресурсов, которых не хватает
для текущей цели; доход по ресурсам; доход → постройка; кандидаты и
mod_score; постройки, P2P, банк; финальные ОП и победы (архетипы, места);
non-inferiority маржи (поселения −0.25, города −0.25, ОП −0.20/игрока).
Парные разности по сидам, bootstrap 10000, 95% ДИ.

Запуск: python3 tools/analyze_tp32.py [sessions/tp32]
"""
from __future__ import annotations

import json
import math
import random
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

RNG = random.Random(0x32)
BOOT = 10000
ARMS = ("A0", "A1")
RES = ("wood", "clay", "grain", "wool", "ore")
BUILD_ACTS = ("build_route", "build_outpost", "build_metropolis")


def boot_ci(vals):
    if not vals:
        return None, None, None
    point = st.mean(vals)
    sample = sorted(st.mean([RNG.choice(vals) for _ in range(len(vals))])
                    for _ in range(BOOT))
    return round(point, 4), round(sample[int(0.025 * BOOT)], 4), round(sample[int(0.975 * BOOT) - 1], 4)


def pair_table(fn, games, seed_ids, label):
    print(f"  --- {label} ---")
    A, B = ARMS
    ps = {s: {a: [] for a in ARMS} for s in seed_ids}
    for (seed, perm, arm), g in games.items():
        ps[seed][arm].append(fn(g))
    m0 = [st.mean(ps[s][A]) for s in seed_ids if ps[s][A]]
    m1 = [st.mean(ps[s][B]) for s in seed_ids if ps[s][B]]
    d = [b - a for a, b in zip(m0, m1)]
    point, lo, hi = boot_ci(d)
    pos = sum(1 for x in d if x > 0)
    print(f"    {A} {st.mean(m0):.3f} → {B} {st.mean(m1):.3f} | Δ {point:+.3f} "
          f"CI95 ({lo}, {hi}) | сидов +{pos}/−{len(d) - pos}")
    return {"metric": label, A: round(st.mean(m0), 3), B: round(st.mean(m1), 3),
            "delta": point, "ci_lo": lo, "ci_hi": hi, "seeds_pos": pos, "seeds_neg": len(d) - pos}


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "sessions/tp32")
    rows = {}
    for arm in ARMS:
        rows[arm] = [json.loads(l) for l
                     in open(root / f"manifest_{arm}.jsonl", encoding="utf-8")]
    ok = {arm: [r for r in rows[arm] if r.get("status") == "completed"] for arm in ARMS}
    errs = {arm: [r for r in rows[arm] if r.get("status") == "error"] for arm in ARMS}
    print("=== QC ===")
    for arm in ARMS:
        print(f"  {arm}: завершено {len(ok[arm])}/{len(rows[arm])}, ошибок {len(errs[arm])}")
        for r in errs[arm][:3]:
            print(f"    ERROR: {str(r.get('error'))[-140:]}")
    by_key = {}
    for arm in ARMS:
        for r in ok[arm]:
            by_key.setdefault((r["seed"], r["perm"]), {})[arm] = r
    full = {k: v for k, v in by_key.items() if len(v) == len(ARMS)}
    map_bad = [k for k, v in full.items() if len({v[a]["map_md5"] for a in ARMS}) != 1]
    integ_bad = [k for k, v in full.items()
                 for a in ARMS for r in [v[a]]
                 if r["integrity"]["blight_mod_present"] or r["integrity"]["ext_blight_state"]
                 or "TradeRoutesMod" not in r["integrity"]["plugins"]
                 or r["integrity"]["journal_blight_count"]]
    perr = {a: sum(r.get("plugin_errors", 0) for r in ok[a]) for a in ARMS}
    print(f"  полных групп: {len(full)}/480 | расхождений карт: {len(map_bad)} | "
          f"нарушений no-blight/без TradeRoutesMod: {len(integ_bad)} | "
          f"plugin-ошибок: {perr}")

    games = {}
    for arm in ARMS:
        for r in ok[arm]:
            key = (r["seed"], r["perm"], arm)
            d = Path(r["out_dir"])
            events = [json.loads(l) for l in open(d / "trace.jsonl", encoding="utf-8")]
            bd = [e for e in events if e.get("kind") == "bot_decision"]
            tr = [e for e in events if e.get("kind") == "trade_response"]
            rd = [e for e in events if e.get("kind") == "route_decision"]
            ra = [e for e in events if e.get("kind") == "route_activation"]
            ra_raw = [{"player": e.get("player"), "resource": e.get("trade_resource")}
                      for e in ra]
            inc_events = [e for e in events if e.get("kind") == "journal"
                          and "получил 1" in e.get("msg", "") and "маршрут" in e.get("msg", "")]
            inc_by_res = Counter()
            for e in inc_events:
                m = e.get("msg", "")
                for rn, ru in (("wood", "дерево"), ("clay", "кирпич"), ("grain", "пшеница"),
                               ("wool", "овца"), ("ore", "руда")):
                    if ru in m:
                        inc_by_res[rn] += 1
                        break
            offers = []
            pending = None
            for e in events:
                k = e.get("kind")
                if k == "trade_proposal":
                    if pending is not None:
                        offers.append(pending)
                    pending = {"turn": e["turn"], "proposer": e["proposer"],
                               "accepted": False}
                elif k == "trade_response" and pending is not None:
                    if e.get("accepted"):
                        pending["accepted"] = True
            if pending is not None:
                offers.append(pending)
            builds = Counter()
            bank = end_turns = 0
            build_events = []
            for e in bd:
                if e.get("applied_ok"):
                    a = e["action"]
                    if a in BUILD_ACTS:
                        builds[a] += 1
                        build_events.append((e["player_id"], e["turn"]))
                    elif a == "trade_bank":
                        bank += 1
                    elif a == "end_turn":
                        end_turns += 1
            # доход → постройка: income событие (pid, turn) → постройка pid в окнах
            inc_build_next = inc_build_3 = inc_total = 0
            inc_by_turn = [(e["turn"], e.get("player_id")) for e in inc_events
                           if e.get("player_id") is not None]
            for iturn, ipid in inc_by_turn:
                inc_total += 1
                # окна: тот же ход (доход приходит в начале собственного хода),
                # следующий собственный ход (+4), три собственных хода (до +12)
                if any(p == ipid and iturn <= t <= iturn + 4 for p, t in build_events):
                    inc_build_next += 1
                if any(p == ipid and iturn <= t <= iturn + 12 for p, t in build_events):
                    inc_build_3 += 1
            # активации: ресурс vs цель (не хватает для цели?)
            act_res = Counter()
            act_missing_for_goal = 0
            for e in ra:
                res = e.get("trade_resource")
                act_res[res] += 1
                rd_match = next((x for x in reversed(rd)
                                 if x["player"] == e["player"] and x["turn"] <= e["turn"]), None)
                if rd_match and rd_match.get("goal_needed"):
                    if res in rd_match["goal_needed"]:
                        act_missing_for_goal += 1
            n_act = len(ra)
            games[key] = {"row": r, "route_acts_raw": ra_raw, "n_offers": len(offers),
                          "acc_offers": sum(1 for o in offers if o["accepted"]),
                          "n_responses": len(tr),
                          "acc_resp": sum(1 for e in tr if e.get("accepted")),
                          "act_res": dict(act_res), "n_act": n_act,
                          "act_missing_goal": act_missing_for_goal,
                          "inc_by_res": dict(inc_by_res), "inc_total": len(inc_events),
                          "inc_build_next": inc_build_next, "inc_build_3": inc_build_3,
                          "builds": dict(builds), "bank": bank, "end_turns": end_turns,
                          "first_metro": next((e["turn"] for e in bd
                                               if e.get("applied_ok")
                                               and e["action"] == "build_metropolis"), None),
                          "final_turns": r.get("turns"),
                          "route_decisions": len(rd),
                          "n_route_cands": sum(x.get("n_candidates", 0) for x in rd)}
            del events
    print(f"\nпартий разобрано: {len(games)}")
    seed_ids = sorted({k[0] for k in games})

    print("\n=== Активации маршрутов ===")
    for arm in ARMS:
        glist = [g for k, g in games.items() if k[2] == arm]
        n = len(glist)
        acts = sum(g["n_act"] for g in glist)
        print(f"  {arm}: активаций {acts / max(1, n):.2f}/партию "
              f"(партий без активации: {sum(1 for g in glist if g['n_act'] == 0)})")
        by_res = Counter()
        for g in glist:
            for k, v in g["act_res"].items():
                by_res[k] += v
        print(f"    по ресурсам: {dict(by_res.most_common())}")
        mg = sum(g["act_missing_goal"] for g in glist)
        print(f"    активаций ресурса из пробела текущей цели: {mg} "
              f"({mg / max(1, acts):.1%} от активаций)")
    print("  Δ активаций/партию (A1 − A0):")
    print("   ", pair_table(lambda g: g["n_act"], games, seed_ids, "активаций/партию"))

    print("\n=== Доход по ресурсам (за партию) ===")
    for arm in ARMS:
        glist = [g for k, g in games.items() if k[2] == arm]
        tot = Counter()
        for g in glist:
            for k, v in g["inc_by_res"].items():
                tot[k] += v
        print(f"  {arm}: {dict(tot.most_common())} | всего {sum(tot.values()) / max(1, len(glist)):.2f}/партию")
    print("  Δ дохода/партию (A1 − A0):",
          pair_table(lambda g: g["inc_total"], games, seed_ids, "доход единиц/партию"))

    print("\n=== Доход → постройка (окно: тот же ход + следующий; и 3 собственных хода) ===")
    print("   ", pair_table(lambda g: g["inc_build_next"] / max(1, g["inc_total"]),
                            games, seed_ids, "доля дохода → постройка (тот же/след. ход)"))
    print("   ", pair_table(lambda g: g["inc_build_3"] / max(1, g["inc_total"]),
                            games, seed_ids, "доля дохода → постройка (3 собственных хода)"))

    print("\n=== Активации по архетипам (за партию) ===")
    # route_activation события несут pid; архетип — из row["seats"]
    act_by_arch = defaultdict(Counter)
    for (seed, perm, arm), g in games.items():
        seats = g["row"].get("seats") or {}
        for e_act in g.get("route_acts_raw", []):
            arch = seats.get(str(e_act["player"]), "?")
            act_by_arch[arm][arch] += 1
    n_by_arm = {a: len([k for k in games if k[2] == a]) for a in ARMS}
    for arm in ARMS:
        line = ", ".join(f"{a}: {v / max(1, n_by_arm[arm]):.2f}/партию"
                         for a, v in sorted(act_by_arch[arm].items()))
        print(f"  {arm}: {line}")

    print("\n=== Кандидаты маршрутов (диагностика решений) ===")
    for arm in ARMS:
        glist = [g for k, g in games.items() if k[2] == arm]
        rd = sum(g["route_decisions"] for g in glist)
        cands = sum(g["n_route_cands"] for g in glist)
        print(f"  {arm}: решений с маршрутными кандидатами {rd / max(1, len(glist)):.1f}/партию, "
              f"кандидатов {cands / max(1, rd):.2f} на решение")

    print("\n=== Строительство, P2P, банк (парные разности) ===")
    for nm, fn in (("build_route", lambda g: g["builds"].get("build_route", 0)),
                   ("build_outpost", lambda g: g["builds"].get("build_outpost", 0)),
                   ("build_metropolis", lambda g: g["builds"].get("build_metropolis", 0)),
                   ("P2P предложений/партию", lambda g: g["n_offers"]),
                   ("принято P2P/партию", lambda g: g["acc_offers"]),
                   ("bank/партию", lambda g: g["bank"]),
                   ("end_turn/партию", lambda g: g["end_turns"])):
        r = pair_table(fn, games, seed_ids, nm)
        if nm == "build_outpost":
            print(f"    non-inferiority (маржа −0.25): "
                  f"{'PASS' if r['ci_lo'] > -0.25 else 'FAIL'}")
        if nm == "build_metropolis":
            print(f"    non-inferiority (маржа −0.25): "
                  f"{'PASS' if r['ci_lo'] > -0.25 else 'FAIL'}")

    print("\n=== Финальные ОП (non-inferiority −0.20/игрока) ===")
    r = pair_table(lambda g: st.mean(list(g["row"]["vp"].values())),
                   games, seed_ids, "финальные ОП/игрока")
    print(f"    non-inferiority (маржа −0.20): {'PASS' if r['ci_lo'] > -0.2 else 'FAIL'}")

    print("\n=== Длина партий и победы (вторично) ===")
    for arm in ARMS:
        turns = [r["turns"] for r in ok[arm]]
        w = Counter(r["winner_type"] for r in ok[arm])
        by_seat = Counter(r["winner_pid"] for r in ok[arm])
        print(f"  {arm}: ходы медиана {int(st.median(turns))} | "
              f"победы {dict(w)} | места {dict(sorted(by_seat.items()))}")

    out = {"games": len(games), "full_groups": len(full), "criteria": {}}
    json.dump(out, open(root / "tp32_analysis_summary.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\nагрегаты: {root / 'tp32_analysis_summary.json'}")


if __name__ == "__main__":
    main()
