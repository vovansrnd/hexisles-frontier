// Shared minimal DOM stub for Node tests: enough surface for the UI modules
// (h/replace/renderIf) to build and inspect trees without a browser.

export class FakeText {
  constructor(text) { this.nodeType = 3; this.textContent = String(text); }
}

export class FakeStyle {
  constructor() { this.values = Object.create(null); }
  setProperty(key, value) { this.values[key] = String(value); }
  removeProperty(key) { delete this.values[key]; }
}

export class FakeClassList {
  constructor(el) { this.el = el; }
  tokens() { return new Set(String(this.el.className || "").split(/\s+/).filter(Boolean)); }
  contains(token) { return this.tokens().has(token); }
  add(...tokens) { const next = this.tokens(); tokens.forEach((t) => next.add(t)); this.el.className = [...next].join(" "); }
  remove(...tokens) { const next = this.tokens(); tokens.forEach((t) => next.delete(t)); this.el.className = [...next].join(" "); }
  toggle(token, force) {
    const next = this.tokens();
    const shouldAdd = force === undefined ? !next.has(token) : Boolean(force);
    if (shouldAdd) next.add(token); else next.delete(token);
    this.el.className = [...next].join(" ");
    return shouldAdd;
  }
}

export class FakeElement {
  constructor(tagName) {
    this.nodeType = 1;
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = Object.create(null);
    this.dataset = Object.create(null);
    this.className = "";
    this.classList = new FakeClassList(this);
    this.style = new FakeStyle();
    this._text = "";
    this._html = "";
    this.listeners = Object.create(null);
    this.__key = undefined;
    this.value = "";
    this.hidden = false;
  }
  append(...nodes) {
    for (const node of nodes.flat(Infinity)) {
      if (node == null || node === false) continue;
      this.children.push(typeof node === "string" || typeof node === "number" ? new FakeText(node) : node);
    }
  }
  appendChild(node) { this.append(node); return node; }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this._html = ""; this.append(...nodes); }
  setAttribute(key, value) {
    this.attributes[key] = String(value);
    if (key === "class") this.className = String(value);
    if (key === "value") this.value = String(value);
  }
  getAttribute(key) { return key in this.attributes ? this.attributes[key] : null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type, event = {}) { (this.listeners[type] || []).forEach((fn) => fn({ target: this, currentTarget: this, ...event })); }
  click() { this.dispatch("click"); }
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

export function installDom() {
  globalThis.document = {
    createElement: (tagName) => new FakeElement(tagName),
    createTextNode: (text) => new FakeText(text),
  };
}
