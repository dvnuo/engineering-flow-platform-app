// Every URL in this file is relative ("api/matrix", not "/api/matrix"): the
// app is served under /{slug}/ once deployed. The one exception is the EFP
// kit, at /_efp/lib/, which every gateway serves to every app.
//
// The matrix is loaded when the page opens and when the visitor asks for it.
// There is no timer: a page that asks every few seconds would keep the app
// from ever being idle. The filters and the order of the rows work on what
// the page already has and ask the server for nothing.
//
// The arithmetic of the heatmap is in matrix.mjs (tested with node); this
// file puts it on the page. A new answer fills the heatmap in a wave, square
// by square; after that, a row is an element kept by its test, so that
// filtering and sorting move the rows that are already there
// (kit.animateList) instead of drawing the heatmap anew.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
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

const { h } = kit;
const SVG = "http://www.w3.org/2000/svg";
const SQUARE = 14; // one build of one test, in the units of the drawing
const GAP = 2;
const STEP = SQUARE + GAP;
const MAX_DRAWN = 100; // rows on the page at once; the filters reach the others
const CLASSES = { p: "passed", f: "failed", s: "skipped", "-": "absent" };
const LEGEND = [
  ["p", "Passed"],
  ["f", "Failed"],
  ["s", "Skipped"],
  ["-", "Not in this build"],
  [" ", "No report"],
];
const NOT_SET_UP = ["not_configured", "connector_disabled", "connector_not_enabled"];

const $ = (id) => document.getElementById(id);
const form = $("choose");
const jobSelect = $("job");
const buildsGroup = $("builds");
const showButton = $("show");
const packageSelect = $("package");
const textInput = $("text");
const grid = $("grid");
const band = $("band");
const tooltip = $("tooltip");

let found = null; // the last answer of api/matrix
let shownChoice = ""; // job and builds of that answer
let packages = []; // the choices of the package filter, in the order of its options
let sortMode = "changes";
let rowsList = $("rows"); // a new list for every answer: see drawRows()
let entries = new Map(); // test -> { row, item, picture, cursor }, for the rows of this answer
let drawn = []; // the entries on the page, in their order
const byPicture = new WeakMap(); // a row's <svg> -> its entry
let pointed = null; // the entry under the pointer or the keyboard
let column = -1; // and the build in it
let tiles = null; // the four tiles, made once and counted up after that

// --- Small things --------------------------------------------------------------------------

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function failure(response) {
  if (response.status === 401) {
    return { code: "sign_in", message: "Your session has ended. Reload the page to sign in again." };
  }
  try {
    const body = await response.json();
    return { code: (body.error && body.error.code) || "", message: (body.error && body.error.message) || "The app answered with an error (" + response.status + ")." };
  } catch (error) {
    return { code: "", message: "The app answered with an error (" + response.status + ")." };
  }
}

function say(text) {
  $("status").textContent = text;
}

function plural(count, word) {
  return count.toLocaleString("en") + " " + word + (count === 1 ? "" : "s");
}

function svg(name, attributes) {
  const made = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes || {})) {
    made.setAttribute(key, value);
  }
  return made;
}

function clock(milliseconds) {
  return milliseconds ? new Date(milliseconds).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
}

function testKey(item) {
  return item.class + "\n" + item.name;
}

// A class and a test name come from Jenkins: they go in as text, never as markup.
function testLabel(item) {
  const parts = splitClass(item.class);
  return h(
    "span",
    { class: "ft-test" },
    h("span", { class: "ft-name", title: item.name, text: item.name }),
    h("span", { class: "ft-class", title: item.class }, parts.short, parts.package ? h("span", { class: "ft-package", text: " " + parts.package }) : null),
  );
}

function outside(url, text) {
  return url ? h("a", { class: "link link-hover", href: url, target: "_blank", rel: "noopener noreferrer", text }) : h("span", { text });
}

// A problem that stops the page: an empty state with what to do, in place of the result.
function showProblem(problem) {
  $("problem").hidden = !problem;
  if (!problem) {
    $("problem").replaceChildren();
    return;
  }
  const notSetUp = NOT_SET_UP.includes(problem.code);
  $("problem").replaceChildren(
    h(
      "div",
      { class: "efp-surface efp-empty" },
      h("span", { class: "efp-empty-icon" }, kit.icon(problem.icon || (notSetUp ? "plug-zap" : "cloud-alert"))),
      h("strong", { text: problem.title || (notSetUp ? "Not set up yet" : "Jenkins could not be read") }),
      h("span", { class: "text-sm max-w-md", text: problem.message }),
    ),
  );
  kit.rise($("problem").firstChild);
}

