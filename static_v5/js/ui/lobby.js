import { h, replace } from "../lib/dom.js";
import { icon } from "../glyphs.js";
import { BOTS, DIFFICULTY } from "../config.js";
import { hexCenter, hexCorners, pointsAttr } from "../geometry.js";

function heroArt() {
  const cells = [[0, 0, "forest", 8], [1, 0, "hills", 6], [-1, 1, "fields", 9], [0, 1, "mountains", 5], [1, -1, "pasture", 4], [-1, 0, "unknown"], [2, -1, "unknown"], [0, -1, "unknown"], [-1, 2, "unknown"], [1, 1, "unknown"]];
  const polys = cells.map(([q, r, t, n]) => {
    const c = hexCenter(q, r), fog = t === "unknown";
    return `<g transform="translate(${c.x} ${c.y})"><polygon points="${pointsAttr(hexCorners(0, 0, 47))}" fill="${fog ? "url(#pat-fog)" : `var(--t-${t})`}" stroke="var(--land-edge)" stroke-width="1.6" ${fog ? 'stroke-dasharray="4 5"' : ""}/>
      ${n ? `<circle r="14" fill="var(--coin)" stroke="rgba(0,0,0,.4)"/><text y="5" text-anchor="middle" font-weight="700" font-size="15" fill="${n === 6 || n === 8 ? "var(--coin-hot)" : "var(--coin-ink)"}">${n}</text>` : `<use href="#ter-unknown" x="-14" y="-14" width="28" height="28" color="rgba(170,200,235,.4)"/>`}</g>`;
  }).join("");
  return h("div", { class: "map-art", "aria-hidden": "true", html: `<svg viewBox="-180 -170 380 360" width="100%">${polys}</svg>` });
}

