// All artwork is inline SVG — no image files, no external fonts, so the template works offline
// and stays crisp at any zoom. `defsMarkup()` returns <symbol>/<pattern> definitions that are
// injected once into the page; `icon(name)` returns a <use> reference.

const S = (id, vb, body) => `<symbol id="${id}" viewBox="${vb}">${body}</symbol>`;
const stroke = 'fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"';

export function defsMarkup() {
  return `<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
  <!-- resources -->
  ${S("res-wood", "0 0 24 24", `<path d="M12 2 6 11h3l-4 6h5v5h4v-5h5l-4-6h3z" fill="currentColor"/>`)}
  ${S("res-clay", "0 0 24 24", `<g fill="currentColor"><rect x="2" y="5" width="9.5" height="6" rx="1"/><rect x="12.5" y="5" width="9.5" height="6" rx="1"/><rect x="6" y="12.5" width="12" height="6" rx="1"/></g>`)}
  ${S("res-grain", "0 0 24 24", `<g ${stroke} stroke-width="1.8"><path d="M12 22V6"/><path d="M12 9c-3 0-4.5-2-4.5-4.5C10 4.5 12 6 12 9zM12 9c3 0 4.5-2 4.5-4.5C14 4.5 12 6 12 9zM12 14c-3 0-4.5-2-4.5-4.5 2.5 0 4.5 1.500 4.500 4.500zM12 14c3 0 4.500-2 4.500-4.500-2.500 0-4.500 1.500-4.500 4.500z"/></g>`)}
  ${S("res-wool", "0 0 24 24", `<g fill="currentColor"><circle cx="8" cy="11" r="4.5"/><circle cx="13.5" cy="9.2" r="4.8"/><circle cx="17" cy="13" r="4"/><circle cx="11" cy="14.5" r="4.4"/><rect x="8" y="17" width="2" height="4" rx="1"/><rect x="14" y="17" width="2" height="4" rx="1"/></g>`)}
  ${S("res-ore", "0 0 24 24", `<path d="M2 20 9 7l4 6 3-4 6 11z" fill="currentColor"/>`)}
  <!-- terrain emblems (drawn faintly under number tokens) -->
  ${S("ter-forest", "0 0 40 40", `<g fill="currentColor"><path d="M12 6 5 20h5l-4 8h12l-4-8h5z"/><path d="M27 12 21 24h4l-3 6h10l-3-6h4z"/></g>`)}
  ${S("ter-hills", "0 0 40 40", `<g fill="currentColor"><rect x="5" y="8" width="14" height="8" rx="1.5"/><rect x="21" y="8" width="14" height="8" rx="1.5"/><rect x="13" y="18" width="14" height="8" rx="1.5"/></g>`)}
  ${S("ter-fields", "0 0 40 40", `<g ${stroke} stroke-width="2"><path d="M8 32V10M16 32V8M24 32V10M32 32V12"/><path d="M8 14l-3-3M8 14l3-3M16 12l-3-3M16 12l3-3M24 14l-3-3M24 14l3-3M32 16l-3-3M32 16l3-3"/></g>`)}
  ${S("ter-pasture", "0 0 40 40", `<g fill="currentColor"><circle cx="13" cy="19" r="6"/><circle cx="21" cy="16" r="6.5"/><circle cx="27" cy="21" r="5.5"/><circle cx="18" cy="23" r="6"/><rect x="13" y="26" width="2.600" height="6" rx="1.300"/><rect x="23" y="26" width="2.600" height="6" rx="1.300"/></g>`)}
  ${S("ter-mountains", "0 0 40 40", `<g fill="currentColor"><path d="M3 32 15 11l7 12 4-6 11 15z"/></g>`)}
  ${S("ter-wasteland", "0 0 40 40", `<g ${stroke} stroke-width="2"><path d="M6 30 14 20l-3-5 9-6 3 8 8 2-5 8z"/><path d="M14 20l8-1"/></g>`)}
  ${S("ter-ruins", "0 0 40 40", `<g fill="currentColor"><rect x="7" y="12" width="5" height="18" rx="1"/><rect x="17" y="8" width="5" height="22" rx="1"/><rect x="27" y="16" width="5" height="14" rx="1"/><rect x="4" y="30" width="32" height="3" rx="1"/></g>`)}
  ${S("ter-unknown", "0 0 40 40", `<g ${stroke} stroke-width="2.400"><path d="M14 15c0-4 3-6 6-6s6 2 6 5c0 4-6 4-6 9"/><circle cx="20" cy="31" r=".6"/></g>`)}
  <!-- pieces -->
  ${S("pc-outpost", "0 0 24 24", `<path d="M12 3 21 11v10H3V11z" />`)}
  ${S("pc-metropolis", "0 0 28 28", `<path d="M3 26V13l5-3V5l3 2 3-4 3 4 3-2v5l5 3v13z"/>`)}
  ${S("pc-raider", "0 0 32 40", `<path d="M16 2c-6 0-10 5-10 11v6c0 4-3 6-4 9h28c-1-3-4-5-4-9v-6C26 7 22 2 16 2z" fill="currentColor"/><circle cx="12.500" cy="15" r="2" fill="#f4e7a4"/><circle cx="19.500" cy="15" r="2" fill="#f4e7a4"/><path d="M10 37h12" stroke="#f4e7a4" stroke-width="2" stroke-linecap="round"/>`)}
  <!-- ui icons -->
  ${S("ic-route", "0 0 24 24", `<g ${stroke} stroke-width="2.200"><path d="M5 19c3-1 4-4 7-5s5-3 7-9"/><circle cx="5" cy="19" r="1.500" fill="currentColor"/><circle cx="19" cy="5" r="1.500" fill="currentColor"/></g>`)}
  ${S("ic-outpost", "0 0 24 24", `<g ${stroke} stroke-width="2.100"><path d="M4 11 12 4l8 7v9H4z"/><path d="M10 20v-5h4v5"/></g>`)}
  ${S("ic-metropolis", "0 0 24 24", `<g ${stroke} stroke-width="2"><path d="M3 21V11l4-2V5l3 2 2-3 2 3 3-2v4l4 2v10z"/><path d="M10 21v-4h4v4"/></g>`)}
  ${S("ic-charter", "0 0 24 24", `<g ${stroke} stroke-width="2"><rect x="5" y="3" width="14" height="18" rx="2.500"/><path d="M12 8.500l1.200 2.500 2.700.3-2 1.800.6 2.700-2.500-1.400-2.500 1.400.6-2.700-2-1.800 2.700-.3z"/></g>`)}
  ${S("ic-trade", "0 0 24 24", `<g ${stroke} stroke-width="2.100"><path d="M4 8h14l-3-3M20 16H6l3 3"/></g>`)}
  ${S("ic-dice", "0 0 24 24", `<g ${stroke} stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="3.500"/></g><g fill="currentColor"><circle cx="9" cy="9" r="1.400"/><circle cx="15" cy="15" r="1.400"/><circle cx="15" cy="9" r="1.400"/><circle cx="9" cy="15" r="1.400"/></g>`)}
  ${S("ic-end", "0 0 24 24", `<g ${stroke} stroke-width="2.200"><path d="M5 12h12M13 6l6 6-6 6"/></g>`)}
  ${S("ic-sword", "0 0 24 24", `<g ${stroke} stroke-width="2"><path d="M14 4h6v6L10 20l-4-4zM6 16l-3 3M9 13l2 2"/></g>`)}
  ${S("ic-star", "0 0 24 24", `<path d="M12 2.500l2.900 6 6.600.9-4.800 4.600 1.200 6.500L12 17.400l-5.900 3.100 1.200-6.500L2.500 9.400l6.600-.9z" fill="currentColor"/>`)}
  ${S("ic-horn", "0 0 24 24", `<g ${stroke} stroke-width="2"><path d="M4 8c8 0 14 3 16 9-8 1-13-2-16-9z"/><path d="M4 8c0 6 4 10 10 12"/></g>`)}
  ${S("ic-coins", "0 0 24 24", `<g ${stroke} stroke-width="2"><ellipse cx="9" cy="8" rx="6" ry="3"/><path d="M3 8v5c0 1.700 2.700 3 6 3s6-1.300 6-3V8"/><path d="M15 11c3.300 0 6 1.300 6 3v4c0 1.700-2.700 3-6 3-1.700 0-3.200-.4-4.200-1"/></g>`)}
  ${S("ic-compass", "0 0 24 24", `<g ${stroke} stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M15.500 8.500 13.500 13.500 8.500 15.500 10.500 10.500z" fill="currentColor"/></g>`)}
  ${S("ic-hunt", "0 0 24 24", `<g ${stroke} stroke-width="2"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 1v5M12 18v5M1 12h5M18 12h5"/></g>`)}
  ${S("ic-gear", "0 0 24 24", `<g ${stroke} stroke-width="2"><circle cx="12" cy="12" r="3.200"/><path d="M12 2.500v3M12 18.500v3M2.500 12h3M18.500 12h3M5.300 5.300l2.100 2.100M16.600 16.600l2.100 2.100M18.700 5.300l-2.100 2.100M7.400 16.600l-2.100 2.100"/></g>`)}
  ${S("ic-chat", "0 0 24 24", `<g ${stroke} stroke-width="2"><path d="M4 5h16v11H9l-5 4z"/></g>`)}
  ${S("ic-log", "0 0 24 24", `<g ${stroke} stroke-width="2"><path d="M5 5h14M5 10h14M5 15h9M5 20h6"/></g>`)}
  ${S("ic-crown", "0 0 24 24", `<path d="M3 18 2 7l5.500 4L12 4l4.500 7L22 7l-1 11z" fill="currentColor"/>`)}
  ${S("ic-road", "0 0 24 24", `<g ${stroke} stroke-width="2.200"><path d="M3 18 10 6M21 18 14 6M12 8v2M12 13v2M12 18v2"/></g>`)}
  ${S("ic-cards", "0 0 24 24", `<g ${stroke} stroke-width="2"><rect x="3" y="6" width="12" height="15" rx="2"/><path d="M8 3h11a2 2 0 0 1 2 2v12"/></g>`)}
  ${S("ic-blight", "0 0 24 24", `<g fill="currentColor"><circle cx="12" cy="12" r="3.500"/><circle cx="5" cy="9" r="2"/><circle cx="19" cy="9" r="2"/><circle cx="8" cy="19" r="2"/><circle cx="16" cy="19" r="2"/><circle cx="12" cy="3.500" r="1.600"/></g>`)}
  ${S("ic-anchor", "0 0 24 24", `<g ${stroke} stroke-width="2"><circle cx="12" cy="5" r="2.200"/><path d="M12 7.200V21M6 12H4a8 8 0 0 0 16 0h-2M8 11h8"/></g>`)}
  ${S("ic-plus", "0 0 24 24", `<g ${stroke} stroke-width="2.400"><path d="M12 5v14M5 12h14"/></g>`)}
  ${S("ic-minus", "0 0 24 24", `<g ${stroke} stroke-width="2.400"><path d="M5 12h14"/></g>`)}
  ${S("ic-fit", "0 0 24 24", `<g ${stroke} stroke-width="2.200"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></g>`)}
  ${S("ic-close", "0 0 24 24", `<g ${stroke} stroke-width="2.400"><path d="M6 6l12 12M18 6 6 18"/></g>`)}
  ${S("ic-check", "0 0 24 24", `<g ${stroke} stroke-width="2.600"><path d="M5 12.500l4.500 4.500L19 7.500"/></g>`)}
  ${S("ic-sound", "0 0 24 24", `<g ${stroke} stroke-width="2"><path d="M4 9v6h4l5 4V5L8 9zM16.500 8.500a5 5 0 0 1 0 7"/></g>`)}
  ${S("ic-mute", "0 0 24 24", `<g ${stroke} stroke-width="2"><path d="M4 9v6h4l5 4V5L8 9zM17 9l4 6M21 9l-4 6"/></g>`)}
  ${S("ic-help", "0 0 24 24", `<g ${stroke} stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M9.500 9.500c0-1.500 1.200-2.500 2.500-2.500s2.500 1 2.500 2.300c0 1.800-2.500 2-2.500 4M12 17v.1"/></g>`)}
  <!-- patterns -->
  <pattern id="pat-fog" width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">
    <rect width="14" height="14" fill="var(--t-unknown)"/><path d="M0 0v14" stroke="rgba(160,190,225,.10)" stroke-width="2"/></pattern>
  <pattern id="pat-blight" width="10" height="10" patternUnits="userSpaceOnUse">
    <circle cx="3" cy="3" r="1.500" fill="rgba(210,140,255,.55)"/><circle cx="8" cy="7" r="1" fill="rgba(210,140,255,.45)"/></pattern>
  <radialGradient id="grad-ocean" cx="50%" cy="45%" r="75%"><stop offset="0" stop-color="var(--ocean-1)"/><stop offset="1" stop-color="var(--ocean-2)"/></radialGradient>
  <filter id="soft-shadow" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="1.600" stdDeviation="1.600" flood-color="#000" flood-opacity=".5"/></filter>
  <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3.500" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs></svg>`;
}

export const icon = (name, cls = "") =>
  `<svg class="ic ${cls}" aria-hidden="true"><use href="#ic-${name}"/></svg>`;
export const resIcon = (res, cls = "") =>
  `<svg class="ic res res-${res} ${cls}" aria-hidden="true"><use href="#res-${res}"/></svg>`;

/** Shape marker so players stay distinguishable without colour (colour-blind safe). */
export function shapePath(shape, s = 6) {
  switch (shape) {
    case "square": return `M${-s} ${-s}H${s}V${s}H${-s}Z`;
    case "diamond": return `M0 ${-s * 1.25}L${s * 1.25} 0L0 ${s * 1.25}L${-s * 1.25} 0Z`;
    case "triangle": return `M0 ${-s * 1.2}L${s * 1.2} ${s}H${-s * 1.2}Z`;
    default: return `M${-s} 0a${s} ${s} 0 1 0 ${2 * s} 0a${s} ${s} 0 1 0 ${-2 * s} 0`;
  }
}
