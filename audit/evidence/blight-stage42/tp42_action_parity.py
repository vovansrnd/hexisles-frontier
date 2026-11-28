#!/usr/bin/env python3
"""tp42_action_parity — Stage 4.2.0 ЧАСТЬ 3: сравнение normalized action
signatures трасс Stage 4.1 (sessions/tp41) и replay Stage 4.2 (sessions/tp42).

Нормализованная сигнатура партии (порядок и состав, без диагностических
полей score):
  * все bot_decision: (seq, turn, player_id, action, params, applied_ok);
  * все corrupt_choice: (seq, turn, player, hex_id);
  * для каждого corrupt_decision: упорядоченный список hex_id кандидатов.

Запуск: python3 tools/tp42_action_parity.py \
    [--old sessions/tp41] [--new sessions/tp42] [--out parity.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _params_key(params) -> str:
    return json.dumps(params, sort_keys=True, default=str)


def signature(events: list[dict]) -> dict:
    bots = [(e.get("seq"), e.get("turn"), e.get("player_id"), e.get("action"),
             _params_key(e.get("params")), e.get("applied_ok"))
            for e in events if e.get("kind") == "bot_decision"]
    choices = [(e.get("seq"), e.get("turn"), e.get("player"), e.get("hex_id"))
               for e in events if e.get("kind") == "corrupt_choice"]
    cand_order = [[c.get("hex_id") for c in (e.get("candidates") or [])]
                  for e in events if e.get("kind") == "corrupt_decision"]
    return {"bot_decisions": bots, "corrupt_choices": choices,
            "candidate_hex_order": cand_order}


def load_pairs(root: Path) -> dict[tuple, Path]:
    pairs = {}
    for d in sorted(root.iterdir()):
        trace = d / "trace.jsonl"
        if not d.is_dir() or not trace.is_file():
            continue
        m = None
        for arm in ("A0", "A1"):
            prefix = f"{arm}_seed"
            if d.name.startswith(prefix):
                rest = d.name[len(prefix):]
                seed_s, perm_s = rest.split("_p")
                m = (arm, int(seed_s), int(perm_s))
                break
        if m:
            pairs[m] = trace
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", type=Path, default=Path("sessions/tp41"))
    ap.add_argument("--new", type=Path, default=Path("sessions/tp42"))
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    old = load_pairs(args.old.resolve())
    new = load_pairs(args.new.resolve())
    only_old = sorted(old.keys() - new.keys())
    only_new = sorted(new.keys() - old.keys())
    matched, mismatched = [], []
    for key in sorted(old.keys() & new.keys()):
        so = signature([json.loads(l) for l in open(old[key], encoding="utf-8") if l.strip()])
        sn = signature([json.loads(l) for l in open(new[key], encoding="utf-8") if l.strip()])
        if so == sn:
            matched.append(list(key))
        else:
            diff = {}
            for part in ("bot_decisions", "corrupt_choices", "candidate_hex_order"):
                if so[part] != sn[part]:
                    diff[part] = {"old_n": len(so[part]), "new_n": len(sn[part])}
            mismatched.append({"pair": list(key), "diff": diff})

    result = {
        "old_root": str(args.old), "new_root": str(args.new),
        "old_games": len(old), "new_games": len(new),
        "matched": len(matched), "mismatched": len(mismatched),
        "only_in_old": only_old, "only_in_new": only_new,
        "mismatch_details": mismatched[:20],
        "parity_ok": len(old) == len(new) == len(matched) and not mismatched,
    }
    print(f"old={len(old)} new={len(new)} matched={len(matched)} "
          f"mismatched={len(mismatched)} only_old={len(only_old)} "
          f"only_new={len(only_new)} parity_ok={result['parity_ok']}")
    for m in mismatched[:10]:
        print("  MISMATCH:", m["pair"], m["diff"])
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"saved: {args.out}")


if __name__ == "__main__":
    main()
