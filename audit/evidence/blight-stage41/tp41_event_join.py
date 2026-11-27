#!/usr/bin/env python3
"""tp41_event_join — Stage 4.1.1: форензика сопоставления событий
corrupt_decision / corrupt_readonly / corrupt_choice / bot_decision.

Проблема прежнего анализатора: choice_map с ключом (turn, player) не
уникален — несколько решений в одном ходу перезаписывали друг друга.

Правильный join: события трассы в порядке seq (монотонность проверяется);
последовательность одной итерации chooser (run_tp41.py):
  1. corrupt_decision
  2. corrupt_readonly
  3. (внутренний choose_action)
  4. corrupt_choice — если выбран corrupt_hex
  5. bot_decision (запись решения, applied_ok записывается после apply)

Каждый corrupt_decision закрывается ближайшим следующим bot_decision;
corrupt_choice относится к pending-решению; повторное использование
запрещено.

Запуск: python3 tools/tp41_event_join.py
"""
from __future__ import annotations

import gzip
import json
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("sessions/tp41")
ARMS = ("A0", "A1")
EPS = 1e-9
BOOT = 10000
BUILD_ACTS = ("build_route", "build_outpost", "build_metropolis")


def load_rows(root: Path):
    rows = {}
    for arm in ARMS:
        rows[arm] = [json.loads(l)
                     for l in open(root / f"manifest_{arm}.jsonl", encoding="utf-8")
                     if l.strip()]
    return rows


def join_events(events: list[dict], row: dict):
    """Возвращает (decisions, stats) для одной трассы."""
    stats = Counter()
    problems = []
    decisions = []
    pending = None
    seqs = [e.get("seq") for e in events]
    stats["seq_monotonic"] = all(
        seqs[i] < seqs[i + 1] for i in range(len(seqs) - 1))
    for e in events:
        k = e.get("kind")
        seq = e.get("seq")
        if k == "corrupt_decision":
            if pending is not None:
                stats["decision_without_bot_decision"] += 1
                problems.append({"seq": seq, "what": "corrupt_decision без bot_decision",
                                 "prev_seq": pending["decision"].get("seq")})
            pending = {"decision": e, "choice": None, "readonly": None,
                       "bot_decision": None}
        elif k == "corrupt_readonly":
            if pending is not None and pending["readonly"] is None:
                pending["readonly"] = e
            else:
                stats["readonly_orphan"] += 1
        elif k == "corrupt_choice":
            if pending is None:
                stats["choice_without_decision"] += 1
            elif pending["choice"] is not None:
                stats["choice_duplicate"] += 1
                problems.append({"seq": seq, "what": "второй corrupt_choice в рамках одного решения"})
            else:
                pending["choice"] = e
        elif k == "bot_decision":
            if pending is not None:
                pending["bot_decision"] = e
                decisions.append(pending)
                pending = None
            # bot_decision без pending corrupt_decision — нормальный не-corrupt путь
    if pending is not None:
        stats["decision_without_bot_decision"] += 1
        problems.append({"what": "незакрытое решение в конце трассы",
                         "seq": pending["decision"].get("seq")})
    # --- проверки внутри каждого закрытого решения ---
    for d in decisions:
        dec, ch, bd = d["decision"], d["choice"], d["bot_decision"]
        d["archetype"] = (row.get("seats") or {}).get(str(dec.get("player")), "?")
        if ch is not None:
            if ch.get("turn") != dec.get("turn") or ch.get("player") != dec.get("player"):
                stats["choice_turn_player_mismatch"] += 1
            if bd is not None:
                if ch.get("hex_id") != (bd.get("params") or {}).get("hex_id"):
                    stats["choice_hex_mismatch_bot_decision"] += 1
                if ch.get("action") != bd.get("action"):
                    stats["choice_action_mismatch_bot_decision"] += 1
        hex_id = (ch or {}).get("hex_id") if ch is not None else None
        cands = dec.get("candidates") or []
        d["chosen_in_candidates"] = (hex_id is not None
                                     and any(c.get("hex_id") == hex_id for c in cands))
        if hex_id is not None and not d["chosen_in_candidates"]:
            stats["chosen_hex_not_in_candidates"] += 1
            problems.append({"seq": seq if False else dec.get("seq"),
                             "seed": row["seed"], "perm": row["perm"],
                             "turn": dec.get("turn"), "player": dec.get("player"),
                             "chosen_hex": hex_id,
                             "candidate_hexes": [c.get("hex_id") for c in cands],
                             "applied_ok": (bd or {}).get("applied_ok")})
        d["applied_ok"] = (bd or {}).get("applied_ok")
        d["chosen_action"] = (bd or {}).get("action")
        d["applied_action"] = (bd or {}).get("action") if d["applied_ok"] else None
        d["chosen_impact"] = (ch or {}).get("impact") if ch is not None else None
        d["mod_scores_max"] = max(
            (float(c.get("mod_score")) for c in cands
             if c.get("mod_score") is not None), default=None)
        d["n_candidates"] = len(cands)
    return decisions, stats, problems


