#!/usr/bin/env python3
"""analyze_settlements — задача B: размещение поселений с учётом Скверны.

Реплей тех же 12 аудиторских сидов (детерминированно, сверка с трассами) с анализом
каждого решения setup_outpost / build_outpost:

  * номинальная оценка бота (как recorded в трассе — сверяется);
  * «скорректированная ожидаемая добыча за бросок» для размещающего игрока
    с учётом стадии Скверны у соседних гексов:
      - нейтрал: заражённый гекс глушит постройку (min(стадия, yield));
      - Культ: иммунитет + слёзы (+1 за заражённый гекс на каждом броске);
      - Орден: как нейтрал сейчас (плюс будущая очистка — качественно);
  * риск будущего заражения: число чистых продуктивных соседей (кандидатов на
    распространение при 7);

И вывод конкретных случаев: где скорректированный лучший кандидат отличается от
выбранного ботом (при нейтральной перспективе). Без правок скоринга.

  python3 tools/analyze_settlements.py
"""
from __future__ import annotations

import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import server_local_v4 as S  # noqa: E402
from isles_core import GamePhase, StructureType, TerrainType  # noqa: E402
from play_session import (  # noqa: E402
    blight_alignment, blight_score, make_session_game, score_alternatives, _jf2,
)

PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
PERMS = [",".join(p) for p in __import__("itertools").permutations(["c3po", "r2d2", "t800", "walle"])]


def vertex_hexes(g, vid: int) -> list[dict]:
    out = []
    for hid in g.map.vertices[vid].adjacent_hexes:
        tile = g.map.hexes[hid]
        out.append({
            "hex_id": hid,
            "terrain": str(tile.terrain).split(".")[-1].lower(),
            "token": int(tile.number_token or 0),
            "pips": PIPS.get(int(tile.number_token or 0), 0),
            "stage": int(tile.ext.get("blight_stage", 0) or 0),
            "productive": (tile.terrain not in (TerrainType.WASTELAND, TerrainType.RUINS)
                           and int(tile.number_token or 0) in PIPS),
        })
    return out


def adjusted(out: list[dict], scenario: str) -> dict:
    """Ожидаемая добыча постройки на вершине за бросок, при разных перспективах."""
    gain = 0.0
    tears = 0.0
    clean_prod = 0
    infected_prod = 0
    for h in out:
        if not h["productive"]:
            continue
        if h["stage"] > 0:
            infected_prod += 1
            if scenario == "cult":
                gain += h["pips"] / 36.0  # иммунитет
                tears += h["pips"] / 36.0  # слёзы с заражённого сектора
            # нейтрал/орден: стадия>=1 глушит постройку (yield 1, min(stage,1)=1)
        else:
            clean_prod += 1
            gain += h["pips"] / 36.0
    return {
        "expected_gain_per_roll": round(gain, 4),
        "expected_tears_per_roll": round(tears, 4),
        "clean_productive_neighbors": clean_prod,
        "infected_productive_neighbors": infected_prod,
        "spread_risk_neighbors": clean_prod,  # кандидаты на заражение при 7
    }


