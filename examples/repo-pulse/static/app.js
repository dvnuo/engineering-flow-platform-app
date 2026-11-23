// The page gets its URLs from the template (url_for), which adds the /{slug}
// prefix once the app is deployed. Do not write "/pulse" or "/refresh" here.
// The one root-absolute URL is the EFP kit's, at /_efp/lib/, which every
// gateway serves to every app.
//
// The charts are loaded when the visitor asks for them. There is no timer:
// when GitHub is still computing, the page says so and offers a button.
//
// The arithmetic of the drawings is in charts.mjs (tested with node); this
// file puts it on the page. The weekly line is ECharts, through kit.chart,
// which follows the theme and the size of its box. The calendar, the punch
// card and the ages are drawn by hand as SVG and fill in with a wave when a
// repository is first shown; a refresh moves numbers, not the whole page.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import {
  DAYS,
  MONTHS,
  barRows,
  calendar,
  dayName,
  formatAge,
  formatCount,
  histogram,
  hourName,
  punchCard,
} from "./charts.mjs";

const { h } = kit;
const SVG = "http://www.w3.org/2000/svg";
const NOT_SET_UP = ["not_configured", "connector_disabled", "connector_not_enabled"];
const main = document.querySelector("main");
const pulseUrl = main.dataset.pulseUrl;
const refreshUrl = main.dataset.refreshUrl;

const $ = (id) => document.getElementById(id);
const form = $("choose");
const repository = $("repository");
const showButton = $("show");
const refreshButton = $("refresh"); // absent for a visitor who is not signed in
const tooltip = $("tooltip");
let shown = ""; // the repository on the page, in lower case: GitHub does not tell the cases apart
let weekly = null; // the ECharts line, made once
let tiles = null; // the four tiles, made once and counted up after that

// --- Small things ------------------------------------------------------------------------

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function failure(response) {
  try {
    const body = await response.json();
    if (body.error && body.error.message) return { code: body.error.code || "", message: body.error.message };
  } catch (error) {
    // not JSON
  }
  return {
    code: "",
    message: response.status === 401 ? "Your session has ended. Reload the page to sign in again." : "The app answered with an error (" + response.status + ").",
  };
}

function say(text) {
  $("status").textContent = text;
}

function plural(count, word) {
  return formatCount(count) + " " + word + (count === 1 ? "" : "s");
}

function svg(name, attributes) {
  const made = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes || {})) {
    made.setAttribute(key, value);
  }
  return made;
}

function label(x, y, text, anchor) {
  const made = svg("text", { x, y, class: "axis", "text-anchor": anchor || "start" });
  made.textContent = text;
  return made;
}

// A link to GitHub. The address was checked by the server; the text goes in as text.
function outside(url, text, className) {
  return url ? h("a", { class: className || "link link-hover", href: url, target: "_blank", rel: "noopener noreferrer", text }) : h("span", { class: className || "", text });
}

function swatch(className) {
  const picture = svg("svg", { viewBox: "0 0 11 11", width: 11, height: 11, "aria-hidden": "true" });
  picture.append(svg("rect", { width: 11, height: 11, rx: 2.5, class: className }));
  return picture;
}

// The same values as a table, for a reader who cannot use the picture.
function numbers(headers, rows) {
  return h(
    "details",
    { class: "rp-numbers" },
    h("summary", {}, kit.icon("table-2", "size-3.5"), "The numbers"),
    h(
      "div",
      { class: "rp-scroll" },
      h(
        "table",
        { class: "table table-xs" },
        h("thead", {}, h("tr", {}, ...headers.map((header) => h("th", { scope: "col", text: header })))),
        h(
          "tbody",
          {},
          ...rows.map((row) => h("tr", {}, ...row.map((value, index) => h(index === 0 ? "th" : "td", index === 0 ? { scope: "row" } : {}, value instanceof Node ? value : String(value))))),
        ),
      ),
    ),
  );
}

