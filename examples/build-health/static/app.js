// Every URL in this file is relative ("api/health", not "/api/health"): the
// app is served under /{slug}/ once deployed. The one exception is the EFP
// kit, at /_efp/lib/, which every gateway serves to every app.
//
// The table is loaded when the page opens and when the visitor asks for it.
// There is no timer: a page that asks every few seconds would keep the app
// from ever being idle, and the server keeps Jenkins's answers anyway.
//
// The arithmetic of the drawings is in charts.mjs (tested with node); this
// file puts it on the page. The two charts at the top are ECharts, through
// kit.chart, which follows the theme and the size of its box.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
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

const { h } = kit;
const SVG = "http://www.w3.org/2000/svg";
const SQUARE = 14; // one build, in the units of the drawing
const GAP = 2;
const $ = (id) => document.getElementById(id);
const refreshButton = $("refresh");
const jobsList = $("jobs");
const tooltip = $("tooltip");
const tips = new WeakMap(); // a mark -> the lines of its tooltip
const charts = {}; // the two ECharts, once drawn
let mayRefresh = false; // known once the server has said whether the visitor is signed in
let shown = false; // the first report is on the page

// The kinds of verdict.kind and of a build's state, as daisyUI colours.
const TONES = { success: "success", unstable: "warning", failure: "error", aborted: "neutral", running: "info", flaky: "warning" };

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

// A problem that stops the page: an empty state with what to do, instead of the board.
function showProblem(problem) {
  const notSetUp = problem && ["not_configured", "connector_disabled", "connector_not_enabled"].includes(problem.code);
  $("problem").hidden = !problem;
  $("problem").replaceChildren(
    problem
      ? h(
          "div",
          { class: "efp-surface efp-empty" },
          h("span", { class: "efp-empty-icon" }, kit.icon(notSetUp ? "plug-zap" : "cloud-alert")),
          h("strong", {}, notSetUp ? "Not set up yet" : "Jenkins could not be read"),
          h("span", { class: "text-sm max-w-md", text: problem.message }),
        )
      : "",
  );
}

function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

function svg(name, attributes) {
  const made = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes || {})) {
    made.setAttribute(key, value);
  }
  return made;
}

function clock(milliseconds) {
  return new Date(milliseconds).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
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
    group.append(svg("rect", { x: x + 1, y: y + 1, width: size - 2, height: size - 2, rx: 3 }));
  } else if (state !== "flaky") {
    group.append(svg("rect", { x: x, y: y, width: size, height: size, rx: 3.5 }));
  }
  const inside = markPath(state, x, y, size);
  if (inside) {
    group.append(svg("path", { d: inside, class: "inside" }));
  }
  return group;
}

function swatch(state) {
  const picture = svg("svg", { viewBox: "0 0 " + SQUARE + " " + SQUARE, width: 12, height: 12, "aria-hidden": "true" });
  picture.append(mark(state, 0, 0, SQUARE));
  return picture;
}

