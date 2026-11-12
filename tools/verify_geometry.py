#!/usr/bin/env python3
"""verify_geometry — сверка реконструкции смежности в view_turn.py с движком (п.1 ТЗ).

Движок — авторитет: HexGame строит карту сам (tile.vertex_ids,
vertex.adjacent_hexes, edge.vertices). view_turn.hex_adjacency_from_geometry
реконструирует вершина→гексы из публичных координат состояния.

Сверяем на N сидах, для КАЖДОГО гекса и вершины:
  * hex→вершины: engine tile.vertex_ids == реконструкция
  * вершина→гексы: engine vertex.adjacent_hexes == реконструкция
  * edge→вершины: engine edge.vertices == state["edges"]

  python3 tools/verify_geometry.py --seeds 40
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import server_local_v4 as S  # noqa: E402
from isles_core import GamePhase  # noqa: E402
import view_turn  # noqa: E402


def state_hexes(g) -> dict[int, dict]:
    """Сериализованные гексы (как их отдаёт state_for — наш «публичный» источник)."""
    state = S.state_for(g, 0)
    return state


def check_seed(seed: int) -> list[str]:
    problems: list[str] = []
    cfg = {
        "points_to_win": 12, "fog_of_war": True, "fast_start": False,
        "friendly_raider": True, "blight": True, "blight_mode": "factions",
        "cult_visibility": "open", "blight_spread_chance": 0.40,
        "routes": False, "bot_difficulty": "skynet",
    }
    g = S.make_game(f"GEO{seed}", cfg, seed=seed)
    for bt in ("c3po", "r2d2", "t800"):
        S.add_bot(g, bt)
    g.start_game()
    # доиграем несколько ходов (структуры владельцев не влияют на геометрию, но
    # проверим и в середине партии)
    for _ in range(60):
        if g.phase in (GamePhase.LOBBY, GamePhase.FINISHED):
            break
        legal = g.enumerate_legal_actions(g.current_player_id)
        if not legal:
            break
        brain = g.bot_brains.get(g.current_player_id)
        a = brain.choose_action(g.current_player_id, legal) if brain else legal[0]
        if not any(x is a for x in legal):
            a = next((x for x in legal if x.type == a.type), legal[0])
        r = g.apply_action(g.current_player_id, a)
        if not r.success and g.phase == GamePhase.MAIN:
            g.end_turn(g.current_player_id)

    state = state_hexes(g)
    hexes = {h["hex_id"]: h for h in state["hexes"]}
    vertices = {v["vertex_id"]: v for v in state["vertices"]}
    edges = {e["edge_id"]: e for e in state["edges"]}

    recon_v2h = view_turn.hex_adjacency_from_geometry(hexes, vertices)  # вершина→[гексы]
    recon_h2v: dict[int, set[int]] = {}
    for vid, hlist in recon_v2h.items():
        for hid in hlist:
            recon_h2v.setdefault(hid, set()).add(vid)

    # Движок (state_for) скрывает вершины, не примыкающие ни к одному видимому
    # гексу/постройке — это корректное ограничение видимости, а не ошибка
    # реконструкции. Поэтому ожидание = углы движка ∩ вершины из state.
    state_vids = set(vertices)
    hidden_vertex_total = len(g.map.vertices) - len(state_vids)

    # 1) hex → вершины
    for hid, tile in g.map.hexes.items():
        engine = set(tile.vertex_ids)
        hidden = engine - state_vids
        expected = engine & state_vids
        rec = set(recon_h2v.get(hid, set()))
        if expected != rec:
            problems.append(
                f"seed {seed} hex {hid}: ожидание {sorted(expected)} != recon {sorted(rec)}"
                + (f" (в тумане скрыты вершины {sorted(hidden)})" if hidden else "")
            )
    # 2) вершина → гексы (только для вершин, присутствующих в state)
    for vid, vert in g.map.vertices.items():
        if vid not in state_vids:
            continue
        engine = set(vert.adjacent_hexes)
        rec = set(recon_v2h.get(vid, []))
        if engine != rec:
            problems.append(
                f"seed {seed} vertex {vid}: engine hexes {sorted(engine)} != recon {sorted(rec)}"
            )
    # 3) рёбра (state тоже скрывает рёбра невидимых областей — сравниваем
    # только присутствующие)
    for eid, edge in g.map.edges.items():
        st = edges.get(eid)
        if st is None:
            continue
        if list(edge.vertices) != list(st["vertices"]):
            problems.append(f"seed {seed} edge {eid}: engine {edge.vertices} != state {st['vertices']}")
    # встречные проверки: каждое ребро из state должно совпадать с движком
    for eid, st in edges.items():
        edge = g.map.edges.get(eid)
        if edge is None:
            problems.append(f"seed {seed} edge {eid}: есть в state, нет в движке")
        elif list(edge.vertices) != list(st["vertices"]):
            problems.append(f"seed {seed} edge {eid} (state): {edge.vertices} != {st['vertices']}")
    # hid = уникальные скрытые вершины за сид; eng_v = все движковые вершины за сид
    return problems, len(g.map.vertices) - len(state_vids), len(g.map.vertices)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--seed-start", type=int, default=9000)
    args = ap.parse_args()

    all_problems: list[str] = []
    hidden_total = 0
    engine_vertices_total = 0
    for i in range(args.seeds):
        seed = args.seed_start + i
        probs, hid, eng_v = check_seed(seed)
        hidden_total += hid
        engine_vertices_total += eng_v
        if probs:
            all_problems.extend(probs[:5])
            print(f"seed {seed}: {len(probs)} расхождений (первые 5 напечатаны)")
        else:
            print(f"seed {seed}: OK")

    if all_problems:
        print(f"\nИТОГ: {len(all_problems)} расхождений на {args.seeds} сидах")
        for p in all_problems[:20]:
            print("  ", p)
        sys.exit(1)
    # eng_v теперь = все движковые вершины за сид; hid = скрытые за сид
    visible_total = engine_vertices_total - hidden_total
    print(
        f"\nИТОГ: реконструкция совпадает с движком на {args.seeds} сидах"
        f" | движковых вершин всего: {engine_vertices_total},"
        f" скрыто туманом: {hidden_total} ({100.0 * hidden_total / max(1, engine_vertices_total):.0f}%),"
        f" видимо: {visible_total}"
    )


if __name__ == "__main__":
    main()
