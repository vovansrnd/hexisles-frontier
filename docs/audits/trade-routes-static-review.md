# Trade Routes — static audit and integrity patch

Status: experimental branch `experiment/trade-routes-integrity`, based directly on
`main@359037100964becc8be77e5e8125d7f65755a209`. Nothing in this document is
a production adoption decision.

## Rules and economic structure observed in code

The route module in `mods_pack.py` currently uses these defaults:

- Up to three route records per player.
- A route is a simple path of 2–6 player-owned roads connecting two of the
  player's buildings. The route cannot pass through an opponent-owned building.
- The player selects a commodity visible along at least one productive hex
  bordering the path.
- Activation costs one wool and one grain.
- The resource-income delay is one table round.
- By default, the player receives one route-income unit per own turn across
  all ready routes. Setting `one_income_per_turn=False` changes this to one
  income unit per ready active route.
- A live route can improve the bank exchange rate for its selected commodity
  to at most 3:1, unless a port or trade pact already provides a better rate.
- A route can be activated once. Suppression does not delete its registration.
- Moving the raider onto a hex bordering a route edge suppresses that route.

The rule design creates several interacting costs and benefits: expansion roads
and endpoint buildings are required before activation; the activation itself
consumes two resources; income is delayed; bank-rate benefit depends on ports
and trade pacts; and the path can be disrupted by an opponent building or the
raider.

## Confirmed integrity problems fixed on this branch

### 1. Active paths were not revalidated

Before this patch, income and bank-rate code trusted the stored route record.
If an opponent later built on an intermediate vertex, the base road network
became disconnected but the registered route continued to generate income
and grant its commodity's bank-rate benefit.

The patch adds `_route_path_is_open`. It validates endpoint ownership,
edge ownership, path continuity and simplicity, and rejects opponent-owned
intermediate vertices. A broken route remains registered (so the unique-route
limit is not reset), but provides neither income nor a bank-rate benefit while
the path is broken. The same live-path check is used by the AI route scorer.

### 2. Suppression duration did not match the "rounds" setting

The suppression reader treats `suppressed_until` as an exclusive expiry.
The former assignment did not reliably cover a full table round, especially
for explicit `suppression_rounds` values, and did not guarantee that every
player's next own-turn income start was suppressed.

The patch defines one round as one turn per seat, uses the explicit
`suppression_rounds` value when provided (one full round by default), and
sets the exclusive expiry one turn beyond the final suppressed turn. The
next N own-turn starts for every player are therefore blocked, including the
player who moved the raider. The route resumes on the first owner turn after
the configured full rounds have elapsed.

### 3. Per-route income mode incorrectly rejected duplicate commodities

The AI scorer rejected a second live route for the same commodity regardless
of `one_income_per_turn`. That made `one_income_per_turn=False` internally
inconsistent: the rules paid every route separately, but the bot refused to
activate a second same-resource route even when it was productive.

The patch applies that same-commodity rejection only in shared-income mode.
In per-route income mode, a second route may be scored because it adds a
separate income stream. Regression coverage verifies that every ready route
actually pays, including same-resource duplicates.

### 4. Shared-income scoring ignored the marginal value of resource diversity

The default income cap pays one unit per owner turn across the player's
unsuppressed routes, rotating the resource supplied. The previous scorer treated
this as a binary benefit: a route was worth +0.85 if there was no existing live
route, otherwise its income benefit was zero. This undervalued a second route
that supplies a different, currently scarce resource, and could reject it
entirely when a port or pact already provided an equal/better bank rate.

The scorer now values the change in the average marginal utility of the route
income portfolio. It compares the candidate commodity's current resource value
with the average value of the unsuppressed active route commodities. The first
route retains the existing +0.85 score bonus; a subsequent route receives a
positive/negative bounded marginal portfolio delta. A route with no bank-rate
benefit and non-positive marginal income value remains rejected. The game
rules, shared one-unit cap, payout rotation and activation cost are unchanged.

This is a testable AI-scoring hypothesis, not yet an experimentally validated
balance improvement. The next stage must compare old and new scorers on paired
maps and inspect trade-to-build, resource bottlenecks and final outcomes.

## Added regression coverage

`tests/test_routes_integration.py` now creates real owned-road paths and tests:

- JSON action round-trip and route activation.
- Fair rotation of the shared one-unit income slot.
- Raider suppression skipping an affected route.
- A rival building cutting an activated path: no further income and no bank
  discount.
- Two configured suppression rounds covering the owner's next two turn starts.
- Per-route income allowing a second route of the same commodity and paying
  both ready routes, while the shared-income cap rejects a redundant live
  duplicate and pays only one unit per owner turn.
- Shared-income scoring values a second route that adds a scarce commodity
  to the income portfolio, but rejects a lower-value commodity that dilutes it
  when there is no bank-rate benefit.

## Remaining design questions — not changed in this patch

These require empirical game experiments, not additional assumptions in code:

1. **Income cap versus resource diversity.** With the shared one-unit cap, a
   second route does not increase total income per own turn, but may diversify
   which commodity arrives. The current scorer does not explicitly value the
   portfolio/diversification effect.
2. **Redundancy and disruption risk.** Duplicate routes can insure against
   suppression, but the existing redundancy term is narrow and does not model
   the probability or expected duration of a future interruption.
3. **Route length.** The scorer rewards longer paths, while longer paths may
   expose more edges to raider disruption and may represent more prior road
   investment. Whether this is a bad incentive needs trace analysis.
4. **Opportunity cost.** Route activation consumes wool and grain that may be
   needed for construction or other actions. The scorer uses a resource-weight
   callback, but it does not yet value the full discounted future route stream
   or the remaining game horizon.
5. **Competitive blocking.** A rival can permanently interrupt a route by
   establishing a building at an intermediate vertex. The strategic value of
   creating or contesting such choke points has not been measured.
6. **Ports and pacts.** Their interaction with route income and the bank-rate
   benefit needs an explicit payoff table and bot experiment.
7. **Fog.** Commodity choices are limited to what the owner can see, but the
   consequences of partial visibility on route activation and future yield
   have not been measured.
8. **Mod interaction.** Blight and route suppression should be studied
   separately and together after the base route economy is characterized.

## Next validation

This branch is intentionally isolated from the trade-policy model branch and
from `main`. The runtime agent must run the full test suite and a focused
route-enabled smoke, including real path interruption, default suppression and
explicit multi-round suppression, with Blight disabled. The agent must not
change code or commit. Only after that validation should broader economic
experiments be designed.