// A problem that stops the page: an empty state with what to do, in place of the charts.
function showProblem(problem) {
  $("problem").hidden = !problem;
  if (!problem) {
    $("problem").replaceChildren();
    return;
  }
  const notSetUp = NOT_SET_UP.includes(problem.code);
  const typo = problem.code === "bad_input";
  $("problem").replaceChildren(
    h(
      "div",
      { class: "efp-surface efp-empty" },
      h("span", { class: "efp-empty-icon" }, kit.icon(notSetUp ? "plug-zap" : typo ? "text-cursor-input" : "cloud-alert")),
      h("strong", { text: notSetUp ? "Not set up yet" : typo ? "That is not a repository name" : "GitHub could not be read" }),
      h("span", { class: "text-sm max-w-md", text: problem.message }),
    ),
  );
  kit.rise($("problem").firstChild);
}

// --- Pointing at a mark: the pointer and the arrow keys do the same ----------------------

function showTip(mark, lines) {
  tooltip.replaceChildren(...lines.map((line, index) => h(index === 0 ? "strong" : "span", { text: line })));
  tooltip.hidden = false;
  const place = mark.getBoundingClientRect();
  const size = tooltip.getBoundingClientRect();
  const left = place.left + place.width / 2 - size.width / 2;
  const above = place.top - size.height - 8;
  tooltip.style.left = Math.max(8, Math.min(left, window.innerWidth - size.width - 8)) + "px";
  tooltip.style.top = (above < 8 ? place.bottom + 8 : above) + "px";
}

// Makes a chart one stop for the Tab key. `moves` says how far each arrow key
// goes in the list of marks; `describe` gives the lines of a mark's tooltip.
function explore(picture, marks, moves, describe, start) {
  let at = -1;
  const leave = () => {
    if (at >= 0) marks[at].classList.remove("pointed");
    tooltip.hidden = true;
  };
  const point = (index, aloud) => {
    leave();
    at = index;
    marks[at].classList.add("pointed");
    const lines = describe(at);
    showTip(marks[at], lines);
    if (aloud) $("spoken").textContent = lines.join(", ");
  };
  picture.setAttribute("tabindex", 0);
  picture.addEventListener("pointermove", (event) => {
    const mark = event.target.closest("[data-mark]");
    if (!mark) leave();
    else if (Number(mark.dataset.mark) !== at || tooltip.hidden) point(Number(mark.dataset.mark), false);
  });
  picture.addEventListener("pointerleave", leave);
  picture.addEventListener("focus", () => point(at >= 0 ? at : start, true));
  picture.addEventListener("blur", leave);
  picture.addEventListener("keydown", (event) => {
    if (!(event.key in moves)) return;
    event.preventDefault();
    const next = at + moves[event.key];
    if (next >= 0 && next < marks.length) point(next, true);
  });
}

window.addEventListener("scroll", () => {
  tooltip.hidden = true;
}, { passive: true });

// --- Commits ------------------------------------------------------------------------------------

function weekLabel(seconds) {
  const date = new Date(seconds * 1000);
  return date.getUTCDate() + " " + MONTHS[date.getUTCMonth()];
}

// The pulse of the year: one point a week, the busiest one marked. No colour
// is written here: the line takes the theme's first colour (primary), so a
// change of theme redraws it right.
function weeklyOption(weeks) {
  const totals = weeks.map((week) => week.days.reduce((sum, day) => sum + day, 0));
  const best = totals.indexOf(Math.max(...totals));
  return {
    grid: { left: 4, right: 24, top: 30, bottom: 2, containLabel: true },
    tooltip: { trigger: "axis", valueFormatter: (value) => plural(value, "commit") },
    xAxis: { type: "category", boundaryGap: false, data: weeks.map((week) => "week of " + weekLabel(week.week)), axisLabel: { formatter: (value) => value.slice(8), hideOverlap: true } },
    yAxis: { type: "value", minInterval: 1, splitNumber: 3 },
    series: [
      {
        name: "Commits",
        type: "line",
        data: totals,
        smooth: 0.35,
        showSymbol: false,
        symbolSize: 8,
        lineStyle: { width: 2.5 },
        areaStyle: { opacity: 0.14 },
        markPoint: totals[best]
          ? { symbol: "circle", symbolSize: 10, data: [{ coord: [best, totals[best]], value: totals[best] }], label: { position: "top", distance: 6, fontWeight: 650, fontSize: 11, textBorderWidth: 0 } }
          : undefined,
        animationDuration: 1500,
        animationEasing: "cubicOut",
      },
    ],
  };
}

