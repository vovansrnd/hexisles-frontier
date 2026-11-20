# Trade policy experiment — controlled comparison

Status: experiment specification; no runtime preset is enabled automatically.

## Question

Separate the effect of the v23.2 acceptance gate from the effect of the probability scale used when a proposed trade competes with non-trade actions.

The acceptance gate must always use the frozen v23.2 model probability. The score probability source must never affect `accepted_by_gate`.

## Arms

| Arm | Code | Gate thresholds (human / merchant / t800 / chaos) | Score probability source |
|---|---|---|---|
| A0 | `main@3590371` | Historical main behavior | Historical main behavior |
| A2 | `a2_reference` | 0.142 / 0.118 / 0.151 / 0.133 | `model` |
| G1 | `candidate_gate` | 0.121 / 0.102 / 0.129 / 0.118 | `model` |
| S1 | `candidate_gate_legacy_scale` | 0.121 / 0.102 / 0.129 / 0.118 | `model_legacy_scale` |

A2, G1 and S1 keep the following fixed:
- score probability weight: 0.65;
- score floor: 0.25;
- leader-help weight: 1.0;
- all personalities, other strategy genes and game rules unchanged.

The helper in `trade_policy_experiments.py` applies A2, G1 or S1 to four already-created bot instances. It fails closed unless the roster has exactly one instance of each normalized runtime archetype. It is never imported by the normal game runtime.

## Comparisons

- **Gate effect:** G1 versus A2. Only per-archetype model acceptance thresholds change.
- **Score-scale effect:** S1 versus G1. Only the probability scale used in the P2P score changes; thresholds and all other score terms remain fixed.
- **Historical context:** A0 versus A2. This is the previously tested legacy-to-model comparison, repeated on the same fresh seed set for context.

No leader-help ablation is included in this series. That penalty remains fixed to preserve the anti-leader behavior while the two primary channels are isolated.

## Run design

- Four players, all four archetypes represented once per game; all 24 seat permutations per seed.
- No-Blight, fog enabled, 12 victory points, Skynet, fast start off, Trade Routes off.
- Use 20 fresh seeds, preferably 68001–68020 only after checking the full experiment ledger for reuse.
- This yields 480 paired games per arm (1,920 games total).
- Each arm must use an isolated worktree/process and the exact pinned code:
  - A0: `main@359037100964becc8be77e5e8125d7f65755a209`
  - A2/G1/S1: `experiment/trade-policy-controls@f13100bf6003006a69611ef99d88a2d061151f3b`
- Before the fresh series, replay A2 seed 64001 across all 24 permutations and compare turn count, winner, offers, VP, decision count and map hash with the official Stage 2.6 manifest. Any mismatch blocks the new series.
- Verify identical maps and seat permutations across all arms. Record the normalized `BotBrain.archetype` assigned to each displayed archetype; abort if the mapping differs from the expected four-key roster.

## Required reporting

Report per arm and paired by seed:
- integrity errors, incomplete games, map hashes and pair completeness;
- offers, accepted offers, response rate and bank trades;
- number of candidates passing the model gate, number rejected by the score floor, and selected-P2P-action to actual-offer conversion;
- accepted trades followed by an outpost/metropolis in the same turn, next own turn and within three own turns;
- total outposts, metropolises, final VP, turns per game and wins by archetype and seat.

Use the seed as the bootstrap cluster (24 seat permutations stay together). Include paired 95% confidence intervals and per-seed direction counts. Do not describe a nonsignificant difference as equivalence.

## Interpretation guardrails

- Do not select a winner using offer count or acceptance rate alone.
- A gate treatment is successful only if it recovers useful trades without a meaningful loss in construction progress or strategic performance.
- A score-scale treatment is successful only if P2P actions are selected and sent more often without degrading trade utility, construction or game outcomes.
- Keep all changes experimental. Do not merge to `main` from this experiment.
