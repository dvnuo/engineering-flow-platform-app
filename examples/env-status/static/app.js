// The page: it shows what the server knows and asks it to check. It never
// names an address to check; the server's list is a file (targets.json).
//
// Every URL in this file is relative ("api/status", not "/api/status"): the
// app is served under /{slug}/ once deployed. Nothing here asks the server on
// a timer: a page that polls keeps the app from ever being idle, and every
// poll would knock on every environment.

import { duration, grouped, headline, pointsOf, sparkline } from "./spark.js";

const SVG = "http://www.w3.org/2000/svg";
const SPARK = { width: 120, height: 28 };

const who = document.getElementById("who");
const headlineText = document.getElementById("headline");
const checkButton = document.getElementById("check");
const status = document.getElementById("status");
const groups = document.getElementById("groups");
const empty = document.getElementById("empty");

let signedIn = false;

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

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) {
    node.textContent = text; // names, addresses and notes are text: never markup
  }
  if (className) {
    node.className = className;
  }
  return node;
}

function shape(name, attributes) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) {
    node.setAttribute(key, String(value));
  }
  return node;
}

function when(at) {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

// The latencies of the kept checks as a small line; a failed check is a red tick on the baseline.
function sparkOf(target) {
  const drawn = sparkline(target.latency, SPARK.width, SPARK.height);
  const svg = shape("svg", { class: "spark", viewBox: "0 0 " + SPARK.width + " " + SPARK.height, role: "img" });
  let label = "No check yet.";
  const checks = target.checks === 1 ? "the last check" : "the last " + target.checks + " checks";
  if (target.checks > 0 && drawn.lines.length === 0) {
    label = "No latency to draw: " + checks + " failed.";
  } else if (target.checks > 0) {
    const range = drawn.low === drawn.high ? duration(drawn.low) : duration(drawn.low) + " to " + duration(drawn.high);
    label = "Latency of " + checks + ": " + range + "." + (drawn.misses.length ? " " + drawn.misses.length + " failed." : "");
  }
  svg.setAttribute("aria-label", label);
  const title = shape("title", {});
  title.textContent = label;
  svg.append(title, shape("line", { class: "base", x1: 0, x2: SPARK.width, y1: SPARK.height - 0.5, y2: SPARK.height - 0.5 }));
  for (const piece of drawn.lines) {
    if (piece.length === 1) {
      svg.append(shape("circle", { cx: piece[0].x, cy: piece[0].y, r: 1.6 }));
    } else {
      svg.append(shape("polyline", { points: pointsOf(piece) }));
    }
  }
  for (const x of drawn.misses) {
    svg.append(shape("line", { class: "miss", x1: x, x2: x, y1: SPARK.height - 8, y2: SPARK.height - 0.5 }));
  }
  return svg;
}

function row(target) {
  const last = target.last;
  const kind = last ? (last.up ? "up" : "down") : "unknown";
  const item = element("li", undefined, "target " + kind);

  const state = element("span", undefined, "state");
  state.append(element("span", undefined, "mark"), { up: "Up", down: "Down", unknown: "Not checked" }[kind]);

  const name = element("span", target.name, "name");
  if (target.url) {
    name.append(element("small", target.url));
  }

  const latency = element("span", last && last.up ? duration(last.ms) : "-", "number");

  const uptime = element("span", target.uptime === null ? "-" : target.uptime + "%", "number uptime");
  if (target.checks > 0) {
    uptime.append(element("small", target.checks === 1 ? "of 1 check" : "of " + target.checks + " checks"));
  }

  const detail = element("span", undefined, "detail");
  if (last) {
    detail.append("Checked " + when(last.at) + (last.status ? ", status " + last.status + "." : "."));
    if (last.note) {
      detail.append(" ", element("span", last.note, "note"));
    }
  } else {
    detail.textContent = "No result yet.";
  }

  item.append(state, name, latency, uptime, sparkOf(target), detail);
  return item;
}

function show(overview) {
  signedIn = overview.signed_in;
  who.textContent = signedIn
    ? "Signed in as " + overview.name + ". \"Check now\" asks every environment once; a result younger than " +
      overview.max_age + " seconds is shown again instead."
    : "You are not signed in: you see the last results. Checking is for members.";
  checkButton.disabled = !signedIn || overview.targets.length === 0;
  headlineText.textContent = headline(overview.targets);
  empty.hidden = overview.targets.length > 0;
  groups.replaceChildren();
  for (const group of grouped(overview.targets)) {
    const list = element("ul", undefined, "targets");
    list.append(...group.targets.map(row));
    groups.append(element("h2", group.name), list);
  }
}

async function load() {
  const answer = await ask("api/status");
  if (!answer.ok) {
    who.textContent = "";
    status.textContent = answer.message;
    return;
  }
  show(answer.data);
}

checkButton.addEventListener("click", async () => {
  checkButton.disabled = true;
  status.textContent = "Checking...";
  // The request carries nothing: what is checked is the server's list, not the page's wish.
  const answer = await ask("api/check", "POST");
  if (!answer.ok) {
    status.textContent = answer.message;
    checkButton.disabled = !signedIn;
    return;
  }
  show(answer.data);
  if (answer.data.checked === 0) {
    status.textContent =
      "These results are less than " + answer.data.max_age + " seconds old, so no environment was asked again.";
  } else {
    status.textContent =
      "Asked " + answer.data.checked + (answer.data.checked === 1 ? " environment" : " environments") + " just now.";
  }
});

load();
