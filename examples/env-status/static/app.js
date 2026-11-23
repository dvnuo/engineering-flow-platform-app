// The page: it shows what the server knows and asks it to check. It never
// names an address to check; the server's list is a file (targets.json).
//
// Every URL in this file is relative ("api/status", not "/api/status"): the
// app is served under /{slug}/ once deployed. Nothing here asks the server on
// a timer: a page that polls keeps the app from ever being idle, and every
// poll would knock on every environment.
//
// The look comes from the EFP kit (/_efp/lib/, docs/UI_KIT.md). "Check now"
// is one request: the server asks every environment at the same time and
// answers with all of them. While it does, every row shows that it is being
// asked; then the rows settle in the order their environments answered
// (fastest first), each with its latency counted up and its line redrawn.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import { duration, grouped, headline, pointsOf, sparkline, tally } from "./spark.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const SPARK = { width: 120, height: 28 };
const SETTLE_STEP = 120; // ms between two rows settling after a check

const checkButton = $("check");
const groupsElement = $("groups");

let overview = null; // what the server said last
let checking = false;
const rows = new Map(); // target id -> the parts of its row, kept between drawings
const groupCards = new Map(); // group name -> {section, list, tally}

// The server and the gateway both answer errors as {"error": {"code", "message"}}.
async function ask(url, method) {
  let response;
  try {
    response = await fetch(url, { method: method || "GET" });
  } catch (error) {
    return { ok: false, message: "The app cannot be reached right now." };
  }
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    // not JSON: the status has to do
  }
  if (response.ok && data) {
    return { ok: true, data };
  }
  let message = data && data.error && data.error.message;
  if (!message) {
    message =
      response.status === 401
        ? "Your session has ended. Reload the page to sign in again."
        : "The app answered with an error (" + response.status + ").";
  }
  return { ok: false, message };
}

function shape(name, attributes) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) {
    node.setAttribute(key, String(value));
  }
  return node;
}

// A pause on the animation clock, to let the rows settle one after the
// other. With less motion, or in a hidden tab, there is nothing to wait for.
function pause(ms) {
  if (kit.reducedMotion() || document.hidden || ms <= 0) return Promise.resolve();
  return new Promise((resolve) => {
    const began = performance.now();
    const wait = (now) => (now - began >= ms ? resolve() : requestAnimationFrame(wait));
    requestAnimationFrame(wait);
  });
}

function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

function stateOf(target) {
  return target.last ? (target.last.up ? "up" : "down") : "unknown";
}