async function drawWeekly(chart) {
  const box = $("weekly");
  box.hidden = chart.state !== "ok" || chart.total === 0;
  if (box.hidden) return;
  if (!weekly) weekly = await kit.chart(box, weeklyOption(chart.weeks));
  else weekly.setOption(weeklyOption(chart.weeks));
}

function drawCommits(chart) {
  if (chart.total === 0) {
    return [quiet("git-commit-horizontal", "No commits in the last year.")];
  }
  const drawn = calendar(chart.weeks, { size: 12, gap: 3 });
  const picture = svg("svg", {
    viewBox: "0 0 " + drawn.width + " " + drawn.height,
    class: "rp-calendar",
    role: "img",
    "aria-label": "Commits per day over the last year. Use the arrow keys to read each day.",
  });
  for (const month of drawn.months) picture.append(label(month.x, 10, month.label));
  for (const day of drawn.days) picture.append(label(0, day.y, day.label));
  // --w and --d: the week and the day, for the wave that fills the year from the left.
  const marks = drawn.cells.map((cell) =>
    svg("rect", { x: cell.x, y: cell.y, width: cell.size, height: cell.size, rx: 2.5, class: "lv lv-" + cell.level, "data-mark": cell.index, style: "--w: " + Math.floor(cell.index / 7) + "; --d: " + (cell.index % 7) }),
  );
  picture.append(...marks);
  explore(
    picture,
    marks,
    { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1 },
    (index) => [plural(drawn.cells[index].count, "commit"), dayName(drawn.cells[index].day)],
    marks.length - 1,
  );
  const scale = h(
    "p",
    { class: "rp-legend" },
    h("span", {}, "Fewer"),
    ...[0, 1, 2, 3, 4].map((level) => swatch("lv lv-" + level)),
    h("span", {}, "More"),
    h("span", { class: "rp-legend-note", text: "up to " + plural(drawn.max, "commit") + " a day" }),
  );
  return [
    h("div", { class: "rp-scroll rp-year" }, picture),
    scale,
    numbers(["Week of", "Commits"], chart.weeks.map((week) => [dayName(week.week), formatCount(week.days.reduce((sum, day) => sum + day, 0))])),
  ];
}

