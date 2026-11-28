#!/usr/bin/env python3
"""tp41_score_precision_audit — Stage 4.1.3: чувствительность анализа к
округлению mod_score (точность 6 знаков в диагностике, EPS=1e-9 в анализаторах).

Разрез max-score групп (задание п.1-2):
  A0: max mod_score == 3.5 (потолок clamped) / < 3.5 (ниже потолка);
  A1: max mod_score > 3.5 (overflow-область) / <= 3.5 (ниже потолка);
  unknown-impact отдельно.
Для каждой группы: решений, связанных кандидатов, одинаковых/разных impact,
потенциально исправимых по impact.

Все исправимые случаи (п.3): seed, perm, turn, player, выбранный hex,
кандидаты top-score группы, записанные mod_score и impact.

precision-uncertain: tie-группы, где равенство записанных mod_score может
быть артефактом 6-значного округления (отмечаются, не реконструируются).

Запуск: python3 tools/tp41_score_precision_audit.py
"""
from __future__ import annotations

import gzip
import json
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

EV = Path("sessions/tp41/evidence")
EPS = 1e-9


def load(arm: str) -> list[dict]:
    src = EV / f"corrupt_decisions_{arm}.jsonl.gz"
    return [json.loads(l) for l in gzip.open(src, "rt", encoding="utf-8")]


def main() -> None:
    out = {"groups": {}, "correctable_cases": []}
    for arm in ("A0", "A1"):
        recs = load(arm)
        groups = Counter()
        g_detail = defaultdict(lambda: {"decisions": 0, "tied_candidates": 0,
                                        "equal_impact": 0, "different_impact": 0,
                                        "correctable": 0})
        correctable = []
        for rec in recs:
            cands = rec.get("candidates") or []
            scored = [c for c in cands if c.get("mod_score") is not None]
            if not scored:
                continue
            mx = max(c["mod_score"] for c in scored)
            group = [c for c in scored if abs(c["mod_score"] - mx) <= EPS]
            chosen_hex = rec.get("chosen_hex_id")
            ch = next((c for c in group if c.get("hex_id") == chosen_hex), None) \
                if chosen_hex is not None else None
            if ch is None:
                # выбрано не corrupt_hex или вне группы (см. Stage 4.1.1: вне группы — 0)
                continue
            impacts = [float(c.get("impact") or 0) for c in group]
            same = (max(impacts) - min(impacts) <= EPS)
            correctable_here = (not same) and (float(ch.get("impact") or 0) < max(impacts) - EPS)
            if arm == "A0":
                gkey = "max==3.5 (потолок)" if abs(mx - 3.5) <= EPS else "max<3.5 (ниже потолка)"
            else:
                gkey = "max>3.5 (overflow)" if mx > 3.5 + EPS else "max<=3.5 (ниже потолка)"
            # precision-uncertain: tie в overflow-области может быть артефактом
            # 6-значного округления (два разных исходных score > 3.5 могут
            # совпасть после round(x, 6)); A0-потолок даёт точное равенство 3.5.
            uncertain = (arm == "A1" and mx > 3.5 + EPS and not same)
            d = g_detail[gkey]
            d["decisions"] += 1
            d["tied_candidates"] += len(group)
            if same:
                d["equal_impact"] += 1
            else:
                d["different_impact"] += 1
                if uncertain:
                    d["precision_uncertain"] += 1
                if correctable_here:
                    d["correctable"] += 1
                    correctable.append({
                        "arm": arm, "seed": rec["seed"], "perm": rec["perm"],
                        "turn": rec.get("turn"), "player": rec.get("player_id"),
                        "chosen_hex": chosen_hex,
                        "chosen_impact": float(ch.get("impact") or 0),
                        "group_max_impact": max(impacts),
                        "gain": round(max(impacts) - float(ch.get("impact") or 0), 4),
                        "group": [{"hex_id": c.get("hex_id"),
                                   "mod_score": c.get("mod_score"),
                                   "impact": c.get("impact")} for c in group],
                        "precision_uncertain": uncertain,
                    })
            groups[gkey] += 1
        print(f"\n=== {arm}: max-score группы ===")
        for k, n in sorted(groups.items()):
            d = g_detail[k]
            print(f"  {k}: решений {n} | связанных кандидатов {d['tied_candidates']} | "
                  f"одинаковый impact {d['equal_impact']} | разный {d['different_impact']} | "
                  f"исправимо {d['correctable']}"
                  + (f" | precision-uncertain {d.get('precision_uncertain', 0)}" if d.get("precision_uncertain") else ""))
        out["groups"][arm] = {k: dict(d) for k, d in g_detail.items()}
        out["correctable_cases"].extend(correctable)

    print("\n=== Все исправимые случаи (A0 и A1) ===")
    for c in out["correctable_cases"]:
        print(f"  {c['arm']} seed{c['seed']} p{c['perm']} T{c['turn']} pid{c['player']}: "
              f"hex{c['chosen_hex']} impact={c['chosen_impact']} < max {c['group_max_impact']} "
              f"(прирост {c['gain']}){', precision-uncertain' if c['precision_uncertain'] else ''}")

    # сводка для финального отчёта
    a0_cap = out["groups"]["A0"].get("max==3.5 (потолок)",
                                     {"decisions": 0, "correctable": 0})
    a0_below = out["groups"]["A0"].get("max<3.5 (ниже потолка)",
                                       {"decisions": 0, "correctable": 0})
    a1_over = out["groups"]["A1"].get("max>3.5 (overflow)",
                                      {"decisions": 0, "correctable": 0, "precision_uncertain": 0})
    a1_below = out["groups"]["A1"].get("max<=3.5 (ниже потолка)",
                                       {"decisions": 0, "correctable": 0})
    print("\n=== Сводка для Stage 4.1.3 ===")
    print(f"A0 cap-induced (max==3.5): решений {a0_cap['decisions']}, "
          f"исправимо {a0_cap['correctable']}")
    print(f"A0 below-cap (max<3.5): решений {a0_below['decisions']}, "
          f"исправимо {a0_below['correctable']}")
    print(f"A1 overflow (max>3.5): решений {a1_over['decisions']}, "
          f"исправимо {a1_over['correctable']}, precision-uncertain ties "
          f"{a1_over.get('precision_uncertain', 0)}")
    print(f"A1 below-cap: решений {a1_below['decisions']}, исправимо {a1_below['correctable']}")
    json.dump(out, open(EV / "score_precision_audit.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\nсохранено: {EV / 'score_precision_audit.json'}")


if __name__ == "__main__":
    main()
