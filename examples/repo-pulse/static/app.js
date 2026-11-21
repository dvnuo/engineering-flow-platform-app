// The page gets its URLs from the template (url_for), which adds the /{slug}
// prefix once the app is deployed. Do not write "/pulse" or "/refresh" here.
//
// The charts are loaded when the visitor asks for them. There is no timer:
// when GitHub is still computing, the page says so and offers a button.

import {
  DAYS,
  barRows,
  calendar,
  dayName,
  formatAge,
  formatCount,
  histogram,
  hourName,
  punchCard,
} from "./charts.mjs";

const SVG = "http://www.w3.org/2000/svg";
const main = document.querySelector("main");
const pulseUrl = main.dataset.pulseUrl;
const refreshUrl = main.dataset.refreshUrl;

const form = document.getElementById("choose");
const repository = document.getElementById("repository");
const showButton = document.getElementById("show");
const refreshButton = document.getElementById("refresh"); // absent for a visitor who is not signed in
const status = document.getElementById("status");
const result = document.getElementById("result");
const tiles = document.getElementById("tiles");
const spoken = document.getElementById("spoken");
const tooltip = document.getElementById("tooltip");
let shown = ""; // the repository on the page

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function errorMessage(response) {
  try {
    const body = await response.json();
    if (body.error && body.error.message) {
      return body.error.message;
    }
  } catch (error) {
    // not JSON
  }
  return response.status === 401
    ? "Your session has ended. Reload the page to sign in again."
    : "The app answered with an error (" + response.status + ").";
}

function say(text, isProblem) {
  status.textContent = text;
  status.classList.toggle("problem", Boolean(isProblem));
}

