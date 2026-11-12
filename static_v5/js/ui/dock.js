import { h, replace, renderIf } from "../lib/dom.js";
import { icon, resIcon } from "../glyphs.js";
import { RESOURCES, RES, COSTS } from "../config.js";
import { buildStates, hasLegal, isMyTurn, resourceDelta } from "../selectors.js";

const BUILD = [
  ["build_route", "route", "Тракт", "1"],
  ["build_outpost", "outpost", "Поселение", "2"],
  ["build_metropolis", "metropolis", "Метрополия", "3"],
  ["buy_charter", "charter", "Патент", "4"],
];

export function createDock(host, handlers) {
  const hand = h("div", { class: "hand", role: "list", "aria-label": "Ваши ресурсы" });
  const actions = h("div", { class: "actions" });
  const limits = h("div", { class: "limits", "aria-label": "Сколько ещё можно построить" });
  host.append(h("div", { class: "hudbar" }, hand, limits), actions);
  const cards = {};
  for (const r of RESOURCES) {
    cards[r] = {
      num: h("b", { class: "num" }, "0"),
      el: h("div", { class: `rcard res-${r}`, role: "listitem", title: RES[r].ru }),
    };
    cards[r].el.append(h("span", { html: resIcon(r) }), cards[r].num, h("small", null, RES[r].ru));
    hand.append(cards[r].el);
  }
  let prevRes = null, prevVersion = -1;

  function renderHand(g) {
    const res = g.me?.resources || {};
    const delta = prevRes && g.state_version !== prevVersion ? resourceDelta(prevRes, res) : {};
    for (const r of RESOURCES) {
      const n = res[r] || 0, c = cards[r];
      c.num.textContent = n; c.el.classList.toggle("zero", n === 0);
      if (delta[r]) {
        c.el.classList.remove("bump"); void c.el.offsetWidth; c.el.classList.add("bump");
        const d = h("span", { class: `delta${delta[r] < 0 ? " neg" : ""}` }, `${delta[r] > 0 ? "+" : ""}${delta[r]}`);
        c.el.append(d); setTimeout(() => d.remove(), 1700);
      }
    }
    prevRes = { ...res }; prevVersion = g.state_version;
    return delta;
  }

  const LIMITS = [["route", "routes_left", "Трактов"], ["outpost", "outposts_left", "Поселений"], ["metropolis", "metropolises_left", "Метрополий"]];
  function renderLimits(player) {
    replace(limits, LIMITS.map(([ic, key, label]) => {
      const n = player?.[key] ?? 0;
      return h("span", { class: n === 0 ? "zero" : "", title: `${label} можно построить: ${n}` }, h("i", { html: icon(ic) }), h("b", { class: "num" }, n));
    }));
  }

  function costRow(type, res, free) {
    if (free) return h("div", { class: "cost" }, h("i", null, "бесплатно"));
    const cost = COSTS[type];
    return h("div", { class: "cost" }, RESOURCES.filter((r) => cost[r]).map((r) =>
      h("i", { class: (res[r] || 0) < cost[r] ? "lack" : "", html: `${cost[r]}${resIcon(r)}` })));
  }

  function render(state) {
    const g = state.game; if (!g) return null;
    const delta = renderHand(g);
    const mine = isMyTurn(g), bs = buildStates(g), mode = state.ui.mode, res = g.me?.resources || {};
    const legalKey = JSON.stringify((g.legal_actions || []).map((a) => [a.type, a.params || {}]));
    const key = JSON.stringify([g.phase, mine, mode, res, legalKey, g.rules?.blight_mode, g.rules?.cult_visibility, g.me?.free_routes_pending, Object.values(bs).map((s) => s.count)]);
    host.hidden = false;
    renderLimits(g.me);
    renderIf(actions, `${key}|${g.current_player}`, () => { buildActions(); if (!actions.children.length) replace(actions, [waitChip()]); });
    return delta;

    function waitChip() {
      const who = (g.players || []).find((p) => p.player_id === g.current_player);
      return h("div", { class: "turn-wait" }, h("i", { style: { background: who?.color || "var(--muted)" } }), mine ? "Ваш ход" : `Ходит ${who?.name ?? "соперник"}…`);
    }
    function buildActions() {
    const nodes = [];
    if (hasLegal(g, "roll_dice"))
      nodes.push(h("button", { class: "abtn go", onClick: handlers.onRoll, title: "Пробел" }, h("span", { html: icon("dice") }), "Бросить", h("div", { class: "cost" }, h("i", null, "Пробел"))));
    for (const [type, ic, label, key] of BUILD) {
      const s = bs[type], active = mode?.type === type;
      if (!s.legal && !active) continue; // show only what can be done right now
      const btn = h("button", {
        class: `abtn${active ? " active" : ""}`, disabled: !s.legal && !active, title: s.legal ? `${label} (${key})` : s.reason,
        "aria-pressed": active ? "true" : "false",
        onClick: () => (type === "buy_charter" ? handlers.onBuy() : handlers.onMode(active ? null : { type })),
      }, h("span", { html: icon(ic) }), label, costRow(type, res, s.free));
      if (s.count > 1 && type !== "buy_charter") btn.append(h("span", { class: "badge" }, s.count));
      nodes.push(btn);
    }
    for (const [type, label, hint] of [["purify_hex", "Очистить", "1 зерно"], ["corrupt_hex", "Осквернить", "1 шерсть"]]) {
      if (!hasLegal(g, type)) continue;
      const active = mode?.type === type;
      nodes.push(h("button", { class: `abtn${active ? " active" : ""}`, onClick: () => handlers.onMode(active ? null : { type }), title: `${label} сектор (${hint})`, "aria-pressed": String(active) },
        h("span", { html: icon("blight") }), label, h("div", { class: "cost" }, h("i", null, hint))));
    }
    const accusations = (g.legal_actions || []).filter((a) => a.type === "accuse_cultist");
    for (const action of accusations) {
      const targetId = Number(action.params?.target_player_id);
      const target = (g.players || []).find((p) => Number(p.player_id) === targetId);
      if (!target) continue;
      nodes.push(h("button", { class: "abtn accuse-cultist", onClick: () => handlers.onAccuseCultist(targetId), title: `Обличить ${target.name}`, "aria-label": `Обличить ${target.name}` },
        h("span", { class: "accuse-target-dot", style: { background: target.color }, "aria-hidden": "true" }),
        h("span", { html: icon("blight") }), `Обличить ${target.name}`));
    }
    if (mine && g.phase === "main")
      nodes.push(h("button", { class: "abtn", onClick: handlers.onTrade, title: "Обмен (T)" }, h("span", { html: icon("trade") }), "Обмен", h("div", { class: "cost" }, h("i", null, "T"))));
    if (hasLegal(g, "end_turn"))
      nodes.push(h("button", { class: "abtn go", onClick: handlers.onEnd, title: "E или Enter" }, h("span", { html: icon("end") }), "Конец хода", h("div", { class: "cost" }, h("i", null, "E"))));
    replace(actions, nodes); tidyLabels(actions);
    }
  }
  const SHORT = { "Поселение": "Посел.", "Осквернить": "Оскв.", "Очистить": "Очист.", "Конец хода": "Конец", "Метрополия": "Город" };
  /** Wrap bare label text so it can ellipsize; on phones use short names (full name stays in the tooltip). */
  function tidyLabels(root) {
    const short = window.matchMedia("(max-width: 860px)").matches;
    for (const b of root.querySelectorAll(".abtn")) for (const n of [...b.childNodes]) {
      if (n.nodeType !== 3 || !n.textContent.trim()) continue;
      const full = n.textContent.trim(); b.title = b.title || full;
      n.replaceWith(h("span", { class: "lbl" }, short ? SHORT[full] || full : full));
    }
  }
  return { render };
}
