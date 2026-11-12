#!/usr/bin/env python3
"""analyze_cult_price_stats — п.1 поручения: парные разности + 95% доверительные интервалы.

Единица парного наблюдения — СИД (начальные условия совпадают; после первого
расходящегося решения траектории и потребление RNG расходятся, поэтому сид, а не
партия, является корректной парой).

Метрики:
  * переходы cult→не-cult (события);
  * уникальные игроки, вышедшие хотя бы раз, за партию;
  * игроки, закончившие партию вне Культа, за партию;
  * слёзы всего и по источникам (direct/suppression/immunity);
  * мод-ОП;
  * победы по архетипам (пары «кто выиграл на этом сиде»).

Интервалы: бутстрап-процентиль (10000 ресемплов по сидам) + нормальная аппроксимация.

  python3 tools/analyze_cult_price_stats.py [sessions/cult_price_experiment_v2.json]
"""
from __future__ import annotations

import json
import random
import statistics as st
import sys
from collections import Counter

PATH = sys.argv[1] if len(sys.argv) > 1 else "sessions/cult_price_experiment_v2.json"
BOOT = 10000
RNG = random.Random(0xC0FFEE)


def bootstrap_ci(vals, stat=lambda xs: st.mean(xs)):
    if not vals:
        return None, None, None
    point = stat(vals)
    lo_list = []
    for _ in range(BOOT):
        sample = [RNG.choice(vals) for _ in range(len(vals))]
        lo_list.append(stat(sample))
    lo_list.sort()
    lo = lo_list[int(0.025 * BOOT)]
    hi = lo_list[int(0.975 * BOOT) - 1]
    se = st.pstdev(vals) / (len(vals) ** 0.5) if len(vals) > 1 else 0.0
    return round(point, 3), (round(lo, 3), round(hi, 3)), round(1.96 * se, 3)


def main() -> None:
    data = json.load(open(PATH, encoding="utf-8"))
    rows = [r for r in data["rows"] if r["status"] == "completed"]
    arms = list(data["arms"])
    seeds = sorted({r["seed"] for r in rows})
    by_sa = {(r["seed"], r["arm"]): r for r in rows}
    # плоские поля для парных разностей
    for r in rows:
        r["cult_exits"] = r["stats"].get("cult_exits", 0)
        r["cult_entries"] = r["stats"].get("cult_entries", 0)
        r["exited_players"] = len(r.get("exited_pids", []))
        r["finished_outside_cult"] = r.get("finished_outside_cult", 0)
        r["tears_total"] = sum(p["tears"] for p in r["players"])
        r["alt_vp_total"] = sum(p["alt_vp"] for p in r["players"])
        r["tears_immunity_player_sum"] = sum(p.get("tears_immunity", 0) for p in r["players"])
        r["tears_direct_sum"] = sum(p.get("tears_direct", 0) for p in r["players"])
        r["tears_suppression_sum"] = sum(p.get("tears_suppression", 0) for p in r["players"])
        r["tears_immunity_sum"] = r["immunity_tears"]
    print(f"партий завершено: {len(rows)} | сидов: {len(seeds)} | руки: {arms}")

    def diffs(arm, field):
        out = []
        for s in seeds:
            a0 = by_sa.get((s, "A0"))
            ai = by_sa.get((s, arm))
            if a0 and ai:
                out.append(ai[field] - a0[field])
        return out

    def pair_table(title, fields):
        print(f"\n== {title} (разность A_i − A0, пары по сидам; 95% CI — бутстрап) ==")
        for arm in arms:
            if arm == "A0":
                continue
            for field in fields:
                d = diffs(arm, field)
                if not d:
                    continue
                mean, ci, nse = bootstrap_ci(d)
                npos = sum(1 for x in d if x > 0)
                nneg = sum(1 for x in d if x < 0)
                print(f"  {arm}−A0 {field:34s}: Δ={mean:>8} CI95 {ci} (±{nse} НА) "
                      f"[сидов: +{npos}/−{nneg}/0={len(d)-npos-nneg}]")

    pair_table("Сторона Культа", [
        "cult_exits",
        "exited_players",
        "finished_outside_cult",
    ])

    pair_table("Экономика", ["tears_total", "alt_vp_total", "immunity_tears",
                             "tears_direct_sum", "tears_suppression_sum", "tears_immunity_sum"])

    # суммы источников слёз по игрокам за партию (уже посчитаны выше)
    for r in rows:
        r["tears_direct_sum"] = sum(p.get("tears_direct", 0) for p in r["players"])
        r["tears_suppression_sum"] = sum(p.get("tears_suppression", 0) for p in r["players"])
        r["tears_immunity_sum"] = sum(p.get("tears_immunity", 0) for p in r["players"])
    pair_table("Источники слёз (за партию)", ["tears_direct_sum", "tears_suppression_sum", "tears_immunity_sum"])

    # победы по архетипам: на каждом сиде — архетип победителя в A0 и в A_i
    print("\n== Победы по архетипам (пары сидов; смена = A_i и A0 победили разные архетипы) ==")
    archs = sorted({p["bot_type"] for r in rows for p in r["players"]})
    for arm in arms:
        if arm == "A0":
            continue
        wins0 = Counter()
        winsi = Counter()
        same = diff = 0
        swap_matrix = Counter()
        for s in seeds:
            a0 = by_sa.get((s, "A0"))
            ai = by_sa.get((s, arm))
            if not a0 or not ai:
                continue
            w0 = a0.get("winner_bot_type")
            wi = ai.get("winner_bot_type")
            wins0[w0] += 1
            winsi[wi] += 1
            if w0 == wi:
                same += 1
            else:
                diff += 1
                swap_matrix[(w0, wi)] += 1
        print(f"  {arm}: победы A0 {dict(wins0)} → {arm} {dict(winsi)} | совпало {same}, сменилось {diff}")
        for (w0, wi), n in swap_matrix.most_common(6):
            print(f"    {w0} → {wi}: {n}")

    # финал вне Культа по архетипам
    print("\n== Игроки, закончившие вне Культа, по архетипам (всего на руку) ==")
    for arm in arms:
        c = Counter(p["bot_type"] for r in rows if r["arm"] == arm for p in r["players"]
                    if p["alignment"] != "cult")
        print(f"  {arm}: {dict(c)}")


if __name__ == "__main__":
    main()
