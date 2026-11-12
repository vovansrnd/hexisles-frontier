// Application wiring: store ⇄ network ⇄ renderers. Every user intent ends in `act()`, which sends one
// game action and reports the server's verdict; the UI never mutates game state locally.
import { createStore, initialState } from "./store.js";
import { createNet } from "./net.js";
import { createBoard } from "./board.js";
import { defsMarkup } from "./glyphs.js";
import { fx } from "./fx.js";
import { STORAGE_KEYS, RES } from "./config.js";
import { $, h } from "./lib/dom.js";
import { inviteUrl as buildInviteUrl, copyText as copyWithFallback } from "./lib/invite.js";
import * as sel from "./selectors.js";
import { createPlayers } from "./ui/players.js";
import { createDock } from "./ui/dock.js";
import { createHud } from "./ui/hud.js";
import { createSide } from "./ui/side.js";
import { createModals } from "./ui/modals.js";
import { createLobby } from "./ui/lobby.js";
import { createWaiting } from "./ui/waiting.js";

document.body.insertAdjacentHTML("afterbegin", defsMarkup());
const params = new URLSearchParams(location.search);
const store = createStore(initialState());
const ls = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
};

/** Full invite link: current page + ?join=ROOM (keeps the ws override if any). */
function inviteUrl(roomId) {
  return buildInviteUrl(location.origin, location.pathname, roomId, { ws: params.get("ws") });
}
function copyText(text, okMsg) {
  copyWithFallback(text, {
    clipboard: navigator.clipboard,
    execCommand: (value) => {
      const ta = h("textarea", { style: { position: "fixed", opacity: 0 } });
      ta.value = value; document.body.append(ta); ta.select();
      try { return document.execCommand("copy"); } finally { ta.remove(); }
    },
    notify: (value, ok) => {
      if (ok) fx.toast(okMsg, "ok");
      else fx.toast(`Не удалось скопировать — ссылка: ${value}`, "err");
    },
  });
}

// ---------- settings ----------
const saved = ls.get(STORAGE_KEYS.settings) || {};
store.set((s) => ({ ui: { ...s.ui, theme: "night", sound: !!saved.sound, motion: saved.motion || "on", patterns: !!saved.patterns, detail: !!saved.detail } }));
function applySettings(ui, fromUserGesture = false) {
  document.documentElement.dataset.theme = "night"; document.documentElement.dataset.motion = ui.motion;
  fx.setSound(ui.sound, fromUserGesture);
}
applySettings(store.get().ui);
let resume = ls.get(STORAGE_KEYS.session);
let wantResume = false;

// ---------- network ----------
const wsPort = document.querySelector('meta[name="ws-port"]')?.content || "";
const demo = params.get("demo");
const wsUrl = params.get("ws") || (/^\d+$/.test(wsPort) ? `ws://${location.hostname}:${wsPort}` : null);
const net = demo ? null : wsUrl ? createNet({
  url: wsUrl,
  getSession: () => (wantResume ? resume : null),
  onState: (data) => acceptState(data),
  onEvent: onEvent,
  onStatus: (conn) => { store.set({ conn }); tryAutoJoin(); },
}) : null;

function onEvent(msg) {
  if (["room_created", "joined", "reconnected"].includes(msg.type)) {
    resume = { room_id: msg.room_id, player_id: msg.player_id, token: msg.reconnect_token || resume?.token };
    ls.set(STORAGE_KEYS.session, resume);
    store.set({ session: resume, screen: "game" }); lobby.setError("");
    // Replace ?join=CODE with the room in the address bar so a reload resumes cleanly.
    if (joinCode) { const u = new URL(location); u.searchParams.delete("join"); history.replaceState(null, "", u); joinCode = ""; }
  } else if (msg.type === "error") {
    const text = msg.message || msg.error || "Ошибка";
    if (store.get().screen === "lobby") { lobby.setError(text); if (wantResume) { ls.del(STORAGE_KEYS.session); resume = null; wantResume = false; lobby.setResume(null); } }
    else fx.toast(text, "err");
    fx.play("error");
  }
}

function enrich(g) {
  const bots = new Set(g.bot_pids || []);
  g.players = (g.players || []).map((p) => ({ ...p, is_bot: p.is_bot ?? bots.has(p.player_id), bot_type: p.bot_type ?? g.bot_types?.[p.player_id] }));
  return g;
}