def replay_and_analyze(seeds: list[int]) -> list[dict]:
    decisions = []
    for seed in seeds:
        perm = PERMS[seed % 24]
        g = make_session_game(f"LAB{seed:04d}", seed, perm.split(","))
        for bt in perm.split(","):
            S.add_bot(g, bt)
        g.start_game()
        # сверка с трассой
        trace_path = PROJECT_ROOT / f"sessions/audit2_s{seed}/trace.jsonl"
        trace_events = [json.loads(l) for l in open(trace_path, encoding="utf-8")]
        # сверяем только решения о поселении (реплей пишет только их)
        trace_seq = [(e["turn"], e["player_id"], e["action"], json.dumps(e.get("params") or {}, sort_keys=True))
                     for e in trace_events
                     if e.get("kind") == "bot_decision" and e.get("action") in ("setup_outpost", "build_outpost")]
        replay_seq = []

        steps = 0
        while g.phase != GamePhase.FINISHED and steps < 60000:
            steps += 1
            if not bot_step_analyzed(g, decisions, trace_seq, replay_seq):
                break
        # финальная сверка
        if replay_seq != trace_seq:
            print(f"  ! seed {seed}: СВЕРКА НЕ СОШЛАСЬ ({len(replay_seq)} vs {len(trace_seq)} решений о поселении)")
            for i, (a, b) in enumerate(zip(replay_seq, trace_seq)):
                if a != b:
                    print(f"    первое расхождение на позиции {i}: replay={a} trace={b}")
                    break
        else:
            print(f"  seed {seed}: реплей совпал с трассой ({len(replay_seq)} решений о поселении)")
    return decisions


def bot_step_analyzed(g, decisions: list, trace_seq, replay_seq) -> bool:
    if g.phase in (GamePhase.LOBBY, GamePhase.FINISHED):
        return False
    offer = g.active_trade_offer
    if offer is not None:
        pend = [p for p in g.bot_pids if p != offer.from_player and p not in offer.rejected_by]
        if pend:
            brain = g.bot_brains.get(pend[0])
            acc = bool(brain.respond_to_trade(offer)) if brain else False
            g.respond_trade(pend[0], acc)
            return True
        return False
    if g.phase == GamePhase.DISCARD:
        pend = sorted(p for p in g.pending_discards if p in g.bot_pids)
        if not pend:
            return False
        pid = pend[0]
    else:
        pid = g.current_player_id
        if pid not in g.bot_pids:
            return False
    legal = g.enumerate_legal_actions(pid)
    if not legal:
        if g.phase == GamePhase.MAIN:
            g.end_turn(pid)
            return True
        return False
    brain = g.bot_brains.get(pid)
    a = brain.choose_action(pid, legal) if brain else legal[0]
    if not any(x is a for x in legal):
        a = next((x for x in legal if x.type == a.type and x.p == a.p), legal[0])

    if a.type in ("setup_outpost", "build_outpost"):
        is_setup = a.type == "setup_outpost"
        vid = int(a.p.get("vertex_id", -1))
        alts = score_alternatives(g, brain, pid, g.phase, legal)
        alt_by_vid = {}
        for x in alts:
            if x.get("action") == a.type and x.get("score") is not None:
                alt_by_vid[int((x.get("params") or {}).get("vertex_id", -1))] = float(x["score"])
        align = blight_alignment(g, pid) or "neutral"
        candidates = []
        for cand_vid, nominal in sorted(alt_by_vid.items()):
            hexes = vertex_hexes(g, cand_vid)
            rec = {
                "vertex_id": cand_vid,
                "nominal_bot_score": round(nominal, 4),
                "hexes": hexes,
                "adj_neutral": adjusted(hexes, "neutral"),
                "adj_cult": adjusted(hexes, "cult"),
            }
            candidates.append(rec)
        chosen_nominal = alt_by_vid.get(vid)
        decisions.append({
            "turn": int(g.global_turn),
            "pid": int(pid),
            "phase": "setup" if is_setup else "main",
            "player_alignment": align,
            "chosen_vertex": vid,
            "chosen_nominal_score": chosen_nominal,
            "candidates": candidates,
        })
        replay_seq.append((int(g.global_turn), int(pid), a.type, json.dumps({k: _jf2(v) for k, v in a.p.items()}, sort_keys=True)))

    r = g.apply_action(pid, a)
    if not r.success and g.phase == GamePhase.MAIN:
        g.end_turn(pid)
    return True


