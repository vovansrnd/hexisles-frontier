#!/usr/bin/env python3
"""sensitivity_settlements_local — уточнение задачи B (без патчей и A/B-прогона).

Локальная предельная ценность ресурса: в шкале скорера вклад гекса =
(pips/5) × resource_base(ресурс, ход) → в номинал решения с коэффициентом
k (1.0 для setup_outpost, 0.88 для build_outpost). Поэтому «подавление»
заражённого гекса в номинальных пунктах = k × (pips/5) × base_r(ход).

Для каждого решения о поселении на заражённой вершине пересчитываются
скорректированные номиналы кандидатов в трёх перспективах:
  нейтрал — подавление полностью;
  культ   — иммунитет (без вычитания), отдельно сообщается слёзный потенциал;
  орден   — подавление сейчас, но восстановимо очисткой (1 зерно + щит),
            восстанавливаемая часть показана отдельно.

  python3 tools/sensitivity_settlements_local.py
"""
from __future__ import annotations

import itertools
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import server_local_v4 as S  # noqa: E402
from isles_core import GamePhase, Resource, TerrainType  # noqa: E402
from play_session import make_session_game  # noqa: E402

PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
PERMS = [",".join(p) for p in itertools.permutations(["c3po", "r2d2", "t800", "walle"])]

BASE_WEIGHTS = {Resource.WOOD: 1.00, Resource.CLAY: 1.00, Resource.GRAIN: 1.07,
                Resource.WOOL: 0.94, Resource.ORE: 1.12}
EARLY = {Resource.WOOD: 1.22, Resource.CLAY: 1.22, Resource.GRAIN: 1.02,
         Resource.WOOL: 0.98, Resource.ORE: 0.83}
LATE = {Resource.WOOD: 0.98, Resource.CLAY: 0.98, Resource.GRAIN: 1.12,
        Resource.WOOL: 0.98, Resource.ORE: 1.17}


def resource_base(res: Resource, turn: int) -> float:
    v = BASE_WEIGHTS.get(res, 1.0)
    if turn <= 4:
        v *= EARLY.get(res, 1.0)
    elif turn >= 20:
        v *= LATE.get(res, 1.0)
    return v


def vertex_hexes(g, vid):
    out = []
    for hid in g.map.vertices[vid].adjacent_hexes:
        tile = g.map.hexes[hid]
        if tile.terrain in (TerrainType.WASTELAND, TerrainType.RUINS) or tile.number_token <= 0:
            continue
        out.append({
            "pips": PIPS.get(int(tile.number_token), 0),
            "resource": str(tile.resource).split(".")[-1].lower(),
            "stage": int(tile.ext.get("blight_stage", 0) or 0),
        })
    return out


def suppressed_points(hexes, turn: int, k: float) -> float:
    """Сколько номинальных пунктов бота «сидит» в заражённых соседях (нейтральный взгляд)."""
    total = 0.0
    for h in hexes:
        if h["stage"] > 0:
            res = Resource(h["resource"]) if h["resource"] in {r.value for r in Resource} else Resource.WOOD
            total += k * (h["pips"] / 5.0) * resource_base(res, turn)
    return round(total, 4)


def tears_per_roll(hexes) -> float:
    return round(sum(h["pips"] / 36.0 for h in hexes if h["stage"] > 0), 4)


def replay_and_collect(seeds):
    collected = []
    for seed in seeds:
        perm = PERMS[seed % 24]
        g = make_session_game(f"LAB{seed:04d}", seed, perm.split(","))
        for bt in perm.split(","):
            S.add_bot(g, bt)
        g.start_game()
        steps = 0
        while g.phase != GamePhase.FINISHED and steps < 60000:
            steps += 1
            if not step(g, collected, seed):
                break
    return collected


def step(g, collected, seed):
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
        k = 1.0 if a.type == "setup_outpost" else 0.88
        turn = int(g.global_turn)
        align = (g.ext.get("blight", {}).get("players", {}).get(str(pid), {}) or {}).get("alignment") or "neutral"
        is_setup = a.type == "setup_outpost"
        is_second = is_setup and getattr(g, "setup_index", 0) >= len(g.players)
        cand_info = []
        for act in legal:
            if act.type != a.type:
                continue
            vid = int(act.p.get("vertex_id", -1))
            if is_second and getattr(brain, "_setup_anchor_vertex", None) is not None:
                try:
                    nominal = float(brain._setup_second_vertex_score(brain._setup_anchor_vertex, vid))
                except Exception:
                    nominal = None
            else:
                try:
                    nominal = float(brain._vertex_score(vid, 2 if is_second else 1) if is_setup
                                    else brain._score_action(act, getattr(brain, "current_goal", None), legal))
                except Exception:
                    nominal = None
            hexes = vertex_hexes(g, vid)
            cand_info.append({
                "vertex_id": vid, "nominal": nominal, "hexes": hexes,
                "suppressed_points_neutral": suppressed_points(hexes, turn, k),
                "tears_per_roll": tears_per_roll(hexes),
            })
        if any(c["hexes"] and any(h["stage"] > 0 for h in c["hexes"]) for c in cand_info):
            collected.append({
                "seed": seed, "turn": turn, "pid": int(pid),
                "phase": "setup" if is_setup else "main",
                "k": k, "player_alignment": align,
                "chosen_vertex": int(a.p.get("vertex_id", -1)),
                "candidates": cand_info,
            })

    r = g.apply_action(pid, a)
    if not r.success and g.phase == GamePhase.MAIN:
        g.end_turn(pid)
    return True


