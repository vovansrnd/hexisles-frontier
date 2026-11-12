// Zero-dependency unit tests: run with `node --test tests/` from the static_v4 folder.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { hexCenter, hexCorners, boardBounds, fitView, clampView, outward, R } from "../js/geometry.js";
import * as sel from "../js/selectors.js";
import { createStore, watch } from "../js/store.js";
import { plural, fmtCost, COSTS } from "../js/config.js";

const fx = (n) => JSON.parse(readFileSync(new URL(`../fixtures/${n}.json`, import.meta.url)));

test("hex corners coincide with the server's vertices (real state)", () => {
  const st = fx("main_mid"), verts = st.vertices;
  let checked = 0;
  for (const h of st.hexes.filter((x) => x.revealed)) {
    const c = hexCenter(h.q, h.r);
    for (const p of hexCorners(c.x, c.y)) {
      checked++;
      assert.ok(verts.some((v) => Math.abs(v.x - p.x) < 0.6 && Math.abs(v.y - p.y) < 0.6), `corner ${p.x},${p.y}`);
    }
  }
  assert.ok(checked >= 60);
});

test("bounds, fit and clamp behave", () => {
  const b = boardBounds(fx("main_mid").hexes);
  assert.ok(b.w > 600 && b.h > 500);
  const v = fitView(b, 800, 600);
  assert.ok(v.k > 0.5 && v.k < 2);
  const far = clampView({ k: 1, tx: 99999, ty: -99999 }, b, 800, 600, 0.35, 3.2);
  assert.ok(far.tx <= 800 - b.x * far.k && far.ty >= -(b.y + b.h) * far.k);
  const o = outward(100, 0, 20); assert.equal(Math.round(o.x), 120);
  assert.equal(R, 50);
});

test("auto mode picks the only board action in forced phases", () => {
  assert.deepEqual(sel.autoMode(fx("setup_outpost")), { type: "setup_outpost" });
  assert.deepEqual(sel.autoMode(fx("setup_route")), { type: "setup_route" });
  assert.deepEqual(sel.autoMode(fx("raider_move")), { type: "move_raider" });
  assert.equal(sel.autoMode(fx("main_mid")), null);
});

test("board targets follow the mode filter", () => {
  const g = fx("setup_outpost"), t = sel.boardTargets(g, { type: "setup_outpost" });
  assert.ok(t.vertex.size > 20 && t.edge.size === 0 && t.hex.size === 0);
  const all = sel.boardTargets(fx("main_mid"), null);
  assert.ok(all.hex.size > 0, "purify/corrupt hexes");
  const none = sel.boardTargets(fx("main_mid"), { type: "build_route" });
  assert.equal(none.edge.size, 0);
});

test("build states explain why a button is disabled", () => {
  const bs = sel.buildStates(fx("main_mid"));
  assert.equal(bs.build_metropolis.legal, false);
  assert.match(bs.build_metropolis.reason, /Не хватает|Нет подходящего/);
  assert.equal(sel.buildStates(fx("setup_outpost")).build_route.legal, false);
});

test("discard helpers match the engine's combinations", () => {
  const g = fx("discard"), { required, combos } = sel.discardInfo(g);
  assert.ok(required > 0 && combos.length > 0);
  assert.equal(sel.sum(combos[0]), required);
  assert.ok(sel.findDiscardMatch(combos[0], combos));
  const sug = sel.suggestDiscard(g.me.resources, required);
  assert.equal(sel.sum(sug), required);
  for (const r of Object.keys(sug)) assert.ok(sug[r] <= g.me.resources[r]);
  assert.equal(sel.findDiscardMatch({ wood: 99 }, combos), null);
});

test("resource delta, roll hexes, standings, log categories", () => {
  assert.deepEqual(sel.resourceDelta({ wood: 1, ore: 2 }, { wood: 3, ore: 0 }), { wood: 2, ore: -2 });
  const g = fx("main_mid"); g.dice = [3, 3];
  for (const id of sel.hexesForRoll(g)) assert.equal(g.hexes.find((h) => h.hex_id === id).number_token, 6);
  g.dice = [3, 4]; assert.deepEqual(sel.hexesForRoll(g), []);
  const s = sel.standings(fx("finished"));
  for (let i = 1; i < s.length; i++) assert.ok(s[i - 1].victory_points >= s[i].victory_points);
  assert.equal(sel.logCategory({ msg: "🔄 бот: 4×овца → дерево" }), "trade");
  assert.equal(sel.logCategory({ msg: "🏴‍☠️ рейдер" }), "raider");
  assert.equal(sel.logCategory({ msg: "🎲 бросок" }), "noise");
});

test("trade options mirror legal actions; offers keep the 1:1 shape", () => {
  const g = fx("main_mid"), p2p = sel.p2pOptions(g);
  for (const [give, set] of p2p) { assert.ok(!set.has(give)); }
  assert.ok(sel.bankOptions(fx("offer_incoming")) instanceof Map);
  const o = fx("offer_incoming").trade_offer;
  assert.equal(Object.keys(o.give).length, 1); assert.equal(Object.keys(o.want).length, 1);
});

test("phase hints", () => {
  assert.match(sel.phaseHint(fx("roll")), /кубик/i);
  assert.match(sel.phaseHint(fx("discard")), /Сбросьте/);
  assert.match(sel.phaseHint(fx("finished")), /Побед/);
});

test("store: immutable updates and watch()", () => {
  const s = createStore({ a: 1, b: { x: 1 } }); const seen = [];
  watch(s, (st) => st.b.x, (v) => seen.push(v));
  s.set({ a: 2 }); s.set((st) => ({ b: { ...st.b, x: 5 } })); s.set((st) => ({ b: st.b }));
  assert.deepEqual(seen, [5]); assert.equal(s.get().a, 2);
});

test("config helpers", () => {
  assert.equal(plural(1, "карта", "карты", "карт"), "карта"); assert.equal(plural(3, "карта", "карты", "карт"), "карты"); assert.equal(plural(11, "карта", "карты", "карт"), "карт");
  assert.equal(fmtCost(COSTS.build_metropolis), "2 зерна, 3 руды");
});
