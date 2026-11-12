import { h, replace } from "../lib/dom.js";
import { icon, resIcon } from "../glyphs.js";
import { RES } from "../config.js";
import { phaseHint, isMyTurn, playerById, autoMode } from "../selectors.js";

const MODE_HINT = { build_route: "Выберите место для тракта", build_outpost: "Выберите место для поселения", build_metropolis: "Выберите поселение для улучшения", purify_hex: "Выберите сектор для очистки", corrupt_hex: "Выберите сектор для осквернения", play_charter: "Выберите сектор" };

const PIP_LAYOUT = { 1: [4], 2: [0, 8], 3: [0, 4, 8], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8] };

export function createHud(els, handlers) {
  // ----- topbar (built once) -----
  const roomBtn = h("button", { class: "room-chip", title: "Скопировать ссылку-приглашение", onClick: handlers.onCopyRoom }, h("span", { html: icon("anchor") }), h("span", { class: "room-code" }, "——"));
  const turnStat = h("div", { class: "stat hide-sm" }, h("b", { class: "num" }, "0"), h("span", null, "ход"));
  const goalStat = h("div", { class: "stat" }, h("b", { class: "num" }, "12"), h("span", null, "до победы"));
  const conn = h("span", { class: "conn", title: "Соединение" });
  const btn = (ic, title, fn, cls = "btn btn-ghost btn-icon") => h("button", { class: cls, title, "aria-label": title, onClick: fn }, h("span", { html: icon(ic) }));
  const soundBtn = btn("mute", "Звук", handlers.onSound);
  els.top.append(
    h("div", { class: "brand" }, h("span", { html: icon("compass") }), h("span", null, "Terra Incognita")),
    roomBtn, h("div", { class: "grow" }), turnStat, goalStat, conn,
    soundBtn, btn("help", "Правила и клавиши (?)", handlers.onHelp), btn("gear", "Настройки", handlers.onSettings),
    h("button", { class: "btn btn-ghost btn-icon side-toggle", title: "Журнал и обмен", onClick: handlers.onDrawer }, h("span", { html: icon("log") })));

  // ----- stage overlays -----
  const hint = h("div", { class: "hint", role: "status" }, h("span", { class: "dot" }), h("span", { class: "hint-t" }));
  const die = () => h("div", { class: "die" }, ...Array.from({ length: 9 }, () => h("i")));
  const d1 = die(), d2 = die(), total = h("div", { class: "dice-total num" }, "–");
  const dice = h("div", { class: "dice", "aria-label": "Кубики" }, d1, d2, total);
  els.stageTop.append(dice, hint);
  const zoom = h("div", { class: "zoom" }, btn("fit", "Вся карта", handlers.onFit, "btn btn-icon"));
  els.stage.append(zoom);
  const offerHost = h("div"); els.stage.append(offerHost);
  const noticeHost = h("div"); els.stage.append(noticeHost);

  const roomCode = roomBtn.querySelector(".room-code");
  const turnNumber = turnStat.querySelector(".num");
  const goalNumber = goalStat.querySelector(".num");
  const hintText = hint.querySelector(".hint-t");
  const hintDot = hint.querySelector(".dot");
  const diceElements = [d1, d2];
  const pipElements = diceElements.map((el) => [...el.querySelectorAll("i")]);

  let lastDice = "", lastOfferKey = "", lastNoticeKey = null;
  let diceAnimationTimeout = null;
  let offerInterval = null, offerRejectTimer = null;

  /** Raider phase must be impossible to miss: a persistent banner while it is
   * our turn to move the raider (the board targets pulse underneath it). */
  function renderRaiderNotice(g) {
    const active = !!g && g.phase === "raider_move" && g.current_player === g.my_id;
    if (active === lastNoticeKey) return;
    lastNoticeKey = active;
    replace(noticeHost, active ? h("div", { class: "raid-notice", role: "alert" },
      h("span", { class: "raid-icon", html: icon("hunt") }),
      h("div", null,
        h("h3", null, "Разбойник!"),
        h("p", null, "Переместите разбойника — допустимые сектора подсвечены на карте."))) : null);
  }

  function clearOfferTimer() {
    if (offerInterval) { clearInterval(offerInterval); offerInterval = null; }
    if (offerRejectTimer) { clearTimeout(offerRejectTimer); offerRejectTimer = null; }
  }

  function renderOffer(g) {
    const o = g.trade_offer;
    // A new rejection by some other player changes the offer object, but must
    // not restart this recipient's countdown.
    const key = o ? JSON.stringify([o.from_player, o.from_name, o.give, o.want, !!o.can_accept, !!o.rejected_by_me]) : "";
    if (key === lastOfferKey) return;
    lastOfferKey = key;
    clearOfferTimer();
    if (!o) return replace(offerHost);
    const [gr, gn] = Object.entries(o.give)[0] || [], [wr, wn] = Object.entries(o.want)[0] || [];
    const side = (r, n, label) => h("div", { class: "xside" }, h("div", { class: `res-${r}`, style: { display: "flex", alignItems: "center", gap: "6px", color: "var(--text)" }, html: `${n}${resIcon(r)}` }), label);
    if (o.from_player === g.my_id) {
      replace(offerHost, h("div", { class: "offer", role: "alert" },
        h("h3", null, "Ваше предложение"),
        h("div", { class: "xch" }, side(gr, gn, "ОТДАЁШЬ"), h("span", { class: "offer-arrow" }, "→"), side(wr, wn, "ПОЛУЧАЕШЬ")),
        h("p", { class: "muted", style: { margin: "0 0 10px", textAlign: "center" } }, "Ждём ответа соперников…"),
        h("div", { class: "row" }, h("button", { class: "btn btn-danger", onClick: handlers.onCancelOffer }, "Отозвать"))));
    } else if (!o.rejected_by_me) {
      const invalidOffer = !!g.trade_invalid;
      const serverLeftRaw = g.trade_ttl == null ? Number.NaN : Number(g.trade_ttl);
      const serverLeft = Number.isFinite(serverLeftRaw) ? Math.max(0, serverLeftRaw) : (invalidOffer ? 3 : 25);
      const durationSec = Math.min(o.can_accept && !invalidOffer ? 20 : 3, serverLeft);
      let left = durationSec;
      const timerText = () => invalidOffer
        ? `Предложение недействительно · закроется через ${left} с`
        : o.can_accept ? `Автоотказ через ${left} с` : `Нет ресурсов · закроется через ${left} с`;
      const timerLabel = h("small", { class: "muted", style: { display: "block", textAlign: "center", margin: "6px 0 0" } }, timerText());

      offerInterval = setInterval(() => {
        left--;
        if (left > 0) timerLabel.textContent = timerText();
        else clearOfferTimer(); // acceptable offers expire server-side at full TTL
      }, 1000);
      // An offer we cannot afford would otherwise squat on the screen until the
      // sender's TTL runs out: the server only cancels it early when the SENDER
      // loses resources. Register our own rejection after the short grace
      // window — the card leaves OUR screen, and once every recipient has
      // declined the server takes the offer off the table for everyone.
      if (!o.can_accept && !invalidOffer) {
        offerRejectTimer = setTimeout(() => {
          offerRejectTimer = null;
          if (lastOfferKey === key) handlers.onRespond(false);
        }, durationSec * 1000);
      }

      const proposer = (g.players || []).find((player) => Number(player.player_id) === Number(o.from_player));
      const proposerName = String(o.from_name || proposer?.name || "Соперник");
      replace(offerHost, h("div", { class: "offer", role: "alertdialog", "aria-label": "Предложение обмена" },
        h("div", { class: "offer-heading" },
          h("span", { class: "offer-proposer-dot", style: { background: proposer?.color || "var(--brass)" }, "aria-hidden": "true" }),
          h("h3", null, `${proposerName} предлагает обмен`)),
        h("div", { class: "xch" }, side(wr, wn, "ОТДАЁШЬ"), h("span", { class: "offer-arrow" }, "→"), side(gr, gn, "ПОЛУЧАЕШЬ")),
        o.can_accept ? null : h("p", { class: "muted offer-no-resource", style: { margin: "0 0 10px", textAlign: "center" } }, `У вас нет карты «${RES[wr]?.ru || wr}»`),
        h("div", { class: "row" },
          h("button", { class: "btn btn-primary", disabled: !o.can_accept, onClick: () => { clearOfferTimer(); handlers.onRespond(true); } }, "Принять"),
          h("button", { class: "btn", onClick: () => { clearOfferTimer(); handlers.onRespond(false); } }, "Отказать")),
        timerLabel));
    } else replace(offerHost);
  }

  function render(state) {
    const g = state?.game;
    if (!g) {
      roomCode.textContent = state?.session?.room_id || "——";
      turnNumber.textContent = "0";
      goalNumber.textContent = "12";
      hintText.textContent = "Ожидание игроков";
      hint.classList.remove("mine");
      hintDot.style.removeProperty("background");
      total.textContent = "–";
      total.classList.remove("seven");
      diceElements.forEach((dieEl, i) => {
        dieEl.classList.remove("roll");
        pipElements[i].forEach((pip) => pip.classList.remove("on"));
      });
      lastDice = "";
      renderOffer({ trade_offer: null });
      lastNoticeKey = null; replace(noticeHost, null);
      return;
    }

    roomCode.textContent = g.room_id || state?.session?.room_id || "——";
    turnNumber.textContent = String(g.turn ?? 0);
    goalNumber.textContent = String(g.points_to_win ?? g.rules?.points_to_win ?? 12);

    const connection = state?.conn || "closed";
    conn.className = `conn ${connection}`;
    conn.title = connection === "open" ? "Соединение установлено" : connection === "connecting" ? "Подключение…" : "Нет соединения";

    const selectedMode = state?.ui?.mode || autoMode(g);
    hintText.textContent = (selectedMode && MODE_HINT[selectedMode.type]) || phaseHint(g);
    const myTurn = isMyTurn(g);
    hint.classList.toggle("mine", myTurn);
    const currentPlayer = playerById(g, g.current_player);
    if (myTurn) hintDot.style.removeProperty("background");
    else if (currentPlayer?.color) hintDot.style.background = currentPlayer.color;
    else hintDot.style.removeProperty("background");

    const diceValues = Array.isArray(g.dice) ? g.dice.slice(0, 2).map(Number) : [];
    const diceKey = diceValues.length === 2 ? diceValues.join(":") : "";
    diceValues.forEach((value, index) => {
      const layout = PIP_LAYOUT[value] || [];
      pipElements[index].forEach((pip, pipIndex) => pip.classList.toggle("on", layout.includes(pipIndex)));
    });
    if (diceValues.length < 2) {
      pipElements.forEach((pips) => pips.forEach((pip) => pip.classList.remove("on")));
    }
    const diceTotal = diceValues.length === 2 && diceValues.every((n) => n >= 1 && n <= 6)
      ? diceValues[0] + diceValues[1] : null;
    total.textContent = diceTotal == null ? "–" : String(diceTotal);
    total.classList.toggle("seven", diceTotal === 7);

    if (diceKey && diceKey !== lastDice) {
      diceElements.forEach((dieEl) => dieEl.classList.remove("roll"));
      // Force a layout so a consecutive roll can restart the CSS animation.
      void d1.offsetWidth;
      diceElements.forEach((dieEl) => dieEl.classList.add("roll"));
      if (diceAnimationTimeout) clearTimeout(diceAnimationTimeout);
      diceAnimationTimeout = setTimeout(() => {
        diceElements.forEach((dieEl) => dieEl.classList.remove("roll"));
        diceAnimationTimeout = null;
      }, 750);
    }
    lastDice = diceKey;
    renderOffer(g);
    renderRaiderNotice(g);
  }

  return { render, offerHost };
}
