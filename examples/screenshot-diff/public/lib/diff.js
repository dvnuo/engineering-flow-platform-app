// The comparison itself: two images as RGBA bytes in, a mask, counts, the
// changed regions and a picture of the differences out.
//
// It works on plain typed arrays ({width, height, data} as getImageData()
// gives them) and touches no DOM, so node can test it, and it works in
// slices: step() handles a few rows and returns, so the page can draw a
// progress bar and stay responsive while a 20-megapixel pair is compared.

// What the mask says about a pixel.
export const SAME = 0;
export const CHANGED = 1;
export const ANTIALIASED = 2; // differs, but only as the blend on an edge that moved by a fraction of a pixel
export const ONLY_ONE = 3; // exists in one image only (the sizes differ)
export const NEITHER = 4; // outside both images (one is wider, the other taller)

export const MAX_PIXELS = 25000000;
export const CELL = 12; // changes are grouped on a grid of this many pixels

const HIGHLIGHT = [226, 0, 116];
const BLEND = [247, 201, 72];
const STRIPES = [
  [70, 120, 210],
  [176, 202, 244],
];

// A sentence when an image is too large to compare in a tab, else "".
export function sizeProblem(width, height) {
  if (width < 1 || height < 1) {
    return "The image is empty.";
  }
  if (width * height > MAX_PIXELS) {
    const megapixels = (count) => (count / 1e6).toFixed(1).replace(/\.0$/, "");
    return (
      "This image is " + width + " x " + height + " (" + megapixels(width * height) + " megapixels). The page compares images up to " +
      megapixels(MAX_PIXELS) + " megapixels: beyond that a tab runs out of memory."
    );
  }
  return "";
}

// Brightness as the eye weighs it, with transparency shown on white.
function brightness(data, at) {
  const alpha = data[at + 3] / 255;
  return (0.299 * data[at] + 0.587 * data[at + 1] + 0.114 * data[at + 2]) * alpha + 255 * (1 - alpha);
}

// Does the pixel sit in a flat area: do at least three of its neighbours have exactly its colour?
function isFlat(image, x, y) {
  const { width, height, data } = image;
  if (x >= width || y >= height) {
    return false;
  }
  const at = (y * width + x) * 4;
  const left = Math.max(x - 1, 0);
  const right = Math.min(x + 1, width - 1);
  const top = Math.max(y - 1, 0);
  const bottom = Math.min(y + 1, height - 1);
  // At the border of the image the missing neighbours count as one equal neighbour.
  let equal = x === left || x === right || y === top || y === bottom ? 1 : 0;
  for (let ny = top; ny <= bottom; ny += 1) {
    for (let nx = left; nx <= right; nx += 1) {
      const other = (ny * width + nx) * 4;
      if (other !== at && data[other] === data[at] && data[other + 1] === data[at + 1] && data[other + 2] === data[at + 2] && data[other + 3] === data[at + 3]) {
        equal += 1;
        if (equal > 2) {
          return true;
        }
      }
    }
  }
  return false;
}

// Is the pixel the blend that anti-aliasing puts on an edge?
//
// Such a pixel lies between its neighbours: some are darker, some brighter,
// and hardly any is equal to it. And the edge is real when the darkest or
// the brightest neighbour sits in a flat area in both images. The test is
// from V. Vysniauskas, "Anti-aliased Pixel and Intensity Slope Detector"
// (2009), the method the pixelmatch library made popular.
function isBlend(image, x, y, other) {
  const { width, height, data } = image;
  const at = (y * width + x) * 4;
  const here = brightness(data, at);
  const left = Math.max(x - 1, 0);
  const right = Math.min(x + 1, width - 1);
  const top = Math.max(y - 1, 0);
  const bottom = Math.min(y + 1, height - 1);
  let equal = x === left || x === right || y === top || y === bottom ? 1 : 0;
  let darkest = 0;
  let brightest = 0;
  let darkX = 0;
  let darkY = 0;
  let brightX = 0;
  let brightY = 0;
  for (let ny = top; ny <= bottom; ny += 1) {
    for (let nx = left; nx <= right; nx += 1) {
      if (nx === x && ny === y) {
        continue;
      }
      const delta = brightness(data, (ny * width + nx) * 4) - here;
      if (delta === 0) {
        equal += 1;
        if (equal > 2) {
          return false; // part of a flat area, not a blend
        }
      } else if (delta < darkest) {
        darkest = delta;
        darkX = nx;
        darkY = ny;
      } else if (delta > brightest) {
        brightest = delta;
        brightX = nx;
        brightY = ny;
      }
    }
  }
  if (darkest === 0 || brightest === 0) {
    return false; // the darkest or the brightest spot around: an edge blend is neither
  }
  return (isFlat(image, darkX, darkY) && isFlat(other, darkX, darkY)) || (isFlat(image, brightX, brightY) && isFlat(other, brightX, brightY));
}

