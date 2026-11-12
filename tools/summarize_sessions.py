#!/usr/bin/env python3
"""Сводка по сессиям play_session (sessions/*/summary.json).

  python3 tools/summarize_sessions.py "bvb_s*"      # ботовая серия
  python3 tools/summarize_sessions.py "my*"         # мои партии
"""
from __future__ import annotations

import glob
import json
import re
import statistics as st
import sys
from collections import Counter, defaultdict

pattern = sys.argv[1] if len(sys.argv) > 1 else "*"
files = sorted(glob.glob(f"sessions/{pattern}/summary.json"))
if not files:
    print("нет сессий по шаблону", pattern)
    sys.exit(1)

wins: Counter = Counter()
vp: dict[str, list] = defaultdict(list)
modvp: dict[str, list] = defaultdict(list)
tears_v: dict[str, list] = defaultdict(list)
purify_v: dict[str, list] = defaultdict(list)
corrupt_v: dict[str, list] = defaultdict(list)
turns: list[int] = []
align: Counter = Counter()
top_act: dict[str, Counter] = defaultdict(Counter)
mod_actions: Counter = Counter()

for f in files:
    s = json.load(open(f))
    turns.append(s["turns"])
    bf = s["blight_final_internal"]["players"]
    for p in s["players"]:
        name = p["name"].replace("🤖 ", "")
        vp[name].append(p["vp"])
        own = bf.get(str(p["player_id"])) or {}
        modvp[name].append(own.get("alternate_vp") or 0)
        tears_v[name].append(own.get("tears") or 0)
        purify_v[name].append(own.get("purified_count") or 0)
        corrupt_v[name].append(own.get("corrupt_count") or 0)
        align[f"{name}→{own.get('alignment')}"] += 1
        for atype, cnt in (p.get("actions") or {}).items():
            top_act[name][atype] += cnt
            if atype in ("corrupt_hex", "purify_hex", "accuse_cultist"):
                mod_actions[f"{name}|{atype}"] += cnt
        if s["winner_id"] == p["player_id"]:
            wins[name] += 1

print(f"партий: {len(files)} | ходов медиана {st.median(turns)}, мин/макс {min(turns)}/{max(turns)}")
print("победы:", dict(wins.most_common()))
for n in sorted(vp):
    acts = ", ".join(f"{a}:{c}" for a, c in top_act[n].most_common(5))
    print(f"  {n:10s} avgVP {st.mean(vp[n]):5.2f} | avg мод-ОП {st.mean(modvp[n]):4.2f} "
          f"| слёз ср. {st.mean(tears_v[n]):5.1f} | очисток ср. {st.mean(purify_v[n]):4.1f} "
          f"| осквернений ср. {st.mean(corrupt_v[n]):4.1f}")
    print(f"             действия: {acts}")
print("итоговые стороны:", dict(align))
if mod_actions:
    print("модовые действия:", dict(mod_actions))
