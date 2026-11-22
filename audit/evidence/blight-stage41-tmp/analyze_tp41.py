#!/usr/bin/env python3
"""analyze_tp41 — Этап 4.1: A/B corrupt target ranking (A0 main vs A1 overflow).

Метрики: tie rate (eps 1e-9), доля выбранных целей с максимальным impact,
chosen/max impact, leader loss, «лидер-цель доступна — выбрана другая»,
«слабая при сильной», corrupt/purify за партию, кандидаты-без-выбора.
Guardrails: поселения/города NI −0.25, ОП NI −0.20, faction outcomes,
plugin errors. По архетипам и местам. Парные сид-кластеры, bootstrap
Random(0x41), 10000 репликаций, percentile CI 2.5/97.5.

Запуск: python3 tools/analyze_tp41.py [sessions/tp41]
"""
from __future__ import annotations

import json
import random
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

RNG = random.Random(0x41)
BOOT = 10000
ARMS = ("A0", "A1")
EPS = 1e-9
BUILD_ACTS = ("build_route", "build_outpost", "build_metropolis")
ARCH_NAME = {"🤖 C3PO": "c3po", "🤖 R2D2": "r2d2", "🤖 T-800": "t800",
             "🤖 Валл-И": "walle"}


def boot_ci(vals):
    if not vals:
        return None, None, None, 0, 0
    point = st.mean(vals)
    sample = sorted(st.mean([RNG.choice(vals) for _ in range(len(vals))])
                    for _ in range(BOOT))
    return (round(point, 4), round(sample[int(0.025 * BOOT)], 4),
            round(sample[int(0.975 * BOOT) - 1], 4),
            sum(1 for x in vals if x > 0), sum(1 for x in vals if x < 0))