// Start a comparison. ``tolerance`` is the difference per colour channel
// (0-255) up to which two pixels still count as the same. The images are
// laid on top of each other at their top left corners; when their sizes
// differ, the part both cover is compared and the rest is marked.
export function createComparison(expected, actual, { tolerance = 0, ignoreAntialiasing = false } = {}) {
  const width = Math.max(expected.width, actual.width);
  const height = Math.max(expected.height, actual.height);
  const columns = Math.ceil(width / CELL);
  const rows = Math.ceil(height / CELL);
  return {
    expected,
    actual,
    tolerance,
    ignoreAntialiasing,
    width,
    height,
    overlapWidth: Math.min(expected.width, actual.width),
    overlapHeight: Math.min(expected.height, actual.height),
    row: 0, // the next row step() will handle
    changed: 0,
    antialiased: 0,
    onlyOne: 0,
    mask: new Uint8Array(width * height),
    picture: new Uint8ClampedArray(width * height * 4),
    // Per grid cell: how many pixels changed there and the box around them.
    columns,
    rows,
    cellCount: new Uint32Array(columns * rows),
    cellLeft: new Int32Array(columns * rows).fill(width),
    cellTop: new Int32Array(columns * rows).fill(height),
    cellRight: new Int32Array(columns * rows).fill(-1),
    cellBottom: new Int32Array(columns * rows).fill(-1),
  };
}

function paint(picture, at, color) {
  picture[at] = color[0];
  picture[at + 1] = color[1];
  picture[at + 2] = color[2];
  picture[at + 3] = 255;
}

// Compare up to ``maxRows`` more rows. True when the whole image is done.
export function step(state, maxRows = 32) {
  const { expected, actual, width, overlapWidth, overlapHeight, tolerance, mask, picture } = state;
  const a = expected.data;
  const b = actual.data;
  const end = Math.min(state.height, state.row + maxRows);
  for (let y = state.row; y < end; y += 1) {
    // Below the shared part, the taller image is alone; right of it, the wider one.
    const present = y < overlapHeight ? width : y < expected.height ? expected.width : actual.width;
    const shared = y < overlapHeight ? overlapWidth : 0;
    const cellRow = Math.floor(y / CELL) * state.columns;
    for (let x = 0; x < width; x += 1) {
      const at = y * width + x;
      const out = at * 4;
      if (x >= present) {
        mask[at] = NEITHER; // the picture stays transparent here
        continue;
      }
      if (x >= shared) {
        mask[at] = ONLY_ONE;
        state.onlyOne += 1;
        paint(picture, out, STRIPES[((x + y) >> 2) & 1]);
        continue;
      }
      const ia = (y * expected.width + x) * 4;
      const ib = (y * actual.width + x) * 4;
      const differs =
        (a[ia + 3] !== 0 || b[ib + 3] !== 0) && // two fully transparent pixels look the same, whatever colour they hide
        (Math.abs(a[ia] - b[ib]) > tolerance ||
          Math.abs(a[ia + 1] - b[ib + 1]) > tolerance ||
          Math.abs(a[ia + 2] - b[ib + 2]) > tolerance ||
          Math.abs(a[ia + 3] - b[ib + 3]) > tolerance);
      if (!differs) {
        // A pale ghost of the screenshot, so a change can be placed.
        const ghost = 255 - (255 - brightness(b, ib)) * 0.25;
        picture[out] = ghost;
        picture[out + 1] = ghost;
        picture[out + 2] = ghost;
        picture[out + 3] = 255;
      } else if (state.ignoreAntialiasing && (isBlend(expected, x, y, actual) || isBlend(actual, x, y, expected))) {
        mask[at] = ANTIALIASED;
        state.antialiased += 1;
        paint(picture, out, BLEND);
      } else {
        mask[at] = CHANGED;
        state.changed += 1;
        paint(picture, out, HIGHLIGHT);
        const cell = cellRow + Math.floor(x / CELL);
        state.cellCount[cell] += 1;
        state.cellLeft[cell] = Math.min(state.cellLeft[cell], x);
        state.cellRight[cell] = Math.max(state.cellRight[cell], x);
        state.cellTop[cell] = Math.min(state.cellTop[cell], y);
        state.cellBottom[cell] = Math.max(state.cellBottom[cell], y);
      }
    }
  }
  state.row = end;
  return end >= state.height;
}

