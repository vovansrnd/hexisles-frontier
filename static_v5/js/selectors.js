// Pure functions that turn a server state into what the UI needs. No DOM, fully unit-tested.
import { COSTS, RESOURCES, PHASE_HINT, CHARTERS } from "./config.js";

const TARGET_KEY = { vertex_id: "vertex", edge_id: "edge", hex_id: "hex" };

export const legalActions = (g) => (g && g.legal_actions) || [];
export const isMyTurn = (g) => !!g && g.current_player === g.my_id && g.phase !== "finished";
export const me = (g) => (g && g.me) || null;
export const players = (g) => (g && g.players) || [];
export const playerById = (g, id) => players(g).find((p) => p.player_id === id) || null;

export function legalByType(g) {
  const map = new Map();
  for (const a of legalActions(g)) {
    if (!map.has(a.type)) map.set(a.type, []);
    map.get(a.type).push(a);
  }
  return map;
}

export const hasLegal = (g, type) => legalActions(g).some((a) => a.type === type);

export function canAfford(res, cost) {
  return RESOURCES.every((r) => (res?.[r] || 0) >= (cost[r] || 0));
}
export function missingFor(res, cost) {
  const out = {};
  for (const r of RESOURCES) {
    const need = (cost[r] || 0) - (res?.[r] || 0);
    if (need > 0) out[r] = need;
  }
  return out;
}

/** What each build button shows: legal now / affordable but nowhere to build / cannot afford. */
export function buildStates(g) {
  const res = g?.me?.resources || {};
  const by = legalByType(g);
  const out = {};
  for (const type of Object.keys(COSTS)) {
    const legal = by.has(type);
    const afford = canAfford(res, COSTS[type]);
    const free = type === "build_route" && (g?.me?.free_routes_pending || 0) > 0;
    let reason = "";
    if (!legal) {
      if (!isMyTurn(g)) reason = "Сейчас не ваш ход";
      else if (g.phase !== "main") reason = "Сначала завершите текущее действие";
      else if (!afford && !free) reason = "Не хватает ресурсов";
      else if (type === "buy_charter") reason = "Колода патентов пуста";
      else reason = "Нет подходящего места";
    }
    out[type] = { legal, afford: afford || free, free, reason, count: by.get(type)?.length || 0 };
  }
  return out;
}

/** Board targets for the given mode (or all legal board targets when mode is null). */
export function boardTargets(g, mode) {
  const sets = { vertex: new Set(), edge: new Set(), hex: new Set() };
  const actions = new Map();      // `${kind}:${id}` -> [actions]
  for (const a of legalActions(g)) {
    if (mode && !matchesMode(a, mode)) continue;
    for (const [param, kind] of Object.entries(TARGET_KEY)) {
      if (a.params && a.params[param] !== undefined) {
        const id = a.params[param];
        sets[kind].add(id);
        const k = `${kind}:${id}`;
        if (!actions.has(k)) actions.set(k, []);
        actions.get(k).push(a);
      }
    }
  }
  return { ...sets, actions };
}

export function matchesMode(a, mode) {
  if (a.type !== mode.type) return false;
  if (mode.charter && a.params?.charter_type !== mode.charter) return false;
  return true;
}

/** If the position has exactly one kind of board action, select it automatically. */
export function autoMode(g) {
  if (!g) return null;
  const board = new Map();
  for (const a of legalActions(g)) {
    const hasTarget = a.params && Object.keys(TARGET_KEY).some((k) => a.params[k] !== undefined);
    if (hasTarget && !["purify_hex", "corrupt_hex", "play_charter"].includes(a.type)) board.set(a.type, a);
  }
  const forced = ["setup_outpost", "setup_route", "move_raider"];
  for (const t of forced) if (board.has(t)) return { type: t };
  if ((g.me?.free_routes_pending || 0) > 0 && board.has("build_route")) return { type: "build_route" };
  return null;
}

