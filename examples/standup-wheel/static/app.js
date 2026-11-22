// The page: it draws the wheel, turns it and asks the server. It never
// chooses: "Spin" asks the server who is next and the wheel is turned to that
// name. The geometry is wheel.js.
//
// Every URL in this file is relative ("api/spin", not "/api/spin"): the app
// is served under /{slug}/ once deployed. Nothing here asks the server on a
// timer; somebody who watches presses "Refresh", and the wheel then turns to
// whatever was spun on another screen in the meantime.
//
// The look comes from the EFP kit (/_efp/lib/, docs/UI_KIT.md). Every motion
// tells something: the last speaker leaves the wheel, the wheel is thrown and
// runs out against the pointer, the chosen slice steps forward. A visitor who
// asked the system for less motion sees each result at once.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import { angleAt, fitLabel, labelSize, landingAngle, sliceAtPointer, sliceColour, wedgePath } from "./wheel.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const SPIN_MS = 3800;
const TURNS = 5; // whole turns before the wheel comes to rest
const WIND_MS = 260; // the hand pulls the wheel back a little before the throw
const WIND_DEG = 14;
const MORPH_MS = 560; // a slice leaving or joining the wheel
const BULBS = 24;

const lessMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const disc = $("disc");
const pointer = $("pointer");
const spinButton = $("spin");
const newRoundButton = $("new-round");
const refreshButton = $("refresh");
const teamRows = $("team-rows");
const confirmDialog = $("confirm");

let state = null; // what the server said last
let rotation = 0; // how far the wheel has turned, in degrees
let drawn = []; // the usernames on the wheel as it is drawn now, in order
let onWheel = []; // the same people, as objects
let spinning = false;
let formFor = null; // the list version and round the owner's form was filled from
let flap = 0; // how far the pegs have pushed the pointer aside, in degrees

const slices = new Map(); // username -> {group, path, text}: kept between drawings
const sliceLayer = shape("g", {});
const pegLayer = shape("g", {});
disc.append(sliceLayer, pegLayer);

// --- The server ------------------------------------------------------------------------

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function ask(url, method, body) {
  const request = { method: method || "GET" };
  if (body !== undefined) {
    request.headers = { "Content-Type": "application/json" };
    request.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, request);
  } catch (error) {
    return { ok: false, status: 0, message: "The app cannot be reached right now." };
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
  return { ok: false, status: response.status, message };
}

// For kit.busy(): a refusal is thrown, so that the button shakes.
function orThrow(answer) {
  if (!answer.ok) throw answer;
  return answer.data;
}

// --- Small helpers -----------------------------------------------------------------------

function shape(name, attributes, text) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) {
    node.setAttribute(key, String(value));
  }
  if (text !== undefined) {
    node.textContent = text; // names are text from elsewhere: never markup
  }
  return node;
}

function listOf(names) {
  return names.length < 2 ? names.join("") : names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}

