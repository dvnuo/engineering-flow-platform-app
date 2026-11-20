// Zoom arithmetic for the viewer. No DOM here: sizes come in as numbers.

export const ZOOM_STEPS = [0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 12, 16];

// The largest zoom at which the image fits the box, never above 100%:
// blowing a small screenshot up to fill the page helps nobody.
export function fitZoom(imageWidth, imageHeight, boxWidth, boxHeight) {
  if (imageWidth < 1 || imageHeight < 1 || boxWidth < 1 || boxHeight < 1) {
    return 1;
  }
  const zoom = Math.min(1, boxWidth / imageWidth, boxHeight / imageHeight);
  return Math.max(ZOOM_STEPS[0] / 2, zoom);
}

// The next step up (direction 1) or down (-1) from any zoom, fitted ones included.
export function stepZoom(zoom, direction) {
  if (direction > 0) {
    const next = ZOOM_STEPS.find((step) => step > zoom * 1.001);
    return next === undefined ? ZOOM_STEPS[ZOOM_STEPS.length - 1] : next;
  }
  const below = ZOOM_STEPS.filter((step) => step < zoom * 0.999);
  return below.length ? below[below.length - 1] : ZOOM_STEPS[0];
}

export function zoomText(zoom) {
  return Math.round(zoom * 100) + "%";
}

export function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}