def main() -> None:
    seeds = list(range(40001, 40013))
    collected = replay_and_collect(seeds)
    print(f"решений о поселении, затрагивающих заражённые гексы (в любом кандидате): {len(collected)}")

    # Применимые к перевороту: игрок не культист и ВЫБРАННАЯ вершина заражена
    applicable = []
    for d in collected:
        if d["player_alignment"] == "cult":
            continue
        ch = next((c for c in d["candidates"] if c["vertex_id"] == d["chosen_vertex"]), None)
        if ch and any(h["stage"] > 0 for h in ch["hexes"]):
            applicable.append(d)
    print(f"из них применимых (игрок не культист, выбранная вершина заражена): {len(applicable)} "
          f"(расстановка {sum(1 for d in applicable if d['phase']=='setup')}, "
          f"экспансия {sum(1 for d in applicable if d['phase']=='main')})")

    flips = 0
    flip_rows = []
    for d in applicable:
        turn = d["turn"]; k = d["k"]
        ch = next(c for c in d["candidates"] if c["vertex_id"] == d["chosen_vertex"])
        adj_ch = (ch["nominal"] or 0.0) - ch["suppressed_points_neutral"]
        best = None
        for c in d["candidates"]:
            if c["vertex_id"] == d["chosen_vertex"] or c["nominal"] is None:
                continue
            adj_c = c["nominal"] - c["suppressed_points_neutral"]
            if adj_c > adj_ch + 1e-9 and (best is None or adj_c > best[1]):
                best = (c, adj_c)
        if best:
            flips += 1
            flip_rows.append({
                "seed": d["seed"], "turn": d["turn"], "phase": d["phase"],
                "chosen_vertex": d["chosen_vertex"],
                "chosen_nominal": ch["nominal"], "chosen_suppressed": ch["suppressed_points_neutral"],
                "suppressed_resources": [f"{h['pips']}×{h['resource']}ст{h['stage']}" for h in ch["hexes"] if h["stage"] > 0],
                "swap_vertex": best[0]["vertex_id"], "swap_nominal": best[0]["nominal"],
                "swap_suppressed": best[0]["suppressed_points_neutral"],
            })
    print(f"переворотов при ЛОКАЛЬНОЙ цене (по ресурсу и ходу, в шкале самого бота): {flips} из {len(applicable)}")
    print("  по фазам:", dict(Counter(r["phase"] for r in flip_rows)))
    for r in flip_rows[:8]:
        print(f"  seed {r['seed']} T{r['turn']} [{r['phase']}]: vid {r['chosen_vertex']} "
              f"(номинал {r['chosen_nominal']}, подавлено {r['chosen_suppressed']} = {r['suppressed_resources']}) "
              f"→ vid {r['swap_vertex']} (номинал {r['swap_nominal']}, подавлено {r['swap_suppressed']})")

    # Три перспективы для 12 стартовых размещений
    print("\n=== Стартовые размещения (расстановка): три перспективы ===")
    setup_rows = [d for d in collected if d["phase"] == "setup"]
    for d in setup_rows:
        ch = next(c for c in d["candidates"] if c["vertex_id"] == d["chosen_vertex"])
        inf = [h for h in ch["hexes"] if h["stage"] > 0]
        if not inf:
            continue
        print(f"seed {d['seed']} pid{d['pid']} vid {d['chosen_vertex']}: подавлено(нейтрал) "
              f"{ch['suppressed_points_neutral']} | слёз-потенциал(культ) {ch['tears_per_roll']}/бросок "
              f"| восстановимо очисткой(орден): та же сумма за 1 зерно + щит")

    out = Path("sessions/sensitivity_settlements_local.json")
    out.write_text(json.dumps({"collected": collected, "flip_rows": flip_rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nсохранено:", out)


if __name__ == "__main__":
    main()
