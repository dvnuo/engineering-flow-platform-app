// The geometry of the wheel and nothing else: no page, no clock. The page
// (app.js) builds the SVG from it and turns it; the tests run it under node.
//
// Angles are degrees, clockwise, with 0 at the top where the pointer is.
// Slice 0 starts at the top; the wheel turns clockwise, so a rotation of r
// brings to the pointer what was r degrees before it.

export const SLICE_COLOURS = 6; // style.css has that many slice colours; neighbours never share one

// Where slice `index` of `count` begins and ends on a wheel that has not turned.
export function sliceSpan(index, count) {
  const size = 360 / count;
  return { start: index * size, end: (index + 1) * size, middle: (index + 0.5) * size };
}

// Which colour a slice gets. Neighbours differ because the colours go round
// in order; only the last slice meets the first, and when both would get
// colour 0 (with 7 slices, 13, ...) the last one takes a colour that neither
// the first (0) nor the slice before it (5) has.
export function sliceColour(index, count) {
  if (count > 1 && index === count - 1 && index % SLICE_COLOURS === 0) {
    return 3;
  }
  return index % SLICE_COLOURS;
}

// The rotation at which slice `index` stands under the pointer: the first
// such rotation that is at least `turns` whole turns beyond `from`. `offset`
// (-0.5 .. 0.5 of a slice) moves the stop away from the middle of the slice,
// so the wheel does not always halt dead centre.
export function landingAngle(from, index, count, turns, offset = 0) {
  const size = 360 / count;
  const target = (((360 - (index + 0.5 + offset) * size) % 360) + 360) % 360;
  const least = from + turns * 360;
  const base = Math.floor(least / 360) * 360 + target;
  return base >= least ? base : base + 360;
}

// The slice under the pointer when the wheel has turned by `rotation`.
export function sliceAtPointer(rotation, count) {
  const at = (((-rotation % 360) + 360) % 360) / (360 / count);
  return Math.min(count - 1, Math.floor(at));
}

// Fast at first, then slower and slower: the way a wheel runs out.
export function easeOut(progress) {
  const left = 1 - Math.min(1, Math.max(0, progress));
  return 1 - left * left * left * left;
}

export function angleAt(from, to, progress) {
  return from + (to - from) * easeOut(progress);
}

function point(radius, degrees) {
  const angle = (degrees * Math.PI) / 180;
  return (Math.round(radius * Math.sin(angle) * 100) / 100) + " " + (Math.round(-radius * Math.cos(angle) * 100) / 100);
}

// The outline of a slice as an SVG path around (0, 0). A single slice is the
// whole disc, which one arc cannot draw: it is made of two halves.
export function wedgePath(radius, start, end) {
  if (end - start >= 360) {
    return "M" + point(radius, 0) + "A" + radius + " " + radius + " 0 1 1 " + point(radius, 180) +
      "A" + radius + " " + radius + " 0 1 1 " + point(radius, 0) + "Z";
  }
  const large = end - start > 180 ? 1 : 0;
  return "M0 0L" + point(radius, start) + "A" + radius + " " + radius + " 0 " + large + " 1 " + point(radius, end) + "Z";
}

// The size of the names on the wheel, in the units of its viewBox (the wheel
// is 200 across): the more slices, the smaller the letters.
export function labelSize(count) {
  return count <= 8 ? 11 : count <= 16 ? 9.5 : count <= 28 ? 7.5 : 6;
}

// A name short enough to fit between the hub and the rim at that size.
export function fitLabel(name, count) {
  const most = count <= 8 ? 12 : count <= 16 ? 13 : count <= 28 ? 14 : 15;
  const letters = Array.from(name); // by character, not by code unit: a name is not always ASCII
  return letters.length <= most ? name : letters.slice(0, most - 1).join("").trimEnd() + "...";
}
