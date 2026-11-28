from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import tp42_action_parity as parity
import tp42_exact_score_audit as exact
import run_tp42 as runner


def cand(order: int, hex_id: int, full: float, impact: float):
    return {
        "candidate_order": order,
        "hex_id": hex_id,
        "mod_score": round(full, 6),
        "mod_score_full": full,
        "mod_score_hex": full.hex(),
        "impact": impact,
        "score_observed_by_chooser": True,
        "chooser_goal": {"kind": "road", "target_id": 5, "needed": {}},
    }


def decision_cycle(start: int, player: int, turn: int, chosen_hex: int,
                   candidates: list[dict]):
    events = [
        {"seq": start, "kind": "corrupt_decision", "turn": turn,
         "player": player, "alignment": "neutral", "candidates": candidates},
        {"seq": start + 1, "kind": "corrupt_readonly",
         "state_unchanged": True, "rng_unchanged": True},
        {"seq": start + 2, "kind": "corrupt_choice", "turn": turn,
         "player": player, "hex_id": chosen_hex},
        {"seq": start + 3, "kind": "bot_decision", "turn": turn,
         "player_id": player, "action": "corrupt_hex",
         "params": {"hex_id": chosen_hex}, "applied_ok": True},
    ]
    return events


def write_session(root: Path, arm: str, name: str, events: list[dict]):
    d = root / arm / name
    d.mkdir(parents=True)
    (d / "trace.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return str(d)


def write_manifest(root: Path, arm: str, rows: list[dict]):
    (root / f"manifest_{arm}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_rounded_tie_is_not_exact_tie(tmp_path: Path):
    root = tmp_path / "tp42"
    # 0.1 + 0.2 rounds to 0.3 at six decimals but is a distinct binary64 value
    a = 0.1 + 0.2          # 0.30000000000000004
    b = 0.3                # 0.29999999999999999 (binary64)
    assert round(a, 6) == round(b, 6) and a != b
    events = decision_cycle(1, 0, 4, 1, [
        cand(0, 1, a, 3), cand(1, 2, b, 8)])
    d = write_session(root, "A0", "one", events)
    write_manifest(root, "A0", [{"seed": 100, "perm": 0, "status": "completed",
                                 "out_dir": d, "seats": {"0": "c3po"}}])
    write_manifest(root, "A1", [])
    result = exact.audit(root)
    rec = result["by_arm"]["A0"]["mutually_exclusive_classifications"]
    assert rec == {"exact_unique_maximum": 1}
    assert result["by_arm"]["A0"]["exact_top_score_ties"] == 0
    assert result["by_arm"]["A0"]["rounded_ties_stage41_comparator"] == 1
    assert result["by_arm"]["A0"]["rounded_only_ties_not_exact"] == 1


def test_exact_tie_classes_and_correctable(tmp_path: Path):
    root = tmp_path / "tp42"
    a0 = decision_cycle(1, 0, 4, 1, [
        cand(0, 1, 3.5, 3), cand(1, 2, 3.5, 7)])
    a1 = decision_cycle(1, 0, 4, 10, [
        cand(0, 10, 2.25, 4), cand(1, 11, 2.25, 4)])
    d0 = write_session(root, "A0", "one", a0)
    d1 = write_session(root, "A1", "one", a1)
    write_manifest(root, "A0", [{"seed": 101, "perm": 0, "status": "completed",
                                 "out_dir": d0, "seats": {"0": "r2d2"}}])
    write_manifest(root, "A1", [{"seed": 101, "perm": 0, "status": "completed",
                                 "out_dir": d1, "seats": {"0": "r2d2"}}])
    result = exact.audit(root)
    a0s = result["by_arm"]["A0"]
    a1s = result["by_arm"]["A1"]
    assert a0s["mutually_exclusive_classifications"] == {
        "exact_tie_below_max_impact": 1}
    assert a0s["exact_top_score_ties"] == 1
    assert a0s["exact_tie_different_impact"] == 1
    assert a0s["exact_correctable_ties"] == 1
    case = a0s["correctable_cases"][0]
    assert case["decision_seq"] == 1 and case["chosen_impact"] == 3.0
    assert case["exact_group_max_impact"] == 7.0
    assert case["exact_group_impacts"][1]["candidate_order"] == 1
    # equal-impact exact tie: chosen is at group max (choice #1 has equal impact)
    assert a1s["mutually_exclusive_classifications"] == {
        "exact_tie_chosen_max_impact": 1}
    assert a1s["exact_tie_equal_impact"] == 1
    assert a1s["exact_tie_different_impact"] == 0
    assert a1s["exact_correctable_ties"] == 0


def test_chosen_outside_exact_group_and_hex_round_trip(tmp_path: Path):
    root = tmp_path / "tp42"
    c = [cand(0, 1, 3.5, 3), cand(1, 2, 2.0, 8)]
    events = decision_cycle(1, 0, 4, 2, c)
    # corrupt the hex representation of the first candidate
    events[0]["candidates"][0]["mod_score_hex"] = (3.499).hex()
    d = write_session(root, "A0", "one", events)
    write_manifest(root, "A0", [{"seed": 102, "perm": 0, "status": "completed",
                                 "out_dir": d, "seats": {"0": "walle"}}])
    write_manifest(root, "A1", [])
    result = exact.audit(root)
    a0s = result["by_arm"]["A0"]
    assert a0s["mutually_exclusive_classifications"] == {
        "chosen_outside_exact_top_group": 1}
    assert a0s["chosen_outside_exact_top_group"] == 1
    assert result["input_integrity"]["hex_round_trip_mismatches"] == 1


def test_action_signature_ignores_diagnostic_fields():
    base = [
        {"seq": 1, "kind": "corrupt_decision", "turn": 4, "player": 0,
         "candidates": [{"hex_id": 5, "mod_score": 1.5, "impact": 3}]},
        {"seq": 2, "kind": "bot_decision", "turn": 4, "player_id": 0,
         "action": "corrupt_hex", "params": {"hex_id": 5}, "applied_ok": True},
        {"seq": 3, "kind": "corrupt_choice", "turn": 4, "player": 0,
         "hex_id": 5},
    ]
    extra = json.loads(json.dumps(base))
    extra[0]["candidates"][0]["mod_score_full"] = 1.5
    extra[0]["candidates"][0]["mod_score_hex"] = (1.5).hex()
    extra[0]["candidates"][0]["candidate_order"] = 0
    assert parity.signature(base) == parity.signature(extra)
    reordered = json.loads(json.dumps(base))
    reordered[1]["applied_ok"] = False
    assert parity.signature(base) != parity.signature(reordered)
    hex_diff = json.loads(json.dumps(base))
    hex_diff[0]["candidates"][0]["hex_id"] = 6
    assert parity.signature(base) != parity.signature(hex_diff)



def test_actual_score_capture_uses_goal_passed_by_choose_action_and_restores_method():
    class FakeAction:
        type = "corrupt_hex"

    class FakeGoal:
        kind = "recomputed"
        target_id = 42
        score = 7.0
        needed = {}
        created_turn = 3
        locked_until_turn = 4

    class FakeBrain:
        def __init__(self):
            self.current_goal = "stale-goal"
            self.observed_goal = None

        def _score_action(self, action, goal, actions):
            self.observed_goal = goal
            return 4.25

        def choose_action(self, player_id, legal_actions):
            actual_goal = FakeGoal()
            for action in legal_actions:
                self._score_action(action, actual_goal, legal_actions)
            return legal_actions[0]

    brain = FakeBrain()
    original = brain._score_action
    action = FakeAction()
    chosen, scores, goals = runner.choose_action_with_actual_score_capture(
        brain, 0, [action]
    )

    assert chosen is action
    assert brain.observed_goal.kind == "recomputed"
    assert scores[id(action)] == 4.25
    assert goals[id(action)]["kind"] == "recomputed"
    assert goals[id(action)]["target_id"] == 42
    assert brain._score_action == original


def test_selected_corrupt_decision_rejects_unobserved_candidate_score(tmp_path: Path):
    root = tmp_path / "tp42"
    events = decision_cycle(1, 0, 4, 1, [
        cand(0, 1, 3.5, 3), cand(1, 2, 3.5, 7)])
    events[0]["candidates"][1]["score_observed_by_chooser"] = False
    d = write_session(root, "A0", "one", events)
    write_manifest(root, "A0", [{"seed": 103, "perm": 0, "status": "completed",
                                 "out_dir": d, "seats": {"0": "c3po"}}])
    write_manifest(root, "A1", [])
    try:
        exact.audit(root)
    except ValueError as exc:
        assert "without actual chooser scores" in str(exc)
    else:
        raise AssertionError("audit accepted a candidate without chooser score")