// --- The squares ---------------------------------------------------------------------------

// The squares of one row. The wave of a new answer runs along --r (the row)
// and --c (the build): each square starts a little after its neighbours.
function squares(cells, gaps) {
  const geometry = rowCells(cells, { size: SQUARE, gap: GAP });
  const marks = gaps.map((index) => svg("rect", { x: index * STEP, y: 0, width: SQUARE, height: SQUARE, rx: 3, class: "gap" }));
  for (const square of geometry.squares) {
    marks.push(
      svg("rect", {
        x: square.x,
        y: square.y,
        width: square.size,
        height: square.size,
        rx: square.state === "-" ? 2 : 3, // the dot of an absent test is a circle
        class: "sq " + CLASSES[square.state],
        style: "--c: " + square.index,
      }),
    );
  }
  if (geometry.crosses) {
    marks.push(svg("path", { d: geometry.crosses, class: "cross" }));
  }
  return { geometry, marks };
}

function swatch(state) {
  const picture = svg("svg", { viewBox: "0 0 " + SQUARE + " " + SQUARE, width: 13, height: 13, "aria-hidden": "true" });
  picture.append(...squares(state === " " ? "" : state, state === " " ? [0] : []).marks);
  return picture;
}

function gapsOf(builds) {
  return builds.map((build, index) => (build.state === "ok" ? -1 : index)).filter((index) => index >= 0);
}

// The build numbers above the squares. HTML, not part of a drawing: the
// squares grow and shrink with the room they have, the numbers keep their size.
function columnsHeader(builds) {
  const width = builds.length * STEP - GAP;
  const share = (units) => (units / width) * 100 + "%";
  const head = $("columns");
  head.style.setProperty("--n", builds.length);
  head.replaceChildren();
  let middle = 0;
  for (const label of columnLabels(builds)) {
    const build = builds[label.index];
    const first = label.index === 0;
    const last = label.index === builds.length - 1 && !first;
    // On a phone every other number in between is left out (.is-alt): they would touch.
    const place = first ? "is-first" : last ? "is-last" : "is-middle" + (middle++ % 2 === 0 ? " is-alt" : "");
    const props = { class: "ft-colnum " + place, text: "#" + label.number, style: first || last ? {} : { left: share(label.index * STEP + SQUARE / 2) } };
    head.append(
      build.url ? h("a", { ...props, href: build.url, target: "_blank", rel: "noopener noreferrer" }) : h("span", props),
      h("span", { class: "ft-tick", style: { left: share(label.index * STEP + SQUARE / 2) } }),
    );
  }
  builds.forEach((build, index) => {
    if (build.state !== "ok") {
      // A dash under the numbers where a build has no column of results.
      head.append(h("span", { class: "ft-colgap", title: "#" + build.number + ": " + describeBuild(build), style: { left: share(index * STEP + 2), width: share(SQUARE - 4) } }));
    }
  });
}

// --- The rows --------------------------------------------------------------------------------

function makeRow(row, wave) {
  const made = squares(row.cells, gapsOf(found.builds));
  const width = made.geometry.width;
  const picture = svg("svg", {
    viewBox: "0 0 " + width + " " + SQUARE,
    width,
    height: SQUARE,
    class: "ft-cells",
    tabindex: -1,
    role: "img",
    style: "--r: " + wave + "; --n: " + row.cells.length,
    "aria-label":
      splitClass(row.class).short + ": " + row.name + ". " + plural(row.flips, "change") + ", " + passCount(row.cells) +
      ". Arrow keys: left and right read the builds, up and down the tests.",
  });
  const cursor = svg("rect", { class: "cursor", y: -1.5, width: SQUARE + 3, height: SQUARE + 3, rx: 4, visibility: "hidden" });
  picture.append(...made.marks, cursor);
  const verdicts = passCount(row.cells).split(" ");
  const item = h(
    "li",
    { class: "ft-row", dataset: { key: testKey(row) }, style: { "--r": wave } },
    testLabel(row),
    h("div", { class: "ft-cells-cell" }, picture),
    h(
      "div",
      { class: "ft-flips", title: plural(row.flips, "change") + " between passing and failing; " + passCount(row.cells) },
      h("span", { class: "ft-flips-count efp-number" }, kit.icon("shuffle", "size-3.5"), String(row.flips)),
      h("span", { class: "ft-flips-rate efp-number", text: verdicts[0] + "/" + verdicts[2] + " passed" }),
    ),
  );
  const entry = { row, item, picture, cursor };
  byPicture.set(picture, entry);
  return entry;
}