function strip(job, row) {
  const drawn = stripCells(job.builds, { size: SQUARE, gap: GAP, slots: SLOTS });
  const picture = svg("svg", {
    viewBox: "0 0 " + drawn.width + " " + drawn.height,
    width: drawn.width,
    height: drawn.height,
    class: "strip",
    role: "group",
    style: "--row: " + row,
    "aria-label": "Builds of " + job.name + ", oldest to newest. Use the arrow keys to move between them.",
  });
  for (let slot = 0; slot < SLOTS - drawn.cells.length; slot += 1) {
    // A job with fewer builds keeps its place: a faint dash where no build is.
    picture.append(svg("rect", { x: slot * (SQUARE + GAP), y: SQUARE / 2 - 2, width: SQUARE, height: 4, rx: 2, class: "no-build" }));
  }
  drawn.cells.forEach((cell, index) => {
    const lines = describeBuild(cell.build, cell.build.timestamp ? clock(cell.build.timestamp) : "");
    // Only the newest square is a tab stop; the arrow keys reach the others.
    const hold = svg(cell.build.url ? "a" : "g", {
      class: "build",
      tabindex: index === drawn.cells.length - 1 ? 0 : -1,
      "aria-label": lines.join(", "),
      style: "--i: " + index,
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

// The durations of a job, oldest to newest: a line that draws itself, a soft area under it.
function spark(health, row) {
  const width = 96;
  const height = 26;
  const drawn = sparkline(health.durations, width, height);
  const picture = svg("svg", { viewBox: "0 0 " + width + " " + height, class: "spark", role: "img", style: "--row: " + row });
  const lines = [
    "Durations of " + plural(health.durations.length, "build"),
    formatDuration(Math.min(...health.durations)) + " to " + formatDuration(Math.max(...health.durations)) + ", oldest to newest",
  ];
  picture.setAttribute("aria-label", lines.join(": "));
  const first = drawn.points[0];
  picture.append(svg("path", { d: drawn.path + "L" + drawn.last.x + " " + height + "L" + first.x + " " + height + "Z", class: "area" }));
  picture.append(svg("path", { d: drawn.path, class: "trend", pathLength: 1 }));
  picture.append(svg("circle", { cx: drawn.last.x, cy: drawn.last.y, r: 3.2, class: "latest" }));
  tips.set(picture, lines);
  return picture;
}

// --- The tiles ---------------------------------------------------------------------------------

function tile({ label, icon, tone, value, format, note, ring }) {
  const number = h("dd", { class: "bh-value" }, "0");
  const box = h(
    "div",
    { class: "efp-surface efp-lift bh-tile", style: { "--bh-tone": `var(--color-${tone})` } },
    h("dt", {}, h("span", { class: "bh-icon" }, kit.icon(icon, "size-4")), label),
    number,
    note ? h("dd", { class: "bh-note", text: note }) : null,
  );
  if (ring !== undefined) {
    const circle = h("dd", { class: "radial-progress", role: "progressbar", "aria-valuenow": ring, style: { "--value": 0 } }, "");
    box.append(circle);
    requestAnimationFrame(() => requestAnimationFrame(() => circle.style.setProperty("--value", ring)));
  }
  if (value === null || value === undefined) number.textContent = "-";
  else kit.countUp(number, value, { from: 0, format, duration: 1100 });
  return box;
}

function showTiles(total) {
  const rate = total.success_rate;
  $("tiles").replaceChildren(
    tile({ label: "Jobs", icon: "boxes", tone: "primary", value: total.jobs, note: total.unread ? plural(total.unread, "job") + " could not be read" : "watched" }),
    tile({ label: "Passing", icon: "circle-check", tone: "success", value: total.passing, note: total.unstable ? total.unstable + " more unstable" : "last build green" }),
    tile({ label: "Broken", icon: "circle-x", tone: "error", value: total.broken, note: total.broken ? "last build failed" : "nothing broken" }),
    tile({ label: "Flaky", icon: "shuffle", tone: "warning", value: total.flaky, note: "pass and fail by turns" }),
    tile({
      label: "Success rate",
      icon: "gauge",
      tone: rate === null ? "neutral" : rate >= 0.9 ? "success" : rate >= 0.7 ? "warning" : "error",
      value: rate === null ? null : Math.round(rate * 100),
      format: (value) => Math.round(value) + "%",
      note: total.builds ? "of " + plural(total.builds, "build") : "",
      ring: rate === null ? undefined : Math.round(rate * 100),
    }),
  );
  $("tiles").lastElementChild.classList.add("col-span-2", "md:col-span-1");
  if (!shown) kit.rise($("tiles").children, { stagger: 60 });
}

// --- The charts -----------------------------------------------------------------------------------

const DAY_STATES = ["success", "unstable", "failure", "aborted"];

// A result's colour for ECharts. Aborted is a solid grey between the text and the
// page, so that it reads on both themes.
function stateColor(state) {
  return state === "aborted" ? kit.color("color-mix(in oklab, var(--color-base-content) 30%, var(--color-base-100))") : kit.color(TONES[state]);
}

function byDay(jobs) {
  const days = new Map();
  for (const job of jobs) {
    for (const build of job.builds) {
      if (!build.timestamp || build.state === "running") continue;
      const day = new Date(build.timestamp);
      const key = day.getFullYear() + "-" + String(day.getMonth() + 1).padStart(2, "0") + "-" + String(day.getDate()).padStart(2, "0");
      if (!days.has(key)) days.set(key, { success: 0, unstable: 0, failure: 0, aborted: 0, date: day });
      days.get(key)[build.state] += 1;
    }
  }
  return Array.from(days.entries())
    .sort(([first], [second]) => first.localeCompare(second))
    .slice(-14)
    .map(([, counts]) => counts);
}

function dayOption(jobs) {
  const days = byDay(jobs);
  return {
    grid: { left: 34, right: 8, top: 34, bottom: 28 },
    legend: { top: 0, right: 0 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "category", data: days.map((day) => day.date.toLocaleDateString(undefined, { day: "numeric", month: "short" })) },
    yAxis: { type: "value", minInterval: 1 },
    series: DAY_STATES.map((state) => ({
      name: STATE_LABELS[state].split(" ")[0],
      type: "bar",
      stack: "builds",
      barMaxWidth: 26,
      color: stateColor(state),
      itemStyle: { borderRadius: state === "success" ? [0, 0, 3, 3] : state === "aborted" ? [4, 4, 0, 0] : 0 },
      emphasis: { focus: "series" },
      animationDelay: (index) => index * 40,
      data: days.map((day) => day[state]),
    })),
  };
}

function stateOption(jobs) {
  const counts = { success: 0, unstable: 0, failure: 0, aborted: 0 };
  for (const job of jobs) for (const build of job.builds) if (build.state in counts) counts[build.state] += 1;
  const total = Object.values(counts).reduce((sum, value) => sum + value, 0);
  return {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    legend: { bottom: 0, left: "center" },
    title: {
      text: String(total),
      subtext: "builds",
      left: "center",
      top: "36%",
      textStyle: { fontSize: 28, fontWeight: 750 },
      subtextStyle: { fontSize: 12, color: kit.color("base-content", 0.55) },
    },
    series: [
      {
        type: "pie",
        radius: ["52%", "74%"],
        center: ["50%", "46%"],
        padAngle: 2,
        itemStyle: { borderRadius: 6 },
        label: { show: false },
        emphasis: { scale: true, scaleSize: 6 },
        animationType: "expansion",
        animationDuration: 1200,
        data: DAY_STATES.filter((state) => counts[state]).map((state) => ({
          name: STATE_LABELS[state].split(" ")[0],
          value: counts[state],
          itemStyle: { color: stateColor(state) },
        })),
      },
    ],
  };
}

async function showCharts(jobs) {
  const any = jobs.some((job) => job.builds.length);
  $("charts").hidden = !any;
  if (!any) return;
  if (!charts.days) {
    charts.days = await kit.chart($("chart-days"), dayOption(jobs));
    charts.states = await kit.chart($("chart-states"), stateOption(jobs));
  } else {
    charts.days.setOption(dayOption(jobs));
    charts.states.setOption(stateOption(jobs));
  }
}

// --- The jobs -----------------------------------------------------------------------------------

function badge(kind, label, detail) {
  const tone = TONES[kind] || "neutral";
  return h("span", { class: "badge badge-sm badge-soft badge-" + tone, title: detail }, kind === "running" ? h("span", { class: "efp-live !bg-info" }) : swatch(kind), label);
}

function jobRow(job, now, row) {
  const name = h(
    "div",
    { class: "min-w-0" },
    job.url
      ? h("a", { class: "bh-name link link-hover", href: job.url, target: "_blank", rel: "noopener noreferrer", text: job.name })
      : h("span", { class: "bh-name", text: job.name }),
    h("span", { class: "bh-path", text: job.path }),
  );
  const item = h("li", { class: "bh-job" }, name);

  if (job.problem) {
    // Jenkins's "no" for this job stays in this row; the other rows are fine.
    item.append(h("div", { class: "alert alert-soft alert-error py-2 text-sm col-span-3" }, kit.icon("circle-alert", "size-4"), h("span", { text: job.problem.message })));
    return item;
  }

  const said = verdict(job.health, now * 1000);
  const badges = h("div", { class: "bh-badges" }, badge(said.kind, said.label, said.detail));
  if (job.health.flaky) {
    badges.append(badge("flaky", "Flaky", "Passing and failing took turns " + job.health.flips + " times in the newest builds."));
  }
  if (job.health.running) {
    badges.append(badge("running", "Running", "A build is running now."));
  }
  name.append(badges);

  item.append(h("div", { class: "bh-strip-cell min-w-0" }, job.builds.length ? strip(job, row) : h("span", { class: "text-sm text-base-content/50" }, "No builds yet.")));

  const rate = job.health.success_rate;
  const tone = rate === null ? "neutral" : rate >= 0.9 ? "success" : rate >= 0.7 ? "warning" : "error";
  const ring = h(
    "div",
    {
      class: "radial-progress bh-rate text-" + tone,
      role: "progressbar",
      "aria-valuenow": rate === null ? 0 : Math.round(rate * 100),
      title: job.health.judged ? job.health.passed + " of " + plural(job.health.judged, "finished build") + " succeeded." : "No finished build yet.",
      style: { "--value": 0 },
    },
    formatPercent(rate),
  );
  setTimeout(() => ring.style.setProperty("--value", rate === null ? 0 : Math.round(rate * 100)), 120 + row * 70);
  item.append(ring);

  const duration = h("div", { class: "bh-duration" }, h("span", {}, formatDuration(job.health.median_duration)));
  if (job.health.durations.length > 1) {
    duration.append(spark(job.health, row));
  }
  item.append(duration);
  return item;
}

function show(report, refreshed) {
  const any = report.jobs.length > 0;
  $("loading").hidden = true;
  $("tiles").hidden = !any;
  $("board").hidden = !any;
  $("empty").hidden = any;
  showProblem(null);
  mayRefresh = report.signed_in && any;
  refreshButton.title = report.signed_in ? "Ask Jenkins again" : "Sign in to refresh";
  if (!any) {
    say("No job to watch yet");
    return;
  }
  showTiles(report.summary);
  showCharts(report.jobs);
  jobsList.replaceChildren(...report.jobs.map((job, row) => jobRow(job, report.now, row)));
  if (!shown) kit.rise([$("charts"), $("board")], { delay: 150, stagger: 90 });
  shown = true;

  const oldest = Math.min(...report.jobs.map((job) => job.fetched_at));
  const kept = report.jobs.length - report.asked;
  let text;
  if (refreshed) {
    text = report.asked ? "Jenkins was asked about " + plural(report.asked, "job") + "." : "Nothing was asked again.";
    if (kept) {
      text += " " + (kept === 1 ? "One job was" : kept + " jobs were") + " asked less than a minute ago.";
    }
    kit.toast(text, { kind: "success", timeout: 3500 });
  } else if (kept === 0) {
    text = "Jenkins was asked just now.";
  } else {
    text = "Jenkins was last asked " + formatAge((report.now - oldest) * 1000) + " ago.";
  }
  say(text + (report.signed_in ? "" : " Sign in to refresh."));
}

async function load(url, request, refreshed) {
  refreshButton.disabled = !mayRefresh && shown;
  jobsList.classList.add("is-stale"); // the old rows stay in place, dimmed, until the new ones are there
  say("Asking Jenkins...");
  const work = async () => {
    let response;
    try {
      response = await fetch(url, request);
    } catch (error) {
      throw { code: "unreachable", message: "The app cannot be reached right now." };
    }
    if (!response.ok) throw await failure(response);
    show(await response.json(), refreshed);
  };
  try {
    await (refreshed ? kit.busy(refreshButton, work) : work());
  } catch (problem) {
    say(problem.message || "Something went wrong.");
    if (shown) {
      kit.toast(problem.message, { kind: "error" });
    } else {
      $("loading").hidden = true;
      showProblem(problem);
    }
  } finally {
    refreshButton.disabled = !mayRefresh;
    jobsList.classList.remove("is-stale");
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
  tooltip.replaceChildren(...lines.map((line, index) => h(index === 0 ? "strong" : "span", { text: line })));
  tooltip.hidden = false;
  const place = holder.getBoundingClientRect();
  const size = tooltip.getBoundingClientRect();
  const left = place.left + place.width / 2 - size.width / 2;
  const above = place.top - size.height - 10;
  tooltip.style.left = Math.max(8, Math.min(left, window.innerWidth - size.width - 8)) + "px";
  tooltip.style.top = (above < 8 ? place.bottom + 10 : above) + "px";
}

function hideTip() {
  tooltip.hidden = true;
}

jobsList.addEventListener("pointerover", (event) => showTip(event.target));
jobsList.addEventListener("pointerout", hideTip);
jobsList.addEventListener("focusin", (event) => showTip(event.target));
jobsList.addEventListener("focusout", hideTip);
window.addEventListener("scroll", hideTip, { passive: true });

// Left and right walk along a strip; Home and End jump to its ends.
jobsList.addEventListener("keydown", (event) => {
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

await kit.ready();
kit.themeSwitch($("theme"));

for (const state of ["success", "unstable", "failure", "aborted", "running"]) {
  $("legend").append(h("li", {}, swatch(state), h("span", {}, STATE_LABELS[state])));
}

refreshButton.addEventListener("click", () => load("api/refresh", { method: "POST" }, true));

load("api/health", undefined, false);