function day(at) {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

// A message above the page: a problem (red) or a plain note. Empty hides it.
function say(text, isProblem, target = $("status")) {
  target.hidden = !text;
  target.replaceChildren(
    text
      ? h("div", { class: "alert alert-soft " + (isProblem ? "alert-error" : "alert-info") }, kit.icon(isProblem ? "circle-alert" : "info", "size-5"), h("span", { text }))
      : "",
  );
}

// A pause on the animation clock, to put one motion after another. It never
// asks the server; with less motion there is nothing to wait for.
function pause(ms) {
  if (lessMotion.matches) return Promise.resolve();
  return new Promise((resolve) => {
    const began = performance.now();
    const wait = (now) => (now - began >= ms ? resolve() : requestAnimationFrame(wait));
    requestAnimationFrame(wait);
  });
}

function people(usernames) {
  const byUsername = new Map(state.team.members.map((member) => [member.username, member]));
  return usernames.map((username) => byUsername.get(username) || { username, name: username });
}

// --- The wheel ---------------------------------------------------------------------------

function turnTo(degrees) {
  rotation = degrees;
  disc.setAttribute("transform", "rotate(" + degrees.toFixed(2) + ")");
}

function showPointer() {
  pointer.setAttribute("transform", "rotate(" + (-flap).toFixed(2) + " 0 -121)");
}

// The lights round the rim, made once. lit(i) says which ones shine.
const bulbs = Array.from({ length: BULBS }, (_, index) => {
  const angle = (index * 2 * Math.PI) / BULBS;
  const bulb = shape("circle", { class: "sw-bulb", r: 2.3, cx: (105.5 * Math.sin(angle)).toFixed(2), cy: (-105.5 * Math.cos(angle)).toFixed(2) });
  $("bulbs").append(bulb);
  return bulb;
});

function light(lit) {
  bulbs.forEach((bulb, index) => bulb.classList.toggle("is-lit", lit(index)));
}

function sliceOf(person) {
  if (!slices.has(person.username)) {
    const path = shape("path", {});
    const text = shape("text", { x: 92, y: 0, "text-anchor": "end", "dominant-baseline": "central" });
    const group = shape("g", { class: "slice" });
    group.append(path, text);
    sliceLayer.append(group);
    slices.set(person.username, { group, path, text });
  }
  return slices.get(person.username);
}

// Lay the slices round the wheel by weight: 1 is a whole share, 0 is none.
// Equal weights are what wheel.js knows; the in-between ones are a slice on
// its way off or onto the wheel.
function layout(entries, count) {
  const total = entries.reduce((sum, entry) => sum + entry.weight, 0) || 1;
  const pegs = [];
  let at = 0;
  for (const { slice, weight } of entries) {
    const size = (360 * weight) / total;
    slice.path.setAttribute("d", size >= 359.99 ? wedgePath(100, 0, 360) : wedgePath(100, at, at + Math.max(size, 0.01)));
    slice.text.setAttribute("transform", "rotate(" + (at + size / 2 - 90).toFixed(2) + ")");
    slice.text.setAttribute("font-size", labelSize(count));
    if (entries.length > 1 && weight > 0.05) pegs.push(at);
    at += size;
  }
  pegLayer.replaceChildren(
    ...pegs.map((angle) => {
      const radians = (angle * Math.PI) / 180;
      return shape("circle", { class: "peg", r: 2.4, cx: (96 * Math.sin(radians)).toFixed(2), cy: (-96 * Math.cos(radians)).toFixed(2) });
    }),
  );
}

// One slice per person. `chosen` is the username the wheel has just stopped
// at; `over` draws the wheel of a round in which everybody has spoken.
function drawWheel(list, chosen = null, over = false) {
  const names = list.map((person) => person.username);
  for (const [name, slice] of slices) {
    if (!names.includes(name)) {
      slice.group.remove();
      slices.delete(name);
    }
  }
  list.forEach((person, index) => {
    const slice = sliceOf(person);
    slice.group.getAnimations().forEach((animation) => animation.cancel());
    slice.group.setAttribute("class", "slice c" + sliceColour(index, list.length) + (person.username === chosen ? " chosen" : ""));
    slice.text.textContent = fitLabel(person.name, list.length);
    slice.text.style.opacity = "";
  });
  drawn = names;
  onWheel = list;
  layout(list.map((person) => ({ slice: slices.get(person.username), weight: 1 })), list.length);
  sliceLayer.querySelectorAll(".blank").forEach((blank) => blank.remove());
  if (list.length === 0) sliceLayer.append(shape("circle", { class: "blank", r: 100 }));
  disc.setAttribute("class", chosen ? "landed" : over ? "over" : "");
  turnTo(rotation % 360);
  light((index) => (chosen ? true : index % 2 === 0));
  $("wheel-text").textContent = list.length ? "The wheel holds " + listOf(list.map((person) => person.name)) + "." : "The wheel is empty.";
}

// From the wheel as it is to `list`: who left shrinks away, who came grows
// in, and the others take their new colours on the way. Returns when done.
function morph(list) {
  const target = list.map((person) => person.username);
  const current = drawn.slice();
  const staying = current.filter((name) => target.includes(name));
  const sameOrder = staying.join("\n") === target.filter((name) => current.includes(name)).join("\n");
  if (lessMotion.matches || !current.length || !target.length || !sameOrder || current.join("\n") === target.join("\n")) {
    drawWheel(list, null, false);
    return Promise.resolve();
  }
  // Both lists are in the order of the team: merge them, leavers where they were.
  const merged = [];
  let index = 0;
  for (const name of target) {
    if (current.includes(name)) {
      while (current[index] !== name) merged.push(current[index++]);
      index += 1;
    }
    merged.push(name);
  }
  merged.push(...current.slice(index));
  const byName = new Map(list.map((person) => [person.username, person]));
  for (const slice of slices.values()) slice.group.getAnimations().forEach((animation) => animation.cancel());
  if (disc.getAttribute("class") === "landed") {
    // The slices that stepped back come forward again for the next spin.
    sliceLayer.querySelectorAll(".slice:not(.chosen)").forEach((other) => other.animate([{ opacity: 0.4 }, { opacity: 1 }], { duration: MORPH_MS, easing: kit.EASE }));
  }
  disc.setAttribute("class", "");
  light((bulb) => bulb % 2 === 0);
  target.forEach((name, position) => {
    const slice = sliceOf(byName.get(name));
    const before = getComputedStyle(slice.path).fill;
    slice.group.setAttribute("class", "slice c" + sliceColour(position, target.length));
    slice.text.textContent = fitLabel(byName.get(name).name, target.length);
    if (current.includes(name)) {
      slice.path.animate([{ fill: before }, { fill: getComputedStyle(slice.path).fill }], { duration: MORPH_MS, easing: kit.EASE });
    }
  });
  const weightOf = (name, k) => (!target.includes(name) ? 1 - k : !current.includes(name) ? k : 1);
  return new Promise((resolve) => {
    const began = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - began) / MORPH_MS);
      const k = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
      layout(merged.map((name) => ({ slice: slices.get(name), weight: weightOf(name, k) })), target.length);
      merged.forEach((name) => (slices.get(name).text.style.opacity = String(Math.min(1, weightOf(name, k) * 1.5))));
      if (t < 1) {
        requestAnimationFrame(step);
      } else {
        drawWheel(list, null, false);
        resolve();
      }
    };
    requestAnimationFrame(step);
  });
}

