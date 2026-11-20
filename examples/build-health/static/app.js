// Every URL in this file is relative ("api/health", not "/api/health"): the
// app is served under /{slug}/ once deployed.
//
// The table is loaded when the page opens and when the visitor asks for it.
// There is no timer: a page that asks every few seconds would keep the app
// from ever being idle, and the server keeps Jenkins's answers anyway.

import {
  SLOTS,
  STATE_LABELS,
  describeBuild,
  formatAge,
  formatDuration,
  formatPercent,
  markPath,
  sparkline,
  stripCells,
  verdict,
} from "./charts.mjs";

const SVG = "http://www.w3.org/2000/svg";
const SQUARE = 14; // one build, in the units of the drawing
const GAP = 2;
const status = document.getElementById("status");
const refreshButton = document.getElementById("refresh");
const tiles = document.getElementById("tiles");
const board = document.getElementById("board");
const legend = document.getElementById("legend");
const rows = document.querySelector("#jobs tbody");
const empty = document.getElementById("empty");
const tooltip = document.getElementById("tooltip");
const tips = new WeakMap(); // a mark -> the lines of its tooltip
let mayRefresh = false; // known once the server has said whether the visitor is signed in

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function errorMessage(response) {
  if (response.status === 401) {
    return "Your session has ended. Reload the page to sign in again.";
  }
  try {
    const body = await response.json();
    return (body.error && body.error.message) || "The app answered with an error (" + response.status + ").";
  } catch (error) {
    return "The app answered with an error (" + response.status + ").";
  }
}

function say(text, isProblem) {
  status.textContent = text;
  status.classList.toggle("problem", Boolean(isProblem));
}

function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

function element(name, className, text) {
  const made = document.createElement(name);
  if (className) {
    made.className = className;
  }
  if (text !== undefined) {
    made.textContent = text;
  }
  return made;
}

function svg(name, attributes) {
  const made = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes || {})) {
    made.setAttribute(key, value);
  }
  return made;
}