// The whole comparison in one go (for tests and small images).
export function compare(expected, actual, options) {
  const state = createComparison(expected, actual, options);
  while (!step(state, 256)) {
    // keep going
  }
  return state;
}

// The numbers of a finished comparison.
export function summarize(state) {
  const compared = state.overlapWidth * state.overlapHeight;
  return {
    width: state.width,
    height: state.height,
    compared,
    changed: state.changed,
    antialiased: state.antialiased,
    onlyOne: state.onlyOne,
    percent: compared ? (state.changed / compared) * 100 : 0,
    sameSize: state.expected.width === state.actual.width && state.expected.height === state.actual.height,
    identical: state.changed === 0 && state.onlyOne === 0,
  };
}

// "0.42%": never "0%" for a difference that is there, and "100%" only when every pixel differs.
export function percentText(percent) {
  if (percent === 0) {
    return "0%";
  }
  if (percent < 0.01) {
    return "less than 0.01%";
  }
  if (percent >= 100) {
    return "100%";
  }
  return (percent >= 10 ? Math.min(percent, 99.9).toFixed(1) : percent.toFixed(2)) + "%";
}

// The changed pixels as boxes: [{x, y, width, height, pixels}], largest first.
//
// Changes that lie in the same or in touching grid cells form one region, so
// the letters of a changed word become one box instead of forty. Two changes
// at most CELL pixels apart always end up together; more than 2 x CELL apart
// they only meet through other changes between them.
export function regions(state) {
  const { columns, rows, cellCount } = state;
  const seen = new Uint8Array(columns * rows);
  const found = [];
  for (let first = 0; first < cellCount.length; first += 1) {
    if (!cellCount[first] || seen[first]) {
      continue;
    }
    let left = state.width;
    let top = state.height;
    let right = -1;
    let bottom = -1;
    let pixels = 0;
    const todo = [first];
    seen[first] = 1;
    while (todo.length) {
      const cell = todo.pop();
      pixels += cellCount[cell];
      left = Math.min(left, state.cellLeft[cell]);
      top = Math.min(top, state.cellTop[cell]);
      right = Math.max(right, state.cellRight[cell]);
      bottom = Math.max(bottom, state.cellBottom[cell]);
      const column = cell % columns;
      const row = (cell - column) / columns;
      for (let ny = Math.max(row - 1, 0); ny <= Math.min(row + 1, rows - 1); ny += 1) {
        for (let nx = Math.max(column - 1, 0); nx <= Math.min(column + 1, columns - 1); nx += 1) {
          const next = ny * columns + nx;
          if (cellCount[next] && !seen[next]) {
            seen[next] = 1;
            todo.push(next);
          }
        }
      }
    }
    found.push({ x: left, y: top, width: right - left + 1, height: bottom - top + 1, pixels });
  }
  return found.sort((one, two) => two.pixels - one.pixels || one.y - two.y || one.x - two.x);
}