function acceptState(data) {
  const g = enrich(data), s = store.get(), prev = s.game;
  if (prev && prev.room_id === g.room_id) sideEffects(prev, g);
  store.set((st) => {
    let ui = st.ui;
    if (ui.mode && !stillValid(g, ui.mode)) ui = { ...ui, mode: null };
    // A bought "Триумф" stays pending for the whole game and is revealed by the
    // victory modal; it clears only when a fresh game (or room) starts.
    const freshGame = !prev || prev.room_id !== g.room_id
      || prev.phase === "lobby" || (prev.phase === "finished" && g.phase !== "finished");
    if (freshGame && g.phase !== "finished" && ui.pendingTriumph) ui = { ...ui, pendingTriumph: 0 };
    return { prev: prev && prev.room_id === g.room_id ? prev : null, game: g, screen: "game", ui };
  });
}
function stillValid(g, mode) { return sel.legalActions(g).some((a) => sel.matchesMode(a, mode)); }

// ---------- charter reveal: action_result first, state diff as a safety net ----------
// The drawn card normally arrives in the action_result of buy_charter. If that
// result is lost (timeout, reconnect), a growing hand still reveals the card —
// the hand contents are visible to their owner.
let lastHandSig = "", charterRevealArmed = false;
function handSig(g) { return (g.me?.charters || []).map((c) => c.type).sort().join(","); }
function revealCharter(type) {
  // "Триумф" is a hidden VP: it stays pending until the victory modal (v3 behavior).
  if (type === "triumph") setUi({ pendingTriumph: (store.get().ui.pendingTriumph || 0) + 1 });
  else modals.charterReveal(type);
}
function charterFallback(prev, g) {
  const sig = handSig(g), prevSig = handSig(prev);
  if (sig === prevSig) return;
  const grew = (g.me?.charters || []).length > (prev.me?.charters || []).length;
  if (grew && !charterRevealArmed && sig !== lastHandSig) {
    const counts = {};
    for (const c of g.me?.charters || []) counts[c.type] = (counts[c.type] || 0) + 1;
    for (const c of prev.me?.charters || []) counts[c.type] = (counts[c.type] || 0) - 1;
    const added = Object.keys(counts).find((k) => counts[k] > 0);
    if (added) revealCharter(added);
  }
  charterRevealArmed = false;
  lastHandSig = sig;
}

/** Phone navigation: visible way out of the panel, Back button that never drops the player out of a game, players overlay. */
let backGuarded = false, backArmedAt = 0;
function armBackGuard() {
  if (els.game.hidden) { backGuarded = false; return; }
  if (!backGuarded) { history.pushState({ game: 1 }, ""); backGuarded = true; }
}
function initMobileNav() {
  const stage = $("#stage"), home = els.sidePlayers.parentNode, nextSibling = els.sidePlayers.nextSibling;
  const closePlayers = () => document.body.classList.remove("players-open");
  const placePlayers = () => { // phones: the full player cards live over the map; wide screens: in the side column
    if (compact()) { if (els.sidePlayers.parentNode !== stage) stage.append(els.sidePlayers); }
    else { closePlayers(); if (els.sidePlayers.parentNode !== home) home.insertBefore(els.sidePlayers, nextSibling); }
  };
  window.matchMedia("(max-width: 860px)").addEventListener("change", placePlayers); placePlayers();
  els.chips.addEventListener("click", () => { if (compact()) document.body.classList.toggle("players-open"); });
  stage.addEventListener("pointerdown", (e) => { // tapping the map closes whatever floats above it
    if (e.target.closest("#players-chips, #side-players")) return;
    closePlayers(); if (store.get().ui.drawer) setUi({ drawer: false });
  }, true);
  $("#board-host").addEventListener("viewchange", (e) => document.querySelector(".zoom")?.classList.toggle("is-moved", e.detail.moved));
  window.addEventListener("popstate", () => {
    if (els.game.hidden) return;
    const ui = store.get().ui;
    if (ui.drawer || document.body.classList.contains("players-open")) { history.pushState({ game: 1 }, ""); closePlayers(); setUi({ drawer: false }); return; }
    if (Date.now() - backArmedAt < 2200) { backGuarded = false; history.back(); return; }
    backArmedAt = Date.now(); history.pushState({ game: 1 }, ""); fx.toast("Нажмите «Назад» ещё раз, чтобы выйти из игры");
  });
}
window.addEventListener("load", initMobileNav, { once: true });

