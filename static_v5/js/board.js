// SVG board renderer with pan / zoom / pinch. Static geometry is built once per map; dynamic
// layers (routes, pieces, targets, fog, blight, raider) are keyed and patched in place so a
// state update never rebuilds the whole board and animations survive re-renders.
import { R, hexCenter, hexCorners, pointsAttr, boardBounds, edgeGeometry, outward, fitView, clampView, round } from "./geometry.js";
import { TERRAIN, PIPS, RES } from "./config.js";
import { shapePath } from "./glyphs.js";

const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}, parent) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
};

export function createBoard(host, { onTarget }) {
  const svg = el("svg", { class: "board", role: "application", "aria-label": "Игровое поле" });
  const bg = el("rect", { class: "board-bg", width: "100%", height: "100%", fill: "url(#grad-ocean)" }, svg);
  const world = el("g", { class: "world" }, svg);
  const L = {};
  for (const n of ["grid", "hexes", "ports", "blight", "routes", "route-highlights", "pieces", "raider", "pulses", "targets"]) L[n] = el("g", { class: `layer layer-${n}` }, world);
  host.appendChild(svg);

  let map = null;                 // { key, bounds, hexEls, vertexPos, edgeVerts }
  let view = { k: 1, tx: 0, ty: 0 };
  let userMoved = false, lastGame = null, lastOpts = {};
  let viewDirty = false; // true once the player zoomed/panned away from the fitted view
  const setDirty = (v) => { if (viewDirty !== v) { viewDirty = v; host.dispatchEvent(new CustomEvent("viewchange", { detail: { moved: v } })); } };
  let insets = { t: 0, b: 0, l: 0, r: 0 };
  const fitBox = () => { const r = host.getBoundingClientRect(); const v = fitView(map.bounds, Math.max(50, r.width - insets.l - insets.r), Math.max(50, r.height - insets.t - insets.b)); return { k: v.k, tx: v.tx + insets.l, ty: v.ty + insets.t }; };
  const routeEls = new Map(), routeHighlightEls = new Map(), pieceEls = new Map(), targetEls = new Map(), portEls = [];
  let previewEdgePath = null;

  // ---------- static layout ----------
  function build(game) {
    for (const n of Object.keys(L)) L[n].replaceChildren();
    routeEls.clear(); routeHighlightEls.clear(); pieceEls.clear(); targetEls.clear(); portEls.length = 0;
    const bounds = boardBounds(game.hexes);
    const vertexPos = new Map(game.vertices.map((v) => [v.vertex_id, { x: v.x, y: v.y }]));
    const edgeVerts = new Map(game.edges.map((e) => [e.edge_id, e.vertices]));
    drawGrid(bounds);
    const hexEls = new Map();
    for (const h of game.hexes) {
      const c = hexCenter(h.q, h.r);
      const g = el("g", { class: "hex", transform: `translate(${round(c.x)} ${round(c.y)})`, "data-hex": h.hex_id }, L.hexes);
      el("polygon", { class: "hex-base", points: pointsAttr(hexCorners(0, 0, R - 0.8)) }, g);
      el("polygon", { class: "hex-sheen", points: pointsAttr(hexCorners(0, 0, R - 5)) }, g);
      el("use", { class: "emblem", x: -20, y: -34, width: 24, height: 24 }, g);
      const coin = el("g", { class: "coin", transform: "translate(0 2)" }, g);
      el("circle", { r: 15.5 }, coin); el("text", { class: "coin-n", y: 1, "text-anchor": "middle", "dominant-baseline": "central" }, coin);
      el("g", { class: "pips", transform: "translate(0 10)" }, coin);
      el("polygon", { class: "blight-ov", points: pointsAttr(hexCorners(0, 0, R - 0.8)) }, g);
      el("title", {}, g);
      hexEls.set(h.hex_id, { g, c });
    }
    map = { key: game.hexes.length + ":" + game.vertices.length, bounds, hexEls, vertexPos, edgeVerts };
    view = fitBox();
    userMoved = false; applyView();
  }

  function drawGrid(b) {
    const cx = b.x + b.w / 2, cy = b.y + b.h / 2, maxR = Math.hypot(b.w, b.h);
    for (let r = 120; r < maxR; r += 120) el("circle", { cx, cy, r, class: "grat" }, L.grid);
    for (let a = 0; a < 360; a += 30) {
      const rad = (a * Math.PI) / 180;
      el("line", { x1: cx, y1: cy, x2: cx + Math.cos(rad) * maxR, y2: cy + Math.sin(rad) * maxR, class: "grat" }, L.grid);
    }
    const rose = el("g", { class: "rose", transform: `translate(${b.x + b.w - 55} ${b.y + b.h - 55})` }, L.grid);
    el("circle", { r: 24, class: "rose-ring" }, rose);
    el("path", { d: "M0-30 6-6 30 0 6 6 0 30-6 6-30 0-6-6z", class: "rose-star" }, rose);
    el("text", { y: -34, "text-anchor": "middle", class: "rose-n" }, rose).textContent = "С";
  }

  // ---------- per-state updates ----------
  function updateHexes(game) {
    for (const h of game.hexes) {
      const rec = map.hexEls.get(h.hex_id); if (!rec) continue;
      const { g } = rec, t = h.revealed ? h.terrain : "unknown", meta = TERRAIN[t] || TERRAIN.unknown;
      const wasUnknown = g.classList.contains("t-unknown"), rev = h.revealed;
      g.setAttribute("class", `hex t-${t}${h.has_raider ? " has-raider" : ""}${h.blight_stage ? " blight b" + h.blight_stage : ""}${rev ? "" : " fog"}${g.classList.contains("roll-hit") ? " roll-hit" : ""}`);
      if (wasUnknown && rev && lastGame) { g.classList.add("reveal"); setTimeout(() => g.classList.remove("reveal"), 900); }
      g.querySelector(".emblem").setAttribute("href", `#ter-${t}`);
      const coin = g.querySelector(".coin"), hasNum = rev && h.number_token > 0;
      coin.style.display = hasNum ? "" : "none";
      if (hasNum) {
        g.querySelector(".coin-n").textContent = h.number_token;
        coin.classList.toggle("hot", h.number_token === 6 || h.number_token === 8);
        const pg = g.querySelector(".pips"), n = PIPS[h.number_token] || 0;
        pg.replaceChildren();
        for (let i = 0; i < n; i++) el("circle", { cx: round((i - (n - 1) / 2) * 3.4), cy: 0, r: 1.15 }, pg);
      }
      g.querySelector("title").textContent = rev
        ? `${meta.ru}${h.number_token ? ", " + h.number_token : ""}${meta.res ? " — " + RES[meta.res].ru : ""}${h.blight_stage ? ` · скверна ${h.blight_stage}` : ""}`
        : "Неизведанный сектор";
    }
    const rh = game.hexes.find((h) => h.has_raider);
    L.raider.replaceChildren();
    if (rh) {
      const c = map.hexEls.get(rh.hex_id).c;
      const g = el("g", { class: "raider", transform: `translate(${round(c.x + 19)} ${round(c.y + 8)}) scale(.82)` }, L.raider);
      el("ellipse", { cx: 0, cy: 21, rx: 15, ry: 4.5, class: "raider-shadow" }, g);
      el("use", { href: "#pc-raider", x: -16, y: -22, width: 32, height: 40, class: "raider-fig" }, g);
    }
  }

  function updatePorts(game) {
    for (const p of portEls) p.remove();
    portEls.length = 0;
    const ports = game.vertices.filter((v) => v.is_port);
    const used = new Set();
    for (const v of ports) {
      if (used.has(v.vertex_id)) continue;
      used.add(v.vertex_id);
      const mate = game.edges.map((e) => e.vertices).find((vs) => vs.includes(v.vertex_id) && ports.some((q) => q.vertex_id === vs.find((x) => x !== v.vertex_id) && q.port_type === v.port_type));
      let mx = v.x, my = v.y, ids = [v.vertex_id];
      if (mate) {
        const other = map.vertexPos.get(mate.find((x) => x !== v.vertex_id)); used.add(mate.find((x) => x !== v.vertex_id));
        mx = (v.x + other.x) / 2; my = (v.y + other.y) / 2; ids = mate;
      }
      const pos = outward(mx, my, 22);
      const g = el("g", { class: "port" }, L.ports);
      for (const id of ids) { const vp = map.vertexPos.get(id); el("line", { x1: round(vp.x), y1: round(vp.y), x2: round(pos.x), y2: round(pos.y), class: "port-line" }, g); }
      const any = v.port_type === "any";
      el("rect", { x: round(pos.x - 15), y: round(pos.y - 10), width: 30, height: 20, rx: 8, class: "port-pill" + (any ? "" : " res-" + v.port_type) }, g);
      const tx = el("text", { x: round(pos.x - (any ? 0 : 6)), y: round(pos.y + 0.5), "text-anchor": "middle", "dominant-baseline": "central", class: "port-t" }, g);
      tx.textContent = any ? "3:1" : "2:1";
      if (!any) el("use", { href: `#res-${v.port_type}`, x: round(pos.x + 1.5), y: round(pos.y - 6), width: 12, height: 12, class: "port-ic res-" + v.port_type }, g);
      el("title", {}, g).textContent = any ? "Порт 3:1 — любой ресурс" : `Порт 2:1 — ${RES[v.port_type]?.ru || v.port_type}`;
      portEls.push(g);
    }
  }

  function updateRoutes(game, style) {
    const seen = new Set();
    for (const e of game.edges) {
      if (e.route_owner === null || e.route_owner === undefined) continue;
      seen.add(e.edge_id);
      const [a, b] = e.vertices.map((id) => map.vertexPos.get(id)); if (!a || !b) continue;
      let g = routeEls.get(e.edge_id);
      if (!g) {
        g = el("g", { class: "route" + (lastGame ? " fresh" : "") }, L.routes);
        const attrs = { x1: round(a.x), y1: round(a.y), x2: round(b.x), y2: round(b.y) };
        el("line", { ...attrs, class: "route-out" }, g); el("line", { ...attrs, class: "route-in" }, g);
        routeEls.set(e.edge_id, g);
      }
      g.style.setProperty("--pc", style(e.route_owner).color);
    }
    for (const [id, g] of routeEls) if (!seen.has(id)) { g.remove(); routeEls.delete(id); }
  }

  function updateRouteHighlights(game) {
    const wanted = new Map();
    const addPath = (path, kind, keyBase) => {
      if (!Array.isArray(path)) return;
      path.forEach((edgeId, index) => {
        const key = `${kind}:${keyBase}:${edgeId}`;
        wanted.set(key, { edgeId: Number(edgeId), kind, key });
      });
    };
    // Only paths included in the observation are rendered. Under fog-of-war
    // opponents' route summaries have no edge_path, so hidden roads stay hidden.
    for (const player of (game.players || [])) {
      for (const [idx, route] of (player.active_routes || []).entries()) {
        addPath(route.edge_path, "active", `${player.player_id}:${idx}`);
      }
    }
    if (previewEdgePath?.length) addPath(previewEdgePath, "preview", "selected");

    for (const [key, rec] of wanted) {
      const endpoints = map.edgeVerts.get(rec.edgeId);
      if (!endpoints || endpoints.length !== 2) continue;
      const [a, b] = endpoints.map((id) => map.vertexPos.get(id));
      if (!a || !b) continue;
      let group = routeHighlightEls.get(key);
      if (!group) {
        group = el("g", { class: `route-highlight route-highlight-${rec.kind}`, "data-edge": rec.edgeId }, L["route-highlights"]);
        const attrs = { x1: round(a.x), y1: round(a.y), x2: round(b.x), y2: round(b.y) };
        el("line", { ...attrs, class: "route-highlight-halo" }, group);
        el("line", { ...attrs, class: "route-highlight-line" }, group);
        routeHighlightEls.set(key, group);
      }
    }
    for (const [key, group] of routeHighlightEls) {
      if (!wanted.has(key)) { group.remove(); routeHighlightEls.delete(key); }
    }
  }

  function updatePieces(game, style, patterns) {
    const seen = new Set();
    for (const v of game.vertices) {
      if (!v.structure_type) continue;
      seen.add(v.vertex_id);
      const key = `${v.vertex_id}:${v.structure_type}:${v.structure_owner}`;
      let rec = pieceEls.get(v.vertex_id);
      if (rec && rec.key === key) continue;
      rec?.g.remove();
      const st = style(v.structure_owner), city = v.structure_type === "metropolis", s = city ? 30 : 23;
      const outer = el("g", { class: `piece ${city ? "city" : "house"}`, transform: `translate(${round(v.x)} ${round(v.y)})` }, L.pieces);
      outer.style.setProperty("--pc", st.color);
      const g = el("g", { class: `piece-in${lastGame ? " fresh" : ""}` }, outer);
      el("ellipse", { cx: 0, cy: s * 0.42, rx: s * 0.5, ry: 3.2, class: "piece-shadow" }, g);
      el("use", { href: city ? "#pc-metropolis" : "#pc-outpost", x: -s / 2, y: -s / 2 - 2, width: s, height: s, class: "piece-body" }, g);
      if (patterns) el("path", { d: shapePath(st.shape, 3.4), class: "piece-mark", transform: `translate(0 ${city ? 4 : 3})` }, g);
      el("title", {}, g).textContent = `${city ? "Метрополия" : "Поселение"} · ${st.name}`;
      pieceEls.set(v.vertex_id, { key, g: outer });
    }
    for (const [id, rec] of pieceEls) if (!seen.has(id)) { rec.g.remove(); pieceEls.delete(id); }
  }

  /** Does this hex touch a building that is not mine? (raider: "there is someone to rob here") */
  function hexHasVictim(c) {
    if (!lastGame) return false;
    const me = lastGame.my_id, owners = new Map(lastGame.vertices.filter((v) => v.structure_owner != null).map((v) => [v.vertex_id, v.structure_owner]));
    for (const [vid, p] of map.vertexPos) if (owners.has(vid) && owners.get(vid) !== me && Math.hypot(p.x - c.x, p.y - c.y) < R * 1.1) return true;
    return false;
  }
  function updateTargets(targets, mode) {
    const want = new Map();
    if (targets) {
      for (const id of targets.vertex) want.set(`vertex:${id}`, ["vertex", id]);
      for (const id of targets.edge) want.set(`edge:${id}`, ["edge", id]);
      for (const id of targets.hex) want.set(`hex:${id}`, ["hex", id]);
    }
    for (const [k, g] of targetEls) if (!want.has(k)) { g.remove(); targetEls.delete(k); }
    for (const [k, [kind, id]] of want) {
      if (targetEls.has(k)) continue;
      const g = el("g", { class: `tgt tgt-${kind}`, tabindex: 0, role: "button", "data-kind": kind, "data-id": id }, L.targets);
      if (kind === "vertex") {
        const p = map.vertexPos.get(id); if (!p) continue;
        g.setAttribute("transform", `translate(${round(p.x)} ${round(p.y)})`);
        el("circle", { r: 11, class: "tgt-ring" }, g); el("circle", { r: 4, class: "tgt-dot" }, g); el("circle", { r: 20, class: "tgt-hit" }, g);
        g.setAttribute("aria-label", `Вершина ${id}`);
      } else if (kind === "edge") {
        const [a, b] = map.edgeVerts.get(id).map((v) => map.vertexPos.get(v)); if (!a || !b) continue;
        const attrs = { x1: round(a.x), y1: round(a.y), x2: round(b.x), y2: round(b.y) };
        el("line", { ...attrs, class: "tgt-edge" }, g); el("line", { ...attrs, class: "tgt-hit-line" }, g);
        const mx = round((a.x + b.x) / 2), my = round((a.y + b.y) / 2);
        el("circle", { cx: mx, cy: my, r: 5, class: "tgt-dot" }, g); el("circle", { cx: mx, cy: my, r: 13, class: "tgt-hit" }, g);
        g.setAttribute("aria-label", `Ребро ${id}`);
      } else {
        const rec = map.hexEls.get(id); if (!rec) continue;
      if (hexHasVictim(rec.c)) g.classList.add("tgt-victim");
        g.setAttribute("transform", `translate(${round(rec.c.x)} ${round(rec.c.y)})`);
        el("polygon", { points: pointsAttr(hexCorners(0, 0, R - 2)), class: "tgt-hex" }, g);
        g.setAttribute("aria-label", `Сектор ${id}`);
      }
      const fire = (ev) => { ev.stopPropagation(); if (userMoved) return; onTarget && onTarget(kind, id); };
      g.addEventListener("click", fire);
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onTarget && onTarget(kind, id); } });
      targetEls.set(k, g);
    }
    svg.classList.toggle("has-targets", targetEls.size > 0);
  }

  function pulse(ids) {
    for (const id of ids) {
      const g = map?.hexEls.get(id)?.g; if (!g) continue;
      g.classList.add("roll-hit");
      setTimeout(() => g.classList.remove("roll-hit"), 2600);
    }
  }

  // ---------- pan / zoom ----------
  function applyView() {
    if (!map) return;
    const r = host.getBoundingClientRect();
    view = clampView(view, map.bounds, r.width, r.height, 0.35, 3.2);
    world.setAttribute("transform", `translate(${round(view.tx)} ${round(view.ty)}) scale(${round(view.k * 1000) / 1000})`);
  }
  const pointers = new Map(); let pinch = null, dragStart = null;
  svg.addEventListener("pointerdown", (e) => {
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    userMoved = false;
    if (pointers.size === 1) dragStart = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty };
    if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), k: view.k }; }
  });
  svg.addEventListener("pointermove", (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2 && pinch) {
      const [a, b] = [...pointers.values()], d = Math.hypot(a.x - b.x, a.y - b.y), r = svg.getBoundingClientRect();
      zoomAt(pinch.k * (d / pinch.d), (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top); userMoved = true;
    } else if (pointers.size === 1 && dragStart) {
      const dx = e.clientX - dragStart.x, dy = e.clientY - dragStart.y;
      if (!userMoved && Math.hypot(dx, dy) < 6) return;
      if (!userMoved) { userMoved = true; setDirty(true); svg.setPointerCapture?.(e.pointerId); svg.classList.add("panning"); }
      view = { ...view, tx: dragStart.tx + dx, ty: dragStart.ty + dy }; applyView();
    }
  });
  const end = (e) => {
    pointers.delete(e.pointerId); if (pointers.size < 2) pinch = null;
    if (!pointers.size) { dragStart = null; svg.classList.remove("panning"); setTimeout(() => { userMoved = false; }, 0); }
  };
  svg.addEventListener("pointerup", end); svg.addEventListener("pointercancel", end);
  function zoomAt(k, px, py) { setDirty(true);
    k = Math.max(0.35, Math.min(3.2, k));
    const f = k / view.k; view = { k, tx: px - (px - view.tx) * f, ty: py - (py - view.ty) * f }; applyView();
  }
  svg.addEventListener("wheel", (e) => {
    e.preventDefault(); const r = svg.getBoundingClientRect();
    zoomAt(view.k * Math.exp(-e.deltaY * 0.0016), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  svg.addEventListener("dblclick", () => fit());
  new ResizeObserver(() => { if (!map) return; if (!userMoved && Math.abs(view.k - fitBox().k) < 1e-6) fit(); else applyView(); }).observe(host);

  function fit() { setDirty(false);
    if (!map) return; view = fitBox(); applyView();
  }

  return {
    update(game, opts = {}) {
      if (!game || !game.hexes?.length) return;
      if (!map || map.key !== game.hexes.length + ":" + game.vertices.length) build(game);
      const players = new Map((game.players || []).map((p, i) => [p.player_id, { color: p.color, name: p.name, shape: ["circle", "square", "diamond", "triangle"][i % 4] }]));
      const style = (id) => players.get(id) || { color: "#999", name: "?", shape: "circle" };
      updateHexes(game); updatePorts(game); updateRoutes(game, style); updateRouteHighlights(game); updatePieces(game, style, !!opts.patterns);
      const prevOpts = lastOpts; lastGame = game; lastOpts = opts; // set first: updateTargets reads lastGame
      updateTargets(opts.targets, opts.mode);
      if (opts.rollHexes?.length && opts.rollKey !== prevOpts.rollKey) pulse(opts.rollHexes);
    },
    fit, setInsets(i) { insets = { ...insets, ...i }; if (map && !userMoved) fit(); }, zoomBy(f) { const r = svg.getBoundingClientRect(); zoomAt(view.k * f, r.width / 2, r.height / 2); },
    previewRoute(path) {
      previewEdgePath = Array.isArray(path) ? path.map(Number) : null;
      if (map && lastGame) updateRouteHighlights(lastGame);
    },
    focusHex(id) {
      const rec = map?.hexEls.get(id); if (!rec) return;
      const r = svg.getBoundingClientRect(); view = { ...view, tx: r.width / 2 - rec.c.x * view.k, ty: r.height / 2 - rec.c.y * view.k }; applyView();
    },
    destroy() { svg.remove(); },
  };
}
