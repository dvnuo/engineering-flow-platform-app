// Every URL in this file is relative ("api/matrix", not "/api/matrix"): the
// app is served under /{slug}/ once deployed.
//
// The matrix is loaded when the page opens and when the visitor asks for it.
// There is no timer: a page that asks every few seconds would keep the app
// from ever being idle. The two filters work on what the page already has
// and ask the server for nothing.

import {
  columnLabels,
  columnAt,
  describeBuild,
  describeCell,
  filterRows,
  formatSeconds,
  packagesOf,
  passCount,
  rowCells,
  splitClass,
} from "./matrix.mjs";

const SVG = "http://www.w3.org/2000/svg";
const SQUARE = 14; // one build of one test, in the units of the drawing
const GAP = 2;
const MAX_DRAWN = 100; // rows on the page at once; the filters reach the others
const CLASSES = { p: "passed", f: "failed", s: "skipped", "-": "absent" };
const LEGEND = [
  ["p", "Passed"],
  ["f", "Failed"],
  ["s", "Skipped"],
  ["-", "Not in this build"],
];

const form = document.getElementById("choose");
const jobSelect = document.getElementById("job");
const buildsSelect = document.getElementById("builds");
const showButton = document.getElementById("show");
const status = document.getElementById("status");
const result = document.getElementById("result");
const tiles = document.getElementById("tiles");
const packageSelect = document.getElementById("package");
const textInput = document.getElementById("text");
const legend = document.getElementById("legend");
const shown = document.getElementById("shown");
const matrix = document.getElementById("matrix");
const columns = document.getElementById("columns");
const body = matrix.querySelector("tbody");
const spoken = document.getElementById("spoken");
const tooltip = document.getElementById("tooltip");