const SORTS = {
  changes: null, // the server's order: the most changes first
  failures: (a, b) => b.failed - a.failed || b.flips - a.flips,
  recent: (a, b) => b.cells.lastIndexOf("f") - a.cells.lastIndexOf("f") || b.flips - a.flips,
  name: (a, b) => (a.name + a.class).localeCompare(b.name + b.class),
};

function wanted() {
  return {
    package: packageSelect.selectedIndex > 0 ? packages[packageSelect.selectedIndex - 1].name : null,
    text: textInput.value,
  };
}

function emptyState(icon, title, text, action) {
  return h(
    "div",
    { class: "efp-empty" },
    h("span", { class: "efp-empty-icon" }, kit.icon(icon)),
    h("strong", { text: title }),
    h("span", { class: "text-sm max-w-md", text }),
    action || null,
  );
}

// Draw the rows the filters and the order ask for. "fresh" is a new answer:
// a new list, filled in a wave; otherwise the rows that are there move.
function drawRows(fresh) {
  hidePoint();
  const matching = filterRows(found.rows, wanted());
  if (SORTS[sortMode]) matching.sort(SORTS[sortMode]);
  const visible = matching.slice(0, MAX_DRAWN);

  if (fresh) {
    // A list of its own for each answer: the old one leaves with its rows, and
    // the list that moves rows about (animateList) never sees the wave.
    const list = h("ul", { id: "rows", class: "ft-rows is-entering", "aria-label": "Tests by build" });
    rowsList.replaceWith(list);
    rowsList = list;
    entries = new Map();
    const settled = list;
    setTimeout(() => settled.classList.remove("is-entering"), 2600);
  }
  const keep = new Set(visible.map(testKey));
  for (const child of Array.from(rowsList.children)) {
    if (!keep.has(child.dataset.key)) child.remove();
  }
  const order = [];
  visible.forEach((row, index) => {
    const key = testKey(row);
    let entry = entries.get(key);
    if (!entry) {
      entry = makeRow(row, Math.min(index, 18));
      entries.set(key, entry);
    }
    // Placed after the row before it: a row on its way out may still be in the list.
    const after = index === 0 ? rowsList.firstChild : order[index - 1].item.nextSibling;
    if (entry.item !== after) rowsList.insertBefore(entry.item, after);
    order.push(entry);
  });
  drawn = order;
  if (fresh) kit.animateList(rowsList, { duration: 320 });

  // One stop for the Tab key: the row that had it, or the first one.
  const current = drawn.find((entry) => entry.picture.getAttribute("tabindex") === "0") || drawn[0];
  for (const entry of drawn) entry.picture.setAttribute("tabindex", entry === current ? 0 : -1);

  grid.hidden = visible.length === 0;
  $("tools").hidden = found.rows.length === 0;
  const none = $("none");
  none.hidden = visible.length > 0;
  if (visible.length === 0) {
    const clear = h("button", { type: "button", class: "btn btn-sm btn-soft btn-primary mt-2", onclick: clearFilters }, kit.icon("x", "size-4"), "Clear the filter");
    const read = found.builds.filter((build) => build.state === "ok").length;
    none.replaceChildren(
      found.rows.length
        ? emptyState("search-x", "No test matches the filter", "Try fewer words, or another package.", clear)
        : found.tests
          ? emptyState("shield-check", "Steady", "No test changed between passing and failing in these " + plural(read, "build") + ".")
          : emptyState("file-question", "No test results to compare", "None of these builds left a test report the app could read."),
    );
  }

  let text = "";
  if (matching.length && visible.length < matching.length) {
    text = "The first " + visible.length + " of " + plural(matching.length, "test") + " are drawn; narrow the filter to see the others.";
  } else if (matching.length) {
    text = plural(matching.length, "test") + (matching.length < found.rows.length ? " of " + found.rows.length : "") + ".";
  }
  if (found.hidden) {
    text += " Another " + plural(found.hidden, "test") + " changed too and " + (found.hidden === 1 ? "was" : "were") + " left out: the page gets the " + found.max_rows + " with the most changes.";
  }
  $("shown").textContent = text.trim();
}

