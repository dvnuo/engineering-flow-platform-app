// Arithmetic for the page and nothing else: no page, no clock. app.js builds
// the SVG from it; the tests run it under node.

// The latencies of the last checks as the pieces of a line. A failed check
// has no latency (null): the line breaks there and the failure is marked at
// its place on the baseline instead.
//
// Returns {lines: [[{x, y}, ...], ...], misses: [x, ...], low, high}; a piece
// of one point is a check with a failure on either side.
export function sparkline(latency, width, height, pad = 2) {
  const known = latency.filter((value) => typeof value === "number");
  const low = known.length ? Math.min(...known) : 0;
  const high = known.length ? Math.max(...known) : 0;
  const step = latency.length > 1 ? (width - 2 * pad) / (latency.length - 1) : 0;
  const lines = [];
  const misses = [];
  let piece = null;
  latency.forEach((value, index) => {
    const x = round(latency.length > 1 ? pad + index * step : width / 2);
    if (typeof value !== "number") {
      misses.push(x);
      piece = null;
      return;
    }
    if (!piece) {
      piece = [];
      lines.push(piece);
    }
    // All the same latency (or one check): a level line through the middle.
    const share = high > low ? (value - low) / (high - low) : 0.5;
    piece.push({ x, y: round(height - pad - share * (height - 2 * pad)) });
  });
  return { lines, misses, low, high };
}

function round(value) {
  return Math.round(value * 10) / 10;
}

export function pointsOf(piece) {
  return piece.map((point) => point.x + "," + point.y).join(" ");
}

// "182 ms", "1.4 s": a latency a person can read.
export function duration(ms) {
  return ms < 1000 ? ms + " ms" : (Math.round(ms / 100) / 10).toFixed(1) + " s";
}

// How many environments are up, down and not yet checked.
export function tally(targets) {
  const count = { up: 0, down: 0, unknown: 0 };
  for (const target of targets) {
    count[target.last ? (target.last.up ? "up" : "down") : "unknown"] += 1;
  }
  return count;
}

// The sentence above the list: "4 up, 1 down." and what is still unknown.
export function headline(targets) {
  const count = tally(targets);
  if (targets.length === 0) {
    return "No environment is listed in targets.json.";
  }
  if (count.up + count.down === 0) {
    return "Nothing has been checked yet.";
  }
  return count.up + " up, " + count.down + " down" + (count.unknown ? ", " + count.unknown + " not checked yet." : ".");
}

// The environments in the groups of targets.json, in the order they first appear there.
export function grouped(targets) {
  const groups = [];
  for (const target of targets) {
    let group = groups.find((candidate) => candidate.name === target.group);
    if (!group) {
      group = { name: target.group, targets: [] };
      groups.push(group);
    }
    group.targets.push(target);
  }
  return groups;
}