def main() -> None:
    rows = load_rows(ROOT)
    all_decisions = []
    total_stats = Counter()
    all_problems = []
    per_game = {}
    games = {}
    for arm in ARMS:
        for r in rows[arm]:
            if r.get("status") != "completed":
                continue
            d = Path(r["out_dir"])
            events = [json.loads(l) for l in open(d / "trace.jsonl", encoding="utf-8")]
            decisions, stats, problems = join_events(events, r)
            for p_ in problems:
                p_.update({"arm": arm, "seed": r["seed"], "perm": r["perm"]})
            all_problems.extend(problems)
            for k, v in stats.items():
                total_stats[k] += v
            key = (r["seed"], r["perm"], arm)
            games_for_metrics = None
            # собираем решения для what-if
            joined = []
            for dec in decisions:
                ch = dec.get("choice")
                cands = dec["decision"].get("candidates") or []
                scored = [c for c in cands if c.get("mod_score") is not None]
                mx = max((float(c["mod_score"]) for c in scored), default=None)
                group = [c for c in scored
                         if mx is not None and abs(float(c["mod_score"]) - mx) <= EPS] \
                    if mx is not None else []
                hex_id = (ch or {}).get("hex_id")
                chosen_cand = next((c for c in cands if c.get("hex_id") == hex_id), None) \
                    if hex_id is not None else None
                chosen_in_group = (chosen_cand is not None
                                   and any(c.get("hex_id") == hex_id for c in group))
                impacts = [float(c.get("impact") or 0) for c in group]
                joined.append({
                    "seed": r["seed"], "perm": r["perm"], "arm": arm,
                    "archetype": dec["archetype"],
                    "align": dec["decision"].get("alignment"),
                    "n_candidates": dec.get("n_candidates"),
                    "at_max_group": len(group),
                    "group_impacts": impacts,
                    "group_same_impact": (max(impacts) - min(impacts) <= EPS)
                    if impacts else True,
                    "chosen_is_corrupt": chosen_cand is not None,
                    "chosen_in_group": chosen_in_group,
                    "chosen_impact": (float(chosen_cand.get("impact") or 0)
                                      if chosen_cand else None),
                    "group_max_impact": max(impacts) if impacts else None,
                    "chosen_leader_buildings": (chosen_cand or {}).get("leader_buildings", 0),
                    "group_leader_impacts": [
                        (round(float(c.get("expected_production_loss") or 0)
                               * float(c.get("leader_loss_share") or 0))
                         * int(c.get("pips") or 0))
                        for c in group],
                    "applied_ok": dec.get("applied_ok"),
                })
            per_game_joined = joined
            # guardrail-агрегаты
            builds = Counter()
            bank = end_turns = pur = cor = 0
            first_metro = None
            bd_all = [e for e in events if e.get("kind") == "bot_decision"]
            for e in bd_all:
                if e.get("applied_ok"):
                    a = e.get("action")
                    if a in BUILD_ACTS:
                        builds[a] += 1
                        if a == "build_metropolis" and first_metro is None:
                            first_metro = e.get("turn")
                    elif a == "trade_bank":
                        bank += 1
                    elif a == "purify_hex":
                        pur += 1
                    elif a == "corrupt_hex":
                        cor += 1
                    elif a == "end_turn":
                        end_turns += 1
            vps = list((r.get("vp") or {}).values())
            per_game_joined = per_game_joined
            games_for_metrics = {
                "row": r, "joined": per_game_joined,
                "builds": dict(builds), "bank": bank, "end_turns": end_turns,
                "pur": pur, "cor": cor, "first_metro": first_metro,
                "vp_mean": round(st.mean(vps), 3) if vps else None,
                "final_turns": r.get("turns"),
                "ro_ok": sum(1 for e in events
                             if e.get("kind") == "corrupt_readonly"
                             and e.get("state_unchanged") and e.get("rng_unchanged")),
                "ro_total": sum(1 for e in events
                                if e.get("kind") == "corrupt_readonly"),
                "decisions_list": per_game_joined,
                "del_events_marker": None,
            }
            games_for_metrics.pop("del_events_marker", None)
            games[key] = games_for_metrics
            all_decisions.extend(per_game_joined)
            del events
    print("=== Forensics: сопоставление событий ===")
    for k, v in sorted(total_stats.items()):
        print(f"  {k}: {v}")
    print(f"  проблем-записей: {len(all_problems)}")
    for p_ in all_problems[:5]:
        print(f"    {json.dumps(p_, ensure_ascii=False)[:200]}")

    # --- ЧАСТЬ B: real chosen corrupt_hex отсутствующие в candidates ---
    stale = [p_ for p_ in all_problems
             if p_.get("what") == "chosen_hex_not_in_candidates"]
    stale_applied = [p_ for p_ in stale if p_.get("applied_ok")]
    stale_rejected = [p_ for p_ in stale if not p_.get("applied_ok")]
    print(f"\n=== ЧАСТЬ B. Выбранный corrupt_hex вне кандидатов своего решения ===")
    print(f"  всего: {len(stale)} | применено движком: {len(stale_applied)} | "
          f"отклонено: {len(stale_rejected)}")
    by_arm = Counter(p_["arm"] for p_ in stale)
    print(f"  по рукам: {dict(by_arm)}")
    if stale:
        Path("sessions/tp41/stale_cases.json").write_text(
            json.dumps(stale, ensure_ascii=False, indent=1), encoding="utf-8")
        print("  список: sessions/tp41/stale_cases.json")
    else:
        print("  случаев НЕТ — прежняя находка 651 отозвана (артефакт join по (turn, player))")

    # --- ЧАСТЬ C: what-if после корректного join ---
    print("\n=== ЧАСТЬ C. Offline what-if (корректный join) ===")
    chosen_all = [d for d in all_decisions if d["chosen_is_corrupt"]]
    print(f"выбрано corrupt_hex: {len(chosen_all)} из {len(all_decisions)} решений "
          f"({len(all_decisions) - len(chosen_all)} — другое действие)")
    not_in_group = sum(1 for d in chosen_all if not d["chosen_in_group"])
    print(f"выбранных вне группы максимума mod_score: {not_in_group}")
    for arm in ARMS:
        c = Counter()
        dec_arm = [d for d in all_decisions if d["arm"] == arm]
        ch_arm = [d for d in dec_arm if d["chosen_is_corrupt"]]
        for d in ch_arm:
            if d["group_same_impact"]:
                c["группа с одинаковым impact"] += 1
            else:
                c["группа с разными impact"] += 1
                if d["chosen_impact"] is not None \
                        and d["chosen_impact"] < d["group_max_impact"] - 1e-9:
                    c["исправимо tie-break"] += 1
                else:
                    c["выбран уже максимум группы"] += 1
        tot = sum(c.values())
        print(f"  {arm}: {dict(c)} | всего {tot}")

    seed_ids = sorted({k[0] for k in games})
    print("\n=== Парные метрики (bootstrap 0x41) ===")
    import random
    RNG = random.Random(0x41)

    def boot_ci(vals):
        if not vals:
            return None, None, None, 0, 0
        point = st.mean(vals)
        sample = sorted(st.mean([RNG.choice(vals) for _ in range(len(vals))])
                        for _ in range(BOOT))
        return (round(point, 4), round(sample[int(0.025 * BOOT)], 4),
                round(sample[int(0.975 * BOOT) - 1], 4),
                sum(1 for x in vals if x > 0), sum(1 for x in vals if x < 0))

    import math

    def pair_metric(fn, A="A0", B="A1", margin=None, label=None):
        ps = {s: {a: [] for a in ARMS} for s in seed_ids}
        for (seed, perm, arm), g in games.items():
            ps[seed][arm].append(fn(g))
        m0 = [st.mean(ps[s][A]) for s in seed_ids if ps[s][A]]
        m1 = [st.mean(ps[s][B]) for s in seed_ids if ps[s][B]]
        d = [b - a for a, b in zip(m0, m1)]
        point, lo, hi, pos, neg = boot_ci(d)
        v = f" | NI (маржа {margin}): {'PASS' if lo > margin else 'FAIL'}" \
            if margin is not None else ""
        print(f"    {label or nm}: {A} {st.mean(m0):.3f} → {B} {st.mean(m1):.3f} | "
              f"Δ {point:+.3f} CI95 ({lo}, {hi}) | сидов +{pos}/−{neg}{v}")
        return {"metric": nm, A: round(st.mean(m0), 3), B: round(st.mean(m1), 3),
                "delta": point, "ci_lo": lo, "ci_hi": hi,
                "seeds_pos": pos, "seeds_neg": neg,
                "seed_diffs": [round(x, 4) for x in d]}

    nm = None
    print("\n--- Объём Blight-действий ---")
    for nm_, fn in (("corrupt_hex/партию", lambda g: g["cor"]),
                    ("purify_hex/партию", lambda g: g["pur"])):
        pair_metric(fn)
    print("\n--- Качество выборов (доля от corrupt-выборов) ---")
    pair_metric(lambda g: (sum(1 for d in g["decisions_list"]
                               if d["chosen_is_corrupt"] and d.get("max_impact_chosen"))
                           / max(1, sum(1 for d in g["decisions_list"]
                                        if d["chosen_is_corrupt"]))),
                label="доля выбранных с максимальным impact")
    print("\n--- Guardrails ---")
    guard = []
    for nm_, fn, margin in (("поселения/партию", lambda g: g["builds"].get("build_outpost", 0), -0.25),
                            ("города/партию", lambda g: g["builds"].get("build_metropolis", 0), -0.25),
                            ("финальные ОП/игрока", lambda g: g["vp_mean"], -0.20)):
        A, B = "A0", "A1"
        ps = {s: {A: [], B: []} for s in seed_ids}
        for (seed, perm, arm), g in games.items():
            ps[seed][arm].append(fn(g))
        m0 = [st.mean(ps[s][A]) for s in seed_ids if ps[s][A]]
        m1 = [st.mean(ps[s][B]) for s in seed_ids if ps[s][B]]
        d = [b - a for a, b in zip(m0, m1)]
        point, lo, hi, pos, neg = boot_ci(d)
        v = f" | NI: {'PASS' if lo > margin else 'FAIL'}"
        print(f"    {nm_}: {A} {st.mean(m0):.3f} → {B} {st.mean(m1):.3f} | "
              f"Δ {point:+.3f} CI95 ({lo}, {hi}){v}")
        guard.append({"metric": nm_, "A0": round(st.mean(m0), 3),
                      "A1": round(st.mean(m1), 3), "delta": point,
                      "ci_lo": lo, "ci_hi": hi})

    # --- по архетипам ---
    print("\n=== По архетипам (tie rate и доля макс-impact) ===")
    for arch in ("c3po", "r2d2", "t800", "walle"):
        for arm in ARMS:
            dd = [d for d in all_decisions
                  if d["arm"] == arm and d["archetype"] == arch]
            ch_ = [d for d in dd if d["chosen_is_corrupt"]]
            tie = sum(1 for d in dd if d["at_max_group"] >= 2)
            mx = sum(1 for d in ch_ if d["chosen_is_max_impact"] if "chosen_is_max_impact" in d) \
                if False else sum(1 for d in ch_
                                  if d.get("chosen_is_max_impact"))
            mx = mx if False else sum(
                1 for d in ch_
                if d.get("group_max_impact") is not None
                and d.get("chosen_impact") is not None
                and abs(d["chosen_impact"] - d["group_max_impact"]) <= 1e-9)
            print(f"  {arm} {arch}: решений {len(dd)} (tie {tie / max(1, len(dd)):.0%}), "
                  f"выбрано corrupt {len(ch_)}, макс-impact {mx / max(1, len(ch_)):.1%}")

    json.dump({"join_stats": dict(total_stats),
               "stale_cases": stale,
               "problems": all_problems[:50]},
              open("sessions/tp41/event_join_forensics.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\nдетали: sessions/tp41/event_join_forensics.json")


if __name__ == "__main__":
    main()