function drawPunchCard(chart) {
  if (chart.total === 0) {
    return [quiet("clock", "No commits to place on the clock yet.")];
  }
  const drawn = punchCard(chart.hours, { step: 24 });
  const picture = svg("svg", {
    viewBox: "0 0 " + drawn.width + " " + drawn.height,
    class: "rp-punch",
    role: "img",
    "aria-label": "Commits by weekday and hour. Use the arrow keys to read each hour.",
  });
  // The working day, behind the dots: nine to six.
  picture.append(svg("rect", { x: 40 + 9 * drawn.step, y: 4, width: 9 * drawn.step, height: 7 * drawn.step, rx: 6, class: "rp-day" }));
  for (const day of drawn.days) picture.append(label(0, day.y, day.label));
  for (const hour of drawn.hours) picture.append(label(hour.x, drawn.height - 4, hour.label, "middle"));
  const marks = drawn.dots.map((dot) => {
    const mark = svg("g", { "data-mark": dot.index });
    // The whole square of an hour answers the pointer, not only the dot in it.
    mark.append(svg("rect", { x: dot.x, y: dot.y, width: drawn.step, height: drawn.step, class: "hit" }));
    mark.append(
      svg("circle", {
        cx: dot.cx,
        cy: dot.cy,
        r: dot.r || 1,
        class: dot.r ? "dot" : "no-dot",
        style: "--h: " + dot.hour + "; --d: " + dot.day + "; --strength: " + (drawn.max ? (0.35 + 0.65 * dot.commits / drawn.max).toFixed(2) : 0),
      }),
    );
    return mark;
  });
  picture.append(...marks);
  const busiest = drawn.dots.reduce((best, dot) => (dot.commits > drawn.dots[best].commits ? dot.index : best), 0);
  explore(
    picture,
    marks,
    { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -24, ArrowDown: 24 },
    (index) => [plural(drawn.dots[index].commits, "commit"), DAYS[drawn.dots[index].day] + ", " + hourName(drawn.dots[index].hour)],
    busiest,
  );
  const top = drawn.dots[busiest];
  return [
    h("div", { class: "rp-scroll" }, picture),
    h(
      "p",
      { class: "rp-legend" },
      h("span", { class: "rp-workday", "aria-hidden": "true" }),
      h("span", {}, "nine to six"),
      h("span", { class: "rp-legend-note", text: "busiest: " + DAYS[top.day] + "s, " + hourName(top.hour) }),
    ),
    numbers(
      ["Day"].concat(chart.hours[0].map((_count, hour) => String(hour).padStart(2, "0"))),
      chart.hours.map((day, row) => [DAYS[row]].concat(day.map((count) => formatCount(count)))),
    ),
  ];
}

function drawContributors(chart) {
  if (chart.people.length === 0) {
    return [quiet("users", "Nobody has contributed yet.")];
  }
  // barRows on a room of 100 units: each length is a share of the top
  // contributor in percent, and a small one is still at least 2 wide.
  const drawn = barRows(chart.people.map((person) => ({ label: person.login, value: person.commits })), { width: 306, label: 150 });
  const total = chart.people.reduce((sum, person) => sum + person.commits, 0);
  const list = h(
    "ol",
    { class: "rp-people" },
    ...chart.people.map((person, index) => {
      const count = h("span", { class: "rp-count efp-number" }, "0");
      kit.countUp(count, person.commits, { from: 0, duration: 1100, format: formatCount });
      return h(
        "li",
        { style: { "--i": index } },
        kit.avatar(person.login, { size: "2rem" }),
        h(
          "div",
          { class: "rp-person" },
          h("div", { class: "rp-person-line" }, outside(person.url, person.login, "rp-login link link-hover"), count),
          h("div", { class: "rp-track", title: Math.round((person.commits / total) * 100) + "% of the commits of these " + chart.people.length }, h("span", { class: "rp-fill", style: { "--share": drawn.rows[index].length / 100 } })),
        ),
      );
    }),
  );
  return shortened(list, 6, "contributors");
}

// --- Pull requests ------------------------------------------------------------------------------

// The colour of an age: fresh, waiting, forgotten.
function ageTone(days) {
  return days < 7 ? "success" : days < 30 ? "warning" : "error";
}

