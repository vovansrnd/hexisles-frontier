# Trade-policy experiment 2.7 — results and independent replication

## Status

Stage 2.7 was executed by the runtime agent on the pinned experiment code:
- A0: `main@359037100964becc8be77e5e8125d7f65755a209`
- A2/G1/S1: `experiment/trade-policy-controls@ed00a88a79b2cb10e348c8d4274200e4b3dd1714`

This document records the agent's reported results. The raw run artifacts remain in the agent's experiment workspace; this summary is not a replacement for those traces.

## Stage 2.7 quality checks

- 20 fresh seeds: `68001–68020`.
- 24 seat permutations per seed.
- Four arms, 480 games each; 1,920/1,920 completed.
- 480/480 complete paired groups, no runtime errors.
- No map mismatches and no No-Blight violations.
- A2 replay of seed 64001 across all 24 permutations matched the official Stage 2.6 manifest.
- All arms used fog, 12 victory points, Skynet, fast start disabled, No-Blight, and Trade Routes disabled.
- Runtime archetype mapping was verified in a live game: C3PO → `human`, R2D2 → `merchant`, T-800 → `t800`, Vall-E → `chaos`.

## Experimental arms

| Arm | Gate thresholds (C3PO / R2D2 / T-800 / Vall-E) | Score probability source |
|---|---|---|
| A0 | Historical main behavior | Historical main behavior |
| A2 | 0.142 / 0.118 / 0.151 / 0.133 | `model` |
| G1 | 0.121 / 0.102 / 0.129 / 0.118 | `model` |
| S1 | 0.121 / 0.102 / 0.129 / 0.118 | `model_legacy_scale` |

A2, G1 and S1 kept probability weight 0.65, score floor 0.25 and leader-help weight 1.0. The acceptance gate continued to use the v23.2 model probability in all model arms.

## Main results

Per-game means and paired seed-bootstrap 95% intervals as reported by the runtime agent:

| Contrast | Offers / game | Accepted offers / game |
|---|---:|---:|
| A2 − A0 | 42.5 → 37.1; Δ −5.3 (−7.3, −3.3) | 9.44 → 9.54; Δ +0.10, not significant |
| G1 − A2 | 37.1 → 40.0; Δ +2.84 (+1.33, +4.04) | Δ +0.37, not significant |
| S1 − G1 | 40.0 → 43.0; Δ +3.04 (+1.91, +4.41) | Δ +0.78 (+0.15, +1.50) |
| S1 − A0 | 42.5 → 43.0; Δ +0.54, not significant | 9.44 → 10.68; Δ +1.24 (+0.68, +1.85) |

The reported candidate diagnostics:
- A2: the model gate passed 40.7% of candidates; the score floor then rejected 10.1% of those passing candidates.
- G1: the lower thresholds increased the gate pass rate to 48.3%.
- S1: using the legacy score scale reduced score-floor rejection to 1.8%; selected-P2P-to-sent-offer conversion rose from 46.3% to 51.8%.

The reported same-turn trade-to-build rate fell from 68.9% in A0 to 56.7% in A2, then recovered to 66.1% in S1. Across all arms, approximately 76% of accepted trades led to a build within three own turns.

S1 versus A0 construction and tempo:
- Settlements/outposts: 10.23 per game in both arms (reported Δ 0.00).
- Cities/metropolises: Δ +0.08, not significant.
- Routes: Δ −0.05, not significant.
- End-turn actions: Δ −0.4, not significant; the earlier increase of 3.8–7.9 in A1/A2 was absent.
- Median game length: 90 versus 92 turns.
- R2D2 had the most wins in all four arms and reached 155 wins in S1 versus 138 in A0. The report found no systematic seat shift.

## Interpretation

The gate and score scale have separable effects:
1. Lowering the model gate thresholds (G1 versus A2) recovered offers, but did not produce a statistically clear increase in accepted offers.
2. Changing only the score probability scale (S1 versus G1) increased both offers and accepted offers. This is consistent with the score scale being the stronger of the two identified mechanisms for lost accepted trades.
3. S1 versus A0 recovered the historical offer volume and exceeded A0 accepted-trade volume in this particular series, without a reported construction or tempo regression.

S1 is therefore the leading candidate configuration for further validation. It is **not yet the production default**.

## Limitations

This evidence is limited to four-player, No-Blight, fog-enabled, Skynet games with Trade Routes disabled. It does not establish generalization to:
- three-player games;
- human opponents or other difficulty levels;
- Blight or Trade Routes;
- altered player counts, map distributions or future rule changes.

Do not describe nonsignificant differences as proof of equivalence. Do not infer causality beyond the controlled contrast between the configured arms.

## Stage 2.8 — held-out replication protocol

The next run is a focused independent replication of the complete S1 policy against A0. It is not another four-arm decomposition.

### Arms

- A0: `main@359037100964becc8be77e5e8125d7f65755a209`
- S1: `experiment/trade-policy-controls`, profile `candidate_gate_legacy_scale`, from the current pinned branch commit.

### Seeds and conditions

- First verify that `66001–66020` are unused in the full experiment ledger. If any are occupied, replace the entire range with the next 20 fully unused seeds and record the substitution before running.
- 20 seeds × 24 seat permutations × 2 arms = 960 games.
- Four players, all four archetypes once each, identical maps and seat permutations across arms.
- Fog enabled, 12 victory points, Skynet, fast start disabled, No-Blight, Trade Routes disabled.
- Use isolated worktrees and processes per arm; use the official response-order logic with randomized responder order.
- No runtime tuning after results become visible.

### Primary success criteria

1. S1 accepted offers per game exceed A0, with a paired seed-bootstrap 95% interval whose lower bound is above zero.
2. The S1 settlement/outpost difference versus A0 is non-inferior within a predeclared margin of **−0.25 per game** (lower bound of the paired 95% interval must be above −0.25).
3. The S1 city/metropolis difference versus A0 is non-inferior within **−0.25 per game**.
4. The accepted-trade-to-build-within-three-own-turns difference is non-inferior within **−0.50 builds per game**.
5. Final VP per game is non-inferior within **−0.20 VP**.
6. All arms pass integrity and map-pairing QC.

These are practical experiment margins, not claims of statistical equivalence. Report effect sizes and intervals even when a criterion fails.

### Required diagnostics

In addition to the Stage 2.7 metrics, report:
- results by bot archetype, not just aggregate;
- offers initiated, accepted offers and trade-to-build conversion per archetype;
- same-turn, next-own-turn and three-own-turn build windows;
- model-gate pass rate, score-floor rejection and selected-P2P-to-sent-offer conversion;
- wins and final VP by archetype and seat;
- 95% paired confidence intervals and number of seeds with a positive/negative effect.

If S1 passes, it becomes the candidate baseline for the next validation stage. It must still be tested separately with Blight and Trade Routes before any production adoption. If it fails, do not retune on this held-out set; report the failure and design a new training/calibration split.
