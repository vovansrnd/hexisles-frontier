#!/usr/bin/env python3
"""Offline score-precision audit using the corrected sequence event join.

Important:
- Read only original per-game trace.jsonl files; never use stale sidecars.
- Associate choices through tp41_event_join.join_trace().
- The logged mod_score is the FINAL plugin score, rounded to six decimals.
  It is not the unadjusted base score: A0 applies a 3.5 clamp before later
  penalties/faction bonuses; A1 applies the overflow transform before those
  later adjustments. Therefore final values above/below 3.5 do not identify
  whether the base score was clamped or transformed.
- Only final-score buckets are reported. Raw base scores are unavailable.
- Every multi-candidate maximum group is precision-uncertain because the
  pre-rounded final float was not saved.

No simulations are launched.

Run:
  python3 audit/evidence/blight-stage41/tp41_score_precision_audit.py \
      --root sessions/tp41 --out-dir audit/evidence/blight-stage41
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import tp41_event_join as event_join

ARMS = ("A0", "A1")
EPS = 1e-9
SCORE_DECIMALS = 6


def bucket(maximum: float) -> str:
    """Partition the observed final score, not the hidden pre-adjustment base."""
    if abs(maximum - 3.5) <= EPS:
        return "final_max==3.5"
    return "final_max<3.5" if maximum < 3.5 else "final_max>3.5"


def top_score_group(candidates: list[dict[str, Any]]) -> tuple[float | None, list[dict[str, Any]]]:
    scored = [
        (candidate, event_join.number(candidate.get("mod_score")))
        for candidate in candidates
    ]
    scored = [(candidate, score) for candidate, score in scored if score is not None]
    if not scored:
        return None, []
    maximum = max(score for _, score in scored)
    return maximum, [
        candidate for candidate, score in scored
        if abs(score - maximum) <= EPS
    ]


def impact_class(group: list[dict[str, Any]]) -> str:
    if len(group) < 2:
        return "not_a_tie"
    impacts = [event_join.number(candidate.get("impact")) for candidate in group]
    if any(impact is None for impact in impacts):
        return "unknown_impact"
    values = [float(impact) for impact in impacts if impact is not None]
    return "equal_impact" if max(values) - min(values) <= EPS else "different_impact"


def classify_record(record: dict[str, Any]) -> dict[str, Any]:
    """Classify one actual corrupt action, already matched by event seq."""
    maximum, group = top_score_group(record.get("candidates") or [])
    chosen_hex = record.get("choice_hex")
    selected_matches = [
        candidate for candidate in (record.get("candidates") or [])
        if candidate.get("hex_id") == chosen_hex
    ]
    selected = selected_matches[0] if len(selected_matches) == 1 else None

    result = {
        "arm": record["arm"],
        "seed": record["seed"],
        "perm": record["perm"],
        "turn": record["turn"],
        "player": record["player"],
        "decision_seq": record["decision_seq"],
        "bot_decision_seq": record["bot_decision_seq"],
        "chosen_hex": chosen_hex,
        "max_score": maximum,
        "score_bucket": bucket(maximum) if maximum is not None else "no_scored_candidates",
        "top_group_size": len(group),
        "top_group": [
            {
                "hex_id": candidate.get("hex_id"),
                "mod_score": candidate.get("mod_score"),
                "impact": candidate.get("impact"),
            }
            for candidate in group
        ],
        "applied_ok": record.get("applied_ok"),
    }

    if selected is None:
        result["classification"] = "chosen_hex_not_in_candidates"
        return result

    top_hexes = {candidate.get("hex_id") for candidate in group}
    if selected.get("hex_id") not in top_hexes:
        result["classification"] = "chosen_outside_max_score_group"
        return result

    if not group:
        result["classification"] = "no_scored_candidates"
        return result

    if len(group) == 1:
        result["classification"] = "unique_score_maximum"
        return result

    kind = impact_class(group)
    result["impact_tie_class"] = kind
    impacts = [event_join.number(candidate.get("impact")) for candidate in group]
    chosen_impact = event_join.number(selected.get("impact"))
    max_impact = max((value for value in impacts if value is not None), default=None)
    result["chosen_impact"] = chosen_impact
    result["group_max_impact"] = max_impact

    if kind == "unknown_impact" or chosen_impact is None or max_impact is None:
        result["classification"] = "tied_impact_unknown"
        return result
    if kind == "equal_impact":
        result["classification"] = "tied_equal_impact"
        return result
    if chosen_impact >= max_impact - EPS:
        result["classification"] = "tied_different_impact_chosen_max"
        return result

    result["classification"] = "tied_different_impact_correctable_if_score_tie_is_exact"
    result["potential_impact_gain_upper_bound"] = max_impact - chosen_impact
    result["score_precision_status"] = "precision_uncertain_six_decimal_final_score"
    return result


def read_manifest(root: Path, arm: str) -> list[dict[str, Any]]:
    return event_join.read_jsonl(root / f"manifest_{arm}.jsonl")


def trace_path(root: Path, row: dict[str, Any]) -> Path:
    path = Path(row["out_dir"])
    if path.is_absolute():
        return path / "trace.jsonl"
    candidates = (root / path / "trace.jsonl", root.parent / path / "trace.jsonl")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"trace.jsonl not found for out_dir={path}")


def audit(root: Path) -> dict[str, Any]:
    all_records: dict[str, list[dict[str, Any]]] = {arm: [] for arm in ARMS}
    qc: dict[str, Counter] = {arm: Counter() for arm in ARMS}
    join_problems: list[dict[str, Any]] = []

    for arm in ARMS:
        rows = read_manifest(root, arm)
        qc[arm]["manifest_rows"] = len(rows)
        for row in rows:
            if row.get("status") != "completed":
                qc[arm]["noncompleted_games"] += 1
                continue
            qc[arm]["completed_games"] += 1
            path = trace_path(root, row)
            events = event_join.read_jsonl(path)
            joined, local_qc, problems = event_join.join_trace(events, row, arm)
            for key, value in local_qc.items():
                qc[arm][key] += value
            join_problems.extend(problems)

            for record in joined:
                if record.get("action") != "corrupt_hex":
                    continue
                qc[arm]["actual_corrupt_actions"] += 1
                if record.get("applied_ok") is True:
                    qc[arm]["applied_corrupt_actions"] += 1
                elif record.get("applied_ok") is False:
                    qc[arm]["rejected_corrupt_actions"] += 1
                else:
                    qc[arm]["unknown_corrupt_application"] += 1
                all_records[arm].append(classify_record(record))

    summaries: dict[str, Any] = {}
    all_correctable: list[dict[str, Any]] = []
    for arm in ARMS:
        arm_records = all_records[arm]
        by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in arm_records:
            by_bucket[record["score_bucket"]].append(record)

        bucket_summary: dict[str, Any] = {}
        for key in sorted(by_bucket):
            records = by_bucket[key]
            classes = Counter(record["classification"] for record in records)
            ties = [record for record in records if record["top_group_size"] >= 2]
            different = [
                record for record in ties
                if record.get("impact_tie_class") == "different_impact"
            ]
            # All logged final scores are rounded to six decimals. No bucket
            # has access to the pre-rounded float, so every recorded tie is
            # precision-uncertain, including final_max==3.5.
            uncertain_ties = ties
            correctable = [
                record for record in records
                if record["classification"] == "tied_different_impact_correctable_if_score_tie_is_exact"
            ]
            for record in correctable:
                all_correctable.append(record)

            bucket_summary[key] = {
                "selected_corrupt_decisions": len(records),
                "unique_maximum_decisions": sum(
                    1 for record in records if record["top_group_size"] == 1
                ),
                "tie_groups": len(ties),
                "tied_candidate_count": sum(record["top_group_size"] for record in ties),
                "equal_impact_tie_groups": sum(
                    1 for record in ties if record.get("impact_tie_class") == "equal_impact"
                ),
                "different_impact_tie_groups": len(different),
                "unknown_impact_tie_groups": sum(
                    1 for record in ties if record.get("impact_tie_class") == "unknown_impact"
                ),
                "correctable_if_recorded_score_tie_is_exact": len(correctable),
                "potential_impact_gain_upper_bound": sum(
                    float(record.get("potential_impact_gain_upper_bound") or 0)
                    for record in correctable
                ),
                "precision_uncertain_tie_groups": len(uncertain_ties),
                "precision_uncertain_different_impact_groups": sum(
                    1 for record in different if record in uncertain_ties
                ),
                "mutually_exclusive_classifications": dict(sorted(classes.items())),
                "classification_sum": sum(classes.values()),
            }
            if bucket_summary[key]["classification_sum"] != len(records):
                raise AssertionError(f"{arm} {key}: classification partition mismatch")

        summaries[arm] = {
            "qc": dict(qc[arm]),
            "selected_corrupt_actions": len(arm_records),
            "buckets": bucket_summary,
            "all_correctable_cases": sum(
                1 for record in arm_records
                if record["classification"] == "tied_different_impact_correctable_if_score_tie_is_exact"
            ),
        }

    return {
        "experiment": "Stage 4.1 — score precision audit (final-score bucket correction)",
        "source": "manifest + original per-game trace.jsonl; NOT corrupt_decisions_*.jsonl.gz",
        "score_precision": {
            "stored_decimals": SCORE_DECIMALS,
            "tie_eps": EPS,
            "raw_round_trip_scores_available": False,
            "raw_base_scores_available": False,
            "minimum_confirmed_cap_induced_tie_groups": 0,
            "score_field_semantics": {
                "logged_field": "candidate.mod_score",
                "meaning": "Final value returned by BlightMod.score_ai_action(), rounded to six decimals by run_tp41.py.",
                "A0": "The base corrupt score is clamped with min(3.5, base); subsequent anti-loop penalties and faction-progress bonuses modify that score. The final returned value can be below, equal to, or above 3.5.",
                "A1": "The raw base corrupt score is passed through the rank-preserving transform; subsequent anti-loop penalties and faction-progress bonuses modify that value. The final returned value can be below, equal to, or above 3.5.",
                "bucket_interpretation": "final_max buckets describe only the logged final score. They do not identify whether the unadjusted A0 base was clamped or whether the unadjusted A1 base entered the overflow transform."
            },
            "interpretation": (
                "Only final mod_score values rounded to six decimals are present in "
                "the trace. Raw pre-adjustment base scores and pre-rounded final "
                "scores are unavailable. Therefore score buckets relative to 3.5 "
                "are descriptive final-output regions only, not cap/overflow regions. "
                "Every multi-candidate recorded maximum group is precision-uncertain, "
                "including groups whose recorded final maximum equals 3.5. The number "
                "of ties actually caused by the base clamp is not identifiable."
            ),
        },
        "by_arm": summaries,
        "correctable_cases": all_correctable,
        "join_problems": join_problems,
    }


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "=== Stage 4.1 — score precision audit (corrected seq join; final-score buckets) ===",
        "Source: original trace.jsonl; corrupt_decisions_*.jsonl.gz excluded",
        "Score precision: six decimal places; EPS=1e-9",
        "Raw pre-rounded scores are unavailable; no reconstruction attempted.",
        "",
    ]
    for arm in ARMS:
        summary = result["by_arm"][arm]
        qc = summary["qc"]
        lines.append(
            f"{arm}: games={qc.get('completed_games', 0)}, "
            f"joined decisions={qc.get('joined_decisions', 0)}, "
            f"selected corrupt_hex={summary['selected_corrupt_actions']}, "
            f"join problems={len(result['join_problems'])}"
        )
        for key, bucket_summary in summary["buckets"].items():
            lines.append(
                f"  {key}: selected={bucket_summary['selected_corrupt_decisions']}, "
                f"unique={bucket_summary['unique_maximum_decisions']}, "
                f"ties={bucket_summary['tie_groups']}, "
                f"equal-impact ties={bucket_summary['equal_impact_tie_groups']}, "
                f"different-impact ties={bucket_summary['different_impact_tie_groups']}, "
                f"correctable-if-exact={bucket_summary['correctable_if_recorded_score_tie_is_exact']}, "
                f"precision-uncertain ties={bucket_summary['precision_uncertain_tie_groups']}"
            )
        lines.append("")

    total_ties = {
        arm: sum(b.get("tie_groups", 0) for b in result["by_arm"][arm]["buckets"].values())
        for arm in ARMS
    }
    total_uncertain = {
        arm: sum(b.get("precision_uncertain_tie_groups", 0) for b in result["by_arm"][arm]["buckets"].values())
        for arm in ARMS
    }
    lines += [
        "=== Interpretation ===",
        "Buckets partition the OBSERVED FINAL mod_score relative to 3.5 only.",
        "They do not classify the pre-adjustment base score or identify clamp/overflow membership.",
        "A0 clamps its base score before later penalties and faction bonuses; A1 transforms its base score before those later adjustments.",
        f"Recorded maximum-score tie groups: A0={total_ties['A0']}, A1={total_ties['A1']}.",
        f"Precision-uncertain recorded tie groups: A0={total_uncertain['A0']}, A1={total_uncertain['A1']}; all ties are uncertain at six-decimal storage precision.",
        "Correctable cases are upper bounds conditional on the recorded six-decimal tie being an exact tie in the underlying final float.",
        "Minimum confirmed cap-induced tie groups: 0; raw base and pre-rounded final scores were not logged.",
        "The number of true ties caused by the A0 base clamp or A1 base overflow transform cannot be inferred from final mod_score.",
        "No games were launched. This is an offline, trace-based classification only.",
        "",
        "=== Correctable cases ===",
    ]
    for record in result["correctable_cases"]:
        lines.append(
            f"{record['arm']} seed={record['seed']} perm={record['perm']} "
            f"turn={record['turn']} player={record['player']} "
            f"decision_seq={record['decision_seq']} bot_seq={record['bot_decision_seq']} "
            f"chosen_hex={record['chosen_hex']} chosen_impact={record.get('chosen_impact')} "
            f"group_max_impact={record.get('group_max_impact')} "
            f"gain_upper_bound={record.get('potential_impact_gain_upper_bound')} "
            f"status={record.get('score_precision_status', 'precision_uncertain_six_decimal_final_score')}"
        )
        for candidate in record.get("top_group", []):
            lines.append(
                f"  hex={candidate['hex_id']} mod_score={candidate['mod_score']} "
                f"impact={candidate['impact']}"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("sessions/tp41"))
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    result = audit(args.root.resolve())
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "score_precision_audit.json"
    report_path = out_dir / "score_precision_audit_report.txt"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_report(result), encoding="utf-8")
    print(f"Traces joined; problems={len(result['join_problems'])}")
    for arm in ARMS:
        print(f"{arm}: corrupt={result['by_arm'][arm]['selected_corrupt_actions']}, "
              f"correctable={result['by_arm'][arm]['all_correctable_cases']}")
    print(f"JSON: {json_path}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
