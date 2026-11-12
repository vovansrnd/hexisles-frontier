#!/usr/bin/env python3
"""sim_cult_price_experiment — изолированный эксперимент A: условная цена потери Культа.

Четыре руки на парных сидах:
  A0 — базовый скорер (без изменений);
  A1 — цена только при немедленном выходе из Культа (score_after ≤ 0);
  A2 — цена при немедленном выходе + умеренная при score_after == 1;
  A3 — более сильная цена в обоих рискованных случаях.

Коэффициенты взяты из измеренных порогов трасс (sensitivity_cult_price2.json,
медианы зазоров «отказ от очищения»), не подобраны под желаемое число выходов:
  P_exit = 1.3  ≈ медиана зазора группы немедленного выхода (1.274);
  P_step = 0.7  ≈ половина медианы группы «в одном шаге» (1.435);
  A3:      1.5 / 1.4 ≈ верхний квартиль немедленных / медиана одношаговых.

Реализация: обёртка над mod.score_ai_action ВНУТРИ воркера симуляции
(исключительно лабораторный инструмент; mods_pack.py/ai_brain.py/правила не меняются).
Дополнительно инструментируется _change_alignment для подсчёта выходов из Культа.

  python3 tools/sim_cult_price_experiment.py --games 300 --seed-start 50001 --workers 10
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sim_blight_variants import BASE_CONFIG, BOT_TYPES  # noqa: E402

ARMS = {
    "A0": {"exit": 0.0, "step": 0.0},
    "A1": {"exit": 1.3, "step": 0.0},
    "A2": {"exit": 1.3, "step": 0.7},
    "A3": {"exit": 1.5, "step": 1.4},
}


def wrap_price(mod, price_exit: float, price_step: float, stats: dict) -> None:
    """Условная цена потери Культа: вычитается из оценки purify_hex.

    Только если игрок сейчас культист; размер зависит от того, куда после
    очищения попадает alignment_score (≤0 — выход; ==1 — один шаг до выхода).
    """
    orig = mod.score_ai_action

    def priced(game, pid, action, context=None):
        base = orig(game, pid, action, context)
        if action.type != "purify_hex" or base is None:
            return base
        st = mod._player_blight_state(game, pid)
        if str(st.get("alignment")) != "cult":
            return base
        after = int(st.get("alignment_score", 0)) - 1
        if after <= 0 and price_exit:
            stats["priced_exit"] = stats.get("priced_exit", 0) + 1
            return base - price_exit
        if after == 1 and price_step:
            stats["priced_step"] = stats.get("priced_step", 0) + 1
            return base - price_step
        return base

    mod.score_ai_action = priced


def wrap_alignment_tracking(mod, stats: dict) -> None:
    """Счётчики переходов стороны (п.2 поручения): события выхода и УНИКАЛЬНЫЕ игроки."""
    orig = mod._change_alignment

    exited_pids: set = stats.setdefault("exited_pids", set())

    def tracked(game, pid, delta):
        before = str(mod._player_blight_state(game, pid).get("alignment", "neutral"))
        result = orig(game, pid, delta)
        after = str(result)
        if before == "cult" and after != "cult":
            stats["cult_exits"] = stats.get("cult_exits", 0) + 1
            exited_pids.add(int(pid))
        if before != "cult" and after == "cult":
            stats["cult_entries"] = stats.get("cult_entries", 0) + 1
        return result

    mod._change_alignment = tracked


def run_one(job):
    seed, arm, price_exit, price_step = job
    import server_local_v4 as server  # noqa
    from mods_pack import BlightMod as _BlightMod  # noqa
    from isles_core import GamePhase  # noqa

    config = dict(BASE_CONFIG)
    game = server.make_game(f"SIM{seed:07d}", config, seed=seed)
    mod = next((p for p in game._plugins if isinstance(p, _BlightMod)), None)
    if mod is None:
        raise RuntimeError("blight mod required")

    stats: dict = {}
    wrap_alignment_tracking(mod, stats)
    if price_exit or price_step:
        wrap_price(mod, price_exit, price_step, stats)

    lineup = list(BOT_TYPES)
    random.Random(seed ^ 0xB17A17).shuffle(lineup)
    for bot_type in lineup:
        ok, message = server.add_bot(game, bot_type)
        if not ok:
            raise RuntimeError(f"could not add bot {bot_type}: {message}")
    if not game.start_game().success:
        raise RuntimeError("could not start game")

    def chooser(pid, legal_actions):
        brain = game.bot_brains.get(pid)
        action = brain.choose_action(pid, legal_actions)
        if not any(action is legal or (action.type == legal.type and action.p == legal.p)
                   for legal in legal_actions):
            raise RuntimeError(f"bot {pid} illegal action {action.type} {action.p}")
        return action

    winner_pid = game.run_to_completion(chooser, max_steps=8000)
    status = "completed" if game.phase == GamePhase.FINISHED and winner_pid is not None else "unfinished"
    player_blight = game.ext.get("blight", {}).get("players", {})
    players = []
    for pid, player in enumerate(game.players):
        state = player_blight.get(str(pid), {})
        players.append({
            "bot_type": str(game.ext.get("bot_types", {}).get(str(pid), "unknown")),
            "alignment": str(state.get("alignment", "neutral")),
            "vp": int(game.get_victory_points(pid, public=False)),
            "win": pid == winner_pid,
            "tears": int(state.get("tears", 0)),
            "purified": int(state.get("purified_count", 0)),
            "corrupted": int(state.get("corrupt_count", 0)),
            "tears_vp": int(state.get("tears_vp", 0)),
            "purify_vp": int(state.get("purify_vp", 0)),
            "alt_vp": int(state.get("alternate_vp", 0)),
            # декомпозиция источников слёз (п.3 поручения)
            "tears_direct": int(state.get("tears_from_direct", 0) or 0),
            "tears_suppression": int(state.get("tears_from_suppression", 0) or 0),
            "tears_immunity": int(state.get("tears_from_immunity", 0) or 0),
            # exited_once вычисляется в агрегации по exited_pids
            "exited_once": int(pid) in stats.get("exited_pids", set()),
        })
    counters = (game.ext.get("mod_telemetry", {}) or {}).get("blight", {}).get("counters", {})
    exited_pids = stats.pop("exited_pids", set())
    return {
        "seed": seed, "arm": arm, "status": status,
        "winner_bot_type": str(game.ext.get("bot_types", {}).get(str(winner_pid), "unknown")) if winner_pid is not None else None,
        "turns": int(game.global_turn),
        "players": players,
        "exited_pids": sorted(exited_pids),
        "finished_outside_cult": sum(1 for p in players if p["alignment"] != "cult"),
        "stats": dict(stats),
        "immunity_tears": int(counters.get("cult_immunity_tears", 0)),
        "blessed_units": int(counters.get("order_blessing_resource_units", 0)),
        "suppressed": int(counters.get("resource_units_suppressed", 0)),
        "alignment_changes": int(counters.get("alignment_changes", 0)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=300, help="пар на руку (парных сидов)")
    ap.add_argument("--seed-start", type=int, default=50001)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--arms", default="A0,A1,A2,A3")
    ap.add_argument("--out", default="sessions/cult_price_experiment_v2.json")
    args = ap.parse_args()

    arms = {a.strip(): ARMS[a.strip()] for a in args.arms.split(",") if a.strip() in ARMS}
    jobs = []
    for i in range(args.games):
        seed = args.seed_start + i
        for arm, price in arms.items():
            jobs.append((seed, arm, price["exit"], price["step"]))
    random.shuffle(jobs)  # равномерная нагрузка воркеров, сиды не зависят от порядка

    print(f"рук: {list(arms)} | пар сидов: {args.games} | всего партий: {len(jobs)}")
    t0 = time.time()
    with mp.Pool(args.workers) as pool:
        rows = []
        for i, row in enumerate(pool.imap_unordered(run_one, jobs, chunksize=4)):
            rows.append(row)
            if (i + 1) % 100 == 0:
                print(f"  {i+1}/{len(jobs)} ({time.time()-t0:.0f}с)", flush=True)
    print(f"готово за {time.time()-t0:.0f}с")

    # агрегация по рукам (только завершённые)
    summary = {}
    for arm in arms:
        done = [r for r in rows if r["arm"] == arm and r["status"] == "completed"]
        n = len(done)
        players = [p for r in done for p in r["players"]]
        exits = sum(r["stats"].get("cult_exits", 0) for r in done)
        entries = sum(r["stats"].get("cult_entries", 0) for r in done)
        wins = Counter(p["bot_type"] for p in players if p["win"])
        summary[arm] = {
            "games_completed": n,
            "cult_exits_total": exits,
            "cult_exits_per_game": round(exits / n, 3) if n else None,
            "cult_entries_total": entries,
            "exited_players_per_game": round(
                sum(len(r.get("exited_pids", [])) for r in done) / n, 3) if n else None,
            "finished_outside_cult_per_game": round(
                sum(r.get("finished_outside_cult", 0) for r in done) / n, 3) if n else None,
            "final_cult_players": sum(1 for p in players if p["alignment"] == "cult"),
            "final_order_players": sum(1 for p in players if p["alignment"] == "order"),
            "purify_total": sum(p["purified"] for p in players),
            "purify_per_game": round(sum(p["purified"] for p in players) / n, 2) if n else None,
            "corrupt_total": sum(p["corrupted"] for p in players),
            "tears_total": sum(p["tears"] for p in players),
            "tears_direct_total": sum(p.get("tears_direct", 0) for p in players),
            "tears_suppression_total": sum(p.get("tears_suppression", 0) for p in players),
            "tears_immunity_total": sum(p.get("tears_immunity", 0) for p in players),
            "alt_vp_total": sum(p["alt_vp"] for p in players),
            "tears_vp_total": sum(p["tears_vp"] for p in players),
            "purify_vp_total": sum(p["purify_vp"] for p in players),
            "immunity_tears_total": sum(r["immunity_tears"] for r in done),
            "blessed_units_total": sum(r["blessed_units"] for r in done),
            "suppressed_total": sum(r["suppressed"] for r in done),
            "avg_turns": round(sum(r["turns"] for r in done) / n, 1) if n else None,
            "wins_by_bot": dict(wins),
            "priced_exit_cases": sum(r["stats"].get("priced_exit", 0) for r in done),
            "priced_step_cases": sum(r["stats"].get("priced_step", 0) for r in done),
        }

    print("\n=== Сводка по рукам ===")
    for arm, s in summary.items():
        print(f"{arm}: партий {s['games_completed']} | выходов из Культа {s['cult_exits_total']} "
              f"({s['cult_exits_per_game']}/партию) | очищений {s['purify_total']} "
              f"| слёз {s['tears_total']} | мод-ОП {s['alt_vp_total']} "
              f"| иммунитет-слёзы {s['immunity_tears_total']} | победы {s['wins_by_bot']} "
              f"| ходов/партия {s['avg_turns']}")

    # парные сравнения по сидам (A_i − A0 на одном сиде)
    by_seed_arm = {(r["seed"], r["arm"]): r for r in rows if r["status"] == "completed"}
    pair = {}
    for arm in arms:
        if arm == "A0":
            continue
        exits_diff = []
        altvp_diff = []
        tears_diff = []
        win_diff = 0
        for i in range(args.games):
            seed = args.seed_start + i
            base = by_seed_arm.get((seed, "A0"))
            test = by_seed_arm.get((seed, arm))
            if not base or not test:
                continue
            exits_diff.append(test["stats"].get("cult_exits", 0) - base["stats"].get("cult_exits", 0))
            altvp_diff.append(sum(p["alt_vp"] for p in test["players"]) - sum(p["alt_vp"] for p in base["players"]))
            tears_diff.append(sum(p["tears"] for p in test["players"]) - sum(p["tears"] for p in base["players"]))
            if test["winner_bot_type"] and test["winner_bot_type"] != base["winner_bot_type"]:
                win_diff += 1
        pair[arm] = {
            "games_paired": len(exits_diff),
            "exits_diff_sum": sum(exits_diff),
            "exits_diff_mean": round(sum(exits_diff) / len(exits_diff), 3) if exits_diff else None,
            "altvp_diff_sum": sum(altvp_diff),
            "tears_diff_sum": sum(tears_diff),
            "winner_changed_games": win_diff,
        }
        print(f"парное {arm}−A0: выходы Δ={pair[arm]['exits_diff_sum']} "
              f"({pair[arm]['exits_diff_mean']}/партию) | мод-ОП Δ={pair[arm]['altvp_diff_sum']} "
              f"| слёзы Δ={pair[arm]['tears_diff_sum']} | смен победителя: {pair[arm]['winner_changed_games']}")

    out = Path(args.out)
    out.write_text(json.dumps({"arms": arms, "summary": summary, "pair": pair,
                               "rows": rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("\nсохранено:", out)


if __name__ == "__main__":
    main()