function drawPulls(chart) {
  if (chart.open === 0) {
    return [quiet("party-popper", "No pull request is open: nothing waits for a review.")];
  }
  const drawn = histogram(chart.buckets, { width: 640, height: 200 });
  const picture = svg("svg", {
    viewBox: "0 0 " + drawn.width + " " + drawn.height,
    class: "rp-ages",
    role: "img",
    "aria-label": "Open pull requests by age. Use the arrow keys to read each column.",
  });
  for (const tick of drawn.ticks) {
    picture.append(svg("line", { x1: drawn.left, x2: drawn.width, y1: tick.y, y2: tick.y, class: tick.value ? "grid" : "base" }));
    picture.append(label(drawn.left - 6, tick.y + 4, formatCount(tick.value), "end"));
  }
  const marks = drawn.columns.map((column) => {
    const mark = svg("g", { "data-mark": column.index, style: "--i: " + column.index });
    mark.append(svg("rect", { x: column.band.x, y: 0, width: column.band.width, height: drawn.height, class: "hit" }));
    const bars = svg("g", { class: "rp-column" });
    for (const part of column.parts) bars.append(svg("path", { d: part.path, class: part.kind }));
    mark.append(bars);
    if (column.total) mark.append(label(column.center, column.top - 6, formatCount(column.total), "middle"));
    mark.append(label(column.center, drawn.height - 6, column.label, "middle"));
    return mark;
  });
  picture.append(...marks);
  explore(
    picture,
    marks,
    { ArrowLeft: -1, ArrowRight: 1 },
    (index) => {
      const column = drawn.columns[index];
      return [plural(column.total, "pull request"), "Open for " + column.label + ": " + column.ready + " ready, " + column.draft + " draft"];
    },
    drawn.columns.length - 1,
  );

  const oldest = chart.oldest.length ? chart.oldest[0].age_days : 1;
  const list = h(
    "ol",
    { class: "rp-oldest" },
    ...chart.oldest.map((pull, index) =>
      h(
        "li",
        { style: { "--i": index } },
        h(
          "div",
          { class: "rp-pull-line" },
          h("span", { class: "rp-pull-number efp-number", text: "#" + pull.number }),
          outside(pull.url, pull.title, "rp-pull-title link link-hover"),
          pull.draft ? h("span", { class: "rp-draft", text: "Draft" }) : null,
        ),
        h(
          "div",
          { class: "rp-pull-meta" },
          pull.author ? h("span", { class: "rp-author" }, kit.avatar(pull.author, { size: "1.25rem" }), h("span", { text: pull.author })) : null,
          h("span", { class: "rp-age", style: { "--share": Math.max(0.02, pull.age_days / oldest), "--tone": `var(--color-${ageTone(pull.age_days)})` }, title: "Open for " + formatAge(pull.age_days) }, h("span", { class: "rp-age-fill" })),
          h("span", { class: "rp-age-text efp-number", text: formatAge(pull.age_days) }),
        ),
      ),
    ),
  );
  const legend = h("p", { class: "rp-legend" }, swatch("ready"), h("span", {}, "Ready for review"), swatch("draft"), h("span", {}, "Draft"));
  const capped = chart.capped ? h("p", { class: "rp-note", text: "GitHub listed the " + chart.open + " oldest open pull requests; there are more." }) : null;
  const table = numbers(["Open for", "Ready", "Draft"], chart.buckets.map((bucket) => [bucket.label, bucket.ready, bucket.draft]));
  const title = h("h3", { text: chart.oldest.length === 1 ? "The oldest" : "The " + chart.oldest.length + " oldest" });
  return [
    h(
      "div",
      { class: "rp-pulls" },
      h("div", { class: "rp-pulls-chart" }, h("div", { class: "rp-scroll" }, picture), legend, capped, table),
      h("div", { class: "rp-pulls-list" }, title, ...shortened(list, 5, "of them")),
    ),
  ];
}

// --- The page -------------------------------------------------------------------------------------

// A long list shows its first few items and a button for the others, so
// that two cards side by side keep a like height.
function shortened(list, few, noun) {
  const items = Array.from(list.children);
  if (items.length <= few + 1) return [list]; // one more is not worth a button
  const rest = items.slice(few);
  rest.forEach((item) => (item.hidden = true));
  const text = h("span", { text: "Show all " + items.length + " " + noun });
  const more = h("button", { type: "button", class: "btn btn-ghost btn-xs rp-more", "aria-expanded": "false" }, kit.icon("chevron-down", "size-3.5"), text);
  more.addEventListener("click", () => {
    const open = more.getAttribute("aria-expanded") !== "true";
    more.setAttribute("aria-expanded", String(open));
    text.textContent = open ? "Show fewer" : "Show all " + items.length + " " + noun;
    rest.forEach((item) => (item.hidden = !open));
    if (open) kit.rise(rest, { stagger: 30, distance: 8 });
  });
  return [list, more];
}