function clock(milliseconds) {
  return new Date(milliseconds).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// --- Marks -----------------------------------------------------------------

// One square of a strip or of the legend. The shape repeats what the colour
// says: a cross for failure, a stroke for unstable, hollow for aborted, a dot
// for running. The mark of a flaky job has no square: it is a zigzag alone.
function mark(state, x, y, size) {
  const group = svg("g", { class: "mark " + state });
  if (state === "running") {
    group.append(svg("circle", { cx: x + size / 2, cy: y + size / 2, r: size / 2 - 2 }));
  } else if (state === "aborted") {
    group.append(svg("rect", { x: x + 1, y: y + 1, width: size - 2, height: size - 2, rx: 2 }));
  } else if (state !== "flaky") {
    group.append(svg("rect", { x: x, y: y, width: size, height: size, rx: 3 }));
  }
  const inside = markPath(state, x, y, size);
  if (inside) {
    group.append(svg("path", { d: inside, class: "inside" }));
  }
  return group;
}

function swatch(state) {
  const picture = svg("svg", {
    viewBox: "0 0 " + SQUARE + " " + SQUARE,
    width: SQUARE,
    height: SQUARE,
    "aria-hidden": "true",
  });
  picture.append(mark(state, 0, 0, SQUARE));
  return picture;
}

function strip(job) {
  const drawn = stripCells(job.builds, { size: SQUARE, gap: GAP, slots: SLOTS });
  const picture = svg("svg", {
    viewBox: "0 0 " + drawn.width + " " + drawn.height,
    width: drawn.width,
    height: drawn.height,
    class: "strip",
    role: "group",
    "aria-label": "Builds of " + job.name + ", oldest to newest. Use the arrow keys to move between them.",
  });
  for (let slot = 0; slot < SLOTS - drawn.cells.length; slot += 1) {
    // A job with fewer builds keeps its place: a faint dash where no build is.
    picture.append(
      svg("rect", { x: slot * (SQUARE + GAP), y: SQUARE / 2 - 2, width: SQUARE, height: 4, rx: 2, class: "no-build" })
    );
  }
  drawn.cells.forEach((cell, index) => {
    const lines = describeBuild(cell.build, cell.build.timestamp ? clock(cell.build.timestamp) : "");
    // Only the newest square is a tab stop; the arrow keys reach the others.
    const hold = svg(cell.build.url ? "a" : "g", {
      class: "build",
      tabindex: index === drawn.cells.length - 1 ? 0 : -1,
      "aria-label": lines.join(", "),
    });
    if (cell.build.url) {
      hold.setAttribute("href", cell.build.url);
      hold.setAttribute("target", "_blank");
      hold.setAttribute("rel", "noopener noreferrer");
    }
    // The hit area is the square and the gap around it.
    hold.append(svg("rect", { x: cell.x - 1, y: -1, width: cell.size + 2, height: cell.size + 2, class: "hit" }));
    hold.append(mark(cell.state, cell.x, cell.y, cell.size));
    tips.set(hold, lines);
    picture.append(hold);
  });
  return picture;
}

function spark(health) {
  const width = 96;
  const height = 24;
  const drawn = sparkline(health.durations, width, height);
  const picture = svg("svg", { viewBox: "0 0 " + width + " " + height, class: "spark", role: "img" });
  const longest = Math.max(...health.durations);
  const shortest = Math.min(...health.durations);
  const lines = [
    "Durations of " + plural(health.durations.length, "build"),
    formatDuration(shortest) + " to " + formatDuration(longest) + ", oldest to newest",
  ];
  picture.setAttribute("aria-label", lines.join(": "));
  picture.append(svg("path", { d: drawn.path, class: "trend" }));
  picture.append(svg("circle", { cx: drawn.last.x, cy: drawn.last.y, r: 3, class: "latest" }));
  tips.set(picture, lines);
  return picture;
}

// --- The page ----------------------------------------------------------------

function tile(label, value, note) {
  const box = element("div", "tile");
  box.append(element("dt", "", label), element("dd", "", value));
  if (note) {
    box.append(element("dd", "note", note));
  }
  return box;
}

function badge(kind, label, detail) {
  const made = element("span", "badge " + kind);
  made.append(swatch(kind), element("span", "", label));
  made.title = detail;
  return made;
}

function cell(label, className) {
  const made = element("td", className);
  made.dataset.label = label; // shown in front of the value when the table is stacked on a narrow screen
  return made;
}

function jobRow(job, now) {
  const row = document.createElement("tr");

  const name = cell("Job", "job");
  if (job.url) {
    const link = element("a", "job-name", job.name);
    link.href = job.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    name.append(link);
  } else {
    name.append(element("span", "job-name", job.name));
  }
  name.append(element("span", "job-path", job.path));
  row.append(name);

  if (job.problem) {
    // Jenkins's "no" for this job stays in this row; the other rows are fine.
    const trouble = cell("Builds", "problem");
    trouble.colSpan = 3;
    trouble.textContent = job.problem.message;
    row.append(trouble);
    return row;
  }

  const said = verdict(job.health, now * 1000);
  const badges = element("div", "badges");
  badges.append(badge(said.kind, said.label, said.detail));
  if (job.health.flaky) {
    badges.append(
      badge("flaky", "Flaky", "Passing and failing took turns " + job.health.flips + " times in the newest builds.")
    );
  }
  if (job.health.running) {
    badges.append(badge("running", "Running", "A build is running now."));
  }
  name.append(badges);

  const builds = cell("Builds");
  if (job.builds.length) {
    builds.append(strip(job));
  } else {
    builds.append(element("span", "quiet", "No builds yet."));
  }
  row.append(builds);

  const rate = cell("Success", "number");
  rate.textContent = formatPercent(job.health.success_rate);
  if (job.health.judged) {
    rate.title = job.health.passed + " of " + plural(job.health.judged, "finished build") + " succeeded.";
  }
  row.append(rate);

  const duration = cell("Median duration", "duration");
  duration.append(element("span", "", formatDuration(job.health.median_duration)));
  if (job.health.durations.length > 1) {
    duration.append(spark(job.health));
  }
  row.append(duration);
  return row;
}

function show(report, refreshed) {
  const total = report.summary;
  tiles.replaceChildren(
    tile("Jobs", String(total.jobs), total.unread ? plural(total.unread, "job") + " could not be read" : ""),
    tile("Passing", String(total.passing), total.unstable ? total.unstable + " more unstable" : ""),
    tile("Broken", String(total.broken)),
    tile("Flaky", String(total.flaky)),
    tile("Success rate", formatPercent(total.success_rate), total.builds ? "of " + plural(total.builds, "build") : "")
  );
  rows.replaceChildren(...report.jobs.map((job) => jobRow(job, report.now)));
  const any = report.jobs.length > 0;
  tiles.hidden = !any;
  board.hidden = !any;
  empty.hidden = any;
  mayRefresh = report.signed_in && any;
  refreshButton.title = report.signed_in ? "Ask Jenkins again" : "Sign in to refresh";

  if (!any) {
    say("");
    return;
  }
  const oldest = Math.min(...report.jobs.map((job) => job.fetched_at));
  const kept = report.jobs.length - report.asked;
  let text;
  if (refreshed) {
    text = report.asked
      ? "Jenkins was asked about " + plural(report.asked, "job") + "."
      : "Nothing was asked again.";
    if (kept) {
      text += " " + (kept === 1 ? "One job was" : kept + " jobs were") + " asked less than a minute ago.";
    }
  } else if (kept === 0) {
    text = "Jenkins was asked just now.";
  } else {
    text = "Jenkins was last asked " + formatAge((report.now - oldest) * 1000) + " ago.";
  }
  say(text + (report.signed_in ? "" : " Sign in to refresh."));
}

async function load(url, request, refreshed) {
  refreshButton.disabled = true;
  board.classList.add("stale"); // the old table stays in place, dimmed, until the new one is there
  say("Asking Jenkins...");
  try {
    const response = await fetch(url, request);
    if (!response.ok) {
      say(await errorMessage(response), true);
      return;
    }
    show(await response.json(), refreshed);
  } catch (error) {
    say("The app cannot be reached right now.", true);
  } finally {
    refreshButton.disabled = !mayRefresh;
    board.classList.remove("stale");
  }
}

// --- Tooltips and the keyboard -------------------------------------------------

function showTip(target) {
  const holder = target.closest ? target.closest(".build, .spark") : null;
  const lines = holder && tips.get(holder);
  if (!lines) {
    tooltip.hidden = true;
    return;
  }
  tooltip.replaceChildren(...lines.map((line, index) => element(index === 0 ? "strong" : "span", "", line)));
  tooltip.hidden = false;
  const place = holder.getBoundingClientRect();
  const size = tooltip.getBoundingClientRect();
  const left = place.left + place.width / 2 - size.width / 2;
  const above = place.top - size.height - 8;
  tooltip.style.left = Math.max(8, Math.min(left, window.innerWidth - size.width - 8)) + "px";
  tooltip.style.top = (above < 8 ? place.bottom + 8 : above) + "px";
}

function hideTip() {
  tooltip.hidden = true;
}

rows.addEventListener("pointerover", (event) => showTip(event.target));
rows.addEventListener("pointerout", hideTip);
rows.addEventListener("focusin", (event) => showTip(event.target));
rows.addEventListener("focusout", hideTip);
window.addEventListener("scroll", hideTip, { passive: true });

// Left and right walk along a strip; Home and End jump to its ends.
rows.addEventListener("keydown", (event) => {
  const current = event.target.closest ? event.target.closest(".build") : null;
  if (!current) {
    return;
  }
  const builds = Array.from(current.parentNode.querySelectorAll(".build"));
  const moves = { ArrowLeft: -1, ArrowRight: 1, Home: -builds.length, End: builds.length };
  if (!(event.key in moves)) {
    return;
  }
  event.preventDefault();
  const next = builds[Math.max(0, Math.min(builds.length - 1, builds.indexOf(current) + moves[event.key]))];
  current.setAttribute("tabindex", -1);
  next.setAttribute("tabindex", 0);
  next.focus();
});

for (const state of ["success", "unstable", "failure", "aborted", "running"]) {
  const item = element("li");
  item.append(swatch(state), element("span", "", STATE_LABELS[state]));
  legend.append(item);
}

refreshButton.addEventListener("click", () => load("api/refresh", { method: "POST" }, true));

load("api/health", undefined, false);