// Turn from where the wheel is to `to`: a short pull back, the throw, and a
// long run-out (wheel.js eases it) while the pegs flick the pointer aside.
// With "reduce motion" the wheel is simply shown at the result.
function turn(to) {
  return new Promise((resolve) => {
    if (lessMotion.matches) {
      turnTo(to);
      resolve();
      return;
    }
    const from = rotation;
    const count = Math.max(1, drawn.length);
    const began = performance.now();
    let last = began;
    let lastAngle = from;
    let under = sliceAtPointer(from, count);
    let chase = -1;
    function frame(now) {
      const elapsed = now - began;
      let angle;
      if (elapsed < WIND_MS) {
        angle = from - WIND_DEG * Math.sin(((elapsed / WIND_MS) * Math.PI) / 2);
      } else {
        angle = angleAt(from - WIND_DEG, to, Math.min(1, (elapsed - WIND_MS) / SPIN_MS));
      }
      turnTo(angle);
      // A peg passing the pointer pushes it aside; it springs back in between.
      const passing = sliceAtPointer(angle, count);
      if (count > 1 && passing !== under) {
        under = passing;
        flap = Math.min(24, flap + 11);
      }
      const step = Math.max(1, now - last);
      flap *= Math.exp(-step / 75);
      showPointer();
      // The names blur into the wheel at speed and come back as it slows.
      const speed = Math.abs(angle - lastAngle) / step;
      disc.style.setProperty("--labels", Math.max(0.2, 1 - speed / 1.3).toFixed(2));
      const lit = Math.floor(elapsed / 85) % 3;
      if (lit !== chase) {
        chase = lit;
        light((index) => index % 3 === lit);
      }
      last = now;
      lastAngle = angle;
      if (elapsed < WIND_MS + SPIN_MS) {
        requestAnimationFrame(frame);
      } else {
        turnTo(to);
        disc.style.removeProperty("--labels");
        settlePointer();
        resolve();
      }
    }
    requestAnimationFrame(frame);
  });
}