function quiet(icon, text) {
  return h("p", { class: "rp-quiet" }, kit.icon(icon, "size-4"), h("span", { text }));
}

// A chart that is not there says why, in the place where it would be.
function fill(id, chart, draw) {
  const holder = $(id);
  if (chart.state === "ok") {
    holder.replaceChildren(...draw(chart));
    return;
  }
  if (chart.state === "computing") {
    const again = h("button", { type: "button", class: "btn btn-sm btn-soft btn-primary efp-press" }, kit.icon("refresh-cw", "size-4"), "Ask again");
    again.addEventListener("click", () => load(false, again));
    holder.replaceChildren(
      h(
        "div",
        { class: "rp-waiting" },
        h("span", { class: "rp-waiting-icon" }, kit.icon("hourglass", "size-5")),
        h("div", {}, h("strong", { text: "GitHub is still computing this" }), h("p", { text: "It counts a repository's statistics in the background. Try again in a moment: the other charts are kept." })),
        again,
      ),
    );
    return;
  }
  holder.replaceChildren(h("div", { class: "alert alert-soft alert-warning text-sm" }, kit.icon("triangle-alert", "size-4"), h("span", { text: chart.message || "This chart could not be read." })));
}

function tile(name, icon, tone) {
  const value = h("dd", { class: "rp-value efp-number" }, "-");
  const note = h("dd", { class: "rp-tile-note" });
  const box = h("div", { class: "efp-surface efp-lift rp-tile", style: { "--rp-tone": `var(--color-${tone})` } }, h("dt", {}, h("span", { class: "rp-icon" }, kit.icon(icon, "size-4")), name), value, note);
  return { box, value, note };
}

// A tile counts from what it showed to what it shows now; a tile without a number shows a dash.
function count(target, value, format, text) {
  target.note.textContent = text || "";
  if (value === null || value === undefined) {
    target.value.textContent = "-";
    delete target.value.dataset.efpValue;
    return;
  }
  kit.countUp(target.value, value, { duration: 1100, format });
}

function showTiles(charts) {
  if (!tiles) {
    tiles = {
      commits: tile("Commits in a year", "git-commit-horizontal", "primary"),
      busiest: tile("Busiest week", "flame", "secondary"),
      open: tile("Open pull requests", "git-pull-request", "warning"),
      age: tile("Median age", "hourglass", "info"),
    };
    $("tiles").replaceChildren(...Object.values(tiles).map((made) => made.box));
    kit.rise($("tiles").children, { stagger: 60 });
  }
  const { commits, pulls } = charts;
  const busiest = commits.state === "ok"
    ? commits.weeks.reduce((best, week) => {
        const total = week.days.reduce((sum, day) => sum + day, 0);
        return total > best.total ? { total, week: week.week } : best;
      }, { total: 0, week: 0 })
    : null;
  const pullsOk = pulls.state === "ok";
  count(tiles.commits, commits.state === "ok" ? commits.total : null, formatCount, commits.state === "ok" ? "in the last 52 weeks" : "not counted yet");
  count(tiles.busiest, busiest && busiest.total ? busiest.total : null, formatCount, busiest && busiest.total ? "week of " + dayName(busiest.week) : "not counted yet");
  count(tiles.open, pullsOk ? pulls.open : null, (value) => formatCount(value) + (pulls.capped ? "+" : ""), pullsOk ? (pulls.drafts ? plural(pulls.drafts, "draft") + " among them" : "none of them a draft") : "");
  count(tiles.age, pullsOk && pulls.open ? pulls.median_age_days : null, formatAge, "of the open pull requests");
}