function longDate(at) {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

// --- The summary ---------------------------------------------------------------------------

const VERDICT_ICONS = { up: "circle-check", down: "circle-x", mixed: "triangle-alert", unknown: "radar", checking: "radar" };

function verdictOf(targets) {
  const count = tally(targets);
  const total = targets.length;
  if (!total) return { state: "unknown", text: "No environment is listed" };
  if (count.up + count.down === 0) return { state: "unknown", text: "Nothing has been checked yet" };
  if (count.down === 0 && count.unknown === 0) return { state: "up", text: total === 1 ? "The environment is up" : "All " + total + " environments are up" };
  if (count.up === 0 && count.unknown === 0) return { state: "down", text: total === 1 ? "The environment is down" : "Every environment is down" };
  if (count.down) return { state: "mixed", text: count.down + " of " + total + (count.down === 1 ? " environments is down" : " environments are down") };
  return { state: "mixed", text: plural(count.unknown, "environment") + " not checked yet" };
}

function showVerdict(state, text) {
  const icon = $("verdict-icon");
  const changed = icon.dataset.state !== state;
  icon.dataset.state = state;
  icon.replaceChildren(kit.icon(VERDICT_ICONS[state] || "radar"));
  $("headline").textContent = text;
  if (changed && state !== "checking") kit.pop(icon, { scale: 1.12 });
}

function showSummary(view) {
  const verdict = verdictOf(view.targets);
  showVerdict(verdict.state, verdict.text);
  const newest = view.targets.map((target) => target.last && target.last.at).filter(Boolean).sort().pop();
  const checkedAt = $("checked-at");
  checkedAt.textContent = newest
    ? headline(view.targets) + " Last checked " + kit.timeAgo(newest) + "."
    : view.targets.length
      ? view.signed_in
        ? 'Press "Check now" to ask every environment once.'
        : "A member presses \"Check now\" to ask every environment."
      : "";
  checkedAt.title = newest ? longDate(newest) : "";
  const count = tally(view.targets);
  kit.countUp($("count-up"), count.up);
  kit.countUp($("count-down"), count.down);
  kit.countUp($("count-unknown"), count.unknown);
  checkButton.disabled = !view.signed_in || view.targets.length === 0;
  checkButton.title = view.signed_in ? "Ask every environment once" : "Sign in to check";
  $("check-note").textContent = !view.signed_in
    ? "Checking is for members. You see the last results."
    : "A result younger than " + plural(view.max_age, "second") + " is shown again instead.";
}

// --- The rows --------------------------------------------------------------------------------

function tone(uptime) {
  return uptime === null ? "" : uptime >= 99 ? "text-success" : uptime >= 90 ? "text-warning" : "text-error";
}

// The latencies of the kept checks as a small line; a failed check is a red tick on the baseline.
function drawSpark(svg, target, animate) {
  const drawn = sparkline(target.latency, SPARK.width, SPARK.height);
  const checks = target.checks === 1 ? "the last check" : "the last " + target.checks + " checks";
  let label = "No check yet.";
  if (target.checks > 0 && drawn.lines.length === 0) {
    label = "No latency to draw: " + checks + " failed.";
  } else if (target.checks > 0) {
    const range = drawn.low === drawn.high ? duration(drawn.low) : duration(drawn.low) + " to " + duration(drawn.high);
    label = "Latency of " + checks + ": " + range + "." + (drawn.misses.length ? " " + drawn.misses.length + " failed." : "");
  }
  svg.setAttribute("aria-label", label);
  const title = shape("title", {});
  title.textContent = label;
  const bottom = SPARK.height - 0.5;
  const parts = [title, shape("line", { class: "base", x1: 0, x2: SPARK.width, y1: bottom, y2: bottom })];
  const lines = [];
  for (const piece of drawn.lines) {
    if (piece.length === 1) {
      parts.push(shape("circle", { cx: piece[0].x, cy: piece[0].y, r: 1.6 }));
      continue;
    }
    const first = piece[0];
    const last = piece[piece.length - 1];
    parts.push(shape("polygon", { class: "area", points: pointsOf(piece) + " " + last.x + "," + bottom + " " + first.x + "," + bottom }));
    const line = shape("polyline", { points: pointsOf(piece), pathLength: 1 });
    lines.push(line);
    parts.push(line);
  }
  for (const x of drawn.misses) {
    parts.push(shape("line", { class: "miss", x1: x, x2: x, y1: SPARK.height - 8, y2: bottom }));
  }
  // The newest check, when it has a latency: a dot at the end of the line.
  const lastPiece = drawn.lines[drawn.lines.length - 1];
  const newest = target.latency.length && typeof target.latency[target.latency.length - 1] === "number" ? lastPiece[lastPiece.length - 1] : null;
  const dot = newest ? shape("circle", { class: "latest", cx: newest.x, cy: newest.y, r: 3 }) : null;
  if (dot) parts.push(dot);
  svg.replaceChildren(...parts);
  if (animate && !kit.reducedMotion()) {
    lines.forEach((line) => line.animate([{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { duration: 900, easing: kit.EASE }));
    if (dot) kit.appear(dot, { delay: 600 });
  }
}

function makeRow(target) {
  const mark = h("span", { class: "es-mark", "aria-hidden": "true" });
  const url = h("span", { class: "es-url" });
  const latency = h("span", { class: "efp-number" });
  const status = h("small");
  const uptime = h("div", { class: "radial-progress es-uptime", role: "progressbar", "aria-label": "Uptime", style: { "--value": 0 } });
  const spark = shape("svg", { class: "es-spark", viewBox: "0 0 " + SPARK.width + " " + SPARK.height, role: "img" });
  const detail = h("div", { class: "es-detail" });
  const item = h(
    "li",
    { class: "es-row", dataset: { id: target.id } },
    h("div", { class: "es-name" }, mark, h("div", { class: "min-w-0" }, h("strong", { text: target.name }), url)),
    h("div", { class: "es-latency" }, latency, status),
    uptime,
    h("div", { class: "es-spark-cell" }, spark),
    detail,
  );
  return { item, mark, url, latency, status, uptime, spark, detail };
}

function fillRow(parts, target, animate) {
  const state = stateOf(target);
  const last = target.last;
  parts.item.classList.remove("is-checking");
  parts.mark.dataset.state = state;
  parts.mark.title = { up: "Up", down: "Down", unknown: "Not checked yet" }[state];
  parts.url.textContent = target.url || ""; // members see the address, guests do not get it at all
  parts.url.title = target.url || "";
  parts.url.hidden = !target.url;
  if (state === "up") {
    kit.countUp(parts.latency, last.ms, { from: animate ? 0 : undefined, format: (value) => duration(Math.round(value)), duration: 800 });
  } else {
    parts.latency.textContent = "-";
    delete parts.latency.dataset.efpValue;
  }
  parts.status.textContent = last ? (last.status ? "HTTP " + last.status : "no answer") : "";
  const percent = target.uptime === null ? 0 : target.uptime;
  parts.uptime.className = "radial-progress es-uptime " + (target.uptime === null ? "is-empty" : tone(target.uptime)) + (target.uptime === 0 ? " is-zero" : "");
  parts.uptime.style.setProperty("--value", percent);
  parts.uptime.setAttribute("aria-valuenow", String(percent));
  parts.uptime.textContent = target.uptime === null ? "-" : target.uptime + "%";
  parts.uptime.title = target.checks ? "Up in " + target.uptime + "% of " + plural(target.checks, "check") : "No check yet";
  drawSpark(parts.spark, target, animate);
  if (last) {
    const when = h("span", { title: longDate(last.at), text: "Checked " + kit.timeAgo(last.at) + (last.status ? ", status " + last.status + "." : ".") });
    parts.detail.replaceChildren(when, last.note ? h("span", { class: "es-note", text: last.note }) : "");
  } else {
    parts.detail.replaceChildren(h("span", { text: "No result yet." }));
  }
}

function showChecking(parts, index) {
  parts.item.classList.add("is-checking");
  parts.item.style.setProperty("--i", index);
  parts.mark.dataset.state = "checking";
  parts.mark.title = "Being asked";
  parts.latency.replaceChildren(h("span", { class: "skeleton h-4 w-12 inline-block align-middle" }));
  delete parts.latency.dataset.efpValue;
  parts.status.textContent = "asking";
}

// A row that has just settled: a short tint in its new colour, and a shake for one that went down.
function settled(parts, before, target) {
  const was = before ? stateOf(before) : "unknown";
  const now = stateOf(target);
  if (kit.reducedMotion()) return;
  kit.appear(parts.mark, { duration: 520 });
  const colour = now === "up" ? "success" : now === "down" ? "error" : "primary";
  parts.item.animate([{ backgroundColor: kit.color(colour, 0.16) }, { backgroundColor: kit.color(colour, 0) }], { duration: 1100, easing: kit.EASE });
  if (was === "up" && now === "down") kit.shake(parts.mark);
  if (was === "down" && now === "up") kit.pop(parts.mark, { scale: 1.6 });
}

function groupCard(name) {
  if (!groupCards.has(name)) {
    const list = h("ul", { class: "es-rows", "aria-label": name });
    const counts = h("span", { class: "flex flex-wrap gap-1.5" });
    const section = h(
      "section",
      { class: "efp-surface overflow-hidden" },
      h("div", { class: "es-group-head" }, h("h2", { class: "font-semibold", text: name }), counts),
      h("div", { class: "es-columns", "aria-hidden": "true" }, h("span", {}, "Environment"), h("span", { class: "text-right" }, "Latency"), h("span", { class: "text-center" }, "Uptime"), h("span", {}, "Trend"), h("span", {}, "Last check")),
      list,
    );
    groupCards.set(name, { section, list, counts });
  }
  return groupCards.get(name);
}

function showGroupCounts(card, targets) {
  const count = tally(targets);
  const badge = (value, word, kind) => (value ? h("span", { class: "badge badge-sm badge-soft badge-" + kind, text: value + " " + word }) : null);
  card.counts.replaceChildren(...[badge(count.up, "up", "success"), badge(count.down, "down", "error"), badge(count.unknown, "not checked", "neutral")].filter(Boolean));
}

// Draw everything from `view`. Rows are kept by id: a second drawing changes them in place.
function render(view, animate) {
  const first = groupCards.size === 0;
  const groups = grouped(view.targets);
  const sections = groups.map((group) => {
    const card = groupCard(group.name);
    const items = group.targets.map((target) => {
      if (!rows.has(target.id)) rows.set(target.id, makeRow(target));
      const parts = rows.get(target.id);
      fillRow(parts, target, animate);
      return parts.item;
    });
    card.list.replaceChildren(...items);
    showGroupCounts(card, group.targets);
    return card.section;
  });
  groupsElement.replaceChildren(...sections);
  $("empty").hidden = view.targets.length > 0;
  showSummary(view);
  if (first && sections.length) kit.rise(sections, { stagger: 90 });
}

// --- Loading and checking ---------------------------------------------------------------------

function showWho(view) {
  $("who").textContent = view.signed_in
    ? "Signed in as " + view.name + '. "Check now" asks every environment once.'
    : "You are not signed in: you see the last results. Checking is for members.";
  if (view.signed_in && !$("me").firstChild) $("me").append(kit.avatar(view.name, { size: "2rem" }));
}

function showProblem(message) {
  $("problem").hidden = !message;
  $("summary").hidden = Boolean(message);
  groupsElement.hidden = Boolean(message);
  $("problem").replaceChildren(
    message
      ? h("div", { class: "efp-surface efp-empty" }, h("span", { class: "efp-empty-icon" }, kit.icon("cloud-alert")), h("strong", {}, "The results could not be loaded"), h("span", { class: "text-sm max-w-md", text: message }))
      : "",
  );
}

async function load() {
  const answer = await ask("api/status");
  if (!answer.ok) {
    $("who").textContent = "";
    showProblem(answer.message);
    return;
  }
  showProblem("");
  overview = answer.data;
  showWho(overview);
  render(overview, true);
}

async function check() {
  if (checking || checkButton.disabled || !overview) return;
  checking = true;
  const before = new Map(overview.targets.map((target) => [target.id, target]));
  showVerdict("checking", "Asking " + plural(overview.targets.length, "environment") + "...");
  Array.from(rows.values()).forEach((parts, index) => showChecking(parts, index));
  try {
    // The request carries nothing: what is checked is the server's list, not the page's wish.
    const fresh = await kit.busy(
      checkButton,
      async () => {
        const answer = await ask("api/check", "POST");
        if (!answer.ok) throw answer;
        return answer.data;
      },
      { tick: false },
    );
    if (!fresh) return;
    overview = fresh;
    showWho(fresh);
    // The rows settle in the order their environments answered: the fastest first.
    const order = fresh.targets.slice().sort((one, other) => (one.last ? one.last.ms : Infinity) - (other.last ? other.last.ms : Infinity));
    for (const target of order) {
      const parts = rows.get(target.id);
      if (!parts) continue;
      fillRow(parts, target, fresh.checked > 0);
      settled(parts, before.get(target.id), target);
      await pause(fresh.checked ? SETTLE_STEP : 0);
    }
    // The rows are drawn; what sums them up follows (drawing the rows again would cut their motion short).
    for (const group of grouped(fresh.targets)) showGroupCounts(groupCard(group.name), group.targets);
    showSummary(fresh);
    const wasDown = Array.from(before.values()).some((target) => stateOf(target) === "down");
    const allUp = fresh.targets.every((target) => stateOf(target) === "up");
    if (fresh.checked === 0) {
      kit.toast("These results are less than " + plural(fresh.max_age, "second") + " old, so no environment was asked again.", { kind: "info" });
    } else if (wasDown && allUp) {
      kit.toast("Everything is up again.", { kind: "success" });
    } else {
      kit.toast("Asked " + plural(fresh.checked, "environment") + " just now.", { kind: allUp ? "success" : "info", timeout: 3200 });
    }
  } catch (problem) {
    render(overview, false); // the rows go back to what was known before
    kit.toast(problem.message || "The check could not be made.", { kind: "error" });
  } finally {
    checking = false;
  }
}

checkButton.addEventListener("click", check);

// "c" checks, wherever the focus is not in a control of its own.
document.addEventListener("keydown", (event) => {
  if (event.key.toLowerCase() !== "c" || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target.closest && event.target.closest("input, textarea, select, button, a")) return;
  event.preventDefault();
  check();
});

await kit.ready();
kit.themeSwitch($("theme"));
load();
