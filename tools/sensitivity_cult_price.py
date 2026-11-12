#!/usr/bin/env python3
"""sensitivity_cult_price — A-чувствительность: условная цена риска выхода из Культа.

По 62 культистским очищениям (sessions/taskA_cult_cleanses.json):
  * для каждого случая — зазор gap = score(purify) − score(лучшей альтернативы);
    выбор переворачивается при штрафе P > gap (P вычитается из purify);
  * распределение зазоров по группам: немедленный выход (6, align_after != cult),
    «в одном шаге» (score_after == 1), прочие (запас ≥ +2);
  * таблица чувствительности: при P из сетки сколько решений меняется в каждой группе.

Ограничение: trade_bank/propose_trade не имеют сопоставимой оценки в трассах —
«лучшая альтернатива» ограничена несделочными действиями (на момент решения
реальный выбор бота уже сравнил сделки и всё равно взял purify).

  python3 tools/sensitivity_cult_price.py
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

P_GRID = [0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]


def main() -> None:
    import glob
    data = json.load(open("sessions/taskA_cult_cleanses.json", encoding="utf-8"))
    cases = data["cases"]

    # индекс сырых событий: (seed, turn, player) → событие purify (для зазора)
    raw_index = {}
    for path in sorted(glob.glob("sessions/audit2_s*/trace.jsonl")):
        for line in open(path, encoding="utf-8"):
            ev = json.loads(line)
            if ev.get("kind") != "bot_decision" or ev.get("action") != "purify_hex":
                continue
            raw_index[(ev.get("player_name"), ev.get("turn"), path.split("/")[1])] = ev

    def event_for(c):
        seed_dir = f"audit2_s{c['seed']}"
        return raw_index.get((c["player"], c["turn"], seed_dir))

    rows = []
    for c in cases:
        ev = event_for(c)
        if ev is None or ev.get("score") is None:
            continue
        score = float(ev["score"])
        # максимум по ВСЕМ прочим легальным действиям (одна шкала, до действия)
        others = [float(a["score"]) for a in (ev.get("alternatives") or [])
                  if a.get("score") is not None
                  and not (a.get("action") == "purify_hex"
                           and (a.get("params") or {}) == (ev.get("params") or {}))]
        alt_score = max(others) if others else 0.0
        if not others:
            alt_action = "end_turn(фолбэк)"
        else:
            best = max((a for a in (ev.get("alternatives") or [])
                        if a.get("score") is not None
                        and not (a.get("action") == "purify_hex"
                                 and (a.get("params") or {}) == (ev.get("params") or {}))),
                       key=lambda a: float(a["score"]), default=None)
            alt_action = (best or {}).get("action", "?")
        gap = round(score - float(alt_score), 4)
        after = c.get("alignment_score_after")
        if c.get("align_after") != "cult":
            group = "immediate_exit"
        elif after == 1:
            group = "one_step_left"
        elif (after or 0) == 2:
            group = "two_left"
        else:
            group = "safe"
        rows.append({
            "seed": c["seed"], "turn": c["turn"], "player": c["player"],
            "archetype": c["archetype"], "group": group,
            "score": score, "best_alt_action": alt_action,
            "best_alt_score": float(alt_score), "gap": gap,
            "recovered_units": c.get("recovered_units"),
            "pips": c.get("pips"),
        })

    by_group = defaultdict_list = {}
    for r in rows:
        by_group.setdefault(r["group"], []).append(r)

    print(f"случаев с сопоставимой оценкой: {len(rows)} из {len(cases)}")
    print("\nЗазор gap = score(purify) − score(лучшей альтернативы); выбор меняется при P > gap")
    for g in ("immediate_exit", "one_step_left", "two_left", "safe"):
        grp = by_group.get(g, [])
        if not grp:
            print(f"  {g}: 0 случаев")
            continue
        gaps = [r["gap"] for r in grp]
        print(f"  {g}: {len(grp)} случаев | gap: min {min(gaps):.3f}, медиана {st.median(gaps):.3f}, "
              f"max {max(gaps):.3f}, среднее {st.mean(gaps):.3f}")

    print("\nЧувствительность: сколько решений переворачивается при штрафе P (вычитается из purify)")
    print(f"{'P':>5} | {'все (62)':>8} | {'немедл.выход (6)':>16} | {'в одном шаге':>12} | {'safe':>6}")
    for P in P_GRID:
        all_f = sum(1 for r in rows if r["gap"] < P)
        ie = sum(1 for r in by_group.get("immediate_exit", []) if r["gap"] < P)
        os_ = sum(1 for r in by_group.get("one_step_left", []) if r["gap"] < P)
        sf = sum(1 for r in by_group.get("safe", []) if r["gap"] < P)
        print(f"{P:>5} | {all_f:>8} | {ie:>16} | {os_:>12} | {sf:>6}")

    # поархетипная разбивка рискованной группы
    print("\nРискованная группа (immediate_exit + one_step_left) по архетипам:")
    risky = by_group.get("immediate_exit", []) + by_group.get("one_step_left", [])
    from collections import Counter
    print(" ", dict(Counter(r["archetype"] for r in risky)))
    risky.sort(key=lambda r: r["gap"])
    print("\nВсе рискованные случаи (отсортированы по gap — порогу переворота):")
    for r in risky:
        print(f"  seed {r['seed']} T{r['turn']:>3} {r['player']} ({r['archetype']}): "
              f"purify={r['score']:.3f} vs {r['best_alt_action']}={r['best_alt_score']:.3f} "
              f"→ gap {r['gap']:.3f} | восстановил {r['recovered_units']} ед. (pips {r['pips']}) [{r['group']}]")

    out = Path("sessions/sensitivity_cult_price.json")
    json.dump({"rows": rows, "p_grid": P_GRID}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\nсохранено:", out)


if __name__ == "__main__":
    main()