def main() -> None:
    seeds = list(range(40001, 40013))
    print("=== Задача B: реплей 12 сидов, размещение с учётом Скверны ===")
    decisions = replay_and_analyze(seeds)
    setup = [d for d in decisions if d["phase"] == "setup"]
    expansion = [d for d in decisions if d["phase"] == "main"]
    print(f"решений о поселении: {len(decisions)} (расстановка {len(setup)}, экспансия {len(expansion)})")

    # сколько выбранных вершин касались заражённых продуктивных гексов
    exposed = [d for d in decisions if chosen_infected(d)]
    print(f"выбранные вершины у заражённых продуктивных гексов: {len(exposed)} "
          f"(расстановка {sum(1 for d in exposed if d['phase']=='setup')}, "
          f"экспансия {sum(1 for d in exposed if d['phase']=='main')})")

    # «свободная замена»: кандидат с номиналом не хуже (−0.05) и добычей
    # (нейтральная перспектива) строго лучше выбранной
    free_swaps = []
    for d in decisions:
        ch = next((c for c in d["candidates"] if c["vertex_id"] == d["chosen_vertex"]), None)
        if not ch:
            continue
        ch_nom = ch["nominal_bot_score"] or 0.0
        ch_adj = ch["adj_neutral"]["expected_gain_per_roll"]
        for c in d["candidates"]:
            if c["vertex_id"] == d["chosen_vertex"]:
                continue
            if (c["nominal_bot_score"] or 0.0) >= ch_nom - 0.05 and \
               c["adj_neutral"]["expected_gain_per_roll"] > ch_adj + 1e-9:
                free_swaps.append({
                    "turn": d["turn"], "pid": d["pid"], "phase": d["phase"],
                    "chosen_vertex": d["chosen_vertex"], "chosen_nominal": ch["nominal_bot_score"],
                    "chosen_adj": ch["adj_neutral"], "chosen_hexes": ch["hexes"],
                    "swap_vertex": c["vertex_id"], "swap_nominal": c["nominal_bot_score"],
                    "swap_adj": c["adj_neutral"], "swap_hexes": c["hexes"],
                })
                break  # лучший достаточно близкий по номиналу
    print(f"«свободных замен» (номинал сопоставим, добыча с учётом Скверны лучше): {len(free_swaps)}")
    for ph in ("setup", "main"):
        print(f"  из них в {'расстановке' if ph == 'setup' else 'экспансии'}: "
              f"{sum(1 for c in free_swaps if c['phase'] == ph)}")

    # широкая метрика для контекста (все кандидаты, у которых adjusted-лучший ≠ выбранного)
    flip = 0
    for d in decisions:
        if not d["candidates"]:
            continue
        best_adj = max(d["candidates"], key=lambda c: (c["adj_neutral"]["expected_gain_per_roll"],
                                                       c["nominal_bot_score"] or 0))
        chosen = next((c for c in d["candidates"] if c["vertex_id"] == d["chosen_vertex"]), None)
        if chosen and best_adj["vertex_id"] != d["chosen_vertex"] and \
           best_adj["adj_neutral"]["expected_gain_per_roll"] > chosen["adj_neutral"]["expected_gain_per_roll"]:
            flip += 1
    print(f"(контекст) широкая метрика «adjusted-лучший ≠ выбранного»: {flip} из {len(decisions)}")

    # топ примеров свободных замен (максимальный прирост добычи)
    free_swaps.sort(key=lambda c: -(c["swap_adj"]["expected_gain_per_roll"] - c["chosen_adj"]["expected_gain_per_roll"]))
    for c in free_swaps[:6]:
        print("\n--- пример свободной замены ---")
        print(json.dumps(c, ensure_ascii=False, indent=1)[:1200])

    out = Path("sessions/taskB_settlements.json")
    json.dump({"decisions": decisions, "free_swaps": free_swaps},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nсохранено:", out)


def chosen_infected(d) -> bool:
    ch = next((c for c in d["candidates"] if c["vertex_id"] == d["chosen_vertex"]), None)
    if not ch:
        return False
    return ch["adj_neutral"]["infected_productive_neighbors"] > 0


if __name__ == "__main__":
    main()
