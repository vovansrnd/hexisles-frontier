from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


event_join = load_module("tp41_event_join_under_test", "tp41_event_join.py")
legacy_analyzer = load_module("analyze_tp41_under_test", "analyze_tp41.py")


def candidate(hex_id: int, score: float, impact: float,
              enemy_loss: float = 2, leader_share: float = 0.5,
              pips: int = 2):
    return {
        "hex_id": hex_id,
        "mod_score": score,
        "impact": impact,
        "expected_production_loss": enemy_loss,
        "leader_loss_share": leader_share,
        "pips": pips,
    }


def event(seq: int, kind: str, **fields):
    return {"seq": seq, "kind": kind, **fields}


def make_choice_cycle(start_seq: int, player: int, turn: int,
                      hex_id: int, candidates: list[dict]):
    return [
        event(start_seq, "corrupt_decision", player=player, turn=turn,
              candidates=candidates, alignment="neutral"),
        event(start_seq + 1, "corrupt_readonly", state_unchanged=True,
              rng_unchanged=True),
        event(start_seq + 2, "corrupt_choice", player=player, turn=turn,
              hex_id=hex_id, impact=next(
                  (c["impact"] for c in candidates if c["hex_id"] == hex_id), None)),
        event(start_seq + 3, "bot_decision", player_id=player, turn=turn,
              action="corrupt_hex", params={"hex_id": hex_id}, applied_ok=True),
    ]


def test_seq_join_distinguishes_multiple_decisions_same_turn_and_player():
    candidates_a = [candidate(10, 3.5, 4)]
    candidates_b = [candidate(20, 3.5, 6)]
    events = (
        make_choice_cycle(1, 7, 12, 10, candidates_a)
        + make_choice_cycle(5, 7, 12, 20, candidates_b)
    )
    records, qc, problems = event_join.join_trace(
        events, {"seed": 1, "perm": 0, "seats": {"7": "c3po"}}, "A0"
    )

    assert len(records) == 2
    assert [record["choice_hex"] for record in records] == [10, 20]
    assert [record["bot_decision_seq"] for record in records] == [4, 8]
    assert qc["decision_without_bot_decision"] == 0
    assert qc["choice_action_mismatch"] == 0
    assert qc["choice_hex_mismatch"] == 0
    assert qc["choice_turn_player_mismatch"] == 0
    assert problems == []


def test_legacy_analyzer_uses_decision_seq_not_turn_player_key():
    events = (
        make_choice_cycle(1, 7, 12, 10, [candidate(10, 3.5, 4)])
        + make_choice_cycle(5, 7, 12, 20, [candidate(20, 3.5, 6)])
    )
    joined, qc = legacy_analyzer.choices_by_decision_seq(events)

    assert joined[1]["hex_id"] == 10
    assert joined[5]["hex_id"] == 20
    assert qc["matched_decisions"] == 2
    assert qc["matched_choices"] == 2


def test_non_corrupt_action_is_not_counted_as_corrupt_choice():
    events = [
        event(1, "corrupt_decision", player=2, turn=3,
              candidates=[candidate(1, 3.5, 4)]),
        event(2, "corrupt_readonly", state_unchanged=True, rng_unchanged=True),
        event(3, "bot_decision", player_id=2, turn=3,
              action="end_turn", params={}, applied_ok=True),
    ]
    records, qc, problems = event_join.join_trace(
        events, {"seed": 1, "perm": 0, "seats": {"2": "r2d2"}}, "A0"
    )
    event_join.classify_corrupt_record(records[0])
    summary = event_join.aggregate(records)

    assert records[0]["classification"] == "non_corrupt_action"
    assert summary["n_corrupt_actions"] == 0
    assert summary["n_non_corrupt_actions"] == 1
    assert problems == []


def test_chosen_hex_not_in_its_own_candidates_is_reported():
    events = [
        event(1, "corrupt_decision", player=2, turn=3,
              candidates=[candidate(1, 3.5, 4)]),
        event(2, "corrupt_readonly", state_unchanged=True, rng_unchanged=True),
        event(3, "corrupt_choice", player=2, turn=3, hex_id=99),
        event(4, "bot_decision", player_id=2, turn=3,
              action="corrupt_hex", params={"hex_id": 99}, applied_ok=True),
    ]
    records, _, _ = event_join.join_trace(
        events, {"seed": 1, "perm": 0, "seats": {"2": "r2d2"}}, "A1"
    )
    event_join.classify_corrupt_record(records[0])
    summary = event_join.aggregate(records)

    assert records[0]["classification"] == "chosen_hex_not_in_candidates"
    assert summary["categories"]["chosen_hex_not_in_candidates"] == 1
    assert summary["categories_sum"] == 1


def test_what_if_categories_are_disjoint_and_only_actual_score_ties_count():
    # A singleton maximum is not a score tie.
    unique = {
        "action": "corrupt_hex", "choice_hex": 1, "applied_ok": True,
        "candidates": [candidate(1, 3.5, 4)],
    }
    equal_tie = {
        "action": "corrupt_hex", "choice_hex": 2, "applied_ok": True,
        "candidates": [candidate(2, 3.5, 4), candidate(3, 3.5, 4)],
    }
    chosen_max_tie = {
        "action": "corrupt_hex", "choice_hex": 5, "applied_ok": True,
        "candidates": [candidate(4, 3.5, 4), candidate(5, 3.5, 6)],
    }
    correctable_tie = {
        "action": "corrupt_hex", "choice_hex": 6, "applied_ok": True,
        "candidates": [
            candidate(6, 3.5, 4, enemy_loss=4, leader_share=0.25),
            candidate(7, 3.5, 6, enemy_loss=3, leader_share=1.0),
        ],
    }
    non_corrupt = {"action": "end_turn", "choice_hex": None,
                   "applied_ok": True, "candidates": []}

    records = [
        event_join.classify_corrupt_record(record)
        for record in (unique, equal_tie, chosen_max_tie, correctable_tie, non_corrupt)
    ]
    summary = event_join.aggregate(records)

    assert summary["n_corrupt_actions"] == 4
    assert summary["n_non_corrupt_actions"] == 1
    assert summary["categories_sum"] == 4
    assert summary["n_score_tie_groups"] == 3
    assert summary["n_equal_impact_ties"] == 1
    assert summary["n_different_impact_ties"] == 2
    assert summary["n_correctable_ties"] == 1
    assert summary["potential_impact_gain"]["total"] == 2
    assert summary["potential_leader_impact_gain"]["total_signed"] == 4


def test_seq_must_be_strictly_monotonic():
    events = [
        event(2, "corrupt_decision", player=1, turn=1, candidates=[]),
        event(1, "corrupt_readonly", state_unchanged=True, rng_unchanged=True),
    ]
    with pytest.raises(ValueError, match="seq"):
        event_join.join_trace(events, {"seed": 1, "perm": 0}, "A0")