function clearFilters() {
  textInput.value = "";
  packageSelect.selectedIndex = 0;
  drawRows(false);
  textInput.focus();
}

// --- Pointing at a square: the pointer and the keyboard do the same ------------------------

// The square, its row and its column light up together, and the tooltip
// says which test, which build and what happened.
function point(entry, at, aloud) {
  if (pointed && pointed !== entry) {
    pointed.cursor.setAttribute("visibility", "hidden");
    pointed.item.classList.remove("is-pointed");
  }
  pointed = entry;
  column = at;
  entry.cursor.setAttribute("x", at * STEP - 1.5);
  entry.cursor.setAttribute("visibility", "visible");
  entry.item.classList.add("is-pointed");

  const build = found.builds[at];
  const said = describeCell(entry.row, found.builds, at);
  const state = entry.row.cells[at];
  tooltip.replaceChildren(
    h("span", { class: "ft-tip-head" }, h("span", { class: "ft-tip-dot " + (CLASSES[state] || "none") }), h("strong", { text: said.build + ": " + said.what })),
    h("span", { text: said.test }),
    h("span", { class: "ft-tip-meta", text: [clock(build.timestamp), build.result ? "build " + build.result.toLowerCase() : ""].filter(Boolean).join(", ") }),
  );
  tooltip.hidden = false;

  const box = entry.picture.getBoundingClientRect();
  const scale = box.width / (found.builds.length * STEP - GAP);
  const where = grid.getBoundingClientRect();
  band.hidden = false;
  band.style.left = box.left - where.left + (at * STEP - 1) * scale + "px";
  band.style.width = (SQUARE + 2) * scale + "px";
  band.firstChild.textContent = "#" + build.number;

  const size = tooltip.getBoundingClientRect();
  const left = box.left + (at * STEP + SQUARE / 2) * scale - size.width / 2;
  const above = box.top - size.height - 10;
  tooltip.style.left = Math.max(8, Math.min(left, window.innerWidth - size.width - 8)) + "px";
  tooltip.style.top = (above < 8 ? box.bottom + 10 : above) + "px";
  if (aloud) {
    $("spoken").textContent = said.test + ", build " + said.build + ": " + said.what;
  }
}

function hidePoint() {
  if (pointed) {
    pointed.cursor.setAttribute("visibility", "hidden");
    pointed.item.classList.remove("is-pointed");
  }
  pointed = null;
  tooltip.hidden = true;
  band.hidden = true;
}

function entryOf(target) {
  const picture = target.closest ? target.closest("svg.ft-cells") : null;
  return picture ? byPicture.get(picture) : null;
}

grid.addEventListener("pointermove", (event) => {
  const entry = entryOf(event.target);
  if (!entry) {
    if (pointed && document.activeElement !== pointed.picture) hidePoint();
    return;
  }
  const box = entry.picture.getBoundingClientRect();
  const count = found.builds.length;
  const x = ((event.clientX - box.left) / box.width) * (count * STEP - GAP);
  const at = columnAt(x, count, { size: SQUARE, gap: GAP });
  if (entry !== pointed || at !== column) point(entry, at, false);
});
grid.addEventListener("pointerleave", () => {
  if (!pointed || document.activeElement !== pointed.picture) hidePoint();
});
grid.addEventListener("focusin", (event) => {
  const entry = entryOf(event.target);
  if (!entry) return;
  for (const other of drawn) other.picture.setAttribute("tabindex", other === entry ? 0 : -1);
  // The newest build first; after that the column the reader was in.
  point(entry, column >= 0 && column < found.builds.length ? column : found.builds.length - 1, true);
});
grid.addEventListener("focusout", hidePoint);
grid.addEventListener("keydown", (event) => {
  const entry = entryOf(event.target);
  if (!entry) return;
  const last = found.builds.length - 1;
  const position = drawn.indexOf(entry);
  const across = { ArrowLeft: column - 1, ArrowRight: column + 1, Home: 0, End: last };
  const down = { ArrowUp: position - 1, ArrowDown: position + 1, PageUp: position - 10, PageDown: position + 10 };
  if (event.key in across) {
    point(entry, Math.max(0, Math.min(last, across[event.key])), true);
  } else if (event.key in down) {
    const next = drawn[Math.max(0, Math.min(drawn.length - 1, down[event.key]))];
    if (next !== entry) {
      next.picture.focus({ preventScroll: true });
      next.item.scrollIntoView({ block: "nearest" });
    }
  } else {
    return;
  }
  event.preventDefault();
});
window.addEventListener("scroll", () => {
  if (!pointed || document.activeElement !== pointed.picture) hidePoint();
}, { passive: true });