export function phaseHint(g) {
  if (!g) return "";
  if (g.phase === "finished") return g.winner_id === g.my_id ? "Вы победили!" : `Победил ${playerById(g, g.winner_id)?.name || "соперник"}`;
  if (g.phase === "discard") return g.pending_discards > 0 ? `Сбросьте ${g.pending_discards} карт` : "Соперники сбрасывают карты…";
  if (!isMyTurn(g) && g.phase !== "lobby" && !(g.phase === "discard")) {
    const who = playerById(g, g.current_player);
    return `Ходит ${who?.name || "соперник"}`;
  }
  if (g.phase === "main" && (g.me?.free_routes_pending || 0) > 0) return `Бесплатные тракты: ${g.me.free_routes_pending}`;
  return PHASE_HINT[g.phase] || "";
}

/** Bank trades the engine currently allows: Map<give, Set<receive>> plus rate per give resource. */
export function bankOptions(g) {
  const out = new Map();
  for (const a of legalActions(g)) {
    if (a.type !== "trade_bank") continue;
    const give = a.params.give;
    if (!out.has(give)) out.set(give, new Set());
    out.get(give).add(a.params.receive);
  }
  return out;
}

/** Player trades are 1:1 only (the engine enumerates exactly these). */
export function p2pOptions(g) {
  const out = new Map();
  for (const a of legalActions(g)) {
    if (a.type !== "propose_trade") continue;
    const give = Object.keys(a.params.give || {})[0], want = Object.keys(a.params.want || {})[0];
    if (!give || !want) continue;
    if (!out.has(give)) out.set(give, new Set());
    out.get(give).add(want);
  }
  return out;
}

export function charterCards(g) {
  const by = legalByType(g).get("play_charter") || [];
  const playable = new Map();
  for (const a of by) {
    const t = a.params.charter_type;
    if (!playable.has(t)) playable.set(t, []);
    playable.get(t).push(a);
  }
  return (g?.me?.charters || []).map((c) => ({
    ...c, meta: CHARTERS[c.type] || { ru: c.type, text: "" }, options: playable.get(c.type) || [],
  }));
}

/** Discard UI: how many cards to drop, and which exact multisets the engine will accept. */
export function discardInfo(g) {
  const combos = legalActions(g).filter((a) => a.type === "discard").map((a) => a.params.resources || {});
  const required = combos.length ? sum(combos[0]) : g?.pending_discards || 0;
  return { required, combos };
}
export const sum = (m) => Object.values(m || {}).reduce((s, n) => s + (n || 0), 0);
export function sameMultiset(a, b) {
  return RESOURCES.every((r) => (a?.[r] || 0) === (b?.[r] || 0));
}
export function findDiscardMatch(selection, combos) {
  return combos.find((c) => sameMultiset(c, selection)) || null;
}
/** A sensible default: keep what the player can use soonest, drop the largest surplus first. */
export function suggestDiscard(res, required) {
  const left = { ...res };
  const pick = {};
  for (let i = 0; i < required; i++) {
    const r = RESOURCES.filter((x) => (left[x] || 0) > 0).sort((a, b) => left[b] - left[a])[0];
    if (!r) break;
    left[r]--; pick[r] = (pick[r] || 0) + 1;
  }
  return pick;
}

export function resourceDelta(prev, next) {
  const out = {};
  for (const r of RESOURCES) {
    const d = (next?.[r] || 0) - (prev?.[r] || 0);
    if (d) out[r] = d;
  }
  return out;
}

/** Hexes that produce on the last roll (visible, matching number, not blocked by the raider). */
export function hexesForRoll(g) {
  const total = (g?.dice || []).reduce((s, d) => s + d, 0);
  if (!total || total === 7) return [];
  return (g.hexes || []).filter((h) => h.revealed && h.number_token === total).map((h) => h.hex_id);
}

export function standings(g) {
  return [...players(g)].sort((a, b) => b.victory_points - a.victory_points || a.player_id - b.player_id);
}

export function logCategory(entry) {
  const m = entry.msg || "";
  if (m.startsWith("💬")) return "chat";
  if (/^(🔄|📣|🚫|🤝)/.test(m)) return "trade";
  if (/^(🏴|💀|♻️)/u.test(m)) return "raider";
  if (/^(☠️|🌑|✨)/u.test(m)) return "blight";
  if (/^(🎲|📦|⏭️)/u.test(m)) return "noise";
  return "build";
}
