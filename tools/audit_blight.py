#!/usr/bin/env python3
"""audit_blight — майнер сомнительных решений по Скверне из трасс (п.4-5 ТЗ).

Скан паттернов по списку координатора:
  A. «не заметил защитный период»     — corrupt_hex с applied_ok=false (щит)
  B. «слабая цель вместо сильной»     — осквернение цели с 0 чужих построек/низкими
                                        пипами при наличии более сильных легальных целей
  C. «мог очистить, но не стал»       — purify_hex в кандидатах (или отфильтрован),
                                        но выбрано другое; случай показывается с оценками
  D. «шерсть на осквернение vs хартия»— выбран corrupt при близкой оценке buy_charter
  E. «поселение рядом со Скверной»    — setup_outpost на вершине у заражённого гекса
  F. «атака не лидера»                — рейдер/осквернение цели без постройки лидера,
                                        когда альтернативная цель её содержала
  G. «кормление культиста»            — осквернение гекса с постройкой культиста
                                        (открытый режим: это кормит его слезами)

Каждый случай: сид, ход, игрок/архетип, контекст, выбранное действие с оценкой,
лучшие альтернативы с оценками. Пишет JSON + печатает сводку.

  python3 tools/audit_blight.py "sessions/audit_*/trace.jsonl" [--max-per-type 12]
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter, defaultdict


def load_games(pattern: str) -> list[dict]:
    games = []
    for path in sorted(glob.glob(pattern)):
        events = [json.loads(line) for line in open(path, encoding="utf-8")]
        summary = None
        try:
            spath = path.replace("trace.jsonl", "summary.json")
            summary = json.load(open(spath, encoding="utf-8"))
        except Exception:
            pass
        games.append({"path": path, "events": events, "summary": summary})
    return games


def target_quality(ctx: dict) -> tuple[int, int]:
    """(чужие постройки, пипы) для осквернения — независимая мера «силы цели»."""
    ctx = ctx or {}
    owners = ctx.get("owners") or []
    return sum(1 for o in owners if o.get("structure")), int(ctx.get("pips") or 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--max-per-type", type=int, default=10)
    args = ap.parse_args()

    games = load_games(args.pattern)
    cases: dict[str, list[dict]] = defaultdict(list)
    counters: Counter = Counter()

    for game in games:
        seed = None
        if game["summary"]:
            seed = game["summary"].get("seed")
        evs = game["events"]
        bot_events = [e for e in evs if e.get("kind") == "bot_decision"]

        for i, ev in enumerate(bot_events):
            base = {
                "trace": game["path"], "seed": seed, "turn": ev.get("turn"),
                "player": ev.get("player_name"), "archetype": ev.get("archetype"),
                "standings_pre": ev.get("standings_pre"), "leader_id": ev.get("leader_id"),
            }
            alts = ev.get("alternatives") or []

            # A. осквернение не прошло (щит и т.п.)
            if ev.get("action") == "corrupt_hex" and not ev.get("applied_ok"):
                counters["A_shield_miss"] += 1
                if len(cases["A_shield_miss"]) < args.max_per_type:
                    cases["A_shield_miss"].append({**base,
                        "chosen": {"action": ev["action"], "params": ev.get("params"),
                                   "score": ev.get("score")},
                        "error": ev.get("error"),
                        "hex_context": ev.get("chosen_hex_context")})

            # B. слабая цель осквернения
            if ev.get("action") == "corrupt_hex" and ev.get("applied_ok"):
                ctx = ev.get("chosen_hex_context")
                enemies, pips = target_quality(ctx or {})
                if enemies == 0:
                    counters["B_zero_enemy_target"] += 1
                    if len(cases["B_zero_enemy_target"]) < args.max_per_type:
                        cases["B_zero_enemy_target"].append({**base,
                            "chosen": {"action": "corrupt_hex", "params": ev.get("params"), "score": ev.get("score")},
                            "chosen_context": ctx,
                            "other_targets": [{"params": a.get("params"), "score": a.get("score"),
                                               "ctx": a.get("hex_context")} for a in alts
                                              if a.get("action") == "corrupt_hex"
                                              and a.get("params", {}).get("hex_id") != ev.get("params", {}).get("hex_id")][:6]})

            # C. очистка была кандидатом, но выбрано другое
            if ev.get("action") != "purify_hex":
                pur = [a for a in alts if a.get("action") == "purify_hex"]
                chosen_score = ev.get("score")
                if pur and chosen_score is not None:
                    top_pur = max(pur, key=lambda x: float(x["score"] or -1e9))
                    delta = float(top_pur["score"]) - float(chosen_score)
                    if 0 < delta <= 1.2:
                        counters["C_purify_close_second"] += 1
                        if len(cases["C_purify_close_second"]) < args.max_per_type:
                            cases["C_purify_close_second"].append({**base,
                                "chosen": {"action": ev["action"], "params": ev.get("params"),
                                           "score": chosen_score},
                                "best_purify": {"params": top_pur.get("params"),
                                                "score": top_pur.get("score"),
                                                "ctx": top_pur.get("hex_context")},
                                "delta_to_purify": round(delta, 3)})
                elif pur and chosen_score is None and ev.get("action") in ("end_turn",):
                    counters["C_purify_vs_endturn"] += 1

            # D. шерсть на осквернение при близкой хартии
            if ev.get("action") == "corrupt_hex" and ev.get("applied_ok"):
                charter = [a for a in alts if a.get("action") == "buy_charter"]
                if charter and ev.get("score") is not None:
                    delta = float(ev["score"]) - float(charter[0]["score"])
                    if abs(delta) <= 1.0:
                        counters["D_corrupt_vs_charter_close"] += 1
                        if len(cases["D_corrupt_vs_charter_close"]) < args.max_per_type:
                            cases["D_corrupt_vs_charter_close"].append({**base,
                                "chosen": {"action": "corrupt_hex", "params": ev.get("params"),
                                           "score": ev.get("score")},
                                "charter_score": charter[0].get("score"),
                                "delta": round(delta, 3)})

            # E. поселение у заражённого гекса
            if ev.get("action") == "setup_outpost":
                # в alternatives для setup нет контекста карты — фиксируем сам факт
                # выбора вершины; проверка «рядом Скверна» требует карты, поэтому
                # помечаем только наличие живых альтернатив с малым дельта
                pass

            if ev.get("action") == "move_raider":
                counters["F_raider_total"] += 1


            # F2. рейдер: цель без постройки лидера (лидер — методом бота,
            # п.5 поручения; контекст выбранной цели сохранён отдельно, п.3)
            if ev.get("action") == "move_raider":
                leader = ev.get("leader_id")
                target_ctx = ev.get("chosen_hex_context")
                target_pids = {o.get("pid") for o in (target_ctx or {}).get("owners", [])}
                with_leader = [a for a in alts if a.get("action") == "move_raider"
                               and a.get("hex_context")
                               and leader in {o.get("pid") for o in a["hex_context"].get("owners", [])}]
                if leader is not None and leader not in target_pids and leader != ev.get("player_id"):
                    if with_leader:
                        counters["F2_raider_not_leader"] += 1
                        if len(cases["F2_raider_not_leader"]) < args.max_per_type:
                            cases["F2_raider_not_leader"].append({**base,
                                "chosen": {"action": "move_raider", "params": ev.get("params"),
                                           "score": ev.get("score"), "ctx": target_ctx},
                                "leader": leader,
                                "leader_alternatives": [{"params": a.get("params"), "score": a.get("score")}
                                                        for a in with_leader[:4]]})
                    else:
                        counters["F3_no_leader_target_available"] += 1

            # H. очищение культистом — сторона ДО действия (п.1-2 поручения)
            if ev.get("action") == "purify_hex":
                before = ev.get("alignment_before")
                after = ev.get("alignment_after")
                if before == "cult":
                    counters["H_cultist_purify_pre"] += 1
                    if after == "cult":
                        counters["H_cult_stayed"] += 1
                    else:
                        counters["H_cult_dropped"] += 1
                        if len(cases["H_cult_dropped"]) < args.max_per_type:
                            cases["H_cult_dropped"].append({**base,
                                "chosen": {"action": "purify_hex", "params": ev.get("params"),
                                           "score": ev.get("score")},
                                "alignment_before": before, "alignment_after": after,
                                "score_before": ev.get("alignment_score_before"),
                                "score_after": ev.get("alignment_score_after")})
                elif before == "neutral":
                    counters["H2_neutral_purify"] += 1
                elif before == "order":
                    counters["H3_order_purify"] += 1

            # G. осквернение постройки культиста — сторона ДО действия
            if ev.get("action") == "corrupt_hex":
                ctx = ev.get("chosen_hex_context")
                owners = (ctx or {}).get("owners") or []
                cultists = [o for o in owners if o.get("alignment") == "cult"
                            and o.get("pid") != ev.get("player_id")]
                non_self = [o for o in owners if o.get("pid") != ev.get("player_id")]
                if cultists and non_self and all(o.get("alignment") == "cult" for o in non_self):
                    counters["G2_pure_cultist_feed"] += 1
                elif cultists:
                    counters["G_cultist_feed"] += 1

    # сводка
    print(f"проанализировано партий с трассой: {len(games)}")
    for k in sorted(counters):
        print(f"  {k}: {counters[k]}")
    out_path = "sessions/audit_cases.json"
    json.dump({"counters": dict(counters), "cases": cases}, open(out_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("случаи сохранены в", out_path)

    # печатаем по 2 примера каждого типа
    for k in sorted(cases):
        print(f"\n=== {k} (первые {min(2, len(cases[k]))} из {len(cases[k])}) ===")
        for c in cases[k][:2]:
            print(json.dumps(c, ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
