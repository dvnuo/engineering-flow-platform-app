// Two small charts, written as SVG by hand: nothing is loaded from anywhere.
//
// The geometry (the scale, where a bar or a point sits) is plain arithmetic
// and is tested under node. The functions that build elements touch the
// document only when they are called, never when this file is imported.
// Colours come from style.css through the classes set here, so the charts
// follow the light and the dark theme without a line of code.

const SVG = "http://www.w3.org/2000/svg";
const WIDTH = 480;
const HEIGHT = 180;
const PLOT = { left: 34, top: 12, width: WIDTH - 34 - 8, height: HEIGHT - 12 - 26 };

function round(value) {
  return Math.round(value * 10) / 10;
}

// The top of the axis: the next multiple of `step` above the largest value.
export function axisTop(values, step = 20) {
  const most = Math.max(0, ...values);
  return Math.max(step, Math.ceil(most / step) * step);
}

// One rectangle for every value, side by side in `box`, the tallest at `top`.
export function barsIn(values, box, top) {
  const slot = box.width / values.length;
  return values.map((value, index) => {
    const height = round((box.height * Math.min(Math.max(value, 0), top)) / top);
    return {
      x: round(box.left + index * slot + slot * 0.12),
      y: round(box.top + box.height - height),
      width: round(slot * 0.76),
      height,
    };
  });
}

// One point for every value, from the left edge of `box` to the right; a single value sits in the middle.
export function pointsIn(values, box, top) {
  return values.map((value, index) => ({
    x: round(box.left + (values.length > 1 ? (box.width * index) / (values.length - 1) : box.width / 2)),
    y: round(box.top + box.height * (1 - Math.min(Math.max(value, 0), top) / top)),
  }));
}

export function pathThrough(points) {
  return points.map((point, index) => (index ? "L" : "M") + point.x + " " + point.y).join("");
}

function node(name, attributes, text) {
  const made = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes || {})) {
    made.setAttribute(key, String(value));
  }
  if (text !== undefined) {
    made.textContent = text; // text, never markup
  }
  return made;
}

// The frame both charts share: three grid lines with their values, and a label at each end of the axis.
function frame(svg, top, startLabel, endLabel) {
  svg.setAttribute("viewBox", "0 0 " + WIDTH + " " + HEIGHT);
  svg.replaceChildren();
  for (const share of [0, 0.5, 1]) {
    const y = round(PLOT.top + PLOT.height * (1 - share));
    svg.append(
      node("line", { class: "grid", x1: PLOT.left, x2: PLOT.left + PLOT.width, y1: y, y2: y }),
      node("text", { class: "tick", x: PLOT.left - 6, y: y + 4, "text-anchor": "end" }, String(Math.round(top * share)))
    );
  }
  const below = PLOT.top + PLOT.height + 17;
  svg.append(
    node("text", { class: "tick", x: PLOT.left, y: below }, startLabel),
    node("text", { class: "tick", x: PLOT.left + PLOT.width, y: below, "text-anchor": "end" }, endLabel)
  );
}

// Speed over the sprint: one bar for every few seconds, and the average as a dashed line.
export function drawSpeed(svg, series, bucketSeconds, average) {
  const top = axisTop(series.concat([average]));
  frame(svg, top, "0 s", series.length * bucketSeconds + " s");
  barsIn(series, PLOT, top).forEach((bar, index) => {
    const rect = node("rect", { class: "bar", x: bar.x, y: bar.y, width: bar.width, height: bar.height, rx: 2 });
    const from = index * bucketSeconds;
    rect.append(node("title", {}, from + " to " + (from + bucketSeconds) + " s: " + Math.round(series[index]) + " words a minute"));
    svg.append(rect);
  });
  const y = pointsIn([average], PLOT, top)[0].y;
  svg.append(
    node("line", { class: "average", x1: PLOT.left, x2: PLOT.left + PLOT.width, y1: y, y2: y }),
    node("text", { class: "tick strong", x: PLOT.left + PLOT.width, y: y - 5, "text-anchor": "end" }, "average " + average)
  );
}

// The member's sprints, oldest first: a line through the speeds. A sprint
// that was not ranked is a hollow dot, the best one a larger dot.
export function drawHistory(svg, runs, best, describe) {
  const speeds = runs.map((run) => run.wpm);
  const top = axisTop(speeds);
  frame(svg, top, runs.length > 1 ? "oldest" : "", runs.length > 1 ? "latest" : "");
  const points = pointsIn(speeds, PLOT, top);
  if (points.length > 1) {
    svg.append(node("path", { class: "trend", d: pathThrough(points) }));
  }
  points.forEach((point, index) => {
    const run = runs[index];
    const isBest = Boolean(best) && run.at === best.at;
    const dot = node("circle", {
      class: "dot" + (run.ranked ? "" : " hollow") + (isBest ? " best" : ""),
      cx: point.x,
      cy: point.y,
      r: isBest ? 5 : 3.5,
    });
    dot.append(node("title", {}, describe(run)));
    svg.append(dot);
  });
}