function settlePointer() {
  let last = performance.now();
  const step = (now) => {
    flap *= Math.exp(-Math.max(1, now - last) / 75);
    last = now;
    if (flap < 0.05) flap = 0;
    showPointer();
    if (flap) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// The wheel has stopped at `pick`: the slice steps forward, the others step
// back, the lights blink, and confetti for the one who speaks next.
async function land(pick, complete) {
  drawWheel(onWheel, pick.username, false);
  const slice = slices.get(pick.username);
  if (slice && !lessMotion.matches) {
    sliceLayer.append(slice.group); // on top of its neighbours while it steps out
    const index = drawn.indexOf(pick.username);
    const middle = (((index + 0.5) * 360) / drawn.length) * (Math.PI / 180);
    // A narrow slice steps further out than a wide one; the rim stays in front of both.
    const out = drawn.length > 3 ? 7 : drawn.length > 1 ? 4 : 0;
    const grow = drawn.length > 3 ? 1.03 : 1;
    const x = Math.round(out * Math.sin(middle) * 100) / 100;
    const y = Math.round(-out * Math.cos(middle) * 100) / 100;
    slice.group.animate(
      [
        { transform: "translate(0px, 0px) scale(1)" },
        { transform: `translate(${x}px, ${y}px) scale(${grow + 0.03})`, offset: 0.45 },
        { transform: `translate(${x * 0.6}px, ${y * 0.6}px) scale(${grow})` },
      ],
      { duration: 820, easing: kit.EASE, fill: "forwards" },
    );
    sliceLayer.querySelectorAll(".slice:not(.chosen)").forEach((other) => other.animate([{ opacity: 1 }, { opacity: 0.4 }], { duration: 520, easing: kit.EASE }));
  }
  kit.celebrate({ from: $("wheel-box"), particles: complete ? 160 : 110 });
  for (let blink = 0; blink < 6 && !lessMotion.matches; blink += 1) {
    light((index) => (index + blink) % 2 === 0);
    await pause(110);
  }
  light(() => true);
}

// --- What the page shows -------------------------------------------------------------------

// Who speaks next, in big letters, or what to do now.
function announce(kind, pick) {
  const next = $("next");
  if (kind === "picked" || kind === "last") {
    next.replaceChildren(
      kit.avatar(pick.name, { size: "3.25rem" }),
      h(
        "div",
        { class: "min-w-0" },
        h("div", { class: "sw-next-name efp-gradient-text truncate", text: pick.name }),
        h("div", { class: "sw-next-note", text: (kind === "last" ? "speaks last" : "speaks next") + (pick.by ? ". Spun by " + pick.by + "." : ".") }),
      ),
    );
    return;
  }
  const hints = {
    spinning: ["disc-3", "The wheel is turning..."],
    complete: ["party-popper", "Everybody has spoken. Start a new round for the next stand-up."],
    start: ["disc-3", "Spin, and the server picks who starts."],
    watch: ["eye", "You are watching: only the people on the wheel can spin it."],
    empty: ["users-round", "Nobody is on the wheel yet."],
    away: ["palmtree", "Everybody on the list is away today."],
  };
  const [icon, text] = hints[kind];
  next.replaceChildren(h("p", { class: "sw-hint flex items-center gap-2" }, kit.icon(icon, "size-5 text-primary"), h("span", { text })));
}

function announceFromState() {
  const round = state.round;
  const last = round.picks[round.picks.length - 1];
  if (round.complete) announce("complete");
  else if (last) announce("picked", last);
  else if (!state.team.members.length) announce("empty");
  else if (!round.remaining.length) announce("away");
  else announce(state.you.may_spin ? "start" : "watch");
}

function grow(bar, percent) {
  const from = bar.style.width || "0%";
  bar.style.width = percent + "%";
  if (!lessMotion.matches && from !== bar.style.width) {
    bar.animate([{ width: from }, { width: percent + "%" }], { duration: 760, easing: kit.EASE });
  }
}

const spokenItems = new Map(); // pick number -> its <li>
const chips = new Map(); // "waiting:alice" or "away:bob" -> its <li>
const historyItems = new Map(); // round number -> its <li>

// Put `items` into `list` in that order, moving only what is out of place.
function place(list, items) {
  items.forEach((item, index) => {
    if (list.children[index] !== item) list.insertBefore(item, list.children[index] || null);
  });
  while (list.children.length > items.length) list.lastElementChild.remove();
}

function chip(kind, person) {
  const key = kind + ":" + person.username;
  if (!chips.has(key)) {
    chips.set(key, h("li", {}, kit.avatar(person.name, { size: "1.65rem" }), h("span", { text: person.name })));
  }
  return chips.get(key);
}

function showLists() {
  const round = state.round;
  const total = round.picks.length + round.remaining.length;
  $("round-badge").textContent = "Round " + round.number;
  $("round-line").textContent = total ? round.remaining.length + " of " + total + " still to speak" : "";
  $("progress-text").textContent = total ? round.picks.length + " of " + total + " have spoken" : "";
  grow($("progress-bar"), total ? Math.round((round.picks.length * 100) / total) : 0);

  const spoken = round.picks.map((pick, index) => {
    if (!spokenItems.has(pick.number)) {
      spokenItems.set(
        pick.number,
        h(
          "li",
          {},
          h("span", { class: "sw-order efp-number", text: String(index + 1) }),
          kit.avatar(pick.name, { size: "1.9rem" }),
          h("span", { class: "font-medium truncate grow", text: pick.name }),
          pick.by ? h("span", { class: "text-xs text-base-content/55 whitespace-nowrap", text: "spun by " + pick.by }) : null,
        ),
      );
    }
    const item = spokenItems.get(pick.number);
    item.classList.toggle("is-latest", index === round.picks.length - 1 && !round.complete);
    return item;
  });
  for (const number of spokenItems.keys()) if (!round.picks.some((pick) => pick.number === number)) spokenItems.delete(number);
  place($("spoken"), spoken);
  $("spoken-empty").hidden = round.picks.length > 0;
  $("spoken-empty").textContent = state.team.members.length ? "Nobody yet. The first spin picks who starts." : "Nobody yet.";

  place($("waiting"), people(round.remaining).map((person) => chip("waiting", person)));
  const waitingEmpty = $("waiting-empty");
  waitingEmpty.hidden = round.remaining.length > 0;
  waitingEmpty.textContent = !state.team.members.length
    ? "Nobody is on the list yet. The owner of this app adds the team."
    : round.complete
      ? "Nobody: everybody has spoken."
      : "Nobody: everybody is away.";

  const away = state.team.members.filter((member) => member.away);
  place($("away"), away.map((person) => chip("away", person)));
  $("away-block").hidden = away.length === 0;
}

function showHistory() {
  const items = state.history.map((closed) => {
    if (!historyItems.has(closed.round)) {
      const order = h("div", { class: "sw-order-line" });
      closed.order.forEach((name, index) => {
        if (index) order.append(kit.icon("chevron-right"));
        order.append(h("span", { class: "inline-flex items-center gap-1" }, kit.avatar(name, { size: "1.25rem" }), name));
      });
      historyItems.set(
        closed.round,
        h(
          "li",
          {},
          h("span", { class: "sw-round efp-number", text: String(closed.round) }),
          h(
            "div",
            { class: "min-w-0" },
            h("p", { class: "text-sm font-medium" }, "Round " + closed.round, h("span", { class: "text-base-content/50 font-normal", text: day(closed.closed_at) ? ", " + day(closed.closed_at) : "" })),
            closed.order.length ? order : h("p", { class: "text-sm text-base-content/60", text: "Closed before anybody spoke." }),
            closed.left.length ? h("p", { class: "text-xs text-base-content/55 mt-1", text: "Did not speak: " + listOf(closed.left) + "." }) : null,
          ),
        ),
      );
    }
    return historyItems.get(closed.round);
  });
  place($("history"), items);
  $("history-empty").hidden = state.history.length > 0;
}

function showButtons() {
  const round = state.round;
  const you = state.you;
  spinButton.disabled = spinning || !you.may_spin || round.remaining.length === 0;
  spinButton.title = !you.signed_in ? "Sign in to spin" : !you.may_spin ? "Only the people on the wheel can spin it" : "";
  $("space-hint").classList.toggle("sm:block", you.may_spin);
  refreshButton.disabled = spinning;
  newRoundButton.hidden = !you.may_spin || round.picks.length === 0;
  newRoundButton.disabled = spinning;
  // The button that is the next step looks like it.
  spinButton.classList.toggle("btn-primary", !round.complete);
  spinButton.classList.toggle("btn-soft", round.complete);
  newRoundButton.classList.toggle("btn-primary", round.complete);
  newRoundButton.classList.toggle("btn-soft", !round.complete);
  const who = $("who");
  if (!you.signed_in) {
    who.textContent = "You are not signed in: you can watch.";
  } else if (you.may_spin) {
    who.textContent = "Signed in as " + you.name + ". Spin, and the server picks who speaks next.";
  } else {
    who.textContent = "Signed in as " + you.name + ". You are not on this wheel, so you can watch.";
  }
  if (you.signed_in && !$("me").firstChild) $("me").append(kit.avatar(you.name, { size: "2rem" }));
}

function showEmptyWheel() {
  const members = state.team.members.length;
  const none = !onWheel.length && !state.round.complete;
  $("wheel-empty").hidden = !none;
  $("wheel-box").classList.toggle("is-empty", none);
  if (!none) return;
  $("wheel-empty-title").textContent = members ? "Everybody is away today" : "Nobody is on the wheel";
  $("wheel-empty-text").textContent = members
    ? "Nobody is left to spin for. The wheel fills again with the next round."
    : state.you.owner
      ? "Add the team in the list below and save it."
      : "The owner of this app adds the team.";
}

// --- The owner's form ------------------------------------------------------------------------

function showTeamEmpty() {
  const empty = teamRows.children.length === 0;
  $("team-empty").hidden = !empty;
  $("team-head").hidden = empty;
}

function markDirty() {
  $("dirty").hidden = false;
  showTeamEmpty();
}

function teamRow(person) {
  const face = h("span", { class: "flex justify-center" }, kit.avatar(person.name || person.username || "?", { size: "2rem" }));
  const username = h("input", {
    type: "text",
    name: "username",
    class: "input input-sm w-full font-mono",
    maxlength: 64,
    required: true,
    autocomplete: "off",
    spellcheck: "false",
    autocapitalize: "off",
    placeholder: "username",
    "aria-label": "Portal username",
  });
  username.value = person.username;
  const name = h("input", { type: "text", name: "name", class: "input input-sm w-full", maxlength: 40, autocomplete: "off", placeholder: "Name on the wheel", "aria-label": "Name on the wheel" });
  name.value = person.name;
  const away = h("input", { type: "checkbox", name: "away", class: "toggle toggle-sm toggle-warning", "aria-label": "Away today" });
  away.checked = Boolean(person.away);
  const row = h("li", { class: person.away ? "is-away" : "" });
  // The avatar follows the name as it is typed: what the wheel and the lists will show.
  const refreshFace = () => face.replaceChildren(kit.avatar(name.value.trim() || username.value.trim() || "?", { size: "2rem" }));
  username.addEventListener("input", refreshFace);
  name.addEventListener("input", refreshFace);
  away.addEventListener("change", () => row.classList.toggle("is-away", away.checked));
  const remove = h(
    "button",
    {
      type: "button",
      class: "btn btn-ghost btn-sm btn-square text-error",
      "aria-label": "Remove " + (person.name || "this person"),
      title: "Remove",
      onclick: () => {
        row.remove();
        markDirty();
        (teamRows.querySelector('input[name="username"]') || $("add-person")).focus();
      },
    },
    kit.icon("trash-2", "size-4"),
  );
  row.append(face, username, name, h("label", { class: "sw-away" }, away, h("span", { class: "sw-away-text", text: "Away today" })), remove);
  return row;
}

// The owner's form is filled when the list or the round changes, not on
// every refresh: a spin by somebody else must not wipe what is being typed.
function showOwner() {
  $("owner").hidden = !state.you.owner;
  $("not-owner").hidden = state.you.owner;
  if (!state.you.owner) {
    return;
  }
  $("owner-note").textContent =
    "The username is the one a person signs in to the Portal with; it decides who may spin. " + '"Away today" lasts until the next round starts.';
  say(state.team.starter ? "This is the starter list that ships with the app (team.json). Replace it with your team and save." : "", false, $("starter"));
  const filledFrom = state.team.version + ":" + state.round.number;
  if (formFor !== filledFrom) {
    formFor = filledFrom;
    teamRows.replaceChildren(...state.team.members.map(teamRow));
    $("dirty").hidden = true;
    showTeamEmpty();
  }
}

// --- Taking in what the server said ------------------------------------------------------------

function sameRound(one, other) {
  return (
    one &&
    other &&
    one.round.number === other.round.number &&
    one.round.picks.length === other.round.picks.length &&
    one.round.remaining.join("\n") === other.round.remaining.join("\n")
  );
}

// Take in the state. The wheel follows the round unless it has just stopped
// and should keep showing where, or nothing about the round has changed.
function adopt(fresh, { keepWheel = false } = {}) {
  const before = state;
  state = fresh;
  showLists();
  showHistory();
  showButtons();
  showOwner();
  if (keepWheel) return Promise.resolve();
  if (before && sameRound(before, fresh) && !spinning) {
    showEmptyWheel();
    return Promise.resolve();
  }
  announceFromState();
  const round = state.round;
  if (round.complete) {
    drawWheel(people(round.picks.map((pick) => pick.username)), null, true);
    showEmptyWheel();
    return Promise.resolve();
  }
  const wanted = people(round.remaining);
  const done = before ? morph(wanted) : (drawWheel(wanted), Promise.resolve());
  return done.then(showEmptyWheel);
}

async function load(button) {
  const before = state;
  const work = async () => orThrow(await ask("api/state"));
  try {
    const fresh = await (button ? kit.busy(button, work, { tick: false }) : work());
    if (!fresh) return false; // a second click while the first is under way
    say("");
    $("wheel-box").classList.remove("is-loading");
    // Somebody spun on another screen since: turn this wheel to their pick too.
    const added = before && fresh.round.number === before.round.number ? fresh.round.picks.slice(before.round.picks.length) : [];
    if (added.length === 1 && drawn.includes(added[0].username) && !spinning) {
      await replay(added[0], fresh);
    } else {
      await adopt(fresh);
    }
    return true;
  } catch (problem) {
    say(problem.message, true);
    return false;
  }
}

// --- Spinning --------------------------------------------------------------------------------

// Turn the wheel to `pick` and take in `fresh` once it has landed.
async function spinTo(pick, fresh) {
  const index = onWheel.findIndex((person) => person.username === pick.username);
  if (index < 0) {
    await adopt(fresh); // this page had an old list: no turn, only the result
    announce(fresh.round.complete ? "last" : "picked", pick);
    return;
  }
  const offCentre = (Math.random() - 0.5) * 0.7; // where in the slice the wheel halts is only looks
  await turn(landingAngle(rotation, index, onWheel.length, TURNS, offCentre));
  spinning = false;
  await adopt(fresh, { keepWheel: true });
  announce(fresh.round.complete ? "last" : "picked", pick);
  kit.appear($("next").children, { stagger: 90 });
  await land(pick, fresh.round.complete);
  if (fresh.round.complete) {
    kit.celebrate({ particles: 180 });
    kit.pop(newRoundButton, { scale: 1.08 });
  }
}

async function replay(pick, fresh) {
  spinning = true;
  showButtons();
  await morph(people(state.round.remaining));
  await spinTo(pick, fresh);
  spinning = false;
  showButtons();
  kit.toast((pick.by || "Somebody") + " spun: " + pick.name + " speaks next.", { kind: "info", timeout: 3600 });
}

async function spin() {
  if (spinning || !state || spinButton.disabled) {
    return;
  }
  spinning = true;
  showButtons();
  say("");
  // The page says which pick it asks for. If somebody else asked for the
  // same one a moment sooner, the server answers with theirs. While the
  // server decides, whoever spoke last leaves the wheel.
  const asking = ask("api/spin", "POST", { round: state.round.number, pick: state.round.picks.length + 1 });
  announce("spinning");
  await morph(people(state.round.remaining));
  const answer = await asking;
  if (!answer.ok) {
    spinning = false;
    kit.shake(spinButton);
    say(answer.message, true);
    if (answer.status === 409) {
      await load(); // the round has moved on: show where it stands
    } else {
      showButtons();
    }
    return;
  }
  const pick = answer.data.pick;
  await spinTo(pick, answer.data.state);
  spinning = false;
  showButtons();
  if (!answer.data.created) {
    kit.toast((pick.by || "Somebody") + " spun at the same moment: this is that one spin.", { kind: "info" });
  }
}

async function closeRound() {
  const number = state.round.number;
  try {
    const data = await kit.busy(newRoundButton, async () => orThrow(await ask("api/round/next", "POST", { round: number })), { tick: false });
    if (!data) return;
    await adopt(data.state);
    kit.toast(data.closed ? "Round " + number + " is closed. Everybody who is not away is back on the wheel." : "That round was closed already.", {
      kind: data.closed ? "success" : "info",
    });
  } catch (problem) {
    say(problem.message, true);
  }
}

spinButton.addEventListener("click", spin);
refreshButton.addEventListener("click", (event) => load(event.currentTarget));

newRoundButton.addEventListener("click", () => {
  if (state.round.complete) {
    closeRound();
    return;
  }
  const left = people(state.round.remaining).map((person) => person.name);
  $("confirm-text").textContent =
    listOf(left) + (left.length === 1 ? " has" : " have") + " not spoken yet. The round is closed as it is and listed under Earlier rounds.";
  confirmDialog.returnValue = "";
  confirmDialog.showModal();
});

confirmDialog.addEventListener("close", () => {
  if (confirmDialog.returnValue === "close") {
    closeRound();
  }
});

// Space spins, wherever the focus is not in a control of its own.
document.addEventListener("keydown", (event) => {
  if (event.key !== " " || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target.closest && event.target.closest("input, textarea, select, button, a, summary, dialog[open]")) return;
  event.preventDefault();
  spin();
});

// --- The owner's form ------------------------------------------------------------------------

$("add-person").addEventListener("click", () => {
  const row = teamRow({ username: "", name: "", away: false });
  teamRows.append(row);
  markDirty();
  row.querySelector("input").focus();
});

$("team-form").addEventListener("input", markDirty);

$("team-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const members = Array.from(teamRows.children).map((row) => ({
    username: row.querySelector('input[name="username"]').value,
    name: row.querySelector('input[name="name"]').value,
    away: row.querySelector('input[name="away"]').checked,
  }));
  say("", false, $("team-status"));
  say("");
  // The version says which list the form was filled from; the server refuses a save over a newer one.
  const answer = await kit.busy($("save-team"), async () => {
    const sent = await ask("api/team", "PUT", { version: state.team.version, members });
    if (!sent.ok) throw sent;
    return sent;
  }).catch((problem) => problem);
  if (!answer) return; // a second click while the first save is under way
  if (answer.ok) {
    formFor = null;
    await adopt(answer.data);
    kit.toast("The list is saved. The wheel shows it from now on.", { kind: "success" });
    return;
  }
  say(answer.message, true, $("team-status"));
  if (answer.status === 409) {
    formFor = null;
    if (await load()) {
      say(answer.message + " The form shows the list as it is now: make your change again.", true, $("team-status"));
    }
  }
});

// --- Start ------------------------------------------------------------------------------------

await kit.ready();
kit.themeSwitch($("theme"));
kit.animateList($("spoken"));
kit.animateList($("waiting"));
kit.animateList($("away"));
kit.animateList($("history"));
kit.animateList(teamRows);
showPointer();
light((index) => index % 2 === 0);
$("wheel-box").classList.add("is-loading");
load();
