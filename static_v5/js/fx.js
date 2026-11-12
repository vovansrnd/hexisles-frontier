// Feedback layer: synthesized sounds (WebAudio, no asset files), toasts, confetti and a screen-reader
// live region. Everything here is optional polish and silently degrades.
import { h } from "./lib/dom.js";

let ctx = null;
const tone = (freq, dur = 0.12, type = "sine", gain = 0.06, when = 0) => {
  if (!ctx) return;
  const t = ctx.currentTime + when, o = ctx.createOscillator(), g = ctx.createGain();
  o.type = type; o.frequency.setValueAtTime(freq, t);
  g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(gain, t + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  o.connect(g).connect(ctx.destination); o.start(t); o.stop(t + dur + 0.02);
};
const SOUNDS = {
  click: () => tone(520, 0.06, "triangle", 0.04),
  roll: () => { for (let i = 0; i < 6; i++) tone(180 + Math.random() * 260, 0.05, "square", 0.025, i * 0.055); },
  gain: () => { tone(660, 0.1, "sine", 0.05); tone(880, 0.14, "sine", 0.05, 0.08); },
  build: () => { tone(220, 0.09, "square", 0.04); tone(330, 0.12, "triangle", 0.05, 0.07); },
  turn: () => { tone(523, 0.12, "sine", 0.05); tone(659, 0.12, "sine", 0.05, 0.1); tone(784, 0.2, "sine", 0.05, 0.2); },
  seven: () => { tone(110, 0.35, "sawtooth", 0.05); tone(98, 0.4, "sawtooth", 0.04, 0.12); },
  error: () => tone(150, 0.18, "sawtooth", 0.04),
  win: () => [523, 659, 784, 1046].forEach((f, i) => tone(f, 0.25, "triangle", 0.06, i * 0.13)),
};

export const fx = {
  enabled: false,
  setSound(on, fromUserGesture = false) {
    this.enabled = Boolean(on);
    // Do not construct or resume WebAudio during page initialization. Browsers
    // require a real user gesture, so unlocking is explicit and error-safe.
    if (this.enabled && fromUserGesture) this.unlockAudio();
  },
  unlockAudio() {
    if (!this.enabled) return;
    if (!ctx) {
      const AudioContextCtor = typeof window === "undefined" ? null : (window.AudioContext || window.webkitAudioContext);
      if (!AudioContextCtor) return;
      try { ctx = new AudioContextCtor(); } catch (_) { ctx = null; return; }
    }
    if (ctx?.state === "suspended") {
      try { void ctx.resume().catch(() => {}); } catch (_) { /* audio is optional */ }
    }
  },
  play(name) { if (this.enabled && ctx?.state === "running" && SOUNDS[name]) SOUNDS[name](); },
  toast(text, kind = "", important = false) {
    const root = document.getElementById("toast-root"); if (!root) return;
    // Routine events linger a bit longer than before (they were unreadable at
    // 2.2s); VP/faction events get two extra seconds on top of that.
    const life = { chat: 7000, rare: 10000, err: 4000 }[kind] ?? (important ? 4200 : 3200);
    const t = h("div", { class: `toast ${kind}${important ? " important" : ""}`, role: "status" }, text);
    root.append(t);
    setTimeout(() => { t.style.animation = "toastout .25s forwards"; setTimeout(() => t.remove(), 260); }, life);
    while (root.children.length > 6) root.firstChild.remove();
  },
  announce(text) { const l = document.getElementById("live"); if (l) { l.textContent = ""; setTimeout(() => { l.textContent = text; }, 30); } },
  confetti() {
    const wrap = h("div", { class: "confetti", "aria-hidden": "true" });
    const colors = ["#f3cd7e", "#dca94b", "#e8604f", "#6fb7f2", "#67c08b", "#c58bf0"];
    for (let i = 0; i < 90; i++) {
      const p = h("i", { style: { left: `${Math.random() * 100}%`, background: colors[i % colors.length], animationDuration: `${2.4 + Math.random() * 2.2}s`, animationDelay: `${Math.random() * 0.9}s`, transform: `rotate(${Math.random() * 360}deg)` } });
      wrap.append(p);
    }
    document.body.append(wrap); setTimeout(() => wrap.remove(), 6000);
  },
};


// If sound was already enabled in localStorage, unlock only on the first actual
// user gesture. This avoids autoplay warnings on load and still restores sound.
if (typeof document !== "undefined") {
  const unlockAfterGesture = () => {
    fx.unlockAudio();
    if (fx.enabled && ctx?.state === "running") {
      document.removeEventListener("pointerdown", unlockAfterGesture, true);
      document.removeEventListener("keydown", unlockAfterGesture, true);
    }
  };
  document.addEventListener("pointerdown", unlockAfterGesture, true);
  document.addEventListener("keydown", unlockAfterGesture, true);
}
