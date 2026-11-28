#!/usr/bin/env python3
"""Stage 4.2.0 — exact-score tie observability audit.

Reads corrective replay traces where every selected corrupt_hex candidate's
score is captured from the real BotBrain._score_action call during
choose_action(), after the chooser itself resolves _get_goal(legal_actions):

  mod_score       — round(actual chooser score, 6) (Stage 4.1 compatibility)
  mod_score_full  — exact return value from the real _score_action invocation
  mod_score_hex   — exact binary64 representation (float.hex())
  candidate_order — position in the original corrupt_acts list
  chooser_goal    — goal actually passed to that _score_action invocation

Event association reuses the Stage 4.1 corrected seq join
(tp41_event_join.join_trace). Exact top-score groups are built with DIRECT
float equality against the exact maximum (no EPS): the Skynet chooser
compares actual floats, so a recorded six-decimal tie is not a real tie
unless the underlying binary64 values are equal.

Metrics per selected corrupt_hex decision:
  1. exact top-score ties (>=2 candidates equal to the exact maximum);
  2. ties visible after round(..., 6) that disappear under exact comparison;
  3. exact ties with equal impact;
  4. exact ties with different impact;
  5. exact ties where the chosen candidate's impact is below the group max;
  6. chosen hex outside the exact top-score group.

Impact is taken from the original computation. It is integral by
construction (integer loss steps × integer pips), so the four-decimal
logged value is exact; the audit verifies integrality and the
mod_score_hex <-> mod_score_full round trip.

No simulations are launched. Run:
  python3 audit/evidence/blight-stage42/tp42_exact_score_audit.py \
      --root <tp42 staging> --out-dir audit/evidence/blight-stage42
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STAGE41 = HERE.parent / "blight-stage41"
if str(STAGE41) not in sys.path:
    sys.path.insert(0, str(STAGE41))
import tp41_event_join as event_join  # noqa: E402

ARMS = ("A0", "A1")
ROUNDED_EPS = 1e-9  # Stage 4.1 comparator, used ONLY for the compatibility cut


def candidate_score(candidate: dict[str, Any]) -> tuple[float, str] | None:
    """(exact binary64 value, hex repr) for one candidate, or None."""
    full = event_join.number(candidate.get("mod_score_full"))
    hex_repr = candidate.get("mod_score_hex")
    if full is None or hex_repr is None:
        return None
    return full, str(hex_repr)


def scored_candidates(candidates: list[dict[str, Any]]) -> list[tuple[dict, float, str]]:
    out = []
    for candidate in candidates:
        sc = candidate_score(candidate)
        if sc is not None:
            out.append((candidate, sc[0], sc[1]))
    return out


def exact_top_group(candidates: list[dict[str, Any]]):
    """(exact maximum, group) via direct float equality — no EPS."""
    scored = scored_candidates(candidates)
    if not scored:
        return None, []
    maximum = max(value for _, value, _ in scored)
    return maximum, [c for c, value, _ in scored if value == maximum]


def rounded_top_group(candidates: list[dict[str, Any]]):
    """Stage 4.1 comparator on the six-decimal field (EPS tie window)."""
    scored = []
    for candidate in candidates:
        value = event_join.number(candidate.get("mod_score"))
        if value is not None:
            scored.append((candidate, value))
    if not scored:
        return None, []
    maximum = max(value for _, value in scored)
    return maximum, [c for c, value in scored
                     if abs(value - maximum) <= ROUNDED_EPS]


def impact_value(candidate: dict[str, Any]) -> float | None:
    return event_join.number(candidate.get("impact"))


def group_impacts(group: list[dict[str, Any]]) -> list[float] | None:
    values = [impact_value(c) for c in group]
    if any(v is None for v in values):
        return None
    return [float(v) for v in values]


def impacts_integral(candidates: list[dict[str, Any]]) -> bool:
    """Logged impact is round(int_loss * pips, 4): integral by construction."""
    for candidate in candidates:
        value = impact_value(candidate)
        if value is None or float(value) != float(int(value)):
            return False
    return True


def classify_exact(record: dict[str, Any]) -> dict[str, Any]:
    """Classify one chosen corrupt_hex action by exact float scores."""
    candidates = record.get("candidates") or []
    unobserved = [
        c.get("hex_id") for c in candidates
        if c.get("score_observed_by_chooser") is not True
        or candidate_score(c) is None
    ]
    if unobserved:
        raise ValueError(
            f"selected corrupt_hex decision seq={record.get('decision_seq')} "
            f"contains candidates without actual chooser scores: {unobserved}"
        )
    goal_snapshots = {
        json.dumps(c.get("chooser_goal"), sort_keys=True)
        for c in candidates
    }
    if len(goal_snapshots) != 1:
        raise ValueError(
            f"selected corrupt_hex decision seq={record.get('decision_seq')} "
            "has inconsistent chooser_goal snapshots across candidates"
        )
    orders = [c.get("candidate_order") for c in candidates]
    if any(not isinstance(order, int) for order in orders) or len(set(orders)) != len(orders):
        raise ValueError(
            f"selected corrupt_hex decision seq={record.get('decision_seq')} "
            "has missing or duplicated candidate_order values"
        )
    maximum, group = exact_top_group(candidates)
    _, rounded_group = rounded_top_group(candidates)
    chosen_hex = record.get("choice_hex")
    matches = [c for c in candidates if c.get("hex_id") == chosen_hex]
    selected = matches[0] if len(matches) == 1 else None
    scored = scored_candidates(candidates)

    result: dict[str, Any] = {
        "arm": record["arm"], "seed": record["seed"], "perm": record["perm"],
        "turn": record["turn"], "player": record["player"],
        "decision_seq": record["decision_seq"],
        "bot_decision_seq": record["bot_decision_seq"],
        "chosen_hex": chosen_hex,
        "exact_max_score": maximum,
        "exact_max_hex": (float(maximum).hex() if maximum is not None else None),
        "exact_top_size": len(group),
        "rounded_top_size": len(rounded_group),
        "impacts_integral": impacts_integral(candidates),
        "hex_round_trip_ok": all(float.fromhex(h) == v for _, v, h in scored),
    }
    if selected is not None:
        chosen_score = candidate_score(selected)
        chosen_impact = impact_value(selected)
        group_values = group_impacts(group)
        result.update({
            "chosen_exact_score_hex": (chosen_score[1] if chosen_score else None),
            "chosen_candidate_order": selected.get("candidate_order"),
            "chosen_impact": chosen_impact,
            "exact_group_impacts": [
                {"hex_id": c.get("hex_id"),
                 "mod_score_full": c.get("mod_score_full"),
                 "mod_score_hex": c.get("mod_score_hex"),
                 "impact": c.get("impact"),
                 "candidate_order": c.get("candidate_order"),
                 "chooser_goal": c.get("chooser_goal")}
                for c in group
            ],
            "exact_group_max_impact": (max(group_values) if group_values else None),
        })

    # mutually exclusive classification of every chosen corrupt_hex action
    if selected is None:
        result["classification"] = "chosen_hex_not_in_candidates"
        return result
    if maximum is None:
        result["classification"] = "no_scored_candidates"
        return result
    if not any(c.get("hex_id") == chosen_hex for c in group):
        result["classification"] = "chosen_outside_exact_top_group"
        return result
    if len(group) == 1:
        result["classification"] = "exact_unique_maximum"
        return result

    chosen_impact = result.get("chosen_impact")
    group_max = result.get("exact_group_max_impact")
    if chosen_impact is None or group_max is None:
        result["classification"] = "exact_tie_impact_unknown"
        return result
    if chosen_impact < group_max:
        result["classification"] = "exact_tie_below_max_impact"
        return result
    result["classification"] = "exact_tie_chosen_max_impact"
    return result


def read_manifest(root: Path, arm: str) -> list[dict[str, Any]]:
    return event_join.read_jsonl(root / f"manifest_{arm}.jsonl")


def trace_path(root: Path, row: dict[str, Any]) -> Path:
    path = Path(row["out_dir"])
    if path.is_absolute():
        return path / "trace.jsonl"
    for candidate in (root / path / "trace.jsonl",
                      root.parent / path / "trace.jsonl"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"trace.jsonl not found for out_dir={path}")


def audit(root: Path) -> dict[str, Any]:
    records: dict[str, list[dict[str, Any]]] = {arm: [] for arm in ARMS}
    qc: dict[str, Counter] = {arm: Counter() for arm in ARMS}
    join_problems: list[dict[str, Any]] = []
    integrity = Counter()

    for arm in ARMS:
        for row in read_manifest(root, arm):
            if row.get("status") != "completed":
                qc[arm]["noncompleted_games"] += 1
                continue
            qc[arm]["completed_games"] += 1
            events = event_join.read_jsonl(trace_path(root, row))
            joined, local_qc, problems = event_join.join_trace(events, row, arm)
            for key, value in local_qc.items():
                qc[arm][key] += value
            join_problems.extend(problems)
            for record in joined:
                if record.get("action") != "corrupt_hex":
                    continue
                qc[arm]["selected_corrupt_hex"] += 1
                if record.get("applied_ok") is True:
                    qc[arm]["applied_corrupt_hex"] += 1
                elif record.get("applied_ok") is False:
                    qc[arm]["rejected_corrupt_hex"] += 1
                else:
                    qc[arm]["unknown_corrupt_hex_application"] += 1
                classified = classify_exact(record)
                records[arm].append(classified)
                integrity["hex_round_trip_mismatches"] += (
                    0 if classified["hex_round_trip_ok"] else 1)
                integrity["non_integral_impact_records"] += (
                    0 if classified["impacts_integral"] else 1)

    summaries: dict[str, Any] = {}
    for arm in ARMS:
        arm_records = records[arm]
        classes = Counter(r["classification"] for r in arm_records)
        exact_ties_all = [r for r in arm_records if r["exact_top_size"] >= 2]
        ties_with_chosen = [r for r in exact_ties_all
                            if r["classification"].startswith("exact_tie_")]
        equal_impact, diff_impact = [], []
        for r in ties_with_chosen:
            values = group_impacts(
                [{"impact": c.get("impact")}
                 for c in r.get("exact_group_impacts") or []])
            if values is None:
                continue
            (equal_impact if max(values) == min(values)
             else diff_impact).append(r)
        unknown_impact = [r for r in ties_with_chosen
                          if r not in equal_impact and r not in diff_impact]
        exact_correctable = [r for r in arm_records
                             if r["classification"] == "exact_tie_below_max_impact"]
        chosen_lowest_order = sum(
            1 for r in exact_ties_all
            if r.get("chosen_candidate_order") is not None
            and r.get("exact_group_impacts")
            and r["chosen_candidate_order"] == min(
                c["candidate_order"] for c in r["exact_group_impacts"]
                if c.get("candidate_order") is not None)
        )

        def case(r: dict[str, Any]) -> dict[str, Any]:
            return {k: r.get(k) for k in (
                "arm", "seed", "perm", "turn", "player", "decision_seq",
                "bot_decision_seq", "chosen_hex", "chosen_impact",
                "exact_group_max_impact", "exact_max_score", "exact_max_hex",
                "chosen_exact_score_hex", "chosen_candidate_order",
                "exact_group_impacts")} | {"classification": r["classification"]}

        summaries[arm] = {
            "qc": dict(qc[arm]),
            "selected_corrupt_hex": len(arm_records),
            "exact_top_score_ties": len(exact_ties_all),
            "exact_ties_with_chosen_in_group": len(ties_with_chosen),
            "exact_tie_equal_impact": len(equal_impact),
            "exact_tie_different_impact": len(diff_impact),
            "exact_tie_unknown_impact": len(unknown_impact),
            "exact_correctable_ties": len(exact_correctable),
            "rounded_ties_stage41_comparator": sum(
                1 for r in arm_records if r["rounded_top_size"] >= 2),
            "rounded_only_ties_not_exact": sum(
                1 for r in arm_records
                if r["rounded_top_size"] >= 2 and r["exact_top_size"] < 2),
            "exact_only_ties_not_rounded": sum(
                1 for r in arm_records
                if r["exact_top_size"] >= 2 and r["rounded_top_size"] < 2),
            "chosen_outside_exact_top_group": sum(
                1 for r in arm_records
                if r["classification"] == "chosen_outside_exact_top_group"),
            "exact_tie_chosen_is_lowest_candidate_order": chosen_lowest_order,
            "mutually_exclusive_classifications": dict(sorted(classes.items())),
            "classification_sum": sum(classes.values()),
            "exact_tie_cases": [case(r) for r in exact_ties_all],
            "correctable_cases": [case(r) for r in exact_correctable],
        }
        if summaries[arm]["classification_sum"] != len(arm_records):
            raise AssertionError(f"{arm}: classification partition mismatch")

    return {
        "experiment": "Stage 4.2.1 — exact actual-chooser score observability (binary64)",
        "source": "corrective replay traces; scores captured inline from BotBrain._score_action during choose_action(); seq join from Stage 4.1",
        "score_capture_semantics": "Captured return value from the real _score_action call after choose_action() resolves _get_goal(legal_actions). No stale brain.current_goal substitution and no extra scorer call for logging.",
        "comparison_policy": {
            "exact": "direct float == against exact maximum; no EPS (chooser compares actual floats)",
            "rounded_compatibility_cut": "Stage 4.1 comparator: round(...,6) with EPS=1e-9",
            "impact_precision": "impact = integer loss steps * integer pips; logged at 4 decimals -> exact; verified integrally",
        },
        "input_integrity": dict(integrity),
        "by_arm": summaries,
        "join_problems": join_problems,
    }


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "=== Stage 4.2.0 — exact-score tie observability (binary64) ===",
        "Source: sessions/tp42 trace.jsonl (mod_score_full / mod_score_hex);",
        "seq join reused from Stage 4.1 (tp41_event_join.join_trace).",
        "Exact top groups: direct float equality, NO EPS — values were captured",
        "from the actual _score_action calls made by the Skynet chooser.",
        "",
    ]
    for arm in ARMS:
        s = result["by_arm"][arm]
        qc = s["qc"]
        lines.append(
            f"{arm}: games={qc.get('completed_games', 0)}, "
            f"selected corrupt_hex={s['selected_corrupt_hex']}, "
            f"join problems={len(result['join_problems'])}, "
            f"hex round-trip mismatches="
            f"{result['input_integrity']['hex_round_trip_mismatches']}, "
            f"non-integral impact records="
            f"{result['input_integrity']['non_integral_impact_records']}"
        )
        lines.append(
            f"  exact top-score ties: {s['exact_top_score_ties']} "
            f"(chosen inside group {s['exact_ties_with_chosen_in_group']}; "
            f"equal-impact {s['exact_tie_equal_impact']}, "
            f"different-impact {s['exact_tie_different_impact']}, "
            f"unknown {s['exact_tie_unknown_impact']}); "
            f"exact correctable: {s['exact_correctable_ties']}"
        )
        lines.append(
            f"  rounded ties (Stage 4.1 comparator): "
            f"{s['rounded_ties_stage41_comparator']}; "
            f"rounded-only (never ties for the AI): "
            f"{s['rounded_only_ties_not_exact']}; "
            f"exact-only (rounding broke the tie): "
            f"{s['exact_only_ties_not_rounded']}"
        )
        lines.append(
            f"  chosen outside exact top group: "
            f"{s['chosen_outside_exact_top_group']}; "
            f"exact-tie chosen has lowest candidate_order: "
            f"{s['exact_tie_chosen_is_lowest_candidate_order']}"
        )
        lines.append("")
    lines += [
        "=== Interpretation ===",
        "The chooser compares actual binary64 floats. Only exact ties exist for",
        "it; recorded six-decimal ties that disappear under exact comparison",
        "were never ties for the AI. Impact values are integral (integer loss",
        "steps times integer pips), so the logged four-decimal impact is exact",
        "and sufficient for candidate comparison.",
        "Scores were captured inside the real chooser evaluation; no scorer",
        "was called a second time solely for logging.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("sessions/tp42"))
    parser.add_argument("--out-dir", type=Path, default=HERE)
    args = parser.parse_args()
    result = audit(args.root.resolve())
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "exact_score_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "exact_score_audit_report.txt").write_text(
        render_report(result), encoding="utf-8")
    print(f"Traces joined; problems={len(result['join_problems'])}")
    for arm in ARMS:
        s = result["by_arm"][arm]
        print(f"{arm}: corrupt={s['selected_corrupt_hex']}, "
              f"exact ties={s['exact_top_score_ties']} "
              f"(diff-impact {s['exact_tie_different_impact']}, "
              f"correctable {s['exact_correctable_ties']}), "
              f"rounded-only ties={s['rounded_only_ties_not_exact']}")
    print(f"JSON: {out_dir / 'exact_score_audit.json'}")
    print(f"Report: {out_dir / 'exact_score_audit_report.txt'}")


if __name__ == "__main__":
    main()
