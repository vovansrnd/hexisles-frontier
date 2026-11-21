# Stage 2.8 — independent review of the uploaded evidence

Evidence source: `audit/trade-policy-stage28-evidence` at commit
`1514faf37e3ed59fd333ba4c97769aaa4806bc6b`.

The review below was recalculated from `games.csv` grouped by seed. It is an independent summary of the uploaded game-level aggregates, not a replay of the underlying 960 games.

## Integrity of the evidence tables

- `games.csv`: 960 rows, 480 for A0 and 480 for S1.
- 20 seeds: 66001–66020.
- 24 paired permutations per seed.
- 480/480 A0–S1 seed/permutation pairs present.
- Map MD5 mismatches: 0.
- No missing games in the table.
- `qc_summary.json` reports zero runtime errors, incomplete traces and No-Blight violations.

The configuration pins A0 to `main@3590371`, S1 to `experiment/trade-policy-controls@e861d59`, and records the `candidate_gate_legacy_scale` preset.

## Recalculation

All game-level means below were recomputed directly from `games.csv`. Paired effects were computed by averaging each metric over the 24 permutations within a seed, then comparing the 20 paired seed means. Confidence intervals below use a 100,000-resample percentile bootstrap over those 20 paired seed clusters. The exact endpoints vary slightly from the agent's two summaries because the bootstrap resampling stream / implementation differs; the decisions do not change.

| Metric per game | A0 | S1 | S1 − A0 | Independent paired 95% CI |
|---|---:|---:|---:|---:|
| Offers | 43.283 | 45.023 | +1.740 | −0.396 … +3.798 |
| Accepted offers | 9.738 | 10.556 | +0.819 | −0.052 … +1.633 |
| Settlements / outposts | 10.027 | 9.831 | −0.196 | −0.642 … +0.254 |
| Cities / metropolises | 9.525 | 9.404 | −0.121 | −0.481 … +0.202 |
| Accepted trade → build within 3 own turns | 7.627 | 8.252 | +0.625 | −0.158 … +1.377 |
| Routes | 32.127 | 31.760 | −0.367 | −1.635 … +0.888 |
| Bank trades | 25.302 | 24.692 | −0.610 | −1.940 … +0.669 |
| End turns | 95.498 | 92.717 | −2.781 | −6.933 … +0.933 |
| Final VP per player | 8.520 | 8.405 | −0.115 | −0.294 … +0.060 |

Per-seed direction counts:
- Accepted offers: S1 higher in 13 seeds, lower in 7.
- Settlements: higher in 9, lower in 11.
- Cities: higher in 11, lower in 9.
- Trade-to-build within three own turns: higher in 14, lower in 6.
- Final VP per player: higher in 9, lower in 11.

The uploaded `archetypes.csv` confirms that accepted-offer share rose for each archetype, while three-own-turn trade-to-build shares remained in a narrow range:
- C3PO: accepted share 22.9% → 23.7%; trade-to-build share 79.5% → 78.4%.
- R2D2: 21.1% → 22.0%; 79.1% → 77.9%.
- T-800: 23.0% → 25.0%; 77.2% → 77.8%.
- Vall-E: 23.1% → 23.3%; 77.5% → 78.6%.

## Decision against the predeclared criteria

The independent calculations agree with the agent's decision:
- QC passes.
- Trade-to-build within three own turns passes the −0.50 non-inferiority margin.
- Accepted offers do not meet the requirement that the lower confidence bound be above zero.
- Settlements and cities do not establish non-inferiority at −0.25 per game.
- Final VP does not establish non-inferiority at −0.20.

This is an **inconclusive replication**, not proof that S1 is harmful. The point estimates for accepted offers and trade-to-build are positive, but their uncertainty is too large to adopt S1 as the default. Do not tune any S1 parameters using seeds 66001–66020.

## Evidence-quality note

The uploaded `criteria_2.8.txt` and `full_metrics_2.8.txt` both contain a `NameError: bd_events is not defined` traceback in the last archetype-analysis section. The main aggregate tables and QC sections precede that exception and are reproducible from `games.csv`; the failing block means the archived text is not a clean, fully completed analysis log. `archetypes.csv` contains the aggregate per-archetype counts and shares listed above, but not per-archetype confidence intervals.

The two uploaded summary files also show slightly different bootstrap interval endpoints for some metrics. The differences are small and do not affect any of the six pass/fail decisions. Future experiment tooling should use one shared bootstrap implementation and save its seed, replicate count and interval method in the evidence manifest.

## Decision

- Keep S1 experimental; do not promote it to a default or merge it to `main`.
- Do not retune thresholds or the score transform on this held-out set.
- Preserve this evidence as a completed but inconclusive replication.
- Move the next development focus to a broader strategic subsystem audit, starting with the Trade Routes mode, whose economy, competition, blocking and opportunity costs have not yet received a comparable controlled evaluation.