// The lists beside the heatmap lead to the row of their test.
function reveal(item) {
  let entry = entries.get(testKey(item));
  if (!entry || !entry.item.isConnected) {
    textInput.value = "";
    packageSelect.selectedIndex = 0;
    drawRows(false);
    entry = entries.get(testKey(item));
  }
  if (!entry || !entry.item.isConnected) {
    kit.toast("This test is not among the rows the page draws.", { kind: "info" });
    return;
  }
  entry.item.scrollIntoView({ block: "center", behavior: kit.reducedMotion() ? "auto" : "smooth" });
  entry.picture.focus({ preventScroll: true });
  kit.flash(entry.item);
}

// --- Tiles and lists ------------------------------------------------------------------------

function tile(label, icon, tone) {
  const value = h("dd", { class: "ft-value efp-number" }, "0");
  const note = h("dd", { class: "ft-note" });
  const box = h("div", { class: "efp-surface efp-lift ft-tile", style: { "--ft-tone": `var(--color-${tone})` } }, h("dt", {}, h("span", { class: "ft-tile-icon" }, kit.icon(icon, "size-4")), label), value, note);
  return { box, value, note };
}

function showTiles() {
  if (!tiles) {
    tiles = {
      tests: tile("Tests", "list-checks", "primary"),
      changed: tile("Changed", "shuffle", "warning"),
      failing: tile("Failing now", "circle-x", "error"),
      read: tile("Builds read", "layers", "info"),
    };
    tiles.ring = h("dd", { class: "radial-progress ft-ring", role: "progressbar", style: { "--value": 0 } });
    tiles.read.box.append(tiles.ring);
    $("tiles").replaceChildren(...["tests", "changed", "failing", "read"].map((name) => tiles[name].box));
    kit.rise($("tiles").children, { stagger: 60 });
  }
  const read = found.builds.filter((build) => build.state === "ok").length;
  const gaps = found.builds.length - read;
  kit.countUp(tiles.tests.value, found.tests, { duration: 1100 });
  kit.countUp(tiles.changed.value, found.changed, { duration: 1100 });
  kit.countUp(tiles.failing.value, found.failing_now, { duration: 1100 });
  kit.countUp(tiles.read.value, read, { duration: 1100, format: (value) => Math.round(value) + " of " + found.builds.length });
  tiles.tests.note.textContent = "in the reports of these builds";
  tiles.changed.note.textContent = found.tests ? Math.round((found.changed / found.tests) * 100) + "% flipped at least once" : "nothing to compare";
  tiles.failing.note.textContent = "in the newest build with a report";
  tiles.read.note.textContent = gaps ? plural(gaps, "build") + " without results" : "every report was read";
  const share = found.builds.length ? Math.round((read / found.builds.length) * 100) : 0;
  tiles.ring.setAttribute("aria-valuenow", share);
  tiles.ring.textContent = share + "%";
  requestAnimationFrame(() => requestAnimationFrame(() => tiles.ring.style.setProperty("--value", share)));
}

// The newest verdicts of a test as small squares: its flips at a glance.
function miniStrip(cells) {
  const verdicts = Array.from(cells).filter((cell) => cell === "p" || cell === "f").slice(-12);
  const picture = svg("svg", { viewBox: "0 0 " + (verdicts.length * 7 - 2) + " 5", class: "ft-mini", "aria-hidden": "true" });
  verdicts.forEach((cell, index) => picture.append(svg("rect", { x: index * 7, y: 0, width: 5, height: 5, rx: 1.2, class: CLASSES[cell] })));
  return picture;
}