function show(found, refreshed) {
  const fresh = found.repository.toLowerCase() !== shown;
  shown = found.repository.toLowerCase();
  const { commits, pulls, contributors, punch_card: punch } = found.charts;
  $("start").hidden = true;
  $("loading").hidden = true;
  showProblem(null);
  const wasHidden = $("result").hidden;
  $("result").hidden = false;
  // The wave of the drawings runs for a repository that was not on the page.
  $("result").classList.toggle("is-entering", fresh);
  if (fresh) setTimeout(() => $("result").classList.remove("is-entering"), 2600);

  showTiles(found.charts);
  drawWeekly(commits);
  fill("commits", commits, drawCommits);
  fill("punch-card", punch, drawPunchCard);
  fill("contributors", contributors, drawContributors);
  fill("pulls", pulls, drawPulls);
  if (wasHidden) kit.rise(Array.from($("result").children).slice(1), { delay: 120, stagger: 80 });
  if (refreshButton) refreshButton.disabled = false;
  // Where the year is wider than the screen, it opens on its newest weeks.
  const year = document.querySelector(".rp-year");
  if (year) year.scrollLeft = year.scrollWidth;

  const times = Object.values(found.charts).filter((chart) => chart.state === "ok").map((chart) => chart.fetched_at);
  let text = found.repository;
  if (times.length) {
    const oldest = Math.min(...times);
    const minutes = Math.floor((found.now - oldest) / 60);
    const clock = new Date(oldest * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    text += ", last updated at " + clock + (minutes < 1 ? " (just now)" : " (" + minutes + (minutes === 1 ? " minute ago)" : " minutes ago)"));
  }
  const computing = Object.values(found.charts).some((chart) => chart.state === "computing");
  say(text + (computing ? ". GitHub is still computing some of the statistics." : ""));
  if (refreshed) {
    kit.toast(found.asked === 0 ? "GitHub was asked less than a minute ago: nothing was asked again." : "GitHub was asked again about " + plural(found.asked, "chart") + ".", { kind: found.asked ? "success" : "info", timeout: 3500 });
  }
}

async function load(refresh, button) {
  const wanted = repository.value.trim();
  if (!wanted) {
    kit.shake(repository.closest("label"));
    repository.focus();
    return;
  }
  const same = wanted.toLowerCase() === shown && !$("result").hidden;
  if (same) {
    $("result").classList.add("is-stale"); // the charts stay in place, dimmed, until the new ones are there
  } else {
    $("start").hidden = true;
    $("problem").hidden = true;
    $("result").hidden = true;
    $("loading").hidden = false;
  }
  say("Asking GitHub...");
  const work = async () => {
    let response;
    try {
      response = refresh
        ? await fetch(refreshUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repository: wanted }) })
        : await fetch(pulseUrl + "?" + new URLSearchParams({ repository: wanted }));
    } catch (error) {
      throw { code: "unreachable", message: "The app cannot be reached right now." };
    }
    if (!response.ok) throw await failure(response);
    show(await response.json(), refresh);
    // The repository goes into the address, so that the view can be shared as a link.
    history.replaceState(null, "", "?" + new URLSearchParams({ repository: wanted }));
  };
  try {
    await kit.busy(button || showButton, work);
  } catch (problem) {
    const message = problem.message || "Something went wrong.";
    say(message);
    $("loading").hidden = true;
    if (same && !NOT_SET_UP.includes(problem.code)) {
      kit.toast(message, { kind: "error" });
    } else {
      // The charts on the page belong to another repository, or to none.
      $("result").hidden = true;
      shown = "";
      if (refreshButton) refreshButton.disabled = true;
      showProblem(problem);
    }
  } finally {
    $("result").classList.remove("is-stale");
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  load(false);
});

if (refreshButton) {
  refreshButton.addEventListener("click", () => load(true, refreshButton));
}

await kit.ready();
kit.themeSwitch($("theme"));
const asked = new URLSearchParams(location.search).get("repository");
if (asked) {
  repository.value = asked.slice(0, 140);
  load(false);
} else {
  repository.focus();
}