const RARE_LOG = /победил|присоедини|покинул|отключ|вернул/i;
// VP awards, faction flips and suppression-tears announcements deserve a longer read.
const IMPORTANT_LOG = /[🏅☯😈🥀]/u;
function toastNewLog(prev, g) {
  const seen = Math.max(0, ...(prev.log || []).map((e) => e.id || 0));
  for (const e of g.log || []) {
    if ((e.id || 0) <= seen) continue;
    const cat = sel.logCategory(e);
    if (cat === "noise") continue;
    fx.toast(e.msg, cat === "chat" ? "chat" : RARE_LOG.test(e.msg) ? "rare" : "", IMPORTANT_LOG.test(e.msg));
  }
}
function applySideTheme(g) {
  const side = g.rules?.blight_mode === "factions" ? g.me?.blight?.alignment : "";
  if (side === "cult" || side === "order") document.documentElement.dataset.side = side;
  else delete document.documentElement.dataset.side;
}

function sideEffects(prev, g) {
  toastNewLog(prev, g); applySideTheme(g); armBackGuard();
  // Personal Cult feedback (server queues it per player; visible to us only).
  const feed = g.me?.blight?.feedback, prevFeed = prev.me?.blight?.feedback;
  if (feed?.length && prevFeed) {
    const seen = Math.max(0, ...(prevFeed || []).map((f) => f.id || 0));
    for (const f of feed) if ((f.id || 0) > seen) fx.toast(f.text, "", true);
  }
  charterFallback(prev, g);
  const d = (g.dice || []).join(), pd = (prev.dice || []).join();
  if (d !== pd && g.dice?.[0]) { const total = g.dice[0] + g.dice[1]; fx.play(total === 7 ? "seven" : "roll"); }
  if (g.current_player === g.my_id && prev.current_player !== g.my_id && g.phase !== "finished") { fx.play("turn"); fx.toast("Ваш ход"); }
  const gain = sel.resourceDelta(prev.me?.resources, g.me?.resources);
  if (Object.values(gain).some((v) => v > 0)) fx.play("gain");
  const mineBuilt = (a, b) => a.vertices.filter((v) => v.structure_owner === b.my_id).length + a.edges.filter((e) => e.route_owner === b.my_id).length;
  if (mineBuilt(g, g) > mineBuilt(prev, g)) fx.play("build");
  const hint = sel.phaseHint(g);
  if (hint !== sel.phaseHint(prev)) fx.announce(hint);
  if (g.phase === "finished" && prev.phase !== "finished") fx.play(g.winner_id === g.my_id ? "win" : "turn");
}

// ---------- actions ----------
// act() resolves with the server's result payload ({ok, ...data}); callers may use extra
// fields (e.g. `charter` returned by buy_charter) beyond the ok/error verdict.
async function act(type, p = {}) {
  if (demo) { fx.toast("Демо-режим: действия отключены", "err"); return { ok: false }; }
  const res = await net.gameAction(type, p);
  if (res && res.ok === false) { fx.toast(res.error || res.message || "Действие отклонено", "err"); fx.play("error"); return res; }
  store.set((s) => ({ ui: { ...s.ui, mode: null } }));
  return res || { ok: true };
}
const setUi = (patch) => store.set((s) => ({ ui: { ...s.ui, ...patch } }));

function onTarget(kind, id) {
  const s = store.get(), g = s.game, mode = effectiveMode(s);
  const t = sel.boardTargets(g, mode), options = t.actions.get(`${kind}:${id}`) || [];
  if (!options.length) return;
  if (options.length === 1) return act(options[0].type, options[0].params);
  modals.pickResources({ title: "Чем платить?", text: "Для этого действия доступно несколько способов оплаты.", count: 1, allowed: options.map((o) => o.params.pay_resource).filter(Boolean),
    onPick: (picked) => { const r = Object.keys(picked)[0]; const o = options.find((x) => x.params.pay_resource === r); if (o) act(o.type, o.params); } });
}
function effectiveMode(s) { return s.ui.mode || sel.autoMode(s.game); }

