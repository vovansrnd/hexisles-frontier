# Stage 3.2 — paired test of route-income portfolio scoring

## Question

Does valuing the marginal resource diversity of a second/third active route
help the bots choose economically useful Trade Routes without reducing ordinary
expansion, trade activity or match performance?

This is a scoring-only experiment. No route rules, road costs, activation
costs, income cap, bank rates or suppression rules may change between arms.

## Pinned arms

- **A0 — previous route scorer:** commit
  `9af21df2856a30e2ad448e3fefa970529477c179`.
- **A1 — marginal portfolio scorer:**
  `experiment/trade-routes-integrity@f1fff48f78526bbe265a37bfb957d2030306dee5`.

Both arms contain the three Stage 3.1 integrity fixes. Their only gameplay
difference must be the route-AI scoring change from commit
`64566310aaea88198db5b4414adea568af705242` (subsequently corrected and
covered by tests in `4c34eb4514aebdccdceab919b2f87c5ed99976da`).
Documentation and tests may differ, but no other runtime gameplay code may
differ. Verify with a runtime diff before launching.

## Game setup

- Four AI players: C3PO, R2D2, T-800 and Vall-E, all present in every game.
- Fog ON, 12 VP, Skynet, fast start OFF.
- Blight OFF.
- Trade Routes ON, default `one_income_per_turn=True`, `routes_limit=3`,
  `income_delay_rounds=1`, `bank_trade_rate=3`, suppression enabled.
- Same official randomized responder order in both arms.
- Separate clean worktrees and spawned processes; no runtime patch beyond
  read-only instrumentation.

Use 20 seeds that are confirmed unused across the complete laboratory ledger.
Do not assume a range is free. Report the selected seeds before starting.
For every seed, run all 24 seating permutations in both arms:
20 × 24 × 2 = **960 complete games**.

## Required read-only diagnostics

For each own main-phase decision where at least one `activate_route` action
is legal, record:

- seed, permutation, arm, turn, player id and archetype;
- number of legal route candidates and their selected commodity / path length;
- each route candidate's score, bank-rate benefit and marginal income-portfolio
  delta used by A1;
- best non-route action and its score;
- selected action and score;
- active route commodities, suppression state and income eligibility.

The instrumentation must not mutate game state, candidate order, RNG state,
or scores. Keep the full diagnostic stream separately from normal game logs.

For each game, record:

- route activations, route lengths, activation turns and chosen commodities;
- route-income units by commodity and player;
- suppression events and total suppressed owner-turns;
- bank trades by resource and exchange rate;
- P2P offers and accepted deals;
- road, outpost and metropolis construction;
- trade-to-build links within the same turn, next own turn and next three own turns;
- ending turn, final VP and winner by archetype and seat.

## QC gates

Before comparing outcomes:

1. Both arms must complete 480/480 games, with no game errors.
2. Every seed/permutation pair must have identical map MD5 across arms.
3. No Blight events or Blight plugin in either arm.
4. TradeRoutesMod must be installed in every game in both arms.
5. Route candidate instrumentation must be read-only and show no plugin errors.
6. At least the 24 permutations for one seed must be reproducible from each
   arm's pinned code and official preset.
7. Verify source diff: the only runtime behavior change is route scoring.

If any gate fails, stop and repair the harness before running the official set.

## Primary and guardrail outcomes

Use paired bootstrap over the 20 seed clusters (all 24 permutations stay
together). Save the bootstrap method, replicate count, RNG seed and interval
method. Report point estimates, 95% intervals and per-seed direction counts.

Primary descriptive outcomes:
- route activation rate when a legal route candidate exists;
- route-income units by commodity, including the distribution across resource
  types rather than only total units;
- fraction of route activations whose commodity was missing from the active
  goal at activation;
- same-turn / next-own-turn / three-own-turn construction after route income.

Guardrails:
- outposts and metropolises per game;
- final VP per player;
- accepted P2P deals and bank-trade usage;
- game length and winner distribution by archetype and seat.

Do not declare success from activation counts alone. A1 should demonstrate
that it chooses more useful resource mixes and that the additional route
income is not purchased at the expense of meaningful construction or final VP.

For non-inferiority, use the predeclared guardrail margins:
- outposts: −0.25 per game;
- metropolises: −0.25 per game;
- final VP/player: −0.20.
A metric passes only if the lower endpoint of its paired 95% interval is above
the relevant margin. For improvement claims, the lower endpoint must be above
zero. A confidence interval crossing zero is inconclusive, not proof of no
effect. Do not retune on the held-out test seeds.

## Evidence package

Upload compact evidence to a separate audit branch:
- manifest with pinned source SHAs, config and exact seeds;
- QC report and map pairing results;
- per-game and per-seed CSV;
- route-candidate diagnostics (compressed JSONL is acceptable);
- per-archetype summary;
- analysis script and exact command;
- SHA-256 sums;
- final report including all failed or discarded harness attempts.

No code edits or commits by the runtime agent. The scoring implementation remains
owned by the coordinator.
