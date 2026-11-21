// Drawing arithmetic for the page: data in, numbers, SVG path strings and
// sentences out. Nothing here touches the document, so the same file runs in
// the browser and under node, where the tests call it.

export const SLOTS = 30; // squares per job: every row has room for this many builds
export const STATE_LABELS = {
  success: "Success",
  unstable: "Unstable",
  failure: "Failure",
  aborted: "Aborted or not built",
  running: "Running",
};

function round(value) {
  return Math.round(value * 10) / 10;
}

// The strip of squares for one job. `builds` is newest first, as the server
// sends it; the squares come back oldest first, and the newest build is always
// the last square, so that "now" is the right edge of every row.
export function stripCells(builds, options) {
  const size = (options && options.size) || 14;
  const gap = options && options.gap !== undefined ? options.gap : 2;
  const slots = (options && options.slots) || SLOTS;
  const cells = builds.slice(0, slots).map((build, index) => ({
    x: (slots - 1 - index) * (size + gap),
    y: 0,
    size: size,
    state: build.state,
    build: build,
  }));
  cells.reverse();
  return { width: slots * (size + gap) - gap, height: size, cells: cells };
}

// A state is never told by colour alone: failure carries a cross, unstable a
// stroke, an aborted build is a hollow square and a running one a dot. This
// is the path of the mark inside a square at (x, y); "" when there is none.
// "flaky" is the mark of a job, not of a build: a line that goes up and down.
export function markPath(state, x, y, size) {
  const left = round(x + size * 0.3);
  const right = round(x + size * 0.7);
  const top = round(y + size * 0.3);
  const bottom = round(y + size * 0.7);
  if (state === "failure") {
    return "M" + left + " " + top + "L" + right + " " + bottom + "M" + right + " " + top + "L" + left + " " + bottom;
  }
  if (state === "unstable") {
    return "M" + left + " " + bottom + "L" + right + " " + top;
  }
  if (state === "flaky") {
    const low = round(y + size * 0.75);
    const high = round(y + size * 0.25);
    const steps = [0.1, 0.37, 0.63, 0.9].map((share) => round(x + size * share));
    return "M" + steps[0] + " " + low + "L" + steps[1] + " " + high + "L" + steps[2] + " " + low + "L" + steps[3] + " " + high;
  }
  return "";
}

// A sparkline: one value per build, oldest first. The scale starts at zero, so
// a line that halves in height is a build that takes half as long.
export function sparkline(values, width, height, pad) {
  const inset = pad === undefined ? 3 : pad;
  if (values.length === 0) {
    return { path: "", points: [], last: null, max: 0 };
  }
  const max = Math.max(1, ...values);
  const step = values.length > 1 ? (width - 2 * inset) / (values.length - 1) : 0;
  const points = values.map((value, index) => ({
    x: round(values.length > 1 ? inset + index * step : width / 2),
    y: round(height - inset - (Math.max(0, value) / max) * (height - 2 * inset)),
  }));
  const path = points.map((point, index) => (index === 0 ? "M" : "L") + point.x + " " + point.y).join("");
  return { path: path, points: points, last: points[points.length - 1], max: max };
}

// 184211 -> "3 min 4 s". Jenkins counts in milliseconds.
export function formatDuration(milliseconds) {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(milliseconds)) {
    return "-";
  }
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) {
    return seconds + " s";
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return minutes + " min" + (seconds % 60 ? " " + (seconds % 60) + " s" : "");
  }
  const hours = Math.floor(minutes / 60);
  return hours + " h" + (minutes % 60 ? " " + (minutes % 60) + " min" : "");
}

// A length of time in words, for "broken for 3 days" and "asked 4 minutes ago".
export function formatAge(milliseconds) {
  const minutes = Math.floor(Math.max(0, milliseconds) / 60000);
  if (minutes < 1) {
    return "less than a minute";
  }
  if (minutes < 60) {
    return minutes + (minutes === 1 ? " minute" : " minutes");
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    return hours + (hours === 1 ? " hour" : " hours");
  }
  return Math.floor(hours / 24) + " days";
}

export function formatPercent(ratio) {
  return ratio === null || ratio === undefined ? "-" : Math.round(ratio * 100) + "%";
}

// The badge of a job: a kind (it picks the colour and the mark) and a sentence.
export function verdict(health, now) {
  if (health.status === "broken") {
    const builds = health.broken.builds;
    const since = health.broken.since ? formatAge(now - health.broken.since) : "";
    return {
      kind: "failure",
      label: since ? "Broken for " + (health.broken.or_longer ? "at least " : "") + since : "Broken",
      detail: builds === 1 ? "The last build failed." : "The last " + builds + " builds failed.",
    };
  }
  if (health.status === "unstable") {
    return { kind: "unstable", label: "Unstable", detail: "The last build finished with failing tests." };
  }
  if (health.status === "passing") {
    return { kind: "success", label: "Passing", detail: "The last build succeeded." };
  }
  return { kind: "aborted", label: "No builds yet", detail: "No build of this job has finished with a result." };
}

// The two lines of a square's tooltip: what happened, then how long and when.
export function describeBuild(build, when) {
  const lines = ["#" + build.number + " " + (STATE_LABELS[build.state] || build.state).toLowerCase()];
  const facts = [];
  if (build.state !== "running" && build.duration) {
    facts.push(formatDuration(build.duration));
  }
  if (when) {
    facts.push(when);
  }
  if (facts.length) {
    lines.push(facts.join(", "));
  }
  return lines;
}
