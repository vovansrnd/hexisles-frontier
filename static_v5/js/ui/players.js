import { h, replace, renderIf } from "../lib/dom.js";
import { icon, shapePath } from "../glyphs.js";
import { BOTS, PLAYER_SHAPES } from "../config.js";

const cleanName = (n) => String(n || "?").replace(/^[^\p{L}\p{N}]+/u, "").trim() || "?";

/** V5 players: one compact row per player in the side panel (desktop) and a
 * chip strip floating over the map (mobile). Both views render from the same
 * state; CSS decides which is visible. */
export function createPlayers(listHost, chipsHost) {
  const rows = new Map();
  const chips = new Map();

  function meta(g, p) {
    const bot = p.is_bot ? BOTS[p.bot_type] : null;
    const winner = g.winner_id === p.player_id;
    const blight = g.rules?.blight_mode === "factions" && p.blight ? p.blight : null;
    const side = blight ? (blight.alignment === "cult" ? "Культ" : blight.alignment === "order" ? "Орден" : blight.alignment === "hidden" ? "сторона ?" : "нейтрал") : null;
    // Faction progress: steps to the next faction VP, or an explicit cap note.
    // Numbers may be absent (hidden mode for other players) — fall back to the
    // plain counter display then.
    const progress = (count, vp, step, cap, noun) => {
      if (count == null) return "";
      if (vp == null || step == null || cap == null) return `${count} ${noun}`;
      if (vp >= cap) return `${count} ${noun} · предел +${cap} ОП`;
      return `${count} ${noun} · до +1 ОП: ${step - (count % step)}`;
    };
    const sideNote = blight ? (blight.alignment === "cult"
      ? progress(blight.tears, blight.tears_vp, blight.tears_vp_step, blight.vp_cap, "слёз")
      : blight.alignment === "order"
        ? progress(blight.purified_count, blight.purify_vp, blight.purify_vp_step, blight.vp_cap, "очищ.")
        : "") : null;
    return { bot, winner, side, sideNote };
  }

  function buildRow(p) {
    const root = h("article", { class: "prow", dataset: { pid: p.player_id } });
    root.style.setProperty("--pc", p.color);
    rows.set(p.player_id, root); listHost.append(root); return root;
  }
  function buildChip(p) {
    const root = h("div", { class: "pchip", dataset: { pid: p.player_id } });
    root.style.setProperty("--pc", p.color);
    chips.set(p.player_id, root); chipsHost.append(root); return root;
  }

  function render(state) {
    const g = state.game; if (!g) return;
    const patterns = state.ui.patterns;
    g.players.forEach((p, i) => {
      const root = rows.get(p.player_id) || buildRow(p);
      const chip = chips.get(p.player_id) || buildChip(p);
      root.style.order = chip.style.order = String(i);
      const isTurn = g.current_player === p.player_id && g.phase !== "finished";
      const cls = `${isTurn ? " turn" : ""}${p.player_id === g.my_id ? " me" : ""}${p.connected ? "" : " off"}`;
      root.className = `prow${cls}`; chip.className = `pchip${cls}`;
      const key = JSON.stringify([p.victory_points, p.total_resources, p.charter_count, p.vanguards_played, p.has_longest_route, p.has_grand_garrison,
        p.connected, g.winner_id === p.player_id, patterns, g.points_to_win, p.name, p.blight]);
      const m = meta(g, p);
      renderIf(root, key, () => {
        const avatar = h("span", { class: "avatar", "aria-hidden": "true" },
          patterns ? h("span", { html: `<svg viewBox="-9 -9 18 18"><path d="${shapePath(PLAYER_SHAPES[i % 4], 6)}"/></svg>` }) : cleanName(p.name)[0].toUpperCase());
        replace(root,
          avatar,
          h("div", { class: "prow-main" },
            h("div", { class: "pname", title: p.name }, cleanName(p.name),
              m.winner ? h("span", { html: icon("crown"), style: { color: "var(--brass-2)", marginLeft: "4px" } }) : null,
              m.side ? h("b", { class: `side side-${p.blight.alignment}` }, m.side) : null),
            h("div", { class: "pchips" },
              h("span", { class: "chip", title: "Карт ресурсов на руке", html: `${icon("cards")}<b class="num">${p.total_resources}</b>` }),
              p.charter_count ? h("span", { class: "chip", title: "Патентов на руке", html: `${icon("charter")}<b class="num">${p.charter_count}</b>` }) : null,
              p.vanguards_played ? h("span", { class: `chip${p.has_grand_garrison ? " gold" : ""}`, title: p.has_grand_garrison ? "Великая стража (+2 ПО)" : "Сыграно стражей", html: `${icon("sword")}<b class="num">${p.vanguards_played}</b>` }) : null,
              p.has_longest_route ? h("span", { class: "chip gold", title: "Самый длинный тракт (+2 ПО)", html: `${icon("road")}<b>тракт</b>` }) : null,
              m.sideNote ? h("span", { class: "chip", title: m.sideNote, textContent: m.sideNote }) : null)),
          h("div", { class: "vp" }, h("b", { class: "num" }, p.victory_points), h("small", null, `/${g.points_to_win}`)),
          h("div", { class: "vpbar", title: "Прогресс до победы" }, h("i", { style: { width: `${Math.min(100, (p.victory_points / g.points_to_win) * 100)}%` } })));
      });
      renderIf(chip, key, () => {
        replace(chip,
          h("span", { class: "pchip-dot", "aria-hidden": "true" }),
          h("b", { class: "pchip-name" }, cleanName(p.name)),
          h("b", { class: "pchip-vp num" }, p.victory_points),
          m.side ? h("span", { class: `pchip-side side-${p.blight.alignment}` }, p.side === "сторона ?" ? "?" : m.side[0]) : null,
      p.has_longest_route ? h("span", { class: "pchip-badge", title: "Самый длинный тракт (+2 ПО)", html: icon("road") }) : null,
      p.has_grand_garrison ? h("span", { class: "pchip-badge", title: "Великая стража (+2 ПО)", html: icon("sword") }) : null);
      });
    });
  }
  return { render };
}
