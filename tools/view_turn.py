#!/usr/bin/env python3
"""view_turn — «кибитцер» для человека-в-контуре (лаборатория v3).

Читает ТОЛЬКО turn.json (то, что движок отдаёт игроку 0, туман отфильтрован
движком) и печатает удобную сводку: расстановка, мои ресурсы/сторона/слёзы,
видимая Скверна, мои легальные действия по типам. Для setup_outpost считает
вес вершин по pip-ам видимых гексов (обычная «математика игрока»).

  python3 tools/view_turn.py [sessions/my_cult_1]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from collections import defaultdict

PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
RU = {"wood": "дер", "clay": "гли", "grain": "пшн", "wool": "шер", "ore": "руд"}
TERR = {"forest": "лес", "hills": "холм", "fields": "поля", "pasture": "паст", "mountains": "гор", "wasteland": "пуст", "ruins": "руин"}
HEX_SIZE = 100.0


def hex_adjacency_from_geometry(hexes: dict, vertices: dict) -> dict[int, list[int]]:
    """Восстановить смежность вершина→гексы по публичным координатам (туман не трогаем).

    Координаты вершин в состоянии — в масштабе 0.5 от движковых (проверено на
    карте: углы на расстоянии ровно 50 при HEX_SIZE=100).
    """
    scale = HEX_SIZE * 0.5
    adj = defaultdict(list)
    for h in hexes.values():
        cx = scale * (math.sqrt(3) * h["q"] + math.sqrt(3) / 2 * h["r"])
        cy = scale * (1.5 * h["r"])
        for vid, v in vertices.items():
            if math.hypot(v["x"] - cx, v["y"] - cy) <= scale * 1.05:
                adj[vid].append(h["hex_id"])
    return dict(adj)


def main() -> None:
    d = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    data = json.loads((d / "turn.json").read_text(encoding="utf-8"))
    s = data["state"]
    print(f"== seq {data['seq']} | phase {s['phase']} | ход {s.get('turn')} | текущий {s.get('current_player')}")
    me = s["me"]
    res = me.get("resources", {})
    print("Мои ресурсы:", {RU[k]: v for k, v in res.items() if v}, "| ОП:", me.get("victory_points"),
          "| хартии:", me.get("charter_count"), "| поселения:", me.get("outposts_left"), "| города:", me.get("metropolises_left"))
    bl = me.get("blight") or {}
    if bl:
        print("Моя сторона:", bl.get("alignment"), "| баланс:", bl.get("alignment_score"),
              "| слёзы:", bl.get("tears"), "| мод-ОП:", bl.get("alternate_vp"),
              "| путь слёз:", bl.get("tears_vp"), "| путь очищения:", bl.get("purify_vp"),
              "| обличение:", "использовано" if bl.get("accusation_used") else "доступно")
    for p in s.get("players", []):
        pb = p.get("blight") or {}
        if p["player_id"] != me.get("player_id") and pb:
            print(f"  {p['name']}: ОП {p.get('victory_points')} | сторона {pb.get('alignment')} "
                  f"| слёзы {pb.get('tears')} | мод-ОП {pb.get('alternate_vp')} | очисток {pb.get('purified_count')} | осквернений {pb.get('corrupt_count')}")

    hexes = {h["hex_id"]: h for h in s.get("hexes", [])}
    vertices = {v["vertex_id"]: v for v in s.get("vertices", [])}
    adjacency = hex_adjacency_from_geometry(hexes, vertices)
    corner_radius = HEX_SIZE * 0.5 * 1.05
    # видимая скверна
    infected = [(hid, h) for hid, h in hexes.items() if h.get("blight_stage", 0) > 0]
    if infected:
        print("Скверна (видимая):")
        for hid, h in sorted(infected):
            srcs = h.get("blight_stage_sources")
            print(f"  hex {hid} ({TERR.get(h.get('terrain'),'?')} {h.get('number_token')}) стадия {h.get('blight_stage')}"
                  + (f" источники {srcs}" if srcs is not None else ""))
        raider = [hid for hid, h in hexes.items() if h.get("has_raider")]
        if raider:
            print("Разбойник в hex:", raider)

    legal = data.get("legal", [])
    by_type = defaultdict(list)
    for a in legal:
        by_type[a["type"]].append(a)
    print("Легальные действия:", {t: len(v) for t, v in sorted(by_type.items())})

    if str(s["phase"]).lower().endswith("setup") and "setup_outpost" in by_type:
        scored = []
        for a in by_type["setup_outpost"]:
            vid = a["params"]["vertex_id"]
            v = vertices.get(vid)
            if not v:
                continue
            adj = adjacency.get(vid, [])
            pips = sum(PIPS.get(hexes[h].get("number_token", 0), 0) for h in adj if h in hexes and hexes[h].get("revealed"))
            terrs = {hexes[h]["terrain"] for h in adj if h in hexes and hexes[h].get("revealed")}
            fog = sum(1 for h in adj if h not in hexes or not hexes[h].get("revealed"))
            near = sum(1 for vid2 in vertices if vid2 != vid and vertices[vid2].get("structure_owner") is not None
                       and math.hypot(vertices[vid2]["x"] - v["x"], vertices[vid2]["y"] - v["y"]) <= corner_radius)
            scored.append((pips - 1.2 * near + 0.6 * fog + 0.8 * len(terrs), vid, pips, sorted(terrs), fog, near))
        scored.sort(reverse=True)
        print("Топ вершин для поселения (вес | pip | территории | туман | рядом чужих):")
        for sc, vid, pips, terrs, fog, near in scored[:8]:
            print(f"  vid {vid:3d}: вес {sc:5.1f} | pip {pips:2d} | {[TERR[t] for t in terrs]} | туман {fog} | рядом {near}")
    elif "setup_route" in by_type:
        print("setup_route варианты:", [a["params"]["edge_id"] for a in by_type["setup_route"]][:30])

    for t in ("purify_hex", "corrupt_hex", "move_raider", "choose_victim", "respond_trade", "buy_charter", "play_charter", "activate_route", "trade_bank", "propose_trade"):
        if t in by_type:
            items = by_type[t]
            if t in ("purify_hex", "corrupt_hex"):
                print(f"{t}:")
                for a in items[:14]:
                    hid = a["params"].get("hex_id")
                    h = hexes.get(hid, {})
                    extra = a["params"].get("pay_resource")
                    print(f"  hex {hid} ({TERR.get(h.get('terrain'),'?')} {h.get('number_token')}) стадия {h.get('blight_stage')}"
                          + (f" оплата {RU.get(extra, extra)}" if extra else ""))
            elif t == "move_raider":
                print("move_raider hex:", [a["params"].get("hex_id") for a in items][:20])
            elif t == "choose_victim":
                print("choose_victim:", [(a["params"].get("victim_id")) for a in items])
            else:
                print(f"{t}: {len(items)} шт., пример: {items[0]['params'] if items else {}}")
    jt = data.get("journal_tail", [])[-12:]
    if jt:
        print("Журнал (последнее):")
        for m in jt:
            print("  ", m)


if __name__ == "__main__":
    main()