function fillList(id, items, line, nothing) {
  const list = $(id);
  list.replaceChildren(
    ...items.map((item) => h("li", {}, line(item))),
  );
  if (items.length === 0) list.append(h("li", { class: "ft-none" }, kit.icon("check", "size-4"), h("span", { text: nothing })));
  kit.rise(list.children, { stagger: 35, delay: 250 });
}

function jump(item) {
  return h("button", { type: "button", class: "ft-jump", title: "Show this test in the heatmap", onclick: () => reveal(item) }, testLabel(item));
}

function buildLink(number) {
  const build = found.builds.find((candidate) => candidate.number === number);
  return outside(build && build.url, "#" + number);
}

function showLists() {
  fillList(
    "flaky",
    found.rows.slice(0, 8),
    (row) => [jump(row), h("span", { class: "ft-meta" }, miniStrip(row.cells), h("span", { class: "badge badge-sm badge-soft badge-warning efp-number", text: plural(row.flips, "change") }))],
    "No test changed between passing and failing.",
  );
  fillList(
    "new-failures",
    found.new_failures,
    (item) => [
      jump(item),
      h("span", { class: "ft-meta" }, h("span", { class: "text-xs text-base-content/60" }, "failing since ", buildLink(item.since)), h("span", { class: "badge badge-sm badge-soft badge-error efp-number", text: plural(item.builds, "build") })),
    ],
    "Nothing started to fail in these builds.",
  );
  const slowest = found.slowest.length ? found.slowest[0].seconds : 1;
  fillList(
    "slowest",
    found.slowest,
    (item) => [
      h("span", { class: "ft-jump is-static" }, testLabel(item)),
      h(
        "span",
        { class: "ft-meta" },
        h("span", { class: "ft-bar", style: { "--share": Math.max(0.03, item.seconds / slowest) } }),
        h("span", { class: "text-xs text-base-content/70 efp-number whitespace-nowrap", title: "On average over " + plural(item.runs, "run"), text: formatSeconds(item.seconds) }),
      ),
    ],
    "No test reported how long it took.",
  );
}

// --- The answer -------------------------------------------------------------------------------

function show(answer, choice) {
  // The same job asked again, and nothing new in it: the heatmap stays as it is.
  const same = found && choice === shownChoice && JSON.stringify(found.builds) === JSON.stringify(answer.builds);
  const fresh = choice !== shownChoice || !found;
  found = answer;
  shownChoice = choice;
  column = -1;
  $("loading").hidden = true;
  showProblem(null);
  const job = jobSelect.options[jobSelect.selectedIndex];
  if (found.builds.length === 0) {
    $("result").hidden = true;
    showProblem({ icon: "hourglass", title: "No finished build yet", message: "This job has no finished build to read. Come back when one has ended." });
    say((job ? job.textContent + ": " : "") + "no finished build yet");
    return;
  }
  const wasHidden = $("result").hidden;
  $("result").hidden = false;
  showTiles();

  if (!same) {
    // The package filter is kept across a new answer for the same job, when that package is still there.
    const before = fresh ? null : wanted().package;
    packages = packagesOf(found.rows);
    packageSelect.replaceChildren(
      h("option", { value: "" }, "All packages (" + found.rows.length + ")"),
      ...packages.map((entry, index) => h("option", { value: String(index), selected: entry.name === before }, (entry.name || "(no package)") + " (" + entry.rows + ")")),
    );
  }
  if (fresh) textInput.value = "";
  if (!same) columnsHeader(found.builds);
  drawRows(!same);
  showLists();
  if (wasHidden) kit.rise([$("heat"), ...$("heat").nextElementSibling.children], { delay: 120, stagger: 80 });

  const gaps = found.builds.filter((build) => build.state !== "ok");
  const kept = found.builds.length - found.fetched;
  let text = found.fetched
    ? plural(found.fetched, "report") + " fetched from Jenkins" + (kept ? ", " + kept + " read from storage." : ".")
    : "Every report was read from storage; Jenkins was asked for the list of builds only.";
  if (gaps.length) {
    text += " Without results: " + gaps.map((build) => "#" + build.number + " (" + describeBuild(build) + ")").join(", ") + ".";
  }
  $("source").textContent = text;
  say((job ? job.textContent : found.job.name) + ", the last " + plural(found.builds.length, "finished build"));
}

