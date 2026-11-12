// Minimal DOM helpers: hyperscript-style `h`, safe text, class toggling. No framework.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function h(tag, props, ...kids) {
  const node = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "style" && typeof v === "object") { for (const [sk, sv] of Object.entries(v)) { if (sk.startsWith("--")) node.style.setProperty(sk, sv); else node.style[sk] = sv; } }
      else if (k === "html") node.innerHTML = v;            // only ever called with trusted markup
      else if (k === "dataset") Object.assign(node.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v === true) node.setAttribute(k, "");
      else node.setAttribute(k, v);
    }
  }
  append(node, kids);
  return node;
}

export function append(node, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

export function replace(node, ...kids) { node.replaceChildren(); append(node, kids); return node; }
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** Re-render guard: runs `fn` only when `key` differs from the last call for this node. */
export function renderIf(node, key, fn) {
  if (node.__key === key) return false;
  node.__key = key; fn(); return true;
}

export const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
export const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