function plural(count, word) {
  return formatCount(count) + " " + word + (count === 1 ? "" : "s");
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

function label(x, y, text, anchor) {
  const made = svg("text", { x: x, y: y, class: "axis", "text-anchor": anchor || "start" });
  made.textContent = text;
  return made;
}

// A link to GitHub. The address was checked by the server; the text goes in as text.
function outside(url, text) {
  if (!url) {
    return element("span", "", text);
  }
  const link = element("a", "", text);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

// The same values as a table, for a reader who cannot use the picture.
function numbers(headers, rows) {
  const details = element("details", "numbers");
  details.append(element("summary", "", "The numbers"));
  const table = document.createElement("table");
  const head = document.createElement("tr");
  for (const header of headers) {
    const cell = element("th", "", header);
    cell.scope = "col";
    head.append(cell);
  }
  table.createTHead().append(head);
  const body = table.createTBody();
  for (const row of rows) {
    const line = document.createElement("tr");
    row.forEach((value, index) => {
      const cell = element(index === 0 ? "th" : "td");
      if (index === 0) {
        cell.scope = "row";
      }
      cell.append(value instanceof Node ? value : String(value));
      line.append(cell);
    });
    body.append(line);
  }
  const scroll = element("div", "scroll");
  scroll.append(table);
  details.append(scroll);
  return details;
}

// --- Pointing at a mark: the pointer and the arrow keys do the same -------------

function showTip(mark, lines) {
  tooltip.replaceChildren(...lines.map((line, index) => element(index === 0 ? "strong" : "span", "", line)));
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
    if (at >= 0) {
      marks[at].classList.remove("pointed");
    }
    tooltip.hidden = true;
  };
  const point = (index, aloud) => {
    leave();
    at = index;
    marks[at].classList.add("pointed");
    const lines = describe(at);
    showTip(marks[at], lines);
    if (aloud) {
      spoken.textContent = lines.join(", ");
    }
  };
  picture.setAttribute("tabindex", 0);
  picture.addEventListener("pointermove", (event) => {
    const mark = event.target.closest("[data-mark]");
    if (!mark) {
      leave();
    } else if (Number(mark.dataset.mark) !== at || tooltip.hidden) {
      point(Number(mark.dataset.mark), false);
    }
  });
  picture.addEventListener("pointerleave", leave);
  picture.addEventListener("focus", () => point(at >= 0 ? at : start, true));
  picture.addEventListener("blur", leave);
  picture.addEventListener("keydown", (event) => {
    if (!(event.key in moves)) {
      return;
    }
    event.preventDefault();
    const next = at + moves[event.key];
    if (next >= 0 && next < marks.length) {
      point(next, true);
    }
  });
}

window.addEventListener("scroll", () => {
  tooltip.hidden = true;
}, { passive: true });

// --- The four charts -----------------------------------------------------------------

function drawCommits(chart) {
  if (chart.total === 0) {
    return [element("p", "quiet", "No commits in the last year.")];
  }
  const drawn = calendar(chart.weeks, { size: 15, gap: 3 });
  const picture = svg("svg", {
    viewBox: "0 0 " + drawn.width + " " + drawn.height,
    class: "calendar",
    role: "img",
    "aria-label": "Commits per day over the last year. Use the arrow keys to read each day.",
  });
  for (const month of drawn.months) {
    picture.append(label(month.x, 11, month.label));
  }
  for (const day of drawn.days) {
    picture.append(label(0, day.y, day.label));
  }
  const marks = drawn.cells.map((cell) =>
    svg("rect", { x: cell.x, y: cell.y, width: cell.size, height: cell.size, rx: 2, class: "level-" + cell.level, "data-mark": cell.index })
  );
  picture.append(...marks);
  explore(
    picture,
    marks,
    { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1 },
    (index) => [plural(drawn.cells[index].count, "commit"), dayName(drawn.cells[index].day)],
    marks.length - 1
  );

  const scale = element("p", "legend");
  scale.append(element("span", "", "Fewer"));
  for (let level = 0; level <= 4; level += 1) {
    const swatch = svg("svg", { viewBox: "0 0 11 11", width: 11, height: 11, "aria-hidden": "true" });
    swatch.append(svg("rect", { width: 11, height: 11, rx: 2, class: "level-" + level }));
    scale.append(swatch);
  }
  scale.append(element("span", "", "More: up to " + plural(drawn.max, "commit") + " a day. Days are counted in UTC."));

  const scroll = element("div", "scroll");
  scroll.append(picture);
  return [
    scroll,
    scale,
    numbers(
      ["Week of", "Commits"],
      chart.weeks.map((week) => [dayName(week.week), formatCount(week.days.reduce((sum, day) => sum + day, 0))])
    ),
  ];
}

function drawPunchCard(chart) {
  if (chart.total === 0) {
    return [element("p", "quiet", "No commits to place on the clock yet.")];
  }
  const drawn = punchCard(chart.hours, { step: 24 });
  const picture = svg("svg", {
    viewBox: "0 0 " + drawn.width + " " + drawn.height,
    class: "punch",
    role: "img",
    "aria-label": "Commits by weekday and hour. Use the arrow keys to read each hour.",
  });
  for (const day of drawn.days) {
    picture.append(label(0, day.y, day.label));
  }
  for (const hour of drawn.hours) {
    picture.append(label(hour.x, drawn.height - 4, hour.label, "middle"));
  }
  const marks = drawn.dots.map((dot) => {
    const mark = svg("g", { "data-mark": dot.index });
    // The whole square of an hour answers the pointer, not only the dot in it.
    mark.append(svg("rect", { x: dot.x, y: dot.y, width: drawn.step, height: drawn.step, class: "hit" }));
    mark.append(svg("circle", { cx: dot.cx, cy: dot.cy, r: dot.r || 1, class: dot.r ? "dot" : "no-dot" }));
    return mark;
  });
  picture.append(...marks);
  explore(
    picture,
    marks,
    { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -24, ArrowDown: 24 },
    (index) => [plural(drawn.dots[index].commits, "commit"), DAYS[drawn.dots[index].day] + ", " + hourName(drawn.dots[index].hour)],
    drawn.dots.reduce((best, dot) => (dot.commits > drawn.dots[best].commits ? dot.index : best), 0)
  );
  const scroll = element("div", "scroll");
  scroll.append(picture);
  return [
    scroll,
    element("p", "quiet", "The area of a dot is the number of commits. Hours are those of each commit's own time zone."),
    numbers(
      ["Day"].concat(chart.hours[0].map((_count, hour) => String(hour).padStart(2, "0"))),
      chart.hours.map((day, row) => [DAYS[row]].concat(day.map((count) => formatCount(count))))
    ),
  ];
}

function drawContributors(chart) {
  if (chart.people.length === 0) {
    return [element("p", "quiet", "Nobody has contributed yet.")];
  }
  const drawn = barRows(chart.people.map((person) => ({ label: person.login, value: person.commits })), { width: 400, label: 140 });
  const picture = svg("svg", {
    viewBox: "0 0 " + drawn.width + " " + drawn.height,
    class: "bars",
    role: "img",
    "aria-label": "Commits of the top contributors. Use the arrow keys to read each one.",
  });
  const marks = drawn.rows.map((row) => {
    const mark = svg("g", { "data-mark": row.index });
    mark.append(svg("rect", { x: 0, y: row.y - 5, width: drawn.width, height: row.thick + 10, class: "hit" }));
    mark.append(label(row.x - 8, row.y + 12, row.label, "end"));
    mark.append(svg("path", { d: row.path, class: "bar" }));
    mark.append(label(row.x + row.length + 6, row.y + 12, formatCount(row.value)));
    return mark;
  });
  picture.append(...marks);
  explore(
    picture,
    marks,
    { ArrowUp: -1, ArrowDown: 1 },
    (index) => [plural(chart.people[index].commits, "commit"), chart.people[index].login],
    0
  );
  return [
    picture,
    numbers(
      ["Contributor", "Commits"],
      chart.people.map((person) => [outside(person.url, person.login), formatCount(person.commits)])
    ),
  ];
}

function drawPulls(chart) {
  if (chart.open === 0) {
    return [element("p", "quiet", "No pull request is open.")];
  }
  const drawn = histogram(chart.buckets, { width: 640, height: 220 });
  const picture = svg("svg", {
    viewBox: "0 0 " + drawn.width + " " + drawn.height,
    class: "columns",
    role: "img",
    "aria-label": "Open pull requests by age. Use the arrow keys to read each column.",
  });
  for (const tick of drawn.ticks) {
    picture.append(svg("line", { x1: drawn.left, x2: drawn.width, y1: tick.y, y2: tick.y, class: tick.value ? "grid" : "base" }));
    picture.append(label(drawn.left - 6, tick.y + 4, formatCount(tick.value), "end"));
  }
  const marks = drawn.columns.map((column) => {
    const mark = svg("g", { "data-mark": column.index });
    mark.append(svg("rect", { x: column.band.x, y: 0, width: column.band.width, height: drawn.height, class: "hit" }));
    for (const part of column.parts) {
      mark.append(svg("path", { d: part.path, class: part.kind }));
    }
    if (column.total) {
      mark.append(label(column.center, column.top - 5, formatCount(column.total), "middle"));
    }
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
    drawn.columns.length - 1
  );

  const key = element("p", "legend");
  for (const [kind, text] of [["ready", "Ready for review"], ["draft", "Draft"]]) {
    const swatch = svg("svg", { viewBox: "0 0 11 11", width: 11, height: 11, "aria-hidden": "true" });
    swatch.append(svg("rect", { width: 11, height: 11, rx: 2, class: kind }));
    key.append(swatch, element("span", "", text));
  }

  const oldest = element("ol", "oldest");
  for (const pull of chart.oldest) {
    const item = element("li");
    const title = element("span", "pull-title");
    title.append(outside(pull.url, "#" + pull.number + " " + pull.title));
    if (pull.draft) {
      title.append(element("span", "tag", "Draft"));
    }
    item.append(title, element("span", "quiet", "open for " + formatAge(pull.age_days) + (pull.author ? ", by " + pull.author : "")));
    oldest.append(item);
  }
  const scroll = element("div", "scroll");
  scroll.append(picture);
  const parts = [scroll, key];
  if (chart.capped) {
    parts.push(element("p", "quiet", "GitHub listed the " + chart.open + " oldest open pull requests; there are more."));
  }
  parts.push(
    element("h3", "", chart.oldest.length === 1 ? "The oldest" : "The " + chart.oldest.length + " oldest"),
    oldest,
    numbers(["Open for", "Ready", "Draft"], chart.buckets.map((bucket) => [bucket.label, bucket.ready, bucket.draft]))
  );
  return parts;
}

// --- The page ---------------------------------------------------------------------------

function tile(name, value, note) {
  const box = element("div", "tile");
  box.append(element("dt", "", name), element("dd", "", value));
  if (note) {
    box.append(element("dd", "note", note));
  }
  return box;
}

// A chart that is not there says why, in the place where it would be.
function fill(id, chart, draw) {
  const holder = document.getElementById(id);
  if (chart.state === "ok") {
    holder.replaceChildren(...draw(chart));
    return;
  }
  const note = element("p", "waiting");
  if (chart.state === "computing") {
    note.textContent = "GitHub is still computing this, try again in a moment. ";
    const again = element("button", "", "Ask again");
    again.type = "button";
    again.addEventListener("click", () => load(false));
    note.append(again);
  } else {
    note.textContent = chart.message || "This chart could not be read.";
    note.classList.add("problem");
  }
  holder.replaceChildren(note);
}

function show(found, refreshed) {
  shown = found.repository;
  const { commits, pulls, contributors, punch_card: punch } = found.charts;
  const busiest = commits.state === "ok"
    ? commits.weeks.reduce((best, week) => {
        const total = week.days.reduce((sum, day) => sum + day, 0);
        return total > best.total ? { total: total, week: week.week } : best;
      }, { total: 0, week: 0 })
    : null;
  tiles.replaceChildren(
    tile("Commits in a year", commits.state === "ok" ? formatCount(commits.total) : "-"),
    tile("Busiest week", busiest && busiest.total ? formatCount(busiest.total) : "-", busiest && busiest.total ? "week of " + dayName(busiest.week) : ""),
    tile(
      "Open pull requests",
      pulls.state === "ok" ? formatCount(pulls.open) + (pulls.capped ? "+" : "") : "-",
      pulls.state === "ok" && pulls.drafts ? plural(pulls.drafts, "draft") : ""
    ),
    tile("Median age", pulls.state === "ok" && pulls.open ? formatAge(pulls.median_age_days) : "-", "of the open pull requests")
  );
  fill("commits", commits, drawCommits);
  fill("punch-card", punch, drawPunchCard);
  fill("contributors", contributors, drawContributors);
  fill("pulls", pulls, drawPulls);
  result.hidden = false;
  if (refreshButton) {
    refreshButton.disabled = false;
  }
  // Where the year is wider than the screen, it opens on its newest weeks.
  const year = document.querySelector("#commits .scroll");
  if (year) {
    year.scrollLeft = year.scrollWidth;
  }

  const times = Object.values(found.charts).filter((chart) => chart.state === "ok").map((chart) => chart.fetched_at);
  let text = found.repository + ". ";
  if (times.length) {
    const oldest = Math.min(...times);
    const minutes = Math.floor((found.now - oldest) / 60);
    const clock = new Date(oldest * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    text += "Last updated at " + clock + (minutes < 1 ? " (just now)." : " (" + minutes + (minutes === 1 ? " minute ago)." : " minutes ago)."));
  }
  if (refreshed && found.asked === 0) {
    text += " GitHub was asked less than a minute ago: nothing was asked again.";
  }
  if (Object.values(found.charts).some((chart) => chart.state === "computing")) {
    text += " GitHub is still computing some of the statistics.";
  }
  say(text);
}

async function load(refresh) {
  const wanted = repository.value.trim();
  showButton.disabled = true;
  if (refreshButton) {
    refreshButton.disabled = true;
  }
  result.classList.add("stale"); // the old charts stay in place, dimmed, until the new ones are there
  say("Asking GitHub...");
  try {
    const response = refresh
      ? await fetch(refreshUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ repository: wanted }),
        })
      : await fetch(pulseUrl + "?" + new URLSearchParams({ repository: wanted }));
    if (!response.ok) {
      if (wanted !== shown) {
        result.hidden = true; // the charts on the page belong to another repository
      }
      say(await errorMessage(response), true);
      return;
    }
    show(await response.json(), refresh);
    // The repository goes into the address, so that the view can be shared as a link.
    history.replaceState(null, "", "?" + new URLSearchParams({ repository: wanted }));
  } catch (error) {
    say("The app cannot be reached right now.", true);
  } finally {
    showButton.disabled = false;
    if (refreshButton && !result.hidden) {
      refreshButton.disabled = false;
    }
    result.classList.remove("stale");
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  load(false);
});

if (refreshButton) {
  refreshButton.addEventListener("click", () => {
    if (form.reportValidity()) {
      load(true);
    }
  });
}

const asked = new URLSearchParams(location.search).get("repository");
if (asked) {
  repository.value = asked.slice(0, 140);
  load(false);
}