const handlers = {
  onRoll: () => act("roll_dice"), onEnd: () => act("end_turn"),
  onBuy: async () => {
    const res = await act("buy_charter");
    if (res?.charter) {
      charterRevealArmed = true; // the state broadcast right after must not re-reveal
      revealCharter(res.charter);
    }
  },
  onPanel: (id) => setUi({ panel: id, drawer: true }),
  onMode: (mode) => { fx.play("click"); setUi({ mode }); },
  onTrade: () => { setUi({ panel: "trade", drawer: true }); },
  onBank: (give, get) => act("trade_bank", { give, receive: get }),
  onPropose: (give, want) => act("propose_trade", { give, want }),
  onRespond: (accept) => act("respond_trade", { accept }),
  onCancelOffer: () => act("cancel_trade"),
  onPreviewRoute: (path) => board.previewRoute(path),
  onActivateRoute: (action) => act(action.type, action.params),
  onAccuseCultist: (targetId) => act("accuse_cultist", { target_player_id: targetId }),
  onDiscard: (resources) => act("discard", { resources }),
  onVictim: (id) => act("choose_victim", { victim_id: id }),
  onChat: (text) => net && net.send({ action: "chat", msg: text }),
  onPlayCharter(card) {
    const t = card.type;
    if (t === "pioneer") { setUi({ mode: { type: "play_charter", charter: "pioneer" }, drawer: false }); fx.toast("Выберите неизведанный сектор"); return; }
    if (t === "abundance") return modals.pickResources({ title: "Рог изобилия", text: "Выберите два ресурса из банка.", count: 2, onPick: (p) => {
      const list = Object.entries(p).flatMap(([r, n]) => Array(n).fill(r)); const o = card.options.find((x) => [x.params.resource1, x.params.resource2].sort().join() === list.sort().join());
      if (o) act(o.type, o.params); else fx.toast("Такой выбор недоступен", "err"); } });
    if (t === "monopoly") return modals.pickResources({ title: "Монополия", text: "Какой ресурс забрать у всех?", count: 1, onPick: (p) => { const r = Object.keys(p)[0]; const o = card.options.find((x) => Object.values(x.params).includes(r)); if (o) act(o.type, o.params); } });
    const o = card.options[0]; if (o) act(o.type, o.params);
  },
  onSetting(key, val) {
    if (key === "theme") val = "night";
    setUi({ [key]: val }); const ui = store.get().ui; applySettings(ui, true);
    ls.set(STORAGE_KEYS.settings, { sound: ui.sound, motion: ui.motion, patterns: ui.patterns, detail: ui.detail });
  },
  onSound: () => handlers.onSetting("sound", !store.get().ui.sound),
  onHelp: () => modals.help(), onSettings: () => modals.settings(),
  onDrawer: () => setUi({ drawer: !store.get().ui.drawer }),
  onZoom: (f) => board.zoomBy(f), onFit: () => board.fit(),
  onCopyRoom() { const id = store.get().game?.room_id || store.get().session?.room_id; if (id) copyText(inviteUrl(id), `Ссылка-приглашение скопирована (${id})`); },
  onCopyInvite() { const id = store.get().game?.room_id || store.get().session?.room_id; if (id) copyText(inviteUrl(id), "Ссылка-приглашение скопирована"); },
  onRestart: () => net && net.send({ action: "restart_game" }), onStart: () => net && net.send({ action: "start_game" }),
  onAddBot: (id) => net && net.send({ action: "add_bot", bot_type: id }),
  onLeave() { ls.del(STORAGE_KEYS.session); net && net.close(); location.href = location.pathname; },
  onConfetti: () => fx.confetti(),
};

// ---------- build UI ----------
const els = { lobby: $("#lobby"), game: $("#game"), top: $("#topbar"), sidePlayers: $("#side-players"), sideTabs: $("#side-tabs"), chips: $("#players-chips"),
  stage: $("#stage"), stageTop: $("#stage-top"), side: $("#side"), dock: $("#dock"), boardHost: $("#board-host"), modal: $("#modal-root") };
const board = createBoard(els.boardHost, { onTarget });
const hud = createHud(els, handlers);
const players = createPlayers(els.sidePlayers, els.chips);
const dock = createDock(els.dock, handlers);
const side = createSide(els.sideTabs, { store, handlers });
const modals = createModals(els.modal, { store, handlers });
const waiting = createWaiting(els.stage, handlers);
const lobby = createLobby(els.lobby, {
  getName: () => ls.get(STORAGE_KEYS.name),
  onCreate: (cfg) => { ls.set(STORAGE_KEYS.name, cfg.name); wantResume = false; sendLobby({ action: "create_room", ...cfg }); },
  onJoin: ({ name, room_id }) => { ls.set(STORAGE_KEYS.name, name); wantResume = false; sendLobby({ action: "join_room", name, room_id }); },
  onResume: () => { wantResume = true; net.close(); net.connect(); },
});
function sendLobby(msg) {
  if (!net) return lobby.setError("Сервер не подключён: откройте игру через server_local (порт веб-сокета не задан).");
  if (!net.send(msg)) lobby.setError("Нет соединения с сервером. Подождите секунду и повторите.");
}
// Offer "Продолжить игру" for a saved session before anything else happens.
if (resume) lobby.setResume(resume);

