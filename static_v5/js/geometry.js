// Pure geometry for the pointy-top hex board. The server sends vertex x/y already laid out with
// hex radius R = 50; hex centres are derived from axial (q, r) and verified against vertices in tests.

export const R = 50;
const SQ3 = Math.sqrt(3);

export function hexCenter(q, r) {
  return { x: R * SQ3 * (q + r / 2), y: R * 1.5 * r };
}

export function hexCorners(cx, cy, radius = R) {
  const pts = [];
  for (let k = 0; k < 6; k++) {
    const a = ((30 + 60 * k) * Math.PI) / 180;
    pts.push({ x: cx + radius * Math.cos(a), y: cy + radius * Math.sin(a) });
  }
  return pts;
}

export const pointsAttr = (pts) => pts.map((p) => `${round(p.x)},${round(p.y)}`).join(" ");
export const round = (n) => Math.round(n * 100) / 100;

/** Axis-aligned bounds of all hexes (revealed or not) plus a margin for ports and coast. */
export function boardBounds(hexes, margin = 46) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const h of hexes) {
    const c = hexCenter(h.q, h.r);
    minX = Math.min(minX, c.x - R * 0.87); maxX = Math.max(maxX, c.x + R * 0.87);
    minY = Math.min(minY, c.y - R); maxY = Math.max(maxY, c.y + R);
  }
  if (!isFinite(minX)) return { x: -200, y: -200, w: 400, h: 400 };
  return { x: minX - margin, y: minY - margin, w: maxX - minX + 2 * margin, h: maxY - minY + 2 * margin };
}

export function edgeGeometry(a, b) {
  const dx = b.x - a.x, dy = b.y - a.y;
  return { mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2, len: Math.hypot(dx, dy), angle: (Math.atan2(dy, dx) * 180) / Math.PI };
}

/** Pushes a point away from the board centre — used to hang port badges off the coast. */
export function outward(x, y, dist, cx = 0, cy = 0) {
  const dx = x - cx, dy = y - cy, d = Math.hypot(dx, dy) || 1;
  return { x: x + (dx / d) * dist, y: y + (dy / d) * dist };
}

/** Linear fit helper: the view box that fits `bounds` into a w×h viewport (letterboxed). */
export function fitView(bounds, w, h) {
  const k = Math.min(w / bounds.w, h / bounds.h);
  return { k, tx: (w - bounds.w * k) / 2 - bounds.x * k, ty: (h - bounds.h * k) / 2 - bounds.y * k };
}

export function clampView(view, bounds, w, h, minK, maxK) {
  const k = Math.max(minK, Math.min(maxK, view.k));
  const slackX = Math.max(60, w * 0.25), slackY = Math.max(60, h * 0.25);
  const left = -(bounds.x + bounds.w) * k + slackX, right = w - bounds.x * k - slackX;
  const top = -(bounds.y + bounds.h) * k + slackY, bottom = h - bounds.y * k - slackY;
  return { k, tx: Math.max(left, Math.min(right, view.tx)), ty: Math.max(top, Math.min(bottom, view.ty)) };
}
