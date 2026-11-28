from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import tp41_score_precision_audit as precision


def ev(seq: int, kind: str, **kwargs):
    return {"seq": seq, "kind": kind, **kwargs}


def decision_cycle(start: int, player: int, turn: int, chosen_hex: int,
                   candidates: list[dict], chosen_action: str = "corrupt_hex"):
    events = [
        ev(start, "corrupt_decision", turn=turn, player=player,
           candidates=candidates, alignment="neutral"),
        ev(start + 1, "corrupt_readonly", state_unchanged=True,
           rng_unchanged=True),
    ]
    if chosen_action == "corrupt_hex":
        chosen = next(c for c in candidates if c["hex_id"] == chosen_hex)
        events.append(ev(start + 2, "corrupt_choice", turn=turn,
                         player=player, hex_id=chosen_hex,
                         impact=chosen["impact"], mod_score=chosen["mod_score"]))
    events.append(ev(start + 3 if chosen_action == "corrupt_hex" else start + 2,
                     "bot_decision", turn=turn, player_id=player,
                     action=chosen_action,
                     params={"hex_id": chosen_hex} if chosen_action == "corrupt_hex" else {},
                     applied_ok=True))
    return events


def cand(hex_id: int, score: float, impact: float):
    return {"hex_id": hex_id, "mod_score": score, "impact": impact}


def test_singleton_is_not_a_tie_and_same_turn_decisions_are_unique(tmp_path: Path):
    root = tmp_path / "tp41"
    a0 = root / "A0" / "one"
    a1 = root / "A1" / "one"
    a0.mkdir(parents=True)
    a1.mkdir(parents=True)

    events_a0 = (
        decision_cycle(1, 0, 4, 1, [cand(1, 3.5, 4), cand(2, 3.5, 8)])
        + decision_cycle(5, 0, 4, 3, [cand(3, 2.0, 5)])
    )
    events_a1 = decision_cycle(
        1, 0, 4, 10, [cand(10, 3.2, 4), cand(11, 3.2, 8)]
    )
    (a0 / "trace.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events_a0), encoding="utf-8"
    )
    (a1 / "trace.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events_a1), encoding="utf-8"
    )

    for arm, path in (("A0", a0), ("A1", a1)):
        row = {
            "seed": 100,
            "perm": 0,
            "status": "completed",
            "out_dir": str(path),
            "seats": {"0": "c3po"},
        }
        (root / f"manifest_{arm}.jsonl").write_text(
            json.dumps(row) + "\n", encoding="utf-8"
        )

    result = precision.audit(root)

    a0_summary = result["by_arm"]["A0"]
    a0_cap = a0_summary["buckets"]["max==3.5 (cap output)"]
    a0_below = a0_summary["buckets"]["max<3.5"]

    assert a0_summary["selected_corrupt_actions"] == 2
    assert a0_cap["tie_groups"] == 1
    assert a0_cap["different_impact_tie_groups"] == 1
    assert a0_cap["correctable_if_recorded_score_tie_is_exact"] == 1
    assert a0_below["unique_maximum_decisions"] == 1
    assert a0_below["tie_groups"] == 0


def test_overflow_ties_are_flagged_precision_uncertain(tmp_path: Path):
    root = tmp_path / "tp41"
    for arm in ("A0", "A1"):
        (root / arm / "one").mkdir(parents=True)
    a0 = root / "A0" / "one"
    a1 = root / "A1" / "one"

    events_a0 = decision_cycle(
        1, 0, 2, 1, [cand(1, 3.0, 4)]
    )
    events_a1 = decision_cycle(
        1, 0, 2, 10, [cand(10, 3.500001, 4), cand(11, 3.500001, 9)]
    )
    for folder, events in ((a0, events_a0), (a1, events_a1)):
        (folder / "trace.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )

    for arm, folder in (("A0", a0), ("A1", a1)):
        row = {
            "seed": 101,
            "perm": 0,
            "status": "completed",
            "out_dir": str(folder),
            "seats": {"0": "r2d2"},
        }
        (root / f"manifest_{arm}.jsonl").write_text(
            json.dumps(row) + "\n", encoding="utf-8"
        )

    result = precision.audit(root)
    a1_overflow = result["by_arm"]["A1"]["buckets"]["max>3.5 (overflow output)"]

    assert a1_overflow["tie_groups"] == 1
    assert a1_overflow["precision_uncertain_tie_groups"] == 1
    assert a1_overflow["different_impact_tie_groups"] == 1
    assert a1_overflow["correctable_if_recorded_score_tie_is_exact"] == 1
    assert len(result["correctable_cases"]) == 1
    assert result["correctable_cases"][0]["decision_seq"] == 1
