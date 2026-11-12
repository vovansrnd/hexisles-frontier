import { h, replace } from "../lib/dom.js";
import { icon, resIcon } from "../glyphs.js";
import { RESOURCES, RES, CHARTERS } from "../config.js";
import { discardInfo, findDiscardMatch, suggestDiscard, sum, standings, playerById } from "../selectors.js";

export function createModals(root, ctx) {
  const { store, handlers } = ctx;
  let current = null;       // { kind, node, onClose, auto }
  const cleanName = (n) => String(n || "?").replace(/^[^\p{L}\p{N}]+/u, "").trim();

  function open(kind, content, { dismissible = true, auto = false, wide = false, onClose } = {}) {
    close(true);
    const node = h("div", { class: "scrim", role: "dialog", "aria-modal": "true", onMousedown: (e) => { if (dismissible && e.target === node) close(); } },
      h("div", { class: `modal${wide ? " wide" : ""}` }, content));
    root.append(node);
    current = { kind, node, onClose, auto, dismissible };
    queueMicrotask(() => node.querySelector("button:not(:disabled), input")?.focus());
    return node;
  }
  function close(silent = false) {
    if (!current) return;
    current.node.remove();
    const cb = current.onClose; current = null;
    if (!silent && cb) cb();
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && current?.dismissible) close(); });

  // ---------- generic resource picker (abundance / monopoly / bank pay) ----------
  function pickResources({ title, text, count = 1, allowed = RESOURCES, onPick }) {
    const sel = {}; let n = 0;
    const done = h("button", { class: "btn btn-primary", disabled: true, onClick: () => { const picked = { ...sel }; close(true); onPick(picked); } }, "Подтвердить");
    const row = h("div", { class: "sel-row" });
    const draw = () => {
      replace(row, RESOURCES.map((r) => h("div", { class: "sel-card" }, h("span", { html: resIcon(r) }), h("div", { class: "cnt num" }, sel[r] || 0), h("small", { class: "muted" }, RES[r].ru),
        h("div", { class: "ctl" },
          h("button", { "aria-label": "меньше", disabled: !sel[r], onClick: () => { sel[r]--; n--; draw(); } }, h("span", { html: icon("minus") })),
          h("button", { "aria-label": "больше", disabled: n >= count || !allowed.includes(r), onClick: () => { sel[r] = (sel[r] || 0) + 1; n++; draw(); } }, h("span", { html: icon("plus") }))))));
      done.disabled = n !== count;
    };
    draw();
    open("pick", [h("h2", null, title), text ? h("p", null, text) : null, row, h("div", { class: "foot" }, h("button", { class: "btn", onClick: () => close() }, "Отмена"), done)]);
  }

  // ---------- discard ----------
  function discard(g) {
    const { required, combos } = discardInfo(g), res = g.me?.resources || {};
    if (current?.kind === "discard" && current.required === required && current.hand === JSON.stringify(res)) return;
    const sel = Object.fromEntries(RESOURCES.map((r) => [r, 0]));
    const row = h("div", { class: "sel-row" }), prog = h("div", { class: "progress" });
    const go = h("button", { class: "btn btn-primary", onClick: () => handlers.onDiscard(Object.fromEntries(Object.entries(sel).filter(([, v]) => v))) }, "Сбросить");
    const draw = () => {
      const n = sum(sel), exact = findDiscardMatch(sel, combos);
      replace(row, RESOURCES.map((r) => h("div", { class: "sel-card", style: { opacity: res[r] ? 1 : .4 } }, h("span", { html: resIcon(r) }), h("div", { class: "cnt num" }, `${sel[r]}/${res[r] || 0}`), h("small", { class: "muted" }, RES[r].ru),
        h("div", { class: "ctl" },
          h("button", { "aria-label": "меньше", disabled: !sel[r], onClick: () => { sel[r]--; draw(); } }, h("span", { html: icon("minus") })),
          h("button", { "aria-label": "больше", disabled: sel[r] >= (res[r] || 0) || n >= required, onClick: () => { sel[r]++; draw(); } }, h("span", { html: icon("plus") }))))));
      prog.textContent = `Выбрано ${n} из ${required}`; prog.classList.toggle("ok", n === required);
      go.disabled = n !== required;
      go.title = n === required && !exact && combos.length ? "Эта комбинация вне списка сервера — сервер может её отклонить" : "";
    };
    const auto = () => {
      const sug = suggestDiscard(res, required);
      const pick = combos.length ? findDiscardMatch(sug, combos) || combos[0] : sug;
      for (const r of RESOURCES) sel[r] = pick?.[r] || 0;
      draw();
    };
    draw();
    const node = open("discard", [h("h2", null, "Выпала семёрка"), h("p", null, `Когда выпадает 7, игроки с более чем семью картами сбрасывают половину. Вам нужно сбросить ${required}.`), row, prog,
      h("div", { class: "foot" }, h("button", { class: "btn btn-ghost", onClick: auto }, "Подобрать автоматически"), go)], { dismissible: false, auto: true });
    current.required = required; current.hand = JSON.stringify(res);
  }

  // ---------- raider victim ----------
  function victim(g) {
    const ids = g.pending_raider_victims || [];
    const key = ids.join(",");
    if (current?.kind === "victim" && current.key === key) return;
    open("victim", [h("h2", null, "У кого украсть?"), h("p", null, "Рейдер стоит на секторе нескольких игроков."),
      ids.map((id) => { const p = playerById(g, id); return h("button", { class: "victim", onClick: () => handlers.onVictim(id) },
        h("div", { class: "avatar", style: { "--pc": p.color } }, cleanName(p.name)[0]?.toUpperCase()),
        h("div", null, h("b", null, cleanName(p.name)), h("div", { class: "psub" }, `${p.total_resources} карт · ${p.victory_points} ПО`)),
        h("span", { style: { marginLeft: "auto", color: "var(--brass)" }, html: icon("hunt") })); })], { dismissible: false, auto: true });
    current.key = key;
  }

  // ---------- help ----------
  function help() {
    const g = store.get().game || {}, r = g.rules || {}, goal = g.points_to_win ?? 12;
    const card = (icon, title, intro, bullets = []) => h("section", { class: "rules-card" },
      h("div", { class: "rules-head" }, h("span", null, icon), h("h3", null, title)),
      intro ? h("p", { class: "rules-intro" }, intro) : null,
      bullets.length ? h("ul", { class: "rules-bullets" }, bullets.map(([lead, text]) => h("li", null, h("b", null, lead), ` ${text}`))) : null);
    const cards = [
      card("🏆", "Цель партии", `Первым набрать ${goal} очков.`, [["Поселение —", "1 ПО, метрополия — 2 ПО."], ["Великий тракт —", "+2 ПО за самую длинную дорогу (от 5 участков)."], ["Великая стража —", "+2 ПО за 3 и более патентов «Страж»."], ["Патенты «Триумф» —", "по 1 скрытому ПО."]]),
      card("🎲", "Ход игрока", "Бросьте кубики: выпавшее число определяет, какие сектора дают ресурсы.", [["1. Ресурсы.", "Поселение даёт 1 карту, метрополия — 2."], ["2. Стройте и торгуйте", "в любом порядке."], ["3. Конец хода.", "При выпадении 7 игроки с 8+ картами сбрасывают половину, затем ходит рейдер."]]),
      card("🧱", "Строительство", "Выбирайте подсвеченные точки прямо на карте.", [["Тракт:", "дерево + кирпич."], ["Поселение:", "дерево + кирпич + зерно + шерсть, не ближе двух участков от другого."], ["Метрополия:", "2 зерна + 3 руды, улучшает поселение."], ["Патент:", "зерно + шерсть + руда. Сыграть можно со следующего хода, один за ход."]]),
      card("💱", "Обмен", "Банк меняет 4 одинаковых ресурса на один выбранный.", [["Порт:", "3:1, а специализированный порт — 2:1 за свой ресурс."], ["Игрокам:", "предложение видно всем и действует ограниченное время."]]),
      card("🗡", "Рейдер", "После 7 или патента «Страж» его нужно переставить.", [["Яркий контур —", "рядом чьи-то постройки: можно украсть карту."], ["Тусклый —", "пустой сектор; сектор рейдера выбрать нельзя."]]),
    ];
    if (r.fog_of_war) cards.push(card("🌫", "Туман войны", "Серые сектора неизвестны.", [["Открытие:", "рядом с вашими постройками они открываются сами, ещё помогает патент «Первопроходец»."], ["Скверна в тумане:", "знак заражения виден, но местность не раскрывается."]]));
    if (r.blight_mode) {
      const mode = r.blight_mode, names = { environmental: "Стихия", tactical: "Тактическая", factions: "Культ ↔ Орден" };
      const bl = [["Стадия I", "отнимает 1 ресурс у соседних построек, стадия II — 2 (не ниже нуля)."], ["Очистка:", "1 зерно (если зерна нет — любые 2 одинаковых). После неё сектор ненадолго защищён."]];
      if (mode === "environmental") bl.push(["Стихия:", "игроки могут только очищать; скверна расходится сама от пустошей и заражённых секторов."]);
      else if (mode === "tactical") bl.push(["Заразить:", "1 шерсть, любой видимый сектор, который ещё можно усилить; заражать и очищать может каждый."]);
      else {
        bl.push(["☠ Культ:", "заражение за шерсть; постройки Культа не теряют добычу, а с заражённых секторов приносят «слёзы»."], ["✧ Орден:", "очистка за зерно; очищенный сектор благословляет ваши соседние постройки (+1 ресурс на три круга)."], ["☯ Стороны не фиксированы:", "они меняются действиями. Каждые 3 слезы Культа и 3 очистки Ордена — +1 ПО (до +3 на каждый путь)."]);
        if (r.cult_visibility === "hidden") bl.push(["🕵 Дедукция (эксперимент):", "сторона других игроков скрыта; Орден может один раз обличить: верно — +1 ПО и потеря слёз у цели, ошибка — потеря карты."]);
        else bl.push(["📊 Видно всем:", "сторона, слёзы и очистки показаны в карточках игроков (на телефоне — нажмите на полосу игроков)."]);
      }
      cards.push(card("☣", `Скверна · ${names[mode] || mode}`, "", bl));
    }
    if (r.routes) cards.push(card("🚚", "Торговые маршруты", "Специализация для своих дорог.", [["Условие:", "цепочка из 2–6 своих дорог между двумя вашими постройками."], ["Цена:", "зерно + шерсть, до трёх маршрутов, каждый — один раз."], ["Выгода:", "курс банка 3:1 по выбранному ресурсу и +1 ресурс за ход."], ["Пират:", "рядом с путём подавляет маршрут на круг."], ["Подсветка:", "нажмите карточку маршрута — путь вспыхнет на карте."]]));
    if (r.friendly_raider) cards.push(card("🛡", "Мягкий рейдер", "Не грабит игроков с 2 ПО и меньше."));
    if (r.fast_start) cards.push(card("⚡", "Быстрый старт", "Вторая стартовая постройка — метрополия."));
    cards.push(card("🖐", "Управление", "Карта — основное место для выбора.", [["Телефон:", "двигайте карту пальцем, щипок — масштаб; значок ≡ — журнал, обмен и патенты."], ["Компьютер:", "колесо — масштаб; Пробел — кубики; E — конец хода; 1–4 — построить; T — обмен; Esc — отмена."]]));
    open("help", [h("h2", null, "Правила"), h("p", { class: "muted" }, "Шпаргалка по этой партии: показаны только включённые моды."),
      h("div", { class: "rules-grid" }, cards),
      h("div", { class: "foot" }, h("button", { class: "btn btn-primary", onClick: () => close() }, "Понятно"))], { wide: true });
  }

  // ---------- settings ----------
  function settings(state) {
    const ui = state.ui;
    const seg = (key, opts, cur) => h("div", { class: "seg", role: "group" }, opts.map(([v, label]) => h("button", { "aria-pressed": String(cur === v), onClick: () => { handlers.onSetting(key, v); settings(store.get()); } }, label)));
    const sw = (key, cur) => h("button", { class: "switch", role: "switch", "aria-checked": String(!!cur), onClick: () => { handlers.onSetting(key, !cur); settings(store.get()); } });
    open("settings", [h("h2", null, "Настройки"),
      h("div", { class: "settings-row" }, h("span", null, "Анимации"), seg("motion", [["on", "Вкл"], ["off", "Выкл"]], ui.motion)),
      h("div", { class: "settings-row" }, h("span", null, "Звуки"), sw("sound", ui.sound)),
      h("div", { class: "settings-row" }, h("span", null, "Значки игроков (для дальтоников)"), sw("patterns", ui.patterns)),
      h("div", { class: "foot" }, h("button", { class: "btn btn-danger", onClick: () => { close(true); handlers.onLeave(); } }, "Покинуть партию"), h("button", { class: "btn btn-primary", onClick: () => close() }, "Готово"))]);
  }

  // ---------- charter reveal (purchase feedback, like v3) ----------
  function charterReveal(type) {
    const meta = CHARTERS[type] || { ru: type, icon: "charter", text: "" };
    const g = store.get().game || {};
    const count = (g.me?.charters?.length || 0) + 1;
    open("charter", [
      h("h2", null, "Новый патент"),
      h("article", { class: "charter-reveal" },
        h("span", { class: "charter-reveal-icon", html: icon(meta.icon || "charter") }),
        h("span", { class: "charter-reveal-kicker" }, "НОВАЯ КАРТА"),
        h("h3", null, meta.ru),
        h("p", null, meta.text),
        h("small", { class: "muted" }, "Сыграть можно со следующего хода; не больше одной карты за ход."),
        h("div", { class: "charter-reveal-count num" }, `Всего патентов: ${count}`)),
      h("div", { class: "foot" },
        h("button", { class: "btn", onClick: () => { close(true); handlers.onPanel?.("cards"); } }, "К патентам"),
        h("button", { class: "btn btn-primary", onClick: () => close() }, "Понятно")),
    ]);
  }

  // ---------- victory ----------
  let victoryDismissed = false, victoryKey = "";
  function victory(g) {
    const triumph = store.get().ui.pendingTriumph || 0;
    const key = JSON.stringify([g.winner_id, triumph, standings(g).map((p) => [p.player_id, p.victory_points])]);
    if (current?.kind === "victory") {
      if (key === victoryKey) return;
      close(true); // standings changed (e.g. a deferred Триумф arrived) — redraw in place
    } else if (victoryDismissed) return;
    victoryKey = key;
    const win = playerById(g, g.winner_id), mine = g.winner_id === g.my_id;
    open("victory", [h("h2", null, mine ? "Победа!" : `Победил ${cleanName(win?.name)}`),
      h("p", null, mine ? "Вся карта в вашей власти." : "Реванш? Хозяин комнаты может начать новую партию."),
      triumph ? h("p", { class: "victory-triumph" }, `🍀 Ваш${triumph > 1 ? "и" : ""} патент${triumph > 1 ? "ы" : ""} «Триумф» раскрыл${triumph > 1 ? "и" : ""}сь в этой победе: +${triumph} ПО`) : null,
      standings(g).map((p) => h("div", { class: `score-row${p.player_id === g.winner_id ? " win" : ""}` },
        h("div", { class: "avatar", style: { "--pc": p.color } }, cleanName(p.name)[0]?.toUpperCase()), h("b", null, cleanName(p.name)), h("span", { class: "vpn num" }, p.victory_points))),
      h("div", { class: "foot" }, h("button", { class: "btn", onClick: () => { close(true); handlers.onLeave(); } }, "В лобби"),
        g.is_host ? h("button", { class: "btn btn-primary", onClick: () => { close(true); handlers.onRestart(); } }, "Новая партия") : null)],
      { dismissible: true, auto: true, onClose: () => { victoryDismissed = true; } });
    if (mine) handlers.onConfetti();
  }

  /** Called on every state: opens/closes the modals the game itself demands. */
  function sync(state) {
    const g = state.game; if (!g) return;
    if (g.phase !== "finished") victoryDismissed = false;
    const needDiscard = g.phase === "discard" && g.pending_discards > 0 && (g.legal_actions || []).some((a) => a.type === "discard");
    const needVictim = g.phase === "raider_steal" && g.current_player === g.my_id && (g.pending_raider_victims || []).length > 0;
    if (g.phase === "finished") return victory(g);
    if (needDiscard) return discard(g);
    if (needVictim) return victim(g);
    if (current?.auto) close(true);
  }

  return { sync, help, settings: () => settings(store.get()), pickResources, charterReveal, close, isOpen: () => !!current };
}