let found = null; // the last answer of api/matrix
let packages = []; // the choices of the package filter, in the order of its options
let drawn = []; // the rows on the page: { row, picture, cursor }
let pointed = null; // the row under the pointer or the keyboard
let column = -1; // and the build in it

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function errorMessage(response) {
  if (response.status === 401) {
    return "Your session has ended. Reload the page to sign in again.";
  }
  try {
    const answer = await response.json();
    return (answer.error && answer.error.message) || "The app answered with an error (" + response.status + ").";
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

function option(value, text) {
  const made = element("option", "", text);
  made.value = value;
  return made;
}

function outside(url, text) {
  const link = element("a", "", text);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

// A class and a test name come from Jenkins: they go in as text, never as markup.
function testLabel(item) {
  const holder = element("span", "test");
  const short = element("span", "test-class", splitClass(item.class).short);
  short.title = item.class;
  holder.append(short, element("span", "test-name", item.name));
  return holder;
}

// --- The matrix ----------------------------------------------------------------

function squares(cells) {
  const geometry = rowCells(cells, { size: SQUARE, gap: GAP });
  const marks = geometry.squares.map((square) =>
    svg("rect", {
      x: square.x,
      y: square.y,
      width: square.size,
      height: square.size,
      rx: 2, // the smallest mark, the dot of an absent test, becomes a circle
      class: CLASSES[square.state],
    })
  );
  if (geometry.crosses) {
    marks.push(svg("path", { d: geometry.crosses, class: "cross" }));
  }
  return { geometry: geometry, marks: marks };
}

function rowPicture(row) {
  const made = squares(row.cells);
  const picture = svg("svg", {
    viewBox: "0 0 " + made.geometry.width + " " + made.geometry.height,
    width: made.geometry.width,
    height: made.geometry.height,
    class: "cells",
    tabindex: 0,
    role: "img",
    "aria-label":
      splitClass(row.class).short + ": " + row.name + ". " + plural(row.flips, "change") + ", " + passCount(row.cells) +
      ". Use the arrow keys to read each build.",
  });
  const cursor = svg("rect", { class: "cursor", y: -1, width: SQUARE + 2, height: SQUARE + 2, rx: 3, visibility: "hidden" });
  picture.append(...made.marks, cursor);
  return { row: row, picture: picture, cursor: cursor };
}

function columnsPicture(builds) {
  const width = builds.length * (SQUARE + GAP) - GAP;
  const picture = svg("svg", {
    viewBox: "0 0 " + width + " 28",
    width: width,
    height: 28,
    class: "columns",
    role: "group",
    "aria-label": "Build numbers, oldest to newest",
  });
  for (const label of columnLabels(builds)) {
    const build = builds[label.index];
    const middle = label.index * (SQUARE + GAP) + SQUARE / 2;
    const first = label.index === 0;
    const last = label.index === builds.length - 1 && !first;
    const text = svg("text", { x: first ? 0 : last ? width : middle, y: 11, "text-anchor": first ? "start" : last ? "end" : "middle" });
    text.textContent = "#" + label.number;
    if (build.url) {
      const link = svg("a", { href: build.url, target: "_blank", rel: "noopener noreferrer" });
      link.append(text);
      picture.append(link);
    } else {
      picture.append(text);
    }
    picture.append(svg("line", { x1: middle, x2: middle, y1: 16, y2: 21, class: "tick" }));
  }
  builds.forEach((build, index) => {
    if (build.state !== "ok") {
      // A dash under the numbers where a build has no column of results.
      const dash = svg("rect", { x: index * (SQUARE + GAP) + 2, y: 24, width: SQUARE - 4, height: 3, rx: 1.5, class: "gap" });
      const title = svg("title");
      title.textContent = "#" + build.number + ": " + describeBuild(build);
      dash.append(title);
      picture.append(dash);
    }
  });
  return picture;
}

function wanted() {
  return {
    package: packageSelect.selectedIndex > 0 ? packages[packageSelect.selectedIndex - 1].name : null,
    text: textInput.value,
  };
}

function drawMatrix() {
  hidePoint();
  const matching = filterRows(found.rows, wanted());
  const visible = matching.slice(0, MAX_DRAWN);
  drawn = visible.map(rowPicture);
  body.replaceChildren(
    ...drawn.map((entry) => {
      const line = document.createElement("tr");
      const head = element("th");
      head.scope = "row";
      head.append(testLabel(entry.row));
      const cells = element("td");
      cells.append(entry.picture);
      line.append(head, cells);
      return line;
    })
  );
  matrix.hidden = visible.length === 0;

  let text;
  if (found.rows.length === 0) {
    text = found.tests
      ? "No test changed between passing and failing in these builds."
      : "These builds have no test results to compare.";
  } else if (matching.length === 0) {
    text = "No test matches the filter.";
  } else if (visible.length < matching.length) {
    text =
      "The " + visible.length + " most flaky of " + plural(matching.length, "test") + " are drawn; " +
      (matching.length - visible.length) + " are hidden. Narrow the filter to see them.";
  } else {
    text = plural(matching.length, "test") + (matching.length < found.rows.length ? " of " + found.rows.length : "") + ", the most flaky first.";
  }
  if (found.hidden) {
    text += " Another " + plural(found.hidden, "test") + " changed too and " + (found.hidden === 1 ? "was" : "were") +
      " left out: the page gets the " + found.max_rows + " with the most changes.";
  }
  shown.textContent = text;
}

// --- Pointing at a cell: the pointer and the arrow keys do the same ------------------

function point(entry, at, aloud) {
  if (pointed) {
    pointed.cursor.setAttribute("visibility", "hidden");
  }
  pointed = entry;
  column = at;
  entry.cursor.setAttribute("x", at * (SQUARE + GAP) - 1);
  entry.cursor.setAttribute("visibility", "visible");
  const said = describeCell(entry.row, found.builds, at);
  tooltip.replaceChildren(element("strong", "", said.build + ": " + said.what), element("span", "", said.test));
  tooltip.hidden = false;
  const place = entry.picture.getBoundingClientRect();
  const scale = place.width / (found.builds.length * (SQUARE + GAP) - GAP);
  const size = tooltip.getBoundingClientRect();
  const left = place.left + (at * (SQUARE + GAP) + SQUARE / 2) * scale - size.width / 2;
  const above = place.top - size.height - 8;
  tooltip.style.left = Math.max(8, Math.min(left, window.innerWidth - size.width - 8)) + "px";
  tooltip.style.top = (above < 8 ? place.bottom + 8 : above) + "px";
  if (aloud) {
    spoken.textContent = said.test + ", build " + said.build + ": " + said.what;
  }
}

function hidePoint() {
  if (pointed) {
    pointed.cursor.setAttribute("visibility", "hidden");
  }
  pointed = null;
  tooltip.hidden = true;
}

function entryOf(target) {
  const picture = target.closest ? target.closest("svg.cells") : null;
  return picture ? drawn.find((entry) => entry.picture === picture) : null;
}

body.addEventListener("pointermove", (event) => {
  const entry = entryOf(event.target);
  if (!entry) {
    hidePoint();
    return;
  }
  const place = entry.picture.getBoundingClientRect();
  const count = found.builds.length;
  const x = ((event.clientX - place.left) / place.width) * (count * (SQUARE + GAP) - GAP);
  const at = columnAt(x, count, { size: SQUARE, gap: GAP });
  if (entry !== pointed || at !== column) {
    point(entry, at, false);
  }
});
body.addEventListener("pointerleave", hidePoint);
body.addEventListener("focusin", (event) => {
  const entry = entryOf(event.target);
  if (entry) {
    // The newest build first; after that the column the reader was in.
    point(entry, column >= 0 && column < found.builds.length ? column : found.builds.length - 1, true);
  }
});
body.addEventListener("focusout", hidePoint);
body.addEventListener("keydown", (event) => {
  const entry = entryOf(event.target);
  if (!entry) {
    return;
  }
  const last = found.builds.length - 1;
  const position = drawn.indexOf(entry);
  if (event.key === "ArrowLeft" || event.key === "ArrowRight" || event.key === "Home" || event.key === "End") {
    const moves = { ArrowLeft: column - 1, ArrowRight: column + 1, Home: 0, End: last };
    point(entry, Math.max(0, Math.min(last, moves[event.key])), true);
  } else if (event.key === "ArrowUp" && position > 0) {
    drawn[position - 1].picture.focus();
  } else if (event.key === "ArrowDown" && position < drawn.length - 1) {
    drawn[position + 1].picture.focus();
  } else {
    return;
  }
  event.preventDefault();
});
window.addEventListener("scroll", () => {
  tooltip.hidden = true;
}, { passive: true });

// --- The rest of the page -------------------------------------------------------

function tile(label, value, note) {
  const box = element("div", "tile");
  box.append(element("dt", "", label), element("dd", "", value));
  if (note) {
    box.append(element("dd", "note", note));
  }
  return box;
}

function fill(list, items, line, nothing) {
  list.replaceChildren(
    ...items.map((item) => {
      const entry = element("li");
      entry.append(testLabel(item), line(item));
      return entry;
    })
  );
  if (items.length === 0) {
    list.append(element("li", "quiet none", nothing));
  }
}

function buildLink(number) {
  const build = found.builds.find((candidate) => candidate.number === number);
  return build && build.url ? outside(build.url, "#" + number) : document.createTextNode("#" + number);
}

function show(answer) {
  found = answer;
  column = -1;
  const read = found.builds.filter((build) => build.state === "ok").length;
  const gaps = found.builds.filter((build) => build.state !== "ok");
  tiles.replaceChildren(
    tile("Tests", found.tests.toLocaleString()),
    tile("Changed", found.changed.toLocaleString(), "between passing and failing"),
    tile("Failing now", found.failing_now.toLocaleString(), "in the newest build with a report"),
    tile("Builds read", read + " of " + found.builds.length, gaps.length ? plural(gaps.length, "build") + " without results" : "")
  );

  packages = packagesOf(found.rows);
  packageSelect.replaceChildren(
    option("", "All packages (" + found.rows.length + ")"),
    ...packages.map((entry, index) => option(String(index), (entry.name || "(no package)") + " (" + entry.rows + ")"))
  );
  textInput.value = "";
  columns.replaceChildren(columnsPicture(found.builds));
  drawMatrix();

  fill(
    document.getElementById("flaky"),
    found.rows.slice(0, 10),
    (row) => element("span", "quiet", plural(row.flips, "change") + ", " + passCount(row.cells)),
    "No test changed between passing and failing."
  );
  fill(
    document.getElementById("new-failures"),
    found.new_failures,
    (item) => {
      const line = element("span", "quiet", "failing since ");
      line.append(buildLink(item.since), " (" + plural(item.builds, "build") + ")");
      return line;
    },
    "Nothing started to fail in these builds."
  );
  fill(
    document.getElementById("slowest"),
    found.slowest,
    (item) => element("span", "quiet", formatSeconds(item.seconds) + " on average over " + plural(item.runs, "run")),
    "No test reported how long it took."
  );
  result.hidden = false;

  if (found.builds.length === 0) {
    result.hidden = true;
    say("This job has no finished build yet.");
    return;
  }
  const kept = found.builds.length - found.fetched;
  let text = found.fetched
    ? plural(found.fetched, "report") + " fetched from Jenkins" + (kept ? ", " + kept + " read from storage." : ".")
    : "Every report was read from storage; Jenkins was asked for the list of builds only.";
  if (gaps.length) {
    text += " Without results: " + gaps.map((build) => "#" + build.number + " (" + describeBuild(build) + ")").join(", ") + ".";
  }
  say(text);
}

async function load() {
  if (!jobSelect.value) {
    return;
  }
  const chosen = new URLSearchParams({ job: jobSelect.value, builds: buildsSelect.value });
  showButton.disabled = true;
  result.classList.add("stale"); // the old matrix stays in place, dimmed, until the new one is there
  say("Asking Jenkins...");
  try {
    const response = await fetch("api/matrix?" + chosen);
    if (!response.ok) {
      result.hidden = true;
      say(await errorMessage(response), true);
      return;
    }
    show(await response.json());
    // The choice goes into the address, so that the view can be shared as a link.
    history.replaceState(null, "", "?" + chosen);
  } catch (error) {
    say("The app cannot be reached right now.", true);
  } finally {
    showButton.disabled = false;
    result.classList.remove("stale");
  }
}

async function start() {
  for (const [state, label] of LEGEND) {
    const item = element("li");
    const picture = svg("svg", { viewBox: "0 0 " + SQUARE + " " + SQUARE, width: SQUARE, height: SQUARE, "aria-hidden": "true" });
    picture.append(...squares(state).marks);
    item.append(picture, element("span", "", label));
    legend.append(item);
  }
  try {
    const response = await fetch("api/jobs");
    if (!response.ok) {
      say(await errorMessage(response), true);
      return;
    }
    const offered = await response.json();
    const asked = new URLSearchParams(location.search);
    jobSelect.replaceChildren(...offered.jobs.map((job) => option(job.key, job.name)));
    buildsSelect.replaceChildren(...offered.build_choices.map((count) => option(String(count), "Last " + count)));
    if (offered.jobs.some((job) => job.key === asked.get("job"))) {
      jobSelect.value = asked.get("job");
    }
    if (offered.build_choices.map(String).includes(asked.get("builds"))) {
      buildsSelect.value = asked.get("builds");
    }
    if (offered.jobs.length === 0) {
      showButton.disabled = true;
      say("No job is listed yet. Add your jobs to jobs.json and deploy again.");
      return;
    }
    load();
  } catch (error) {
    say("The app cannot be reached right now.", true);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  load();
});
packageSelect.addEventListener("change", drawMatrix);
textInput.addEventListener("input", drawMatrix);

start();
