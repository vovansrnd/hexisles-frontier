import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// Small DOM surface used to exercise the HUD module under Node. This is more
// than a syntax check: createHud() and render() must both run without a browser.
class FakeText {
  constructor(text) { this.nodeType = 3; this.textContent = String(text); }
}
class FakeStyle {
  constructor() { this.values = Object.create(null); }
  setProperty(key, value) { this.values[key] = String(value); }
  removeProperty(key) { delete this.values[key]; }
}
class FakeClassList {
  constructor(el) { this.el = el; }
  tokens() { return new Set(String(this.el.className || "").split(/\s+/).filter(Boolean)); }
  contains(token) { return this.tokens().has(token); }
  add(...tokens) { const next = this.tokens(); tokens.forEach((token) => next.add(token)); this.el.className = [...next].join(" "); }
  remove(...tokens) { const next = this.tokens(); tokens.forEach((token) => next.delete(token)); this.el.className = [...next].join(" "); }
  toggle(token, force) {
    const next = this.tokens();
    const shouldAdd = force === undefined ? !next.has(token) : Boolean(force);
    if (shouldAdd) next.add(token); else next.delete(token);
    this.el.className = [...next].join(" ");
    return shouldAdd;
  }
}
class FakeElement {
  constructor(tagName) {
    this.nodeType = 1;
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = Object.create(null);
    this.className = "";
    this.classList = new FakeClassList(this);
    this.style = new FakeStyle();
    this._text = "";
    this._html = "";
    this.listeners = Object.create(null);
  }
  append(...nodes) {
    for (const node of nodes.flat(Infinity)) {
      if (node == null) continue;
      this.children.push(typeof node === "string" || typeof node === "number" ? new FakeText(node) : node);
    }
  }
  appendChild(node) { this.append(node); return node; }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this._html = ""; this.append(...nodes); }
  setAttribute(key, value) { this.attributes[key] = String(value); if (key === "class") this.className = String(value); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  set textContent(value) { this.children = []; this._html = ""; this._text = String(value ?? ""); }
  get textContent() { return this._text + this.children.map((child) => child.textContent || "").join(""); }
  set innerHTML(value) { this.children = []; this._text = ""; this._html = String(value ?? ""); }
  get innerHTML() { return this._html; }
  get offsetWidth() { return 100; }
  matches(selector) { return this.#matches(selector); }
  #matches(selector) {
    if (selector.startsWith(".")) return this.classList.contains(selector.slice(1));
    if (selector.startsWith("#")) return this.attributes.id === selector.slice(1);
    return this.tagName.toLowerCase() === selector.toLowerCase();
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const found = [];
    const walk = (el) => {
      for (const child of el.children) {
        if (child.nodeType === 1) {
          if (child.matches(selector)) found.push(child);
          walk(child);
        }
      }
    };
    walk(this);
    return found;
  }
}

globalThis.document = {
  createElement: (tagName) => new FakeElement(tagName),
  createTextNode: (text) => new FakeText(text),
};

const { createHud } = await import("../js/ui/hud.js");
const fixture = JSON.parse(readFileSync(new URL("../fixtures/main_mid.json", import.meta.url), "utf8"));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function makeHud() {
  const els = { top: new FakeElement("header"), stageTop: new FakeElement("div"), stage: new FakeElement("main") };
  const handlers = {
    onCopyRoom() {}, onSound() {}, onHelp() {}, onSettings() {}, onDrawer() {}, onFit() {},
    onCancelOffer() {}, onRespond() {},
  };
  return { hud: createHud(els, handlers), els };
}

test("V4 HUD creates and renders a real game state without a missing render reference", () => {
  const { hud, els } = makeHud();
  const game = structuredClone(fixture);
  hud.render({ game, conn: "open", ui: { mode: null } });
  assert.equal(els.top.querySelector(".room-code").textContent, game.room_id);
  assert.equal(els.top.querySelector(".conn").classList.contains("open"), true);
  assert.equal(els.stageTop.querySelector(".hint-t").textContent.length > 0, true);
  assert.equal(els.stageTop.querySelector(".dice-total").textContent, String(game.dice[0] + game.dice[1]));
});

test("V4 incoming trade dialog shows the proposer name and user's give/receive direction", async () => {
  const { hud, els } = makeHud();
  const game = structuredClone(fixture);
  game.trade_ttl = 0;
  game.trade_offer = {
    from_player: 1,
    from_name: "Проверочный Игрок",
    give: { ore: 1 },
    want: { grain: 1 },
    rejected_by_me: false,
    can_accept: false,
  };
  hud.render({ game, conn: "open", ui: { mode: null } });
  const offer = els.stage.querySelector(".offer");
  assert.ok(offer, "incoming offer card should be rendered");
  assert.match(offer.textContent, /Проверочный Игрок предлагает обмен/);
  assert.match(offer.textContent, /ОТДАЁШЬ/);
  assert.match(offer.textContent, /ПОЛУЧАЕШЬ/);
  assert.match(offer.textContent, /→/);
  await wait(10); // lets the zero-second test TTL clear its own timer
});
