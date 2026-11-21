// Pan and zoom as arithmetic. A view is {x, y, k}: a point of the map at
// (mx, my) is drawn at (x + mx * k, y + my * k) on the screen. No DOM here.

export const MIN_ZOOM = 0.2;
export const MAX_ZOOM = 4;

export function clampZoom(k) {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, k));
}

// Zoom by ``factor`` so that the map point under the screen point (sx, sy)
// stays where it is: what the visitor points at does not slide away.
export function zoomAt(view, factor, sx, sy) {
  const k = clampZoom(view.k * factor);
  const ratio = k / view.k;
  return { x: sx - (sx - view.x) * ratio, y: sy - (sy - view.y) * ratio, k };
}

export function pan(view, dx, dy) {
  return { x: view.x + dx, y: view.y + dy, k: view.k };
}

// The view that shows the box {left, top, right, bottom} centred in a
// width x height area, as large as fits but never beyond ``maxZoom``.
export function fitView(box, width, height, padding = 24, maxZoom = 1.25) {
  const boxWidth = Math.max(1, box.right - box.left);
  const boxHeight = Math.max(1, box.bottom - box.top);
  const k = clampZoom(Math.min(maxZoom, (width - 2 * padding) / boxWidth, (height - 2 * padding) / boxHeight));
  return {
    x: width / 2 - ((box.left + box.right) / 2) * k,
    y: height / 2 - ((box.top + box.bottom) / 2) * k,
    k,
  };
}

// Screen point to map point.
export function toMap(view, sx, sy) {
  return { x: (sx - view.x) / view.k, y: (sy - view.y) / view.k };
}

// Two fingers moved from ``before`` to ``after`` (each a pair of screen
// points): zoom by how much they spread and keep the point between them
// under the fingers.
export function pinch(view, before, after) {
  const span = (points) => Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y) || 1;
  const middle = (points) => ({ x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2 });
  const from = middle(before);
  const to = middle(after);
  const zoomed = zoomAt(view, span(after) / span(before), from.x, from.y);
  return pan(zoomed, to.x - from.x, to.y - from.y);
}