// ---------- invite links (?join=CODE): one click from a friend joins the room ----------
let joinCode = (params.get("join") || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
let autoJoinTried = false;
function tryAutoJoin() {
  if (autoJoinTried || !joinCode || !net || !net.isOpen()) return;
  autoJoinTried = true;
  // An invite link is explicit intent: it outranks a stale saved session. Only
  // a session from the very same room reconnects instead of joining a seat.
  if (resume && resume.room_id === joinCode) { wantResume = true; net.close(); net.connect(); return; }
  const name = ls.get(STORAGE_KEYS.name);
  if (name) sendLobby({ action: "join_room", name, room_id: joinCode });
}
if (joinCode) { lobby.setInvite(joinCode); tryAutoJoin(); }

// ---------- board insets: keep the fitted map clear of overlays ----------
const compact = () => window.matchMedia("(max-width: 860px)").matches;
function applyInsets() {
  const root = document.documentElement.style;
  if (compact()) {
    const dockH = els.dock.hidden ? 0 : els.dock.offsetHeight;
    root.setProperty("--dock-h", `${dockH}px`);
    root.setProperty("--rail-r", "0px");
    root.setProperty("--chips-h", `${els.chips.offsetTop + els.chips.offsetHeight}px`);
    board.setInsets({ t: els.chips.offsetHeight + 46, b: dockH ? dockH + 18 : 0, l: 0, r: 0 });
  } else {
    // Controls float in the corners: reserve only the hand bar, plus the action column when the stage is too narrow to leave room beside the map.
    root.setProperty("--dock-h", "0px");
    const spare = els.stage.clientWidth - 1.15 * (els.stage.clientHeight - 58);
    const reserveRight = spare < 380 ? 214 : 0;
    root.setProperty("--rail-r", `${reserveRight}px`);
    board.setInsets({ t: 48, b: 50, l: 0, r: reserveRight });
  }
}
new ResizeObserver(applyInsets).observe(els.dock);
new ResizeObserver(applyInsets).observe(els.stage);
new ResizeObserver(applyInsets).observe(els.chips);
window.addEventListener("resize", applyInsets);

// ---------- render loop ----------
let raf = 0, lastRollKey = "";
store.subscribe(() => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; render(store.get()); }); });
function render(s) {
  const inGame = s.screen === "game" && !!s.game;
  els.lobby.hidden = inGame; els.game.hidden = !inGame;
  if (!inGame) return;
  const g = s.game, mode = effectiveMode(s);
  const targets = mode ? sel.boardTargets(g, mode) : null;
  const rollHexes = sel.hexesForRoll(g), rollKey = `${g.turn}:${(g.dice || []).join("-")}:${g.phase === "roll" ? "r" : "m"}`;
  const fresh = rollKey !== lastRollKey && g.phase !== "roll" && g.phase !== "setup"; if (fresh) lastRollKey = rollKey;
  board.update(g, { targets, mode, patterns: s.ui.patterns, rollHexes: fresh ? rollHexes : [], rollKey: fresh ? rollKey : "" });
  hud.render(s); players.render(s); dock.render(s); side.render(s); waiting.render(s);
  els.side.classList.toggle("open", s.ui.drawer); document.body.classList.toggle("drawer-open", s.ui.drawer);
  modals.sync(s);
}

// ---------- keyboard ----------
document.addEventListener("keydown", (e) => {
  if (e.target.matches?.("input, textarea, select") || e.ctrlKey || e.metaKey || e.altKey) return;
  const s = store.get(), g = s.game; if (!g || s.screen !== "game" || modals.isOpen()) return;
  const key = e.key.toLowerCase(), by = sel.legalByType(g);
  if (key === " " && by.has("roll_dice")) { e.preventDefault(); handlers.onRoll(); }
  else if ((key === "e" || (key === "enter" && document.activeElement === document.body)) && by.has("end_turn")) handlers.onEnd();
  else if (["1", "2", "3"].includes(key)) { const type = ["build_route", "build_outpost", "build_metropolis"][+key - 1]; if (by.has(type)) handlers.onMode(s.ui.mode?.type === type ? null : { type }); }
  else if (key === "4" && by.has("buy_charter")) handlers.onBuy();
  else if (key === "t") handlers.onTrade();
  else if (key === "escape") setUi({ mode: null, drawer: false });
  else if (key === "+" || key === "=") handlers.onZoom(1.25);
  else if (key === "-") handlers.onZoom(0.8);
  else if (key === "0") handlers.onFit();
  else if (key === "?" || key === "/") handlers.onHelp();
});

// ---------- boot ----------
if (demo) {
  fetch(`fixtures/${encodeURIComponent(demo)}.json`).then((r) => r.json()).then((data) => { store.set({ conn: "open" }); acceptState(data); });
} else if (net) net.connect();
else store.set({ screen: "lobby" });
window.__ti = { store, handlers, board, sel };   // debugging / tests
