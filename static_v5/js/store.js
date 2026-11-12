// Tiny observable store. One source of truth; modules subscribe and re-render only what changed.
// State is replaced immutably via `set`, so subscribers can compare references cheaply.

export function createStore(initial) {
  let state = initial;
  const subs = new Set();
  return {
    get: () => state,
    set(patch) {
      const next = typeof patch === "function" ? patch(state) : patch;
      if (!next || next === state) return;
      const prev = state;
      state = { ...state, ...next };
      for (const fn of [...subs]) fn(state, prev);
    },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
  };
}

/** Subscribe to a derived slice; the callback fires only when the selected value changes. */
export function watch(store, select, fn, eq = Object.is) {
  let last = select(store.get());
  return store.subscribe((s, p) => {
    const v = select(s);
    if (!eq(v, last)) { const old = last; last = v; fn(v, old, s, p); }
  });
}

export const initialState = () => ({
  screen: "lobby",           // "lobby" | "game"
  conn: "closed",            // "connecting" | "open" | "closed"
  session: null,             // { room_id, player_id, token }
  game: null,                // last server state (public + my private view)
  prev: null,                // previous server state, for diffs and animations
  error: null,
  ui: {
    mode: null,              // active build/target intent, e.g. { type: "build_route" }
    drawer: false, panel: "info", modal: null, lobbyTab: "new",
    theme: "night", sound: false, motion: "on", patterns: false, detail: false,
    tradeGive: null, tradeGet: null, filter: "all",
  },
});