def pair_table(fn, games, seed_ids, label, A="A0", B="A1", margin=None):
    ps = {s: {a: [] for a in ARMS} for s in seed_ids}
    for (seed, perm, arm), g in games.items():
        ps[seed][arm].append(fn(g))
    m0 = [st.mean(ps[s][A]) for s in seed_ids if ps[s][A]]
    m1 = [st.mean(ps[s][B]) for s in seed_ids if ps[s][B]]
    d = [b - a for a, b in zip(m0, m1)]
    point, lo, hi, pos, neg = boot_ci(d)
    verdict = ""
    if margin is not None:
        verdict = f" | NI (маржа {margin}): {'PASS' if lo > margin else 'FAIL'}"
    print(f"    {label}: {A} {st.mean(m0):.3f} → {B} {st.mean(m1):.3f} | "
          f"Δ {point:+.3f} CI95 ({lo}, {hi}) | сидов +{pos}/−{neg}{verdict}")
    return {"metric": label, A: round(st.mean(m0), 3), B: round(st.mean(m1), 3),
            "delta": point, "ci_lo": lo, "ci_hi": hi,
            "seeds_pos": pos, "seeds_neg": neg,
            "seed_diffs": [round(x, 4) for x in d],
            "verdict": verdict}


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "sessions/tp41")
    rows = {a: [json.loads(l) for l in open(root / f"manifest_{a}.jsonl",
                                            encoding="utf-8") if l.strip()]
            for a in ARMS}
    ok = {a: [r for r in rows[a] if r.get("status") == "completed"] for a in ARMS}
    errs = {a: [r for r in rows[a] if r.get("status") == "error"] for a in ARMS}
    print("=== QC ===")
    for a in ARMS:
        print(f"  {a}: завершено {len(ok[a])}/{len(rows[a])}, ошибок {len(errs[a])}")
        for r in errs[a][:3]:
            print(f"    ERROR: {str(r.get('error'))[-140:]}")
    by_key = {}
    for a in ARMS:
        for r in ok[a]:
            by_key.setdefault((r["seed"], r["perm"]), {})[a] = r
    full = {k: v for k, v in by_key.items() if len(v) == 2}
    map_bad = [k for k, v in full.items() if v["A0"]["map_md5"] != v["A1"]["map_md5"]]
    perr = {a: sum(r.get("plugin_errors", 0) for r in ok[a]) for a in ARMS}
    # в blight-эксперименте blight-состояние/журнал ЛЕГАЛЬНЫ; целостность =
    # BlightMod установлен + нет чужих плагинов + нет no-blight мусора
    integ_bad = [k for k, v in full.items()
                 for a in ARMS for r in [v[a]]
                 if not r["integrity"]["blight_mod_present"]
                 or r["integrity"]["plugins"] != ["BlightMod"]]
    print(f"  пар: {len(full)}/480 | расхождений карт: {len(map_bad)} | "
          f"нарушений blight-целостности: {len(integ_bad)} | plugin-ошибок: {perr}")

    games = {}
    ro_total = ro_ok = 0
    for arm in ARMS:
        for r in ok[arm]:
            key = (r["seed"], r["perm"], arm)
            d = Path(r["out_dir"])
            events = [json.loads(l) for l in open(d / "trace.jsonl", encoding="utf-8")]
            bd = [e for e in events if e.get("kind") == "bot_decision"]
            ro_ev = [e for e in events if e.get("kind") == "corrupt_readonly"]
            ro_total += len(ro_ev)
            ro_ok += sum(1 for e in ro_ev
                         if e.get("state_unchanged") and e.get("rng_unchanged"))
            ro_fail_local = sum(1 for e in ro_ev
                                if not (e.get("state_unchanged") and e.get("rng_unchanged")))
            cd = [e for e in events if e.get("kind") == "corrupt_decision"]
            choice_map = {(e.get("turn"), e.get("player")): e
                          for e in events if e.get("kind") == "corrupt_choice"}
            pur = sum(1 for e in bd
                      if e.get("applied_ok") and e.get("action") == "purify_hex")
            cor = sum(1 for e in bd
                      if e.get("applied_ok") and e.get("action") == "corrupt_hex")
            builds = Counter()
            bank = end_turns = 0
            for e in bd:
                if e.get("applied_ok"):
                    a = e["action"]
                    if a in BUILD_ACTS:
                        builds[a] += 1
                    elif a == "trade_bank":
                        bank += 1
                    elif a == "end_turn":
                        end_turns += 1
            final = next((e for e in reversed(events)
                          if e.get("kind") == "snapshot" and e.get("tag") == "final"), None)
            bl = (final or {}).get("blight_internal") or {}
            fp = bl.get("players") or {}
            align_counts = Counter(str(v.get("alignment")) for v in fp.values())
            tears_total = sum(int(v.get("tears") or 0) for v in fp.values())
            # сборка решений с join выбранного по (turn, player, hex_id)
            decisions = []
            for e in cd:
                turn, pid = e.get("turn"), e.get("player")
                cands = e.get("candidates") or []
                scored = [c for c in cands if c.get("mod_score") is not None]
                mx_sc = max((c["mod_score"] for c in scored), default=None)
                at_max = sum(1 for c in scored
                             if mx_sc is not None
                             and abs(c["mod_score"] - mx_sc) <= EPS)
                uniq = sorted({round(c["mod_score"], 9) for c in scored}, reverse=True)
                second_gap = (round(uniq[0] - uniq[1], 6) if len(uniq) >= 2 else None)
                choice_ev = choice_map.get((turn, pid))
                chosen_hex = choice_ev.get("hex_id") if choice_ev else None
                chosen_cand = next((c for c in cands if c["hex_id"] == chosen_hex), None) \
                    if chosen_hex is not None else None
                max_impact = max((c["impact"] for c in cands), default=None)
                decisions.append({
                    "turn": turn, "pid": pid, "seq": e.get("seq"),
                    "arch": ARCH_NAME.get(next((b.get("player_name", "") for b in bd
                                                if b.get("player_id") == pid
                                                and b.get("player_name")), ""),
                                          "?"),
                    "align": e.get("alignment"),
                    "lead_margin": e.get("lead_margin"),
                    "n_cands": len(cands),
                    "at_max_group": at_max,
                    "second_gap": second_gap,
                    "max_impact": max_impact,
                    "chosen_is_corrupt": chosen_hex is not None,
                    "chosen_impact": (chosen_cand or {}).get("impact"),
                    "chosen_leader_loss": (chosen_cand or {}).get("expected_production_loss"),
                    "chosen_is_max_impact": (chosen_cand is not None and max_impact is not None
                                             and abs((chosen_cand or {}).get("impact", 0)
                                                     - max_impact) <= EPS),
                    "leader_avail": any(c["leader_buildings"] > 0 and c["impact"] > 0
                                        for c in cands),
                    "chosen_has_leader": bool(chosen_cand
                                              and chosen_cand["leader_buildings"] > 0),
                    "candidates": cands,
                })
            # corrupt-решения, где бот выбрал другое действие
            other_action = sum(1 for d in decisions if not d["chosen_is_corrupt"])
            games[key] = {"row": r, "decisions": decisions,
                          "other_action": other_action,
                          "ro_total": len(ro_ev), "ro_ok": sum(
                              1 for e in ro_ev
                              if e.get("state_unchanged") and e.get("rng_unchanged")),
                          "ro_fail": ro_fail_local,
                          "purify": pur, "corrupt_applied": cor,
                          "builds": dict(builds), "bank": bank,
                          "end_turns": end_turns,
                          "align_counts": dict(align_counts),
                          "tears_total": tears_total,
                          "first_metro": next((e["turn"] for e in bd
                                               if e.get("applied_ok")
                                               and e["action"] == "build_metropolis"), None),
                          "final_turns": r.get("turns")}
            del events
    print(f"\nпартий разобрано: {len(games)} | read-only контроль: "
          f"{sum(g['ro_ok'] for g in games.values())}/{ro_total} PASS, "
          f"нарушений {ro_ok and ro_total - ro_ok}")

    seed_ids = sorted({k[0] for k in games})

    # --- tie rate ---
    print("\n=== Tie rate (eps 1e-9) ===")
    per_arm_tie = {}
    all_dec = []
    for arm in ARMS:
        dd = [d for k, g in games.items() if k[2] == arm for d in g["decisions"]]
        t = sum(1 for d in dd if d["at_max_group"] >= 2)
        per_arm_tie[arm] = round(t / max(1, len(dd)), 4)
        print(f"  {arm}: решений {len(dd)} | tie rate {t / max(1, len(dd)):.1%} "
              f"(связка максимального score)")
        all_dec.extend((arm, d) for d in dd)
    r_tie = pair_table(lambda g: (sum(1 for d in g["decisions"]
                                      if d["at_max_group"] >= 2)
                                  / max(1, len(g["decisions"]))),
                       games, seed_ids, "tie rate")
    gaps = [d["second_gap"] for _, d in all_dec
            if d["at_max_group"] == 1 and d["second_gap"] is not None]
    if gaps:
        print(f"  разрыв лучший−второй (уникальный максимум): медиана {st.median(gaps):.4f}, "
              f"n={len(gaps)}")

    # --- качество выбранных целей (выбран corrupt_hex) ---
    chosen_total = {arm: sum(1 for k, g in games.items() if k[2] == arm
                             for d in g["decisions"] if d["chosen_is_corrupt"])
                    for arm in ARMS}
    max_imp = {arm: sum(1 for k, g in games.items() if k[2] == arm
                        for d in g["decisions"]
                        if d["chosen_is_corrupt"] and d["chosen_is_max_impact"])
               for arm in ARMS}
    lead_miss = {arm: sum(1 for k, g in games.items() if k[2] == arm
                          for d in g["decisions"]
                          if d["chosen_is_corrupt"] and d["leader_avail"]
                          and not d["chosen_has_leader"])
                 for arm in ARMS}
    lead_hit = {arm: sum(1 for k, g in games.items() if k[2] == arm
                         for d in g["decisions"]
                         if d["chosen_is_corrupt"] and d["leader_avail"]
                         and d["chosen_has_leader"])
                for arm in ARMS}
    weak = {arm: sum(1 for k, g in games.items() if k[2] == arm
                     for d in g["decisions"]
                     if d["chosen_is_corrupt"] and d["chosen_impact"] is not None
                     and d["max_impact"] is not None
                     and d["chosen_impact"] < d["max_impact"] - EPS)
            for arm in ARMS}
    other_action = {arm: sum(g["other_action"] for k, g in games.items() if k[2] == arm)
                    for arm in ARMS}
    ch_imp = {arm: [d["chosen_impact"] for k, g in games.items() if k[2] == arm
                    for d in g["decisions"] if d["chosen_is_corrupt"]
                    and d["chosen_impact"] is not None] for arm in ARMS}
    lead_imp = {arm: [d["chosen_leader_loss"] for k, g in games.items() if k[2] == arm
                      for d in g["decisions"] if d["chosen_is_corrupt"]
                      and d["chosen_leader_loss"] is not None] for arm in ARMS}
    print("\n=== Качество выбранных целей ===")
    for arm in ARMS:
        print(f"  {arm}: выбранных corrupt {chosen_total[arm]} | "
              f"с максимальным impact {max_imp[arm]} "
              f"({max_imp[arm] / max(1, chosen_total[arm]):.1%}) | "
              f"выбранная expected loss mean {st.mean(ch_imp[arm]):.3f} | "
              f"leader loss mean {st.mean(lead_imp[arm]):.3f} | "
              f"лидер-цель доступна и выбрана: {lead_hit[arm]}, "
              f"доступна и выбрана другая: {lead_miss[arm]} | "
              f"слабая при сильной: {weak[arm]} | "
              f"кандидаты были, выбрано другое действие: {other_action[arm]}")
    print("\n=== Парные метрики качества (bootstrap 0x41) ===")
    res_quality = []
    for nm, fn in (("доля выбранных с максимальным impact",
                    lambda g: (sum(1 for d in g["decisions"]
                                   if d["chosen_is_corrupt"]
                                   and d["chosen_is_max_impact"])
                               / max(1, sum(1 for d in g["decisions"]
                                            if d["chosen_is_corrupt"])))),
                   ("expected production loss выбранной цели",
                    lambda g: st.mean([d["chosen_impact"] for d in g["decisions"]
                                       if d["chosen_is_corrupt"]
                                       and d["chosen_impact"] is not None] or [0])),
                   ("leader loss выбранной цели",
                    lambda g: st.mean([d["chosen_leader_loss"] for d in g["decisions"]
                                       if d["chosen_is_corrupt"]
                                       and d["chosen_leader_loss"] is not None] or [0])),
                   ("tie rate решений",
                    lambda g: sum(1 for d in g["decisions"]
                                  if d["at_max_group"] >= 2)
                    / max(1, len(g["decisions"])))):
        res_quality.append(pair_table(fn, games, seed_ids, nm))

    # --- объём Blight-действий ---
    print("\n=== Объём Blight-действий ===")
    for nm, fn in (("corrupt_hex применено/партию", lambda g: g["corrupt_applied"]),
                   ("purify_hex применено/партию", lambda g: g["purify"])):
        pair_table(fn, games, seed_ids, nm)

    # --- guardrails ---
    print("\n=== Guardrails ===")
    guard = []
    guard.append(pair_table(lambda g: g["builds"].get("build_outpost", 0),
                            games, seed_ids, "поселения/партию", margin=-0.25))
    guard.append(pair_table(lambda g: g["builds"].get("build_metropolis", 0),
                            games, seed_ids, "города/партию", margin=-0.25))
    guard.append(pair_table(lambda g: st.mean(list(g["row"]["vp"].values())),
                            games, seed_ids, "финальные ОП/игрока", margin=-0.20))
    for nm, fn in (("build_route/партию", lambda g: g["builds"].get("build_route", 0)),
                   ("bank/партию", lambda g: g["bank"]),
                   ("end_turn/партию", lambda g: g["end_turns"]),
                   ("первая метрополия (ход)", lambda g: g["first_metro"] or 0)):
        pair_table(fn, games, seed_ids, nm)

    # --- faction outcomes ---
    print("\n=== Faction outcomes (финальные состояния, вторично) ===")
    for arm in ARMS:
        al = Counter()
        tears = 0
        for k, g in games.items():
            if k[2] != arm:
                continue
            for a, n in g["align_counts"].items():
                al[a] += n
            tears += g["tears_total"]
        print(f"  {arm}: финальные alignment {dict(al)} | tears {tears}")

    # --- по архетипам ---
    print("\n=== По архетипам (corrupt-решения и качество выборов) ===")
    for arch in ("c3po", "r2d2", "t800", "walle"):
        for arm in ARMS:
            dd = [d for k, g in games.items() if k[2] == arm
                  for d in g["decisions"] if d["arch"] == arch]
            chosen = [d for d in dd if d["chosen_is_corrupt"]]
            mx = sum(1 for d in chosen if d["chosen_is_max_impact"])
            tie = sum(1 for d in dd if d["at_max_group"] >= 2)
            lm = sum(1 for d in chosen if d["leader_avail"] and not d["chosen_has_leader"])
            print(f"  {arm} {arch}: решений {len(dd)} (tie {tie / max(1, len(dd)):.0%}), "
                  f"выбрано corrupt {len(chosen)}, с макс. impact "
                  f"{mx / max(1, len(chosen)):.1%}, лидер-промах {lm}")

    # --- победы и места (вторично) ---
    print("\n=== Победы и места (вторично) ===")
    for arm in ARMS:
        w = Counter(r["winner_type"] for r in ok[arm])
        seats = Counter(r["winner_pid"] for r in ok[arm])
        turns = [r["turns"] for r in ok[arm]]
        print(f"  {arm}: ходы медиана {int(st.median(turns))} | победы {dict(w)} | "
              f"места {dict(sorted(seats.items()))}")

    out = {"tie_rate": per_arm_tie,
           "chosen": {arm: {"total": chosen_total[arm], "max_impact": max_imp[arm],
                            "leader_hit": lead_hit[arm], "leader_missed": lead_miss[arm],
                            "weak": weak[arm], "other_action": other_action[arm]}
                      for arm in ARMS},
           "quality": res_quality, "guardrails": guard,
           "plugin_errors": perr, "pairs": len(full), "map_mismatch": len(map_bad)}
    json.dump(out, open(root / "tp41_analysis_summary.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\nагрегаты: {root / 'tp41_analysis_summary.json'}")


if __name__ == "__main__":
    main()