export function createLobby(host, { onCreate, onJoin, onResume, getName }) {
  const state = { tab: "new", mode: "bots", bots: ["c3po", "r2d2", "t800"], points: 12, fog: true, blight: true, routes: false, blightMode: "factions", cultVisibility: "open", spreadChance: 40, friendly: true, fast: false, difficulty: "skynet", name: getName() || "", room: "", resume: null, invite: "" };
  const err = h("div", { class: "err-line", role: "alert" });
  const card = h("section", { class: "lobby-card panel" });
  host.append(
    h("section", { class: "hero" },
      h("div", { class: "brand", style: { fontSize: "16px" }, html: `${icon("compass")} <span>Terra Incognita</span>` }),
      h("h1", { html: "Земли, которых <em>нет на карте</em>" }),
      h("p", null, "Стройте, торгуйте и открывайте туманные острова. Партия на четверых — с живыми друзьями или ботами с характером."),
      h("ul", null,
        h("li", { html: `${icon("route")} Туман войны: карта открывается по мере продвижения` }),
        h("li", { html: `${icon("blight")} Скверна: земли портятся — их очищают… или можно перейти на тёмную сторону` }),
        h("li", { html: `${icon("trade")} Порты, банк и обмен между игроками` })),
      heroArt(),
      h("small", { class: "muted build-stamp" }, `сборка клиента от ${document.querySelector('meta[name="build"]')?.content || "—"} · порт игры ${document.querySelector('meta[name="ws-port"]')?.content || "?"}`)),
    card);

  const seg = (key, options) => h("div", { class: "seg" }, options.map(([v, label]) => h("button", { type: "button", "aria-pressed": String(state[key] === v), onClick: () => { state[key] = v; draw(); } }, label)));
  const sw = (key, label, hint) => h("div", { class: "settings-row" }, h("div", null, h("div", null, label), hint ? h("small", { class: "muted" }, hint) : null),
    h("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(state[key]), "aria-label": label, onClick: () => { state[key] = !state[key]; draw(); } }));

  function tabs() {
    const items = [["new", "Новая игра"], ["join", "По коду"]];
    return h("div", { class: "lobby-tabs", role: "tablist" }, items.map(([id, label]) => h("button", { role: "tab", "aria-selected": String(state.tab === id), onClick: () => { state.tab = id; err.textContent = ""; draw(); } }, label)));
  }
  const nameField = () => h("div", { class: "field" }, h("label", { for: "pname" }, "Ваше имя"),
    h("input", { type: "text", id: "pname", value: state.name, maxlength: 18, placeholder: "Капитан", autocomplete: "nickname", onInput: (e) => { state.name = e.target.value; } }));

  /** Explicit choice: bots start at once; "friends" makes a room and an invite link first. */
  const modeSwitch = () => h("div", { class: "lobby-tabs", role: "tablist", "aria-label": "Как играть" },
    [["bots", "С ботами"], ["friends", "С друзьями"]].map(([id, label]) => h("button", { id: `mode-${id}`, role: "tab", "aria-selected": String(state.mode === id), onClick: () => { state.mode = id; draw(); } }, label)));
  const friendsNote = () => h("p", { class: "muted friends-note" }, "Вы получите ссылку-приглашение: друзья входят по ней одним нажатием. Свободные места можно занять ботами уже в комнате ожидания.");

  function newGame() {
    return [nameField(), modeSwitch(),
      state.mode === "bots" ? h("div", { class: "field" }, h("span", { class: "label" }, `Боты за столом: ${state.bots.length} из 3`),
        h("div", { class: "bots" }, Object.entries(BOTS).map(([id, b]) => h("button", { type: "button", class: "bot", "aria-pressed": String(state.bots.includes(id)),
          onClick: () => { const on = state.bots.includes(id); if (on) state.bots = state.bots.filter((x) => x !== id); else if (state.bots.length < 3) state.bots = [...state.bots, id]; draw(); } },
          h("div", { class: "avatar", style: { "--hue": b.hue } }, b.name[0]), h("div", null, h("b", null, b.name), h("small", null, `${b.role}. ${b.blurb}`)))))) : friendsNote(),
      h("div", { class: "grid2" },
        h("div", { class: "field" }, h("span", { class: "label" }, "Очков для победы"), seg("points", [[10, "10"], [12, "12"], [14, "14"]])),
        h("div", { class: "field" }, h("label", { for: "diff" }, "Сложность ботов"),
          h("select", { id: "diff", onChange: (e) => { state.difficulty = e.target.value; } }, DIFFICULTY.map(([v, l]) => h("option", { value: v, selected: v === state.difficulty }, l))))),
      h("div", { class: "toggles" },
        sw("fog", "Туман войны", "Часть карты скрыта, пока её не откроют"),
        sw("blight", "Скверна", "Портит сектора; очистка стоит зерно"),
        sw("routes", "Торговые маршруты", "До трёх специализаций для собственных дорог"),
        sw("friendly", "Мягкий разбойник", "Не грабит игроков с 2 ПО и меньше"),
        sw("fast", "Быстрый старт", "Вторая стартовая постройка — метрополия")),
      h("div", { class: "field" }, h("label", { for: "blight-mode" }, "Режим Скверны"),
        h("select", { id: "blight-mode", disabled: !state.blight, onChange: (e) => { state.blightMode = e.target.value; draw(); } },
          [["environmental", "Природная"], ["tactical", "Тактическая"], ["factions", "Культ ↔ Орден"]].map(([v, label]) => h("option", { value: v, selected: v === state.blightMode }, label))),
        h("small", { class: "muted" }, state.blightMode === "factions"
          ? "Культовые постройки иммунны к скверне; Орден благословляет соседние постройки после очистки. Именно этот режим исследовался в синтетических проверках баланса."
          : state.blightMode === "environmental"
            ? "Два очага, природное распространение и очистка. Баланс этого режима отдельно не проверялся."
            : "Классические действия заражения и очистки. Баланс этого режима отдельно не проверялся.")),
      state.blightMode === "factions" ? h("div", { class: "field" }, h("label", { for: "cult-visibility" }, "Отображение стороны"),
        h("select", { id: "cult-visibility", onChange: (e) => { state.cultVisibility = e.target.value; draw(); } },
          [["open", "Открытая"], ["hidden", "Дедукция · эксперимент"]].map(([v, label]) => h("option", { value: v, selected: v === state.cultVisibility }, label))),
        h("small", { class: "muted" }, state.cultVisibility === "hidden"
          ? "Это не тайная роль: сторону можно вычислить по публичным действиям. Интерфейс скрывает числовые детали; доступно экспериментальное обличение."
          : "Сторона меняется по ходу партии и видна всем. Для первого плейтеста используйте этот открытый режим.")) : null,
      h("div", { class: "field" }, h("label", { for: "blight-spread-chance" }, `Интенсивность скверны · ${state.spreadChance}%`),
        h("input", { type: "range", id: "blight-spread-chance", min: 0, max: 100, step: 5, value: state.spreadChance, disabled: !state.blight, onInput: (e) => { state.spreadChance = Number(e.target.value); const label = e.currentTarget.parentElement?.querySelector?.("label"); if (label) label.textContent = `Интенсивность скверны · ${state.spreadChance}%`; } })),
      h("button", { id: "create-btn", class: "btn btn-primary lobby-cta", style: { minHeight: "48px", fontSize: "16px" }, disabled: state.mode === "bots" && state.bots.length === 0, onClick: () => {
        err.textContent = ""; onCreate({ name: state.name.trim() || "Капитан", points_to_win: state.points, fog_of_war: state.fog, blight: state.blight, routes: state.routes, blight_mode: state.blightMode, cult_visibility: state.cultVisibility, blight_spread_chance: state.spreadChance / 100, friendly_raider: state.friendly, fast_start: state.fast,
          bot_difficulty: state.difficulty, bot_types: state.mode === "bots" ? state.bots : [], start_immediately: state.mode === "bots" });
      } }, state.mode === "bots" ? "Начать игру" : "Создать комнату и получить ссылку")];
  }
  function join() {
    return [h("div", { class: "invite-note", hidden: !state.invite },
      h("span", { html: icon("anchor") }),
      h("span", null, `Вы приглашены в комнату ${state.invite || ""}. Введите имя и нажмите «Присоединиться».`)),
      nameField(), h("div", { class: "field" }, h("label", { for: "room" }, "Код комнаты"),
      h("input", { type: "text", id: "room", value: state.room, maxlength: 8, placeholder: "ABC123", style: { textTransform: "uppercase", letterSpacing: ".2em", fontFamily: "var(--font-mono)" }, onInput: (e) => { state.room = e.target.value.toUpperCase(); }, onKeydown: (e) => { if (e.key === "Enter") submit(); } })),
      h("button", { class: "btn btn-primary", style: { minHeight: "48px" }, onClick: submit }, "Присоединиться")];
    function submit() { err.textContent = ""; if (!state.room.trim()) { err.textContent = "Введите код комнаты"; return; } onJoin({ name: state.name.trim() || "Капитан", room_id: state.room.trim().toUpperCase() }); }
  }

  function draw() {
    replace(card,
      h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } }, h("h2", null, "К столу")),
      state.resume ? h("div", { class: "sect" }, h("div", null, `Партия ${state.resume.room_id} ждёт вас.`), h("button", { class: "btn btn-primary", onClick: onResume }, "Продолжить игру")) : null,
      tabs(), state.tab === "new" ? newGame() : join(), err);
  }
  draw();
  return {
    setError(msg) { err.textContent = msg || ""; },
    setResume(s) { state.resume = s; draw(); },
    /** Prefill the join tab from an invite link (?join=CODE). */
    setInvite(code) { state.invite = code; state.tab = "join"; state.room = code; err.textContent = ""; draw(); },
  };
}
