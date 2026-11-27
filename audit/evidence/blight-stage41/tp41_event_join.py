#!/usr/bin/env python3
"""Stage 4.1.1 forensic event join and offline what-if analysis.

Reads existing sessions/tp41/manifest_{A0,A1}.jsonl and per-game trace.jsonl.
No games are launched. Event association is positional in the trace's strictly
monotonic seq order: corrupt_decision -> corrupt_readonly -> optional
corrupt_choice -> bot_decision. corrupt_choice has no action field; the action
and hex_id are checked against the matching bot_decision.

Run:
  python3 audit/evidence/blight-stage41/tp41_event_join.py \
      --root sessions/tp41 --out-dir sessions/tp41
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics as st
from collections import Counter
from pathlib import Path
from typing import Any

ARMS = ("A0", "A1")
ARCHETYPES = ("c3po", "r2d2", "t800", "walle")
EPS = 1e-9
BOOTSTRAP_REPS = 10_000
BOOTSTRAP_SEED = 0x41
BUILD_ACTIONS = ("build_route", "build_outpost", "build_metropolis")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def same_id(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def leader_loss(candidate: dict[str, Any]) -> int | None:
    enemy = number(candidate.get("expected_production_loss"))
    share = number(candidate.get("leader_loss_share"))
    if enemy is None or share is None:
        return None
    return int(round(enemy * share))


def candidate_leader_impact(candidate: dict[str, Any]) -> float | None:
    loss = leader_loss(candidate)
    pips = number(candidate.get("pips"))
    if loss is None or pips is None:
        return None
    return loss * pips


def close_pending(pending: dict[str, Any], bot: dict[str, Any],
                  row: dict[str, Any], arm: str, qc: Counter,
                  problems: list[dict[str, Any]]) -> dict[str, Any]:
    decision = pending["decision"]
    choice = pending["choice"]
    params = bot.get("params") or {}
    action = bot.get("action")
    player = decision.get("player")
    turn = decision.get("turn")

    if pending["readonly"] is None:
        qc["decision_without_readonly"] += 1

    bot_player = bot.get("player_id", bot.get("player"))
    if bot_player is not None and not same_id(bot_player, player):
        qc["bot_decision_player_mismatch"] += 1
        problems.append({
            "type": "bot_decision_player_mismatch", "arm": arm,
            "seed": row.get("seed"), "perm": row.get("perm"),
            "decision_seq": decision.get("seq"), "bot_seq": bot.get("seq"),
            "decision_player": player, "bot_player": bot_player,
        })
    bot_turn = bot.get("turn")
    if bot_turn is not None and bot_turn != turn:
        qc["bot_decision_turn_mismatch"] += 1
        problems.append({
            "type": "bot_decision_turn_mismatch", "arm": arm,
            "seed": row.get("seed"), "perm": row.get("perm"),
            "decision_seq": decision.get("seq"), "bot_seq": bot.get("seq"),
            "decision_turn": turn, "bot_turn": bot_turn,
        })

    if choice is not None:
        if choice.get("turn") != turn or not same_id(choice.get("player"), player):
            qc["choice_turn_player_mismatch"] += 1
        if action != "corrupt_hex":
            qc["choice_action_mismatch"] += 1
        if params.get("hex_id") != choice.get("hex_id"):
            qc["choice_hex_mismatch"] += 1
    elif action == "corrupt_hex":
        qc["corrupt_action_without_choice"] += 1

    applied = bot.get("applied_ok")
    if not isinstance(applied, bool):
        qc["bot_decision_without_applied_result"] += 1

    seats = row.get("seats") or {}
    archetype = seats.get(str(player), decision.get("archetype", "?"))
    if archetype not in ARCHETYPES:
        archetype = str(archetype or "?").lower()

    return {
        "arm": arm,
        "seed": row.get("seed"),
        "perm": row.get("perm"),
        "archetype": archetype,
        "turn": turn,
        "player": player,
        "decision_seq": decision.get("seq"),
        "readonly_seq": (pending["readonly"] or {}).get("seq"),
        "choice_seq": (choice or {}).get("seq"),
        "bot_decision_seq": bot.get("seq"),
        "choice_hex": (choice or {}).get("hex_id"),
        "action": action,
        "action_hex": params.get("hex_id"),
        "applied_ok": applied,
        "candidates": decision.get("candidates") or [],
        "alignment": decision.get("alignment"),
    }


def join_trace(events: list[dict[str, Any]], row: dict[str, Any], arm: str
               ) -> tuple[list[dict[str, Any]], Counter, list[dict[str, Any]]]:
    qc: Counter = Counter()
    problems: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []

    seqs = [event.get("seq") for event in events]
    seq_valid = (
        all(isinstance(seq, int) and not isinstance(seq, bool) for seq in seqs)
        and all(seqs[index] < seqs[index + 1]
                for index in range(len(seqs) - 1))
    )
    qc["trace_seq_monotonic"] += int(seq_valid)
    if not seq_valid:
        problems.append({
            "type": "trace_seq_not_strictly_monotonic",
            "arm": arm, "seed": row.get("seed"), "perm": row.get("perm"),
            "first_bad_pairs": [
                [seqs[i], seqs[i + 1]]
                for i in range(len(seqs) - 1)
                if not isinstance(seqs[i], int)
                or not isinstance(seqs[i + 1], int)
                or seqs[i] >= seqs[i + 1]
            ][:10],
        })
        raise ValueError(
            f"{arm} seed={row.get('seed')} perm={row.get('perm')}: "
            "trace seq is missing, non-integer, duplicated, or non-monotonic"
        )

    pending: dict[str, Any] | None = None
    seen_choice_seq: set[int] = set()
    seen_bot_seq: set[int] = set()

    for event in events:
        kind = event.get("kind")
        if kind == "corrupt_decision":
            if pending is not None:
                qc["decision_without_bot_decision"] += 1
                problems.append({
                    "type": "decision_without_bot_decision", "arm": arm,
                    "seed": row.get("seed"), "perm": row.get("perm"),
                    "decision_seq": pending["decision"].get("seq"),
                    "next_decision_seq": event.get("seq"),
                })
            pending = {"decision": event, "readonly": None, "choice": None}

        elif kind == "corrupt_readonly":
            if pending is None:
                qc["readonly_without_decision"] += 1
            elif pending["readonly"] is not None:
                qc["duplicate_readonly_for_decision"] += 1
            else:
                pending["readonly"] = event

        elif kind == "corrupt_choice":
            if pending is None:
                qc["choice_without_decision"] += 1
                problems.append({
                    "type": "choice_without_decision", "arm": arm,
                    "seed": row.get("seed"), "perm": row.get("perm"),
                    "choice_seq": event.get("seq"), "hex_id": event.get("hex_id"),
                })
            elif pending["choice"] is not None:
                qc["duplicate_choice_for_decision"] += 1
            else:
                pending["choice"] = event
                choice_seq = event.get("seq")
                if choice_seq in seen_choice_seq:
                    qc["choice_reused"] += 1
                seen_choice_seq.add(choice_seq)

        elif kind == "bot_decision" and pending is not None:
            bot_seq = event.get("seq")
            if bot_seq in seen_bot_seq:
                qc["bot_decision_reused"] += 1
            seen_bot_seq.add(bot_seq)
            records.append(close_pending(pending, event, row, arm, qc, problems))
            pending = None

    if pending is not None:
        qc["decision_without_bot_decision"] += 1
        problems.append({
            "type": "unclosed_decision_at_eof", "arm": arm,
            "seed": row.get("seed"), "perm": row.get("perm"),
            "decision_seq": pending["decision"].get("seq"),
        })

    qc["corrupt_decisions"] += sum(
        1 for event in events if event.get("kind") == "corrupt_decision"
    )
    qc["joined_decisions"] += len(records)
    qc["corrupt_choice_events"] += sum(
        1 for event in events if event.get("kind") == "corrupt_choice"
    )
    return records, qc, problems


def score_group(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = [(candidate, number(candidate.get("mod_score")))
              for candidate in candidates]
    scored = [(candidate, score) for candidate, score in scored if score is not None]
    if not scored:
        return []
    maximum = max(score for _, score in scored)
    return [candidate for candidate, score in scored if abs(score - maximum) <= EPS]


def classify_corrupt_record(record: dict[str, Any]) -> dict[str, Any]:
    candidates = record["candidates"]
    choice_hex = record["choice_hex"]
    group = score_group(candidates)
    by_hex = [candidate for candidate in candidates
              if candidate.get("hex_id") == choice_hex]
    selected = by_hex[0] if len(by_hex) == 1 else None
    group_hexes = {candidate.get("hex_id") for candidate in group}
    chosen_in_group = selected is not None and selected.get("hex_id") in group_hexes

    record["max_score_group_size"] = len(group)
    record["chosen_candidate_found"] = selected is not None
    record["chosen_in_max_score_group"] = chosen_in_group
    record["chosen_candidate"] = selected

    if record["action"] != "corrupt_hex":
        record["classification"] = "non_corrupt_action"
        return record
    if record["choice_hex"] is None:
        record["classification"] = "missing_corrupt_choice"
        return record
    if selected is None:
        record["classification"] = "chosen_hex_not_in_candidates"
        return record
    if not group:
        record["classification"] = "no_scored_candidates"
        return record
    if not chosen_in_group:
        record["classification"] = "chosen_not_in_max_score_group"
        return record
    if len(group) == 1:
        record["classification"] = "unique_score_maximum"
        return record

    impacts = [number(candidate.get("impact")) for candidate in group]
    if any(value is None for value in impacts):
        record["classification"] = "tied_impact_unknown"
        return record

    max_impact = max(value for value in impacts if value is not None)
    min_impact = min(value for value in impacts if value is not None)
    chosen_impact = number(selected.get("impact"))
    record["group_max_impact"] = max_impact
    record["chosen_impact"] = chosen_impact
    if max_impact - min_impact <= EPS:
        record["classification"] = "tied_equal_impact"
        return record
    if chosen_impact is None:
        record["classification"] = "tied_impact_unknown"
        return record
    if chosen_impact >= max_impact - EPS:
        record["classification"] = "tied_different_impact_chosen_max"
        return record

    record["classification"] = "tied_different_impact_correctable"
    record["potential_impact_gain"] = max_impact - chosen_impact

    selected_leader_impact = candidate_leader_impact(selected)
    max_impact_candidates = [
        candidate for candidate in group
        if number(candidate.get("impact")) is not None
        and number(candidate.get("impact")) >= max_impact - EPS
    ]
    top_leader_values = [
        value for value in (candidate_leader_impact(candidate)
                            for candidate in max_impact_candidates)
        if value is not None
    ]
    if selected_leader_impact is not None and top_leader_values:
        # Upper bound among the candidates that achieve maximum impact.
        record["potential_leader_impact_gain"] = (
            max(top_leader_values) - selected_leader_impact
        )
    else:
        record["potential_leader_impact_gain"] = None
    return record


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    corrupt = [record for record in records if record["action"] == "corrupt_hex"]
    categories = Counter(record["classification"] for record in corrupt)
    applied = Counter()
    for record in records:
        status = ("applied" if record["applied_ok"] is True else
                  "rejected" if record["applied_ok"] is False else "unknown")
        applied[f'{record["action"] or "unknown"}:{status}'] += 1

    fixable = [
        record for record in corrupt
        if record["classification"] == "tied_different_impact_correctable"
    ]
    impact_gains = [record["potential_impact_gain"] for record in fixable]
    leader_gains = [
        record["potential_leader_impact_gain"] for record in fixable
        if record["potential_leader_impact_gain"] is not None
    ]
    valid_max = [
        record for record in corrupt
        if record["classification"] in {
            "unique_score_maximum", "tied_equal_impact",
            "tied_different_impact_chosen_max",
            "tied_different_impact_correctable",
        }
    ]
    tie_equal = categories["tied_equal_impact"]
    tie_different = (
        categories["tied_different_impact_chosen_max"]
        + categories["tied_different_impact_correctable"]
    )
    tie_unknown = categories["tied_impact_unknown"]
    score_ties = tie_equal + tie_different + tie_unknown

    # Primary classes are mutually exclusive and must partition all selected
    # corrupt actions; subcounts are deliberately not added to this total.
    classified_total = sum(categories.values())
    if classified_total != len(corrupt):
        raise AssertionError(
            f"corrupt classification does not partition: "
            f"{classified_total} != {len(corrupt)}"
        )

    return {
        "n_records": len(records),
        "n_corrupt_actions": len(corrupt),
        "n_non_corrupt_actions": sum(
            1 for record in records if record["action"] != "corrupt_hex"
        ),
        "categories": dict(sorted(categories.items())),
        "categories_sum": classified_total,
        "n_score_tie_groups": score_ties,
        "n_equal_impact_ties": tie_equal,
        "n_different_impact_ties": tie_different,
        "n_unknown_impact_ties": tie_unknown,
        "n_correctable_ties": categories["tied_different_impact_correctable"],
        "correctable_share_all_corrupt": (
            len(fixable) / len(corrupt) if corrupt else None
        ),
        "correctable_share_different_impact_ties": (
            len(fixable) / tie_different if tie_different else None
        ),
        "selected_max_impact_share_valid": (
            sum(1 for record in valid_max
                if record["classification"] != "tied_different_impact_correctable")
            / len(valid_max) if valid_max else None
        ),
        "potential_impact_gain": {
            "n": len(impact_gains),
            "total": sum(impact_gains),
            "mean": st.mean(impact_gains) if impact_gains else 0.0,
            "median": st.median(impact_gains) if impact_gains else 0.0,
        },
        "potential_leader_impact_gain": {
            "n": len(leader_gains),
            "total_signed": sum(leader_gains),
            "total_positive": sum(value for value in leader_gains if value > EPS),
            "total_negative": sum(value for value in leader_gains if value < -EPS),
            "mean": st.mean(leader_gains) if leader_gains else 0.0,
            "median": st.median(leader_gains) if leader_gains else 0.0,
            "positive_n": sum(1 for value in leader_gains if value > EPS),
            "negative_n": sum(1 for value in leader_gains if value < -EPS),
        },
        "application_status_by_action": dict(sorted(applied.items())),
    }


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty percentile sample")
    index = min(len(ordered) - 1, max(0, int(q * len(ordered))))
    return ordered[index]


def paired_cluster_bootstrap(
    seed_rows: dict[int, dict[str, dict[str, Any]]],
    metric,
    rng: random.Random,
) -> dict[str, Any]:
    common = sorted(seed for seed, arms in seed_rows.items()
                    if all(arm in arms for arm in ARMS))
    if not common:
        return {"n_seed_clusters": 0, "delta_A1_minus_A0": None, "ci95": None}
    differences = [
        metric(seed_rows[seed]["A1"]) - metric(seed_rows[seed]["A0"])
        for seed in common
    ]
    point = st.mean(differences)
    samples = [
        st.mean(rng.choice(differences) for _ in range(len(differences)))
        for _ in range(BOOTSTRAP_REPS)
    ]
    return {
        "n_seed_clusters": len(common),
        "delta_A1_minus_A0": point,
        "ci95": [percentile(samples, 0.025), percentile(samples, 0.975)],
        "seed_directions": {
            "positive": sum(1 for value in differences if value > 0),
            "negative": sum(1 for value in differences if value < 0),
            "zero": sum(1 for value in differences if abs(value) <= EPS),
        },
        "per_seed_deltas": {
            str(seed): value for seed, value in zip(common, differences)
        },
    }


def make_report(root: Path, out_dir: Path) -> dict[str, Any]:
    manifests = {
        arm: read_jsonl(root / f"manifest_{arm}.jsonl")
        for arm in ARMS
    }
    all_records: list[dict[str, Any]] = []
    all_problems: list[dict[str, Any]] = []
    qc_by_arm: dict[str, Counter] = {arm: Counter() for arm in ARMS}
    application_by_arm: dict[str, Counter] = {arm: Counter() for arm in ARMS}
    readonly_by_arm: dict[str, Counter] = {arm: Counter() for arm in ARMS}
    input_hashes: dict[str, str] = {}

    for arm in ARMS:
        completed = [row for row in manifests[arm]
                     if row.get("status") == "completed"]
        errors = [row for row in manifests[arm]
                  if row.get("status") != "completed"]
        qc_by_arm[arm]["manifest_rows"] = len(manifests[arm])
        qc_by_arm[arm]["completed_games"] = len(completed)
        qc_by_arm[arm]["noncompleted_games"] = len(errors)
        for row in completed:
            out = Path(row["out_dir"])
            if not out.is_absolute():
                out = root / out
            trace = out / "trace.jsonl"
            if not trace.is_file():
                raise FileNotFoundError(trace)
            rel = f'{arm}/seed-{row["seed"]}/perm-{row["perm"]}/trace.jsonl'
            input_hashes[rel] = sha256_file(trace)
            events = read_jsonl(trace)
            records, local_qc, problems = join_trace(events, row, arm)
            for key, value in local_qc.items():
                qc_by_arm[arm][key] += value
            all_problems.extend(problems)
            for record in records:
                classify_corrupt_record(record)
                all_records.append(record)
                status = ("applied" if record["applied_ok"] is True else
                          "rejected" if record["applied_ok"] is False else "unknown")
                application_by_arm[arm][f'{record["action"] or "unknown"}:{status}'] += 1

            ro_events = [event for event in events
                         if event.get("kind") == "corrupt_readonly"]
            readonly_by_arm[arm]["total"] += len(ro_events)
            readonly_by_arm[arm]["state_and_rng_pass"] += sum(
                1 for event in ro_events
                if event.get("state_unchanged") is True
                and event.get("rng_unchanged") is True
            )
            readonly_by_arm[arm]["state_or_rng_fail"] += sum(
                1 for event in ro_events
                if event.get("state_unchanged") is not True
                or event.get("rng_unchanged") is not True
            )

    by_arm_records = {
        arm: [record for record in all_records if record["arm"] == arm]
        for arm in ARMS
    }
    arm_summaries = {arm: aggregate(by_arm_records[arm]) for arm in ARMS}
    seed_ids = sorted({int(record["seed"]) for record in all_records})
    seed_rows: dict[int, dict[str, dict[str, Any]]] = {}
    for seed in seed_ids:
        seed_rows[seed] = {}
        for arm in ARMS:
            records = [record for record in by_arm_records[arm]
                       if int(record["seed"]) == seed]
            seed_rows[seed][arm] = aggregate(records)

    archetype_summary: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        archetype_summary[arm] = {}
        for archetype in ARCHETYPES:
            subset = [record for record in by_arm_records[arm]
                      if record["archetype"] == archetype]
            archetype_summary[arm][archetype] = aggregate(subset)

    rng = random.Random(BOOTSTRAP_SEED)
    pair_metrics = {
        "correctable_share_all_corrupt": paired_cluster_bootstrap(
            seed_rows,
            lambda summary: summary["correctable_share_all_corrupt"] or 0.0,
            rng,
        ),
        "correctable_share_different_impact_ties": paired_cluster_bootstrap(
            seed_rows,
            lambda summary: summary["correctable_share_different_impact_ties"] or 0.0,
            rng,
        ),
        "different_impact_tie_share_of_all_score_ties": paired_cluster_bootstrap(
            seed_rows,
            lambda summary: (
                summary["n_different_impact_ties"] / summary["n_score_tie_groups"]
                if summary["n_score_tie_groups"] else 0.0
            ),
            rng,
        ),
        "potential_impact_gain_total": paired_cluster_bootstrap(
            seed_rows,
            lambda summary: summary["potential_impact_gain"]["total"],
            rng,
        ),
        "potential_impact_gain_per_corrupt_choice": paired_cluster_bootstrap(
            seed_rows,
            lambda summary: (
                summary["potential_impact_gain"]["total"]
                / summary["n_corrupt_actions"]
                if summary["n_corrupt_actions"] else 0.0
            ),
            rng,
        ),
        "potential_leader_impact_gain_total_signed": paired_cluster_bootstrap(
            seed_rows,
            lambda summary: summary["potential_leader_impact_gain"]["total_signed"],
            rng,
        ),
        "potential_leader_impact_gain_per_corrupt_choice": paired_cluster_bootstrap(
            seed_rows,
            lambda summary: (
                summary["potential_leader_impact_gain"]["total_signed"]
                / summary["n_corrupt_actions"]
                if summary["n_corrupt_actions"] else 0.0
            ),
            rng,
        ),
    }

    qc_summary = {
        "by_arm": {arm: dict(qc_by_arm[arm]) for arm in ARMS},
        "readonly_by_arm": {
            arm: dict(readonly_by_arm[arm]) for arm in ARMS
        },
        "application_by_arm": {
            arm: dict(sorted(application_by_arm[arm].items())) for arm in ARMS
        },
        "problems_total": len(all_problems),
        "problem_types": dict(Counter(problem["type"] for problem in all_problems)),
    }
    output = {
        "experiment": "Stage 4.1.1 event-join forensics / offline what-if",
        "input": {
            "root": str(root),
            "trace_count": len(input_hashes),
            "trace_sha256": input_hashes,
        },
        "method": {
            "join": "trace order with strictly increasing seq; one pending corrupt_decision closed by next bot_decision",
            "choice_action_validation": "corrupt_choice has no action field; compare bot_decision.action == corrupt_hex and params.hex_id == corrupt_choice.hex_id",
            "score_tie_eps": EPS,
            "bootstrap": {
                "unit": "seed cluster; all seating permutations within a seed stay together",
                "reps": BOOTSTRAP_REPS,
                "rng_seed": BOOTSTRAP_SEED,
                "ci": "percentile 2.5/97.5",
            },
            "potential_leader_impact": "signed delta between selected candidate and the best leader-impact candidate among maximum-impact candidates; upper bound, not a simulated policy",
        },
        "qc": qc_summary,
        "by_arm": arm_summaries,
        "by_archetype": archetype_summary,
        "paired_seed_cluster_metrics": pair_metrics,
        "by_seed_cluster": {
            str(seed): {arm: seed_rows[seed][arm] for arm in ARMS}
            for seed in seed_ids
        },
        "problems": all_problems,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "event_join_forensics.json"
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    lines = [
        "=== Stage 4.1.1 event join forensic report ===",
        f"Traces: {len(input_hashes)} | seed clusters: {len(seed_ids)}",
        "Join: strict seq order; corrupt_decision -> readonly -> optional choice -> bot_decision",
        "",
        "=== Join QC ===",
    ]
    for arm in ARMS:
        lines.append(f"{arm}:")
        for key, value in sorted(qc_summary["by_arm"][arm].items()):
            lines.append(f"  {key}: {value}")
        lines.append(f"  read-only: {readonly_by_arm[arm]['state_and_rng_pass']}/"
                     f"{readonly_by_arm[arm]['total']} state+RNG PASS")
        lines.append(f"  applied status: {dict(sorted(application_by_arm[arm].items()))}")
    lines += ["", "=== Offline what-if (corrupt_hex selected; maximum-score group only) ==="]
    for arm in ARMS:
        summary = arm_summaries[arm]
        lines.append(f"{arm}:")
        lines.append(f"  corrupt choices: {summary['n_corrupt_actions']}; "
                     f"non-corrupt actions: {summary['n_non_corrupt_actions']}")
        lines.append(f"  disjoint categories: {summary['categories']} "
                     f"(sum={summary['categories_sum']})")
        lines.append(f"  score-tie groups: {summary['n_score_tie_groups']}; "
                     f"equal-impact ties: {summary['n_equal_impact_ties']}; "
                     f"different-impact ties: {summary['n_different_impact_ties']}; "
                     f"unknown-impact ties: {summary['n_unknown_impact_ties']}")
        lines.append(f"  correctable: {summary['n_correctable_ties']} "
                     f"({summary['correctable_share_all_corrupt']:.3%} of corrupt choices; "
                     f"{summary['correctable_share_different_impact_ties'] or 0:.3%} "
                     "of different-impact ties)")
        gain = summary["potential_impact_gain"]
        lines.append(f"  potential impact gain: n={gain['n']}, total={gain['total']:.3f}, "
                     f"mean={gain['mean']:.3f}, median={gain['median']:.3f}")
        leader = summary["potential_leader_impact_gain"]
        lines.append(f"  potential leader-impact delta (upper bound): "
                     f"n={leader['n']}, signed total={leader['total_signed']:.3f}, "
                     f"positive={leader['total_positive']:.3f}, "
                     f"negative={leader['total_negative']:.3f}, "
                     f"mean={leader['mean']:.3f}, median={leader['median']:.3f}")
    lines += ["", "=== Paired bootstrap by seed cluster (A1 - A0) ==="]
    for name, value in pair_metrics.items():
        lines.append(f"{name}: delta={value.get('delta_A1_minus_A0')} "
                     f"CI95={value.get('ci95')} "
                     f"clusters={value.get('n_seed_clusters')}")
    lines += [
        "",
        "Interpretation: offline what-if is an upper bound, not an observed game outcome.",
        "No simulation is started by this script.",
        f"Full structured results: {json_path}",
    ]
    report_path = out_dir / "event_join_report.txt"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("sessions/tp41"))
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    out_dir = (args.out_dir or root).resolve()
    result = make_report(root, out_dir)
    print(f"Completed: {result['input']['trace_count']} traces")
    print(f"Problems: {result['qc']['problems_total']}")
    print(f"Reports: {out_dir / 'event_join_report.txt'}")
    for arm in ARMS:
        summary = result["by_arm"][arm]
        print(
            f"{arm}: corrupt={summary['n_corrupt_actions']}, "
            f"ties-different-impact={summary['n_different_impact_ties']}, "
            f"correctable={summary['n_correctable_ties']}"
        )


if __name__ == "__main__":
    main()