function choice() {
  const picked = buildsGroup.querySelector("input:checked");
  return new URLSearchParams({ job: jobSelect.value, builds: picked ? picked.value : "" });
}

async function load(button) {
  if (!jobSelect.value) return;
  const chosen = choice();
  const shown = !$("result").hidden;
  if (shown) {
    rowsList.classList.add("is-stale"); // the old heatmap stays in place, dimmed, until the new one is there
  } else {
    $("problem").hidden = true;
    $("loading").hidden = false;
  }
  say("Asking Jenkins...");
  const work = async () => {
    let response;
    try {
      response = await fetch("api/matrix?" + chosen);
    } catch (error) {
      throw { code: "unreachable", message: "The app cannot be reached right now." };
    }
    if (!response.ok) throw await failure(response);
    show(await response.json(), chosen.toString());
    // The choice goes into the address, so that the view can be shared as a link.
    history.replaceState(null, "", "?" + chosen);
  };
  try {
    await (button ? kit.busy(button, work) : work());
  } catch (problem) {
    const message = problem.message || "Something went wrong.";
    say(message);
    $("loading").hidden = true;
    if (shown && !NOT_SET_UP.includes(problem.code)) {
      kit.toast(message, { kind: "error" });
    } else {
      $("result").hidden = true;
      found = null;
      showProblem(problem);
    }
  } finally {
    rowsList.classList.remove("is-stale");
  }
}

// --- Starting -----------------------------------------------------------------------------------

async function start() {
  await kit.ready();
  kit.themeSwitch($("theme"));
  $("legend").replaceChildren(...LEGEND.map(([state, label]) => h("li", {}, swatch(state), h("span", { text: label }))));
  band.append(h("span", { class: "ft-band-label" }));

  let offered;
  try {
    const response = await fetch("api/jobs");
    if (!response.ok) throw await failure(response);
    offered = await response.json();
  } catch (problem) {
    say(problem.message || "The app cannot be reached right now.");
    showProblem(problem.message ? problem : { message: "The app cannot be reached right now." });
    return;
  }
  const asked = new URLSearchParams(location.search);
  jobSelect.replaceChildren(...offered.jobs.map((job) => h("option", { value: job.key }, job.name)));
  if (offered.jobs.some((job) => job.key === asked.get("job"))) jobSelect.value = asked.get("job");
  const builds = offered.build_choices.map(String).includes(asked.get("builds")) ? asked.get("builds") : String(offered.build_choices[0]);
  buildsGroup.replaceChildren(
    ...offered.build_choices.map((count) =>
      h("input", { type: "radio", name: "builds", value: String(count), class: "join-item btn btn-sm", "aria-label": "Last " + count, checked: String(count) === builds }),
    ),
  );
  if (offered.jobs.length === 0) {
    say("No job is listed yet");
    showProblem({ icon: "list-plus", title: "No job is listed yet", message: "Add your Jenkins jobs to jobs.json and deploy again." });
    return;
  }
  jobSelect.disabled = false;
  showButton.disabled = false;
  load();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  load(showButton);
});
jobSelect.addEventListener("change", () => load(showButton));
buildsGroup.addEventListener("change", () => load(showButton));
packageSelect.addEventListener("change", () => drawRows(false));
textInput.addEventListener("input", () => drawRows(false));
textInput.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && textInput.value) {
    event.preventDefault();
    clearFilters();
  }
});
for (const button of document.querySelectorAll("[data-sort]")) {
  button.addEventListener("click", () => {
    sortMode = button.dataset.sort;
    for (const other of document.querySelectorAll("[data-sort]")) other.setAttribute("aria-pressed", String(other === button));
    if (found) drawRows(false);
  });
}
// "/" jumps to the search, as in many tools; not while the visitor is typing elsewhere.
document.addEventListener("keydown", (event) => {
  const typing = event.target.closest && event.target.closest("input, select, textarea");
  if (event.key === "/" && !typing && !$("result").hidden && found && found.rows.length) {
    event.preventDefault();
    textInput.focus();
  }
});

start();
