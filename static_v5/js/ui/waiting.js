import { h, replace } from "../lib/dom.js";
import { icon } from "../glyphs.js";
import { BOTS } from "../config.js";

/** Pre-game room shown over the stage while phase === "lobby". */
export function createWaiting(host, handlers) {
  const root = h("div", { class: "scrim", style: { position: "absolute", zIndex: 8 } });
  root.hidden = true; host.append(root);
  let key = "";
  function render(state) {
    const g = state.game, show = !!g && g.phase === "lobby";
    root.hidden = !show; if (!show) return;
    const k = JSON.stringify([g.players.map((p) => [p.name, p.connected]), g.is_host, g.bot_types]);
    if (k === key) return; key = k;
    const seats = 4 - g.players.length, used = new Set(Object.values(g.bot_types || {}));
    replace(root, h("div", { class: "modal" },
      h("h2", null, "Комната ожидания"),
      h("p", null, "Отправьте друзьям ссылку — по ней они войдут в эту комнату одним нажатием. Свободные места можно занять ботами."),
      h("div", { class: "invite-box" },
        h("div", { class: "invite-link", title: "Ссылка-приглашение" }, `${location.origin}${location.pathname}?join=${g.room_id}`),
        h("button", { class: "btn btn-primary", onClick: handlers.onCopyInvite, title: "Скопировать полную ссылку с кодом комнаты" }, "Скопировать ссылку")),
      h("button", { class: "room-chip", style: { alignSelf: "center", fontSize: "22px", padding: "8px 20px" }, onClick: handlers.onCopyRoom, title: "Скопировать ссылку-приглашение" }, h("span", { html: icon("anchor") }), g.room_id),
      h("div", null, g.players.map((p) => h("div", { class: "score-row", style: { marginBottom: "6px" } }, h("div", { class: "avatar", style: { "--pc": p.color } }, String(p.name).replace(/^[^\p{L}\p{N}]+/u, "")[0]?.toUpperCase()), h("b", null, p.name),
        h("span", { class: "chip", style: { marginLeft: "auto" } }, p.is_bot ? "бот" : p.player_id === g.my_id ? "вы" : "игрок")))),
      seats > 0 && g.is_host ? h("div", null, h("div", { class: "label", style: { marginBottom: "6px" } }, "Добавить бота"),
        h("div", { class: "bots" }, Object.entries(BOTS).filter(([id]) => !used.has(id)).map(([id, b]) => h("button", { class: "bot", onClick: () => handlers.onAddBot(id) }, h("div", { class: "avatar", style: { "--hue": b.hue } }, b.name[0]), h("div", null, h("b", null, b.name), h("small", null, b.role)))))) : null,
      h("div", { class: "foot" }, h("button", { class: "btn btn-ghost", onClick: handlers.onLeave }, "Выйти"),
        g.is_host ? h("button", { class: "btn btn-primary", disabled: g.players.length < 2, onClick: handlers.onStart }, g.players.length < 2 ? "Ждём игроков" : "Начать игру") : h("span", { class: "muted" }, "Скоро начнём"))));
  }
  return { render };
}
