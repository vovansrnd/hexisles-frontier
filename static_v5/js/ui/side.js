import { h, replace, renderIf } from "../lib/dom.js";
import { icon, resIcon } from "../glyphs.js";
import { RESOURCES, RES, COSTS, fmtCost } from "../config.js";
import { bankOptions, p2pOptions, charterCards, isMyTurn, logCategory, hasLegal, sum } from "../selectors.js";

const FILTERS = [["all", "Всё"], ["build", "Ходы"], ["trade", "Торговля"], ["raider", "Рейдер"], ["blight", "Скверна"], ["chat", "Чат"]];

export function createSide(host, ctx) {
  const { store, handlers } = ctx;
  const tabs = h("div", { class: "tabs", role: "tablist" });
  const body = h("div", { style: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0 } });
  host.append(tabs, body);
  const tabDefs = [["info", "help", "Инфо"], ["log", "log", "Журнал"], ["trade", "trade", "Обмен"], ["cards", "charter", "Патенты"]];
  const tabEls = {};
  for (const [id, ic, label] of tabDefs) {
    tabEls[id] = h("button", { class: "tab", role: "tab", id: `tab-${id}`, onClick: () => store.set((s) => ({ ui: { ...s.ui, panel: id } })) }, h("span", { html: icon(ic) }), label);
    tabs.append(tabEls[id]);
  }
  const panes = { info: buildInfo(), log: buildLog(), trade: buildTrade(), cards: buildCards() };
  for (const p of Object.values(panes)) body.append(p.root);

  // ---------------- info: goal, active mods, prices, rules ----------------
  function buildInfo() {
    const root = h("div", { class: "tabpane info" });
    const MODS = [["fog_of_war", "Туман войны"], ["routes", "Торговые маршруты"], ["friendly_raider", "Защита отстающих"], ["fast_start", "Быстрый старт"]];
    const BLIGHT = { environmental: "Стихия", tactical: "Скверна", factions: "Культ ↔ Орден" };
    const PRICES = [["build_route", "Дорога", "route"], ["build_outpost", "Поселение", "outpost"], ["build_metropolis", "Город", "metropolis"], ["buy_charter", "Патент", "charter"]];
    const costChips = (cost) => Object.entries(cost || {}).map(([r, n]) => h("span", { class: "chip", html: `${resIcon(r)}×${n}` }));
    /** Personal VP breakdown: what is public, what the mod gave, what is hidden. */
    function scoreBlock(g) {
      const mePub = (g.players || []).find((p) => p.player_id === g.my_id)?.victory_points ?? 0;
      const b = g.me?.blight || null;
      const triumphs = (g.me?.charters || []).filter((c) => c.type === "triumph").length;
      const rows = [h("div", { class: "score-line" }, h("span", null, "Публичные очки"), h("b", { class: "num" }, mePub))];
      if (b && g.rules?.blight_mode === "factions") {
        const tearsTail = b.tears_vp >= b.vp_cap ? "предел" : `до +1: ${b.tears_vp_step - (b.tears % b.tears_vp_step)}`;
        const purifyTail = b.purify_vp >= b.vp_cap ? "предел" : `до +1: ${b.purify_vp_step - (b.purified_count % b.purify_vp_step)}`;
        rows.push(
          h("div", { class: "score-line", title: `Слёз: ${b.tears}` }, h("span", null, "☠ Слёзы Культа"), h("b", { class: "num" }, `+${b.tears_vp} · ${tearsTail}`)),
          h("div", { class: "score-line", title: `Очисток: ${b.purified_count}` }, h("span", null, "✧ Очистки"), h("b", { class: "num" }, `+${b.purify_vp} · ${purifyTail}`)));
        if (b.accusation_vp) rows.push(h("div", { class: "score-line" }, h("span", null, "🕵 Обличение"), h("b", { class: "num" }, `+${b.accusation_vp}`)));
      }
      if (triumphs) rows.push(h("div", { class: "score-line" }, h("span", null, "🃏 Патенты «Триумф» (скрытые)"), h("b", { class: "num" }, `+${triumphs}`)));
      const total = mePub + (b?.alternate_vp || 0) + (b?.accusation_vp || 0) + triumphs;
      rows.push(h("div", { class: "score-line total" }, h("span", null, "Итого в гонке до цели"), h("b", { class: "num" }, total)));
      return h("div", { class: "info-score" }, h("small", null, "Мои очки"), rows);
    }

    return { root, id: "info", render(state) {
      const g = state.game, rules = g.rules || {}, goal = g.points_to_win ?? 12;
      const mods = MODS.filter(([key]) => rules[key]).map(([, label]) => label);
      if (rules.blight_mode) mods.push(BLIGHT[rules.blight_mode] || rules.blight_mode);
      const mePub = (g.players || []).find((p) => p.player_id === g.my_id)?.victory_points ?? 0;
      const b = g.me?.blight;
      const scoreSig = [mePub, b?.tears, b?.tears_vp, b?.purify_vp, b?.alternate_vp, b?.accusation_vp,
        (g.me?.charters || []).filter((c) => c.type === "triumph").length];
      renderIf(root, JSON.stringify([goal, mods, scoreSig]), () => replace(root,
        scoreBlock(g),
        h("div", { class: "info-top" },
          h("div", null, h("small", null, "Цель"), h("b", { class: "info-goal" }, `🏆 ${goal} очков`)),
          h("div", null, h("small", null, "Моды"), h("div", { class: "chips" }, mods.length ? mods.map((m) => h("span", { class: "chip" }, m)) : h("span", { class: "muted" }, "нет")))),
        h("div", { class: "info-prices" }, h("small", null, "Цены построек"),
          PRICES.map(([key, label, ic]) => h("div", { class: "price-row" },
            h("span", { class: "price-name" }, h("i", { html: icon(ic) }), label), h("span", { class: "price-cost" }, costChips(COSTS[key]))))),
        h("button", { class: "btn info-rules", onClick: handlers.onHelp }, h("span", { html: icon("help") }), "Правила игры")));
    } };
  }

  // ---------------- log + chat ----------------
  function buildLog() {
    const filters = h("div", { class: "filters" });
    const list = h("ol", { class: "log", "aria-live": "off" });
    const scroller = h("div", { class: "tabpane" }, filters, list);
    const input = h("input", { type: "text", placeholder: "Сообщение за столом…", maxlength: 200, "aria-label": "Сообщение", onKeydown: (e) => { if (e.key === "Enter") send(); } });
    const send = () => { const t = input.value.trim(); if (t) { handlers.onChat(t); input.value = ""; } };
    const root = h("div", { style: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0 } }, scroller, h("div", { class: "chatbox" }, input, h("button", { class: "btn btn-primary btn-icon", "aria-label": "Отправить", onClick: send }, h("span", { html: icon("end") }))));
    let lastId = -1, lastFilter = "", lastDetail = null;
    function render(state) {
      const g = state.game, f = state.ui.filter, detail = state.ui.detail;
      replace(filters, FILTERS.map(([id, label]) => h("button", { class: "fchip", "aria-pressed": String(f === id), onClick: () => store.set((s) => ({ ui: { ...s.ui, filter: id } })) }, label)),
        h("button", { class: "fchip", style: { marginLeft: "auto" }, "aria-pressed": String(detail), onClick: () => store.set((s) => ({ ui: { ...s.ui, detail: !s.ui.detail } })) }, "Подробно"));
      const entries = g.log || [];
      const reset = f !== lastFilter || detail !== lastDetail || (entries.length && entries[0].id > lastId + 400);
      if (reset) { list.replaceChildren(); lastId = -1; }
      const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 60;
      for (const e of entries) {
        if (e.id <= lastId) continue;
        lastId = e.id;
        const cat = logCategory(e);
        if (cat === "noise" && !detail) continue;
        if (f !== "all" && cat !== f && !(f === "build" && cat === "noise")) continue;
        list.append(h("li", { class: `${cat}${e.player_id === g.my_id ? " mine" : ""}` }, e.msg));
      }
      while (list.children.length > 300) list.firstChild.remove();
      lastFilter = f; lastDetail = detail;
      if (atBottom || reset) scroller.scrollTop = scroller.scrollHeight;
    }
    return { root, render, id: "log" };
  }

  // ---------------- trade ----------------
  function buildTrade() {
    const root = h("div", { class: "tabpane" });
    const sel = { bankGive: null, bankGet: null, give: {}, want: {} };
    let sub = null;               // active sub-tab: "bank" | "players" | "routes"
    const picker = (kind, key, allowed, counts, need) => h("div", { class: "rpick" }, RESOURCES.map((r) =>
      h("button", { "aria-pressed": String(sel[key] === r), disabled: !allowed.has(r) || (need && (counts[r] || 0) < need(r)), title: RES[r].ru,
        onClick: () => { sel[key] = sel[key] === r ? null : r; rerender(); } },
        h("span", { html: resIcon(r) }), h("b", { class: "num" }, kind === "give" ? (counts[r] || 0) : "·"), RES[r].ru)));
    /** If the engine allows exactly one "give" (or one "get" for the chosen give), choose it for the player. */
    function autoPick(s, giveKey, getKey, options) {
      if (!options.has(s[giveKey]) && options.size === 1) s[giveKey] = [...options.keys()][0];
      const gets = options.get(s[giveKey]);
      if (gets && !gets.has(s[getKey]) && gets.size === 1) s[getKey] = [...gets][0];
    }
    let lastState = null;
    function rerender() { lastState && draw(lastState); }
    const clean = (obj) => Object.fromEntries(Object.entries(obj).filter(([, v]) => v > 0));
    const resList = (obj) => Object.entries(clean(obj)).map(([r, n]) => `${n} ${RES[r].gen}`).join(" + ") || "—";
    /** One compose row: resource, context line and a −/+ stepper. A resource can appear on only one side. */
    function stepperRow(r, obj, max, have, side) {
      const n = obj[r] || 0;
      const other = side === "give" ? sel.want : sel.give;
      const change = (d) => {
        obj[r] = Math.max(0, Math.min(max, n + d));
        if (obj[r] > 0) delete other[r]; else delete obj[r];
        rerender();
      };
      return h("div", { class: "tstep" },
        h("span", { class: `res-${r}`, html: resIcon(r) }),
        h("div", { class: "tstep-main" }, h("b", null, RES[r].ru), h("small", { class: "muted" }, side === "give" ? `в руке: ${have}` : "любое количество")),
        h("div", { class: "ctl" },
          h("button", { "aria-label": `меньше ${RES[r].ru}`, disabled: !n, onClick: () => change(-1) }, h("span", { html: icon("minus") })),
          h("b", { class: "num" }, n),
          h("button", { "aria-label": `больше ${RES[r].ru}`, disabled: n >= max, onClick: () => change(1) }, h("span", { html: icon("plus") }))));
    }
    function bankSection(g, mine, me, bank) {
      const rate = (r) => g.bank_rates?.[r] || 4;
      autoPick(sel, "bankGive", "bankGet", bank);
      if (!bank.has(sel.bankGive)) sel.bankGive = null;
      if (sel.bankGive && !bank.get(sel.bankGive)?.has(sel.bankGet)) sel.bankGet = null;
      const bankGetOk = new Set(sel.bankGive ? bank.get(sel.bankGive) : RESOURCES);
      const canBank = !!(sel.bankGive && sel.bankGet);
      return h("section", { class: "sect" }, h("h3", null, h("span", { html: icon("anchor") }), "Банк и порты"),
        h("div", { class: "label" }, "Отдаю (курс на кнопке)"),
        h("div", { class: "rpick" }, RESOURCES.map((r) => h("button", { "aria-pressed": String(sel.bankGive === r), disabled: !bank.has(r), title: `${RES[r].ru} — ${rate(r)}:1`,
          onClick: () => { sel.bankGive = sel.bankGive === r ? null : r; rerender(); } }, h("span", { html: resIcon(r) }), h("b", { class: "num" }, `${rate(r)}:1`), `${me[r] || 0} шт.`))),
        h("div", { class: "label" }, "Получаю"), picker("get", "bankGet", bankGetOk, me, null),
        h("button", { class: "btn btn-primary", disabled: !canBank || !mine, onClick: async () => { const res = await handlers.onBank(sel.bankGive, sel.bankGet); if (res && res.ok) { sel.bankGive = sel.bankGet = null; rerender(); } } },
          canBank ? `Обменять ${rate(sel.bankGive)} ${RES[sel.bankGive].gen} на 1 ${RES[sel.bankGet].gen}` : "Выберите, что отдать и получить"));
    }
    function playersSection(g, mine, me, p2p) {
      // The engine keeps a single active offer on the table: while any offer is
      // pending, new proposals are rejected — explain instead of dead-ending.
      if (g.trade_offer) {
        const own = Number(g.trade_offer.from_player) === Number(g.my_id);
        return h("section", { class: "sect" }, h("h3", null, h("span", { html: icon("trade") }), "Предложение игрокам"),
          h("p", { class: "muted", style: { margin: "0 0 6px" } }, own
            ? "Ваше предложение на столе — соперники видят его и решают, пока горит таймер."
            : `${g.trade_offer.from_name || "Соперник"} уже предложил обмен — на столе одно предложение.`),
          h("p", { class: "muted", style: { margin: 0, fontSize: "12.5px" } }, own
            ? "Отозвать предложение можно в карточке поверх карты."
            : "Новое предложение можно составить, когда стол освободится."));
      }
      const giveRows = RESOURCES.filter((r) => (me[r] || 0) > 0).map((r) => stepperRow(r, sel.give, me[r] || 0, me[r] || 0, "give"));
      const wantRows = RESOURCES.filter((r) => !(sel.give[r] > 0)).map((r) => stepperRow(r, sel.want, 9, null, "want"));
      const giveOk = sum(clean(sel.give)) > 0, wantOk = sum(clean(sel.want)) > 0;
      return h("section", { class: "sect" }, h("h3", null, h("span", { html: icon("trade") }), "Предложение игрокам"),
        h("p", { class: "muted", style: { margin: "0 0 10px", fontSize: "12.5px" } }, "Составьте обмен с любыми количествами. Отвечает первый согласившийся."),
        h("div", { class: "label" }, "Я отдаю"),
        giveRows.length ? giveRows : h("p", { class: "muted", style: { margin: "0 0 6px" } }, "Рука пуста — предлагать нечего."),
        h("div", { class: "label" }, "Я хочу"),
        wantRows.length ? wantRows : h("p", { class: "muted", style: { margin: "0 0 6px" } }, "Все ресурсы уже стоят в колонке «отдаю»."),
        h("button", { class: "btn btn-primary", disabled: !giveOk || !wantOk || !mine, onClick: async () => {
          const res = await handlers.onPropose(clean(sel.give), clean(sel.want));
          if (res && res.ok) { sel.give = {}; sel.want = {}; rerender(); }
        } },
          giveOk && wantOk ? `Предложить: ${resList(sel.give)} → ${resList(sel.want)}` : "Выберите, что отдать и что просить"));
    }
    function draw(state) {
      const g = state.game, me = g.me?.resources || {}, mine = isMyTurn(g) && g.phase === "main";
      const bank = bankOptions(g), p2p = p2pOptions(g);
      const tabs = [
        ["bank", "Банк", mine && bank.size > 0],
        ["players", "Игроки", mine && (p2p.size > 0 || !!g.trade_offer)],
        ["routes", "Маршруты", !!g.rules?.routes],
      ].filter(([, , on]) => on);
      if (!tabs.some(([id]) => id === sub)) sub = tabs[0]?.[0] || null;
      // keep the compose selection consistent with the current hand
      for (const r of RESOURCES) {
        if ((sel.give[r] || 0) > (me[r] || 0)) sel.give[r] = me[r] || 0;
        if (!sel.give[r]) delete sel.give[r];
        if ((sel.want[r] || 0) > 0 && (sel.give[r] || 0) > 0) delete sel.want[r];
      }
      const tabBar = tabs.length > 1 ? h("div", { class: "filters" }, tabs.map(([id, label]) =>
        h("button", { class: "fchip", "aria-pressed": String(sub === id), onClick: () => { sub = id; rerender(); } }, label))) : null;
      const content = !sub
        ? h("div", { class: "empty" }, mine ? "Сейчас обменять нечего: нет подходящих курсов и активных предложений." : g.phase === "main" ? "Обмен доступен в ваш ход." : "Обмен доступен после броска кубиков.")
        : sub === "bank" ? bankSection(g, mine, me, bank)
        : sub === "players" ? playersSection(g, mine, me, p2p)
        : routeSection(g, mine);
      replace(root, tabBar ? [tabBar, content] : [content]);
    }
    function routeSection(g, mine) {
      const active = g.me?.active_routes || [];
      const candidates = (g.legal_actions || []).filter((a) => a.type === "activate_route");
      const cost = "1 шерсть + 1 зерно";
      const routeCards = active.map((route, i) => {
        const path = Array.isArray(route.edge_path) ? route.edge_path : [];
        const resource = RES[route.trade_resource]?.ru || route.trade_resource || "ресурс";
        const suppressed = Number(route.suppressed_until ?? -1) >= Number(g.turn ?? 0);
        const waiting = Number(route.income_eligible_turn ?? 0) > Number(g.turn ?? 0);
        return h("div", { class: "route-card" },
          h("div", { class: "route-card-main" },
            h("b", null, `${resource} · ${route.edge_count || path.length} участков`),
            h("small", null, suppressed ? "Доход временно подавлен разбойником" : waiting ? "Доход начнётся после задержки" : "Приносит ресурс при подходящем броске")),
          path.length ? h("button", { class: "btn btn-sm route-show", "aria-pressed": "false", onClick: (e) => {
            const pressed = e.currentTarget.getAttribute("aria-pressed") === "true";
            root.querySelectorAll(".route-show").forEach((b) => b.setAttribute("aria-pressed", "false"));
            if (pressed) handlers.onPreviewRoute(null); else { e.currentTarget.setAttribute("aria-pressed", "true"); handlers.onPreviewRoute(path); }
          } }, "Показать путь") : h("span", { class: "muted route-private" }, "Путь скрыт по туману"));
      });
      const candidateCards = candidates.map((action) => {
        const p = action.params || {};
        const path = Array.isArray(p.edge_path) ? p.edge_path : [];
        const resource = RES[p.trade_resource]?.ru || p.trade_resource || "ресурс";
        const card = h("div", { class: "route-card route-candidate", onMouseenter: () => handlers.onPreviewRoute(path), onMouseleave: () => handlers.onPreviewRoute(null), onClick: () => handlers.onPreviewRoute(path) },
          h("div", { class: "route-card-main" },
            h("b", null, `${resource} · ${path.length} участков`),
            h("small", null, `Нажмите, чтобы показать на карте · активация: ${cost}`)),
          h("button", { class: "btn btn-primary btn-sm", disabled: !mine, onClick: () => { handlers.onPreviewRoute(null); handlers.onActivateRoute(action); } }, "Активировать"));
        return card;
      });
      return h("section", { class: "sect routes-sect" },
        h("h3", null, h("span", { html: icon("route") }), "Торговые маршруты"),
        h("p", { class: "muted route-intro" }, "Активируйте специализацию своей дороги: маршрут приносит выбранный ресурс, но может быть подавлен разбойником. Максимум — три маршрута."),
        h("p", { class: "route-cost" }, `Активация: ${cost}. Доход начинается с задержкой; за ход срабатывает не более одного маршрута.`),
        active.length ? h("div", { class: "route-list" }, routeCards) : h("p", { class: "muted" }, "Активных маршрутов пока нет."),
        candidates.length ? h("div", { class: "route-options" }, h("div", { class: "label" }, `Доступные маршруты (${candidates.length})`), candidateCards) : h("p", { class: "muted" }, active.length >= 3 ? "Лимит из трёх маршрутов достигнут." : "Нет доступных для активации путей. Нужны собственные постройки на концах пути и ресурсы."));
    }

    return { root, id: "trade", render(state) {
      lastState = state; const g = state.game;
      const routeActions = (g.legal_actions || []).filter((a) => a.type === "activate_route").map((a) => a.params);
      const sig = [g.me?.resources, g.bank_rates, g.phase, isMyTurn(g), [...bankOptions(g)].map(([k, v]) => [k, [...v]]), [...p2pOptions(g)].map(([k, v]) => [k, [...v]]), g.rules?.routes, g.me?.active_routes, routeActions];
      renderIf(root, JSON.stringify(sig), () => draw(state));
    } };
  }

  // ---------------- charters ----------------
  function buildCards() {
    const root = h("div", { class: "tabpane" });
    return {
      root, id: "cards",
      render(state) {
        const g = state.game;
        renderIf(root, JSON.stringify([g.me?.charters, g.charters_remaining, g.phase, charterCards(g).map((c) => c.options.length), hasLegal(g, "buy_charter")]), () => {
          const cards = charterCards(g), canBuy = hasLegal(g, "buy_charter");
          replace(root,
            h("section", { class: "sect" }, h("h3", null, h("span", { html: icon("charter") }), "Колода патентов"),
              h("p", { class: "muted", style: { margin: 0 } }, `В колоде: ${g.charters_remaining} · цена ${fmtCost(COSTS.buy_charter)}`),
              h("button", { class: "btn btn-primary", disabled: !canBuy, onClick: handlers.onBuy }, "Купить патент")),
            cards.length ? cards.map((c) => h("div", { class: "charter" }, h("span", { html: icon(c.meta.icon || "charter") }),
              h("div", { style: { flex: 1 } }, h("h4", null, c.meta.ru), h("p", null, c.meta.text),
                c.type === "triumph" ? h("span", { class: "chip" }, "раскроется сам") :
                  h("button", { class: "btn btn-sm", disabled: !c.options.length, onClick: () => handlers.onPlayCharter(c) }, c.options.length ? "Сыграть" : "Недоступно сейчас"))))
              : h("div", { class: "empty" }, "Патентов на руке нет."));
        });
      },
    };
  }

  function render(state) {
    const g = state.game; if (!g) return;
    const panel = state.ui.panel;
    for (const [id] of tabDefs) {
      tabEls[id].setAttribute("aria-selected", String(panel === id));
      panes[id].root.hidden = panel !== id;
    }
    const cards = (g.me?.charters || []).length;
    tabEls.cards.querySelector(".badge")?.remove();
    if (cards) tabEls.cards.append(h("span", { class: "badge" }, cards));
    panes[panel].render(state);
  }
  return { render, setTab: (id) => store.set((s) => ({ ui: { ...s.ui, panel: id } })) };
}
