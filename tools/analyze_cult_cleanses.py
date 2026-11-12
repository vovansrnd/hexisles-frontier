#!/usr/bin/env python3
"""analyze_cult_cleanses — задача A: разбор всех очищений игроками в Культе.

Для каждого очищения (alignment_before == "cult"):
  * потеря выравнивания и дистанция до выхода из Культа (score → 0);
  * восстановленная добыча (свои структуры у гекса × стадия до очищения);
  * платёж (зерно 1 или экстренный ресурс ×2);
  * благословение/щит: щит есть всегда; благословение — только у Ордена
    (handle_purify_hex в factions даёт blessing/VP только alignment == "order");
  * лучшая альтернатива с оценкой и запас (margin) до неё;
  * «цена переворота решения»: какой штраф за потерю Культа перевернул бы выбор.

Для каждого игрока-культиста — траектория: все его purify/corrupt по ходам,
баланс выравнивания после каждого, итоговая сторона (из summary).

  python3 tools/analyze_cult_cleanses.py "sessions/audit2_s*/trace.jsonl"
"""
from __future__ import annotations

import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

STRUCT_YIELD = {"OUTPOST": 1, "METROPOLIS": 2}


def load(pattern: str) -> list[dict]:
    games = []
    for path in sorted(glob.glob(pattern)):
        evs = [json.loads(line) for line in open(path, encoding="utf-8")]
        summ = json.load(open(path.replace("trace.jsonl", "summary.json"), encoding="utf-8"))
        games.append({"path": path, "events": evs, "summary": summ})
    return games


def best_alt(alt_list: list[dict], action: str, params: dict, only_considered=True) -> dict | None:
    cands = []
    for a in alt_list:
        if a.get("action") == action and (a.get("params") or {}) == (params or {}):
            continue
        if only_considered and not a.get("considered", True):
            continue
        if a.get("score") is None:
            continue
        cands.append(a)
    if not cands:
        return None
    return max(cands, key=lambda x: float(x["score"]))


def main() -> None:
    pattern = sys.argv[1] if len(sys.argv) > 1 else "sessions/audit2_s*/trace.jsonl"
    games = load(pattern)
    cases = []
    trajectories: dict[tuple, list] = defaultdict(list)

    for game in games:
        seed = game["summary"].get("seed")
        evs = game["events"]
        bot_events = [e for e in evs if e.get("kind") == "bot_decision"]
        # траектории: все модовые действия всех игроков
        for e in bot_events:
            if e.get("action") in ("purify_hex", "corrupt_hex"):
                trajectories[(seed, e["player_id"])].append({
                    "turn": e["turn"], "action": e["action"],
                    "score_before": e.get("alignment_score_before"),
                    "score_after": e.get("alignment_score_after"),
                    "align_before": e.get("alignment_before"),
                    "align_after": e.get("alignment_after"),
                    "ok": e.get("applied_ok"),
                })

        for e in bot_events:
            if e.get("action") != "purify_hex":
                continue
            if e.get("alignment_before") != "cult":
                continue
            ctx = e.get("chosen_hex_context") or {}
            stage = int(ctx.get("stage") or 0)
            pips = int(ctx.get("pips") or 0)
            own = [o for o in (ctx.get("owners") or []) if o.get("pid") == e.get("player_id")]
            recovered_units = sum(min(stage, STRUCT_YIELD.get(o.get("structure"), 1)) for o in own)
            # платёж: экстренная оплата (pay_resource в параметрах) = 2 ресурса
            pay = (e.get("params") or {}).get("pay_resource")
            pay_desc = (f"2×{pay}" if pay else "1×зерно")
            alt = best_alt(e.get("alternatives") or [], "purify_hex", e.get("params"))
            score = e.get("score")
            margin = None
            flip_price = None
            if alt is not None and score is not None:
                margin = round(float(alt["score"]) - float(score), 4)  # >0: альтернатива была сильнее (шум/ничья)
                flip_price = margin if margin > 0 else 0.0
            cases.append({
                "trace": game["path"], "seed": seed, "turn": e["turn"],
                "player": e.get("player_name"), "archetype": e.get("archetype"),
                "hex": (e.get("params") or {}).get("hex_id"),
                "stage_before": stage, "pips": pips,
                "own_structures": [o.get("structure") for o in own],
                "recovered_units": recovered_units,
                "expected_gain_per_roll": round(pips * recovered_units / 36.0, 3),
                "payment": pay_desc,
                "alignment_score_before": e.get("alignment_score_before"),
                "alignment_score_after": e.get("alignment_score_after"),
                "align_after": e.get("alignment_after"),
                "blessing": False,  # factions: blessing только у order-aligned
                "shield": f"до T{e['turn'] + 1 * 4}",  # purified_shield_rounds=1 × 4 игрока
                "score": score,
                "best_alt": ({"action": alt["action"], "params": alt.get("params"),
                              "score": alt.get("score")} if alt else None),
                "margin_to_alt": margin,
                "flip_price": flip_price,
            })

    # агрегаты
    n = len(cases)
    print(f"=== Задача A: очищения игроков-культистов ({n} случаев, партий: {len(games)}) ===")
    dropped = [c for c in cases if c["align_after"] != "cult"]
    stayed = [c for c in cases if c["align_after"] == "cult"]
    print(f"остались культистами: {len(stayed)} | вышли в нейтрал этим же действием: {len(dropped)}")
    close_to_exit = [c for c in stayed if (c["alignment_score_after"] or 0) <= 1]
    print(f"после очищения остались Культом с score<=1 (один шаг до выхода): {len(close_to_exit)}")
    recovering = [c for c in cases if c["recovered_units"] > 0]
    zero_recover = [c for c in cases if c["recovered_units"] == 0]
    print(f"восстановили свою добычу: {len(recovering)} | без собственной добычи у гекса: {len(zero_recover)}")
    alt_stronger = [c for c in cases if (c["margin_to_alt"] or 0) > 0.15]
    print(f"случаи, где альтернатива была сильнее выбранного (margin>0.15, выбор из-за шума): {len(alt_stronger)}")
    if n:
        import statistics as st
        print(f"средний margin до лучшей альтернативы: {st.mean([abs(c['margin_to_alt']) for c in cases if c['margin_to_alt'] is not None]):.3f}")
        print(f"макс. цена переворота решения (штраф за Культ, переворачивающий выбор): "
              f"{max((c['flip_price'] or 0) for c in cases):.3f}")

    # траектории игроков: накопленная цена
    print("\n=== Траектории культистов (модовые действия в порядке ходов) ===")
    final_align = {}
    for g in games:
        bf = g["summary"]["blight_final_internal"]["players"]
        for pid, data in bf.items():
            final_align[(g["summary"]["seed"], int(pid))] = data.get("alignment")
    for (seed, pid), traj in sorted(trajectories.items()):
        who = None
        arch = None
        for e in traj:
            pass
        # имя/archetype из любого события
        for game in games:
            if game["summary"]["seed"] != seed:
                continue
            for e in game["events"]:
                if e.get("kind") == "bot_decision" and e.get("player_id") == pid:
                    who, arch = e.get("player_name"), e.get("archetype")
                    break
            if who:
                break
        purifies = [t for t in traj if t["action"] == "purify_hex" and t["ok"]]
        corrupts = [t for t in traj if t["action"] == "corrupt_hex" and t["ok"]]
        net = (len(corrupts) - len(purifies))
        print(f"seed {seed} pid{pid} {who} ({arch}): oсквернений {len(corrupts)}, очищений {len(purifies)}, "
              f"нетто-баланс {net:+d}, финал: {final_align.get((seed, pid))}")
        if purifies:
            chain = " ".join(f"T{t['turn']}:{t['action'][:3]}{t['score_before']}→{t['score_after']}" for t in traj)
            print(f"    {chain}")

    out = Path("sessions/taskA_cult_cleanses.json")
    json.dump({"cases": cases,
               "trajectories": {f"{s}:{p}": t for (s, p), t in trajectories.items()}},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nдетали сохранены в", out)


if __name__ == "__main__":
    main()
