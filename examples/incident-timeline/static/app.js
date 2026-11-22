// The page of the incident timeline: paste the notes, let the model pick out
// the events, correct them on a time axis, keep the timeline, take a
// post-mortem skeleton with you.
//
// Every URL in this file is relative ("api/extract", not "/api/extract"):
// the app is served under /{slug}/ once deployed. The one exception is the
// EFP kit at /_efp/lib/, which every gateway serves (docs/UI_KIT.md).
//
// Nothing here asks the server on a timer: the saved timelines are loaded
// when the page opens and after something the visitor did. Whatever came
// from notes or from the model is put into the page as text (kit.h,
// textContent, input values), never as markup.
//
// The axis is drawn from state.events and every card is kept by its event:
// an edit changes that card only, a new time moves it to its place (FLIP),
// and only a whole new timeline drops in from the top. The rules (what a
// time is, the order, the post-mortem, the picture) are in timeline.js,
// which the tests run with node.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import { KINDS, cleanTime, duration, eventProblem, extractSummary, fileName, layout, postmortem, sortEvents, splitTime, suggestedTitle } from "./timeline.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const notes = $("notes");
const axis = $("axis");
const title = $("title");

// The name of the SVG namespace: an identifier, not an address that is fetched.
const SVG = "http://www.w3.org/2000/svg";

// Each kind's colour, from the theme, and its icon (https://lucide.dev/icons).
// The kind is always written out too: colour alone tells nothing to somebody
// who cannot tell the colours apart.
const LOOKS = {
  detection: { tone: "var(--color-error)", icon: "siren" },
  action: { tone: "var(--color-primary)", icon: "wrench" },
  escalation: { tone: "var(--color-warning)", icon: "megaphone" },
  resolution: { tone: "var(--color-success)", icon: "circle-check" },
  note: { tone: "color-mix(in oklab, var(--color-base-content) 45%, var(--color-base-100))", icon: "sticky-note" },
};

const state = {
  limits: { notes: 16000, events: 100, actor: 60, text: 200, title: 120 }, // replaced by what the server says
  signedIn: false,
  modelUsable: false,
  open: false, // a timeline is on the page
  events: [], // in the order of the axis: by time, events without a valid time last
  id: null, // of the saved timeline that is open, if any
  version: 0, // the version of it that this page opened
  savedAt: null,
  dirty: false,
  editing: null, // the event whose card is open for editing
  saved: [], // the member's saved timelines, as the server listed them
  savedLoaded: false,
  savedLimit: 50,
};
const cards = new Map(); // event -> { item, when, dot, card }
const days = new Map(); // "2026-11-12" -> the <li> of its heading
const savedItems = new Map(); // timeline id -> its <li>
let addItem = null; // the last <li> of the axis: "Add an event"

// --- Small helpers ----------------------------------------------------------------------

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function errorMessage(response) {
  try {
    const body = await response.json();
    if (body.error && body.error.message) return body.error.message;
  } catch (error) {
    // not JSON
  }
  return response.status === 401 ? "Your session has ended. Reload the page to sign in again." : "The app answered with an error (" + response.status + ").";
}

// What a request answered: its JSON, or an Error that carries the sentence to show.
async function answer(request) {
  let response;
  try {
    response = await request;
  } catch (error) {
    throw new Error("The app cannot be reached right now.");
  }
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

function sendJson(url, method, data) {
  return fetch(url, { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}

function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

function today() {
  const now = new Date();
  const two = (number) => String(number).padStart(2, "0");
  return now.getFullYear() + "-" + two(now.getMonth() + 1) + "-" + two(now.getDate());
}

function kindOf(id) {
  return KINDS.find((kind) => kind.id === id) || KINDS[KINDS.length - 1];
}

function lookOf(id) {
  return LOOKS[id] || LOOKS.note;
}

// "2026-11-12" -> "Thu, 12 Nov 2026"; the date is the incident's, not a moment in a time zone.
function dayLabel(day) {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date)).toLocaleDateString("en", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

// An alert in place of `holder`; an empty text hides it.
function alertIn(holder, text, kind = "info") {
  const icons = { info: "info", success: "circle-check", warning: "triangle-alert", error: "circle-alert" };
  holder.hidden = !text;
  holder.replaceChildren(text ? h("div", { class: "alert alert-soft text-sm alert-" + kind }, kit.icon(icons[kind], "size-5"), h("span", { text })) : "");
  if (text) kit.rise(holder.firstChild, { distance: 6, duration: 420 });
}

// A question before something is lost. Resolves to true for "yes".
function ask(heading, text, ok, { danger = false, icon = "triangle-alert" } = {}) {
  const dialog = $("confirm");
  $("confirm-title").textContent = heading;
  $("confirm-text").textContent = text;
  $("confirm-ok").textContent = ok;
  $("confirm-ok").className = "btn " + (danger ? "btn-error" : "btn-primary");
  $("confirm-icon").replaceChildren(kit.icon(icon));
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true }));
}

$("confirm-cancel").addEventListener("click", () => $("confirm").close("cancel"));

function download(name, text, type) {
  // The file is made in the browser from what is on the page: nothing is asked from the server.
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = h("a", { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// --- The notes ---------------------------------------------------------------------------

let shownLines = 0;

// The line numbers, the counter and the meter of the limit follow every keystroke.
function showNotes() {
  const length = notes.value.length;
  const lines = (notes.value.match(/\n/g) || []).length + 1;
  if (lines !== shownLines) {
    shownLines = lines;
    $("gutter").textContent = Array.from({ length: lines }, (_, index) => index + 1).join("\n");
    $("lines").textContent = plural(lines, "line");
  }
  const share = length / state.limits.notes;
  $("notes-count").textContent = length.toLocaleString("en") + " / " + state.limits.notes.toLocaleString("en");
  $("meter").style.setProperty("--share", String(Math.min(1, share)));
  $("editor").dataset.level = share > 1 ? "over" : share > 0.85 ? "near" : "";
  $("notes-over").hidden = share <= 1;
  $("notes-over").textContent =
    "Only the first " + state.limits.notes.toLocaleString("en") + " of these " + length.toLocaleString("en") +
    " characters will be read: an incident is told from its beginning.";
  $("clear-button").hidden = length === 0;
  $("gutter").scrollTop = notes.scrollTop;
}

notes.addEventListener("input", showNotes);
notes.addEventListener("scroll", () => {
  $("gutter").scrollTop = notes.scrollTop;
});
notes.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    $("notes-form").requestSubmit();
  }
});

$("clear-button").addEventListener("click", () => {
  const before = notes.value;
  notes.value = "";
  showNotes();
  notes.focus();
  kit.toast("The notes are cleared.", { action: { label: "Undo", onClick: () => ((notes.value = before), showNotes()) } });
});

// --- Extracting ------------------------------------------------------------------------------

let clockRun = 0;

// Seconds since the model was asked, so a long wait still shows that something is going on.
function startClock() {
  const run = ++clockRun;
  const began = performance.now();
  const tick = () => {
    if (run !== clockRun) return;
    $("reading-time").textContent = Math.floor((performance.now() - began) / 1000) + " s";
    setTimeout(tick, 1000);
  };
  tick();
}

function showBoard(mode) {
  $("board-empty").hidden = mode !== "empty";
  $("board-reading").hidden = mode !== "reading";
  $("board-open").hidden = mode !== "open";
}

function intoView(element) {
  // On a phone the board is below the notes: bring it up once there is something to see.
  if (element.getBoundingClientRect().top > window.innerHeight * 0.6) {
    element.scrollIntoView({ block: "start", behavior: kit.reducedMotion() ? "auto" : "smooth" });
  }
}

async function extract(button) {
  if (button.classList.contains("efp-busy")) return; // Ctrl+Enter while the model is still reading
  if (!notes.value.trim()) {
    alertIn($("extract-status"), "Paste the notes of an incident first.", "warning");
    kit.shake($("editor"));
    notes.focus();
    return;
  }
  if (!(await mayReplace())) return;
  const wasOpen = state.open;
  alertIn($("extract-status"), "");
  showBoard("reading");
  intoView($("board"));
  startClock();
  try {
    // The server reads the beginning anyway; sending less keeps the request small.
    const result = await kit.busy(button, () => answer(sendJson("api/extract", "POST", { text: notes.value.slice(0, state.limits.notes * 4) })), { tick: false });
    const worry = result.dropped || result.incomplete || result.cut || result.repaired;
    alertIn($("extract-status"), extractSummary(result), result.events.length === 0 ? "info" : worry ? "warning" : "success");
    if (result.events.length > 0) {
      show({ id: null, version: 0, title: suggestedTitle(result.events, today()), events: result.events }, true, { drop: true });
      kit.toast(plural(result.events.length, "event") + " found. Check them against your notes: a model can misread.", { kind: "success" });
    } else {
      showBoard(wasOpen ? "open" : "empty");
    }
  } catch (error) {
    showBoard(wasOpen ? "open" : "empty");
    alertIn($("extract-status"), error.message, "error");
  } finally {
    clockRun += 1;
  }
}

$("notes-form").addEventListener("submit", (event) => {
  event.preventDefault();
  extract($("extract-button"));
});

for (const button of document.querySelectorAll("[data-blank]")) {
  button.addEventListener("click", async () => {
    if (await mayReplace()) {
      show({ id: null, version: 0, title: "", events: [] }, false);
      intoView($("board"));
      title.focus();
    }
  });
}

// --- The axis: keyed cards, moved with FLIP ---------------------------------------------------

// The order of the axis: events with a valid time by time (stable), the others after them.
function order(events) {
  const timed = events.filter((event) => cleanTime(event.time) !== null);
  return [...sortEvents(timed), ...events.filter((event) => cleanTime(event.time) === null)];
}

// Run change(), then let every item that moved glide from where it was (First, Last, Invert, Play).
function flip(change) {
  const before = new Map(Array.from(axis.children, (item) => [item, item.getBoundingClientRect().top]));
  change();
  if (kit.reducedMotion()) return;
  for (const item of axis.children) {
    if (!before.has(item)) {
      drop([item], 0);
      continue;
    }
    const shift = before.get(item) - item.getBoundingClientRect().top;
    if (Math.abs(shift) > 1) {
      item.animate([{ transform: `translateY(${shift}px)` }, { transform: "none" }], { duration: 560, easing: kit.SPRING });
    }
  }
}

// Items fall onto the axis one after the other, each dot landing with a bounce.
function drop(items, delay = 140) {
  if (kit.reducedMotion()) return;
  const step = Math.min(90, 1500 / Math.max(1, items.length));
  items.forEach((item, index) => {
    const start = delay + index * step;
    item.animate([{ opacity: 0, transform: "translateY(-22px)" }, { opacity: 1, transform: "none" }], { duration: 560, delay: start, easing: kit.EASE, fill: "backwards" });
    const dot = item.querySelector(".it-dot");
    if (dot) dot.animate([{ transform: "scale(0)" }, { transform: "scale(1)" }], { duration: 640, delay: start + 160, easing: kit.BOUNCE, fill: "backwards" });
  });
  if (items.length > 2) {
    try {
      // The line of the axis draws itself down as the events land on it.
      axis.animate([{ transform: "scaleY(0)" }, { transform: "scaleY(1)" }], {
        duration: delay + items.length * step + 300,
        easing: "cubic-bezier(0.3, 0.6, 0.4, 1)",
        pseudoElement: "::before",
        fill: "backwards",
      });
    } catch (error) {
      // a browser that cannot animate a pseudo-element just shows the line
    }
  }
}

// A card's height follows its new content instead of jumping (view <-> edit).
function morph(card, update) {
  const from = card.getBoundingClientRect().height;
  update();
  if (kit.reducedMotion()) return;
  const to = card.getBoundingClientRect().height;
  card.classList.add("is-morphing");
  card.animate([{ height: from + "px" }, { height: to + "px" }], { duration: 380, easing: kit.EASE }).finished.catch(() => {}).then(() => card.classList.remove("is-morphing"));
  card.firstElementChild?.animate([{ opacity: 0, transform: "translateY(4px)" }, { opacity: 1, transform: "none" }], { duration: 320, delay: 60, easing: kit.EASE, fill: "backwards" });
}

// A removed card folds away; the cards below close the gap as it goes.
function collapse(item) {
  if (kit.reducedMotion()) return Promise.resolve();
  item.style.overflow = "hidden";
  const frames = [{ height: item.offsetHeight + "px", opacity: 1, transform: "none" }, { height: "0px", opacity: 0, transform: "translateX(28px)" }];
  return item.animate(frames, { duration: 340, easing: kit.EASE }).finished.catch(() => {});
}

function dayItem(day) {
  if (!days.has(day)) days.set(day, h("li.it-day", {}, h("span", {}, kit.icon("calendar", "size-3.5"), dayLabel(day))));
  return days.get(day);
}

function addRow() {
  addItem ||= h(
    "li.it-event.it-add-row",
    {},
    h("span"),
    h("span.it-dot.it-add-dot", { "aria-hidden": "true" }, kit.icon("plus", "size-3")),
    h("button.btn.btn-sm.it-add", { type: "button", onclick: addEvent }, kit.icon("plus", "size-4"), "Add an event"),
  );
  return addItem;
}

function itemOf(event) {
  if (!cards.has(event)) {
    const parts = { when: h("div.it-when"), dot: h("span.it-dot", { "aria-hidden": "true" }), card: h("div.it-card") };
    parts.item = h("li.it-event", {}, parts.when, parts.dot, parts.card);
    cards.set(event, parts);
    fill(event);
  }
  return cards.get(event).item;
}

// Put the axis in the order of state.events, with a heading where the day changes.
function arrange(animate) {
  const sequence = [];
  const used = new Set();
  let day = null;
  for (const event of state.events) {
    const time = cleanTime(event.time);
    const eventDay = time ? splitTime(time)[0] : "";
    if (eventDay && eventDay !== day && !used.has(eventDay)) {
      used.add(eventDay);
      sequence.push(dayItem(eventDay));
    }
    day = eventDay || day;
    sequence.push(itemOf(event));
  }
  sequence.push(addRow());
  for (const [key, item] of days) {
    if (used.has(key)) continue;
    days.delete(key);
    item.remove();
  }
  const place = () => sequence.forEach((node, index) => axis.children[index] !== node && axis.insertBefore(node, axis.children[index] || null));
  if (animate) flip(place);
  else place();
}

function describe(event) {
  return (event.time || "No time") + ", " + (event.actor.trim() || "unknown") + ", " + kindOf(event.kind).label + ": " + (event.text.trim() || "nothing said yet");
}

// The time, the dot's colour and (unless it is being edited) the card of one event.
function fill(event) {
  const parts = cards.get(event);
  if (!parts) return;
  const time = cleanTime(event.time);
  const problem = eventProblem(event, state.limits);
  parts.item.style.setProperty("--it-tone", lookOf(event.kind).tone);
  parts.item.classList.toggle("has-problem", problem !== null);
  const clock = time ? splitTime(time)[1] : "";
  parts.when.replaceChildren(
    ...(time
      ? [h("span.it-clock", {}, clock.slice(0, 5)), clock.length > 5 ? h("span.it-sec", {}, clock.slice(5)) : ""]
      : [h("span.it-clock.is-missing", { title: "No valid time yet" }, "--:--")]),
  );
  if (state.editing === event) return;
  const actor = event.actor.trim();
  const text = event.text.trim();
  const who = actor ? h("span.it-who", {}, kit.avatar(actor, { size: "1.3rem" }), h("span.truncate", { text: actor })) : h("span.it-who.is-unknown", {}, "Unknown");
  parts.card.replaceChildren(
    h(
      "button.it-card-main",
      { type: "button", "aria-label": describe(event) + ". Edit.", onclick: () => openEditor(event) },
      h("span.it-card-top", {}, h("span.it-kind", {}, kit.icon(lookOf(event.kind).icon, "size-3.5"), kindOf(event.kind).label), who),
      h("span.it-text", { class: text ? null : "is-missing", text: text || "Say what happened" }),
      problem ? h("span.it-problem", {}, kit.icon("triangle-alert", "size-3.5"), "Not saved like this: " + problem + ".") : null,
    ),
    h(
      "span.it-tools",
      {},
      h("button.btn.btn-ghost.btn-xs.btn-square", { type: "button", "aria-label": "Edit", title: "Edit", onclick: () => openEditor(event) }, kit.icon("pencil", "size-3.5")),
      h("button.btn.btn-ghost.btn-xs.btn-square.text-error", { type: "button", "aria-label": "Remove", title: "Remove", onclick: () => removeEvent(event) }, kit.icon("trash-2", "size-3.5")),
    ),
  );
}

// --- Editing in place -------------------------------------------------------------------------

let forms = 0;

function editorOf(event) {
  forms += 1;
  const time = h("input.input.input-sm.font-mono.w-full", { type: "text", maxlength: 20, autocomplete: "off", spellcheck: "false", placeholder: "14:05", "aria-label": "Time: 14:05 or 2026-11-12 14:05" });
  const actor = h("input.input.input-sm.w-full", { type: "text", maxlength: state.limits.actor, autocomplete: "off", placeholder: "Who", "aria-label": "Who" });
  const text = h("textarea.textarea.textarea-sm.it-what", { rows: 2, maxlength: state.limits.text, placeholder: "What happened, in one sentence", "aria-label": "What happened" });
  time.value = event.time;
  actor.value = event.actor;
  text.value = event.text;
  const count = h("span.it-count.efp-number");
  const note = h("p.it-edit-problem", { role: "status" });
  const kinds = h(
    "div.it-kinds",
    { role: "radiogroup", "aria-label": "Kind" },
    KINDS.map((kind) => {
      const choice = h("input.sr-only", { type: "radio", name: "kind-" + forms, value: kind.id, checked: kind.id === event.kind });
      choice.addEventListener("change", () => {
        event.kind = kind.id;
        changed(event);
      });
      return h("label.it-kind-choice", { style: { "--it-tone": lookOf(kind.id).tone } }, choice, h("span.it-kind-dot", { "aria-hidden": "true" }), kind.label);
    }),
  );
  const check = () => {
    const problem = eventProblem(event, state.limits);
    time.setAttribute("aria-invalid", String(cleanTime(event.time) === null));
    text.setAttribute("aria-invalid", String(!event.text.trim()));
    note.textContent = problem ? "Not saved like this: " + problem + "." : "";
    count.textContent = event.text.length + " / " + state.limits.text;
  };
  time.addEventListener("input", () => {
    event.time = time.value;
    changed(event);
    check();
  });
  time.addEventListener("change", () => {
    const clean = cleanTime(time.value);
    if (clean !== null) time.value = event.time = clean; // the form the server keeps
  });
  actor.addEventListener("input", () => {
    event.actor = actor.value;
    changed(event);
  });
  text.addEventListener("input", () => {
    event.text = text.value;
    changed(event);
    check();
  });
  check();
  const form = h(
    "form.it-edit",
    { "aria-label": "Edit the event" },
    h("div.it-edit-row", {}, h("label.it-field", {}, h("span", {}, "Time"), time), h("label.it-field", {}, h("span", {}, "Who"), actor)),
    kinds,
    h("label.it-field", {}, h("span", {}, "What happened"), text, count),
    note,
    h(
      "div.flex.items-center.gap-2",
      {},
      h("span.text-xs.text-base-content/50.hidden.sm:inline", { "aria-hidden": "true" }, h("kbd.kbd.kbd-xs", {}, "Enter"), " or ", h("kbd.kbd.kbd-xs", {}, "Esc"), " closes"),
      h("button.btn.btn-ghost.btn-sm.text-error.ml-auto", { type: "button", onclick: () => removeEvent(event) }, kit.icon("trash-2", "size-4"), "Remove"),
      h("button.btn.btn-primary.btn-sm", { type: "submit" }, kit.icon("check", "size-4"), "Done"),
    ),
  );
  form.addEventListener("submit", (submitted) => {
    submitted.preventDefault();
    closeEditor(event);
  });
  form.addEventListener("keydown", (pressed) => {
    if (pressed.key === "Escape") {
      pressed.preventDefault();
      closeEditor(event);
    } else if (pressed.key === "Enter" && pressed.target === text) {
      pressed.preventDefault(); // one sentence: Enter is "done", not a new line
      form.requestSubmit();
    }
  });
  return { form, first: cleanTime(event.time) === null ? time : text };
}

// Something of the event changed in its form: the dot, the clock and the facts follow at once.
function changed(event) {
  state.dirty = true;
  fill(event);
  refresh();
}

function openEditor(event) {
  if (state.editing === event) return;
  if (state.editing) closeEditor(state.editing, { focus: false });
  const parts = cards.get(event);
  if (!parts) return;
  state.editing = event;
  const { form, first } = editorOf(event);
  morph(parts.card, () => {
    parts.item.classList.add("is-editing");
    parts.card.replaceChildren(form);
  });
  first.focus({ preventScroll: true });
  if (first.setSelectionRange) first.setSelectionRange(first.value.length, first.value.length);
}

function closeEditor(event, { focus = true } = {}) {
  if (state.editing !== event) return;
  state.editing = null;
  const parts = cards.get(event);
  if (!parts) return;
  const clean = cleanTime(event.time);
  if (clean !== null) event.time = clean;
  morph(parts.card, () => {
    parts.item.classList.remove("is-editing");
    fill(event);
  });
  // A new time moves the card to its place; the ring says "it went here".
  const was = state.events.indexOf(event);
  state.events = order(state.events);
  arrange(true);
  if (state.events.indexOf(event) !== was) kit.flash(parts.card, lookOf(event.kind).tone);
  if (focus) parts.card.querySelector(".it-card-main").focus({ preventScroll: true });
  refresh();
}

// A click anywhere outside the open card, or Tab out of it, closes it.
function closeUnless(inside) {
  const parts = state.editing && cards.get(state.editing);
  if (parts && inside && !parts.item.contains(inside)) closeEditor(state.editing, { focus: false });
}
document.addEventListener("pointerdown", (pressed) => closeUnless(pressed.target), true);
axis.addEventListener("focusout", (left) => closeUnless(left.relatedTarget));

function addEvent() {
  if (state.events.length >= state.limits.events) {
    kit.toast("A timeline holds at most " + state.limits.events + " events.", { kind: "warning" });
    return;
  }
  if (state.editing) closeEditor(state.editing, { focus: false });
  const timed = sortEvents(state.events.filter((event) => cleanTime(event.time) !== null));
  // A new event starts at the time of the last one: usually it comes after it.
  const event = { time: timed.length ? cleanTime(timed[timed.length - 1].time) : "", actor: "", text: "", kind: "note" };
  state.events.push(event);
  state.dirty = true;
  arrange(true);
  openEditor(event);
  cards.get(event).item.scrollIntoView({ block: "nearest", behavior: kit.reducedMotion() ? "auto" : "smooth" });
  refresh();
}

async function removeEvent(event) {
  const index = state.events.indexOf(event);
  if (index === -1) return;
  if (state.editing === event) state.editing = null;
  const parts = cards.get(event);
  const next = state.events[index + 1] || state.events[index - 1];
  state.events.splice(index, 1);
  cards.delete(event);
  state.dirty = true;
  refresh();
  await collapse(parts.item);
  parts.item.remove();
  arrange(true);
  const focusTarget = next && cards.get(next) ? cards.get(next).card.querySelector("button") : addItem.querySelector("button");
  if (focusTarget) focusTarget.focus({ preventScroll: true });
  const undo = () => {
    state.events = order([...state.events.slice(0, index), event, ...state.events.slice(index)]);
    state.dirty = true;
    arrange(true);
    refresh();
  };
  kit.toast("The event is removed. It is gone for good when you save.", { action: { label: "Undo", onClick: undo } });
}

// --- The head of the board: facts, kinds, saved or not ------------------------------------------

function legend() {
  const list = $("legend");
  if (!list.children.length) {
    const item = (kind) => h("li", { style: { "--it-tone": lookOf(kind.id).tone }, dataset: { kind: kind.id } }, h("span.it-kind-dot"), kind.label, h("span.it-legend-count.efp-number", {}, "0"));
    list.replaceChildren(...KINDS.map(item));
  }
  for (const item of list.children) {
    const count = String(state.events.filter((event) => event.kind === item.dataset.kind).length);
    const shown = item.querySelector(".it-legend-count");
    item.classList.toggle("is-none", count === "0");
    if (shown.textContent !== count) {
      shown.textContent = count;
      kit.pop(shown, { scale: 1.35 });
    }
  }
}

function refresh() {
  if (!state.open) return;
  const valid = state.events.filter((event) => eventProblem(event, state.limits) === null);
  const people = new Set(state.events.map((event) => event.actor.trim().toLowerCase()).filter(Boolean));
  const span = duration(valid);
  kit.countUp($("fact-events"), state.events.length, { duration: 600 });
  $("fact-events-word").textContent = state.events.length === 1 ? "event" : "events";
  $("fact-span-item").hidden = span === null;
  $("fact-span").textContent = span || "";
  kit.countUp($("fact-people"), people.size, { duration: 600 });
  $("axis-empty").hidden = state.events.length > 0;
  legend();

  const badge = $("save-state");
  const look = !state.signedIn
    ? ["badge-neutral", "lock", "Not kept: you are not signed in"]
    : state.dirty
      ? ["badge-warning", "circle-dot", state.id ? "Changes not saved" : "Not saved yet"]
      : ["badge-success", "cloud-check", "Saved " + (state.savedAt ? kit.timeAgo(state.savedAt) : "")];
  const label = look[2].trim();
  if (badge.dataset.label !== label) {
    badge.dataset.label = label;
    badge.className = "badge badge-soft gap-1.5 " + look[0];
    badge.replaceChildren(kit.icon(look[1], "size-3.5"), label);
  }
  $("save-button").disabled = !state.signedIn;
  $("save-button").title = state.signedIn ? "Save (Ctrl+S)" : "Sign in to keep timelines";
}

title.addEventListener("input", () => {
  state.dirty = true;
  refresh();
});
title.addEventListener("keydown", (event) => {
  if (event.key === "Enter") title.blur();
});

// --- One timeline on the page --------------------------------------------------------------------

function show(timeline, dirty, { drop: dropIn = false } = {}) {
  state.events = order(timeline.events.map((event) => ({ time: event.time, actor: event.actor || "", text: event.text, kind: event.kind })));
  state.id = timeline.id;
  state.version = timeline.version;
  state.savedAt = timeline.saved_at || null;
  state.dirty = dirty;
  state.editing = null;
  state.open = true;
  title.maxLength = state.limits.title;
  title.value = timeline.title;
  showBoard("open");
  cards.clear();
  days.clear();
  axis.replaceChildren();
  arrange(false);
  refresh();
  if (dropIn) drop(Array.from(axis.children));
  else kit.rise(axis.children, { stagger: 25, distance: 8 });
  showSaved();
}

async function mayReplace() {
  if (!state.open || !state.dirty || state.events.length === 0) return true;
  return ask("Replace the timeline on the page?", "It has changes that are not saved. They will be lost.", "Replace it", { danger: true, icon: "replace" });
}

// What keeps the timeline from being saved or exported: { message, event } or null.
function firstProblem(needsTitle) {
  if (needsTitle && !title.value.trim()) return { message: "Give the timeline a title first.", field: title };
  if (state.events.length > state.limits.events) return { message: "A timeline holds at most " + state.limits.events + " events." };
  const broken = state.events.find((event) => eventProblem(event, state.limits) !== null);
  if (broken) return { message: "Correct the marked event first, or remove it.", event: broken };
  return null;
}

function stopAt(problem) {
  kit.toast(problem.message, { kind: "error" });
  if (problem.field) {
    problem.field.focus();
    kit.shake(problem.field);
  } else if (problem.event) {
    openEditor(problem.event);
    const parts = cards.get(problem.event);
    parts.item.scrollIntoView({ block: "center", behavior: kit.reducedMotion() ? "auto" : "smooth" });
    kit.shake(parts.card);
  }
}

function cleanEvents() {
  return order(state.events).map((event) => ({ time: cleanTime(event.time), actor: event.actor.trim(), text: event.text.trim(), kind: event.kind }));
}

async function save() {
  const button = $("save-button");
  if (!state.signedIn || button.classList.contains("efp-busy")) return;
  if (state.editing) closeEditor(state.editing, { focus: false });
  const problem = firstProblem(true);
  if (problem) {
    stopAt(problem);
    return;
  }
  try {
    const body = { title: title.value.trim(), events: cleanEvents(), version: state.version };
    const saved = await kit.busy(button, () =>
      answer(state.id ? sendJson("api/timelines/" + encodeURIComponent(state.id), "PUT", body) : sendJson("api/timelines", "POST", body)),
    );
    // The server tidies and sorts what it keeps; the cards stay and take its words.
    if (saved.events.length === state.events.length) {
      state.events.forEach((event, index) => Object.assign(event, saved.events[index]));
      state.events.forEach(fill);
    }
    state.id = saved.id;
    state.version = saved.version;
    state.savedAt = saved.saved_at;
    state.dirty = false;
    title.value = saved.title;
    refresh();
    kit.toast("Saved. Only you can open it.", { kind: "success" });
    loadSaved(null, saved.id);
  } catch (error) {
    kit.toast(error.message, { kind: "error", timeout: 9000 });
  }
}

$("save-button").addEventListener("click", save);

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && state.open) {
    event.preventDefault();
    save();
  }
});

// --- Export: the post-mortem as Markdown, the timeline as a picture -------------------------------

// The timeline as an SVG file, laid out by layout() of timeline.js (SVG does
// not wrap text by itself). Its colours are those of the theme on the page.
function picture(events) {
  const plan = layout(events, 760);
  const top = 70;
  const width = plan.width;
  const height = plan.height + top + 16;
  const ink = kit.color("base-content");
  const muted = kit.color("base-content", 0.6);
  const paper = kit.color("base-100");
  const node = (name, attributes, text) => {
    const made = document.createElementNS(SVG, name);
    for (const [key, value] of Object.entries(attributes)) made.setAttribute(key, String(value));
    if (text !== undefined) made.textContent = text;
    return made;
  };
  const drawing = node("svg", { width, height, viewBox: "0 0 " + width + " " + height, "font-family": "Inter, Segoe UI, Helvetica, Arial, sans-serif", "font-size": 14 });
  const span = duration(events);
  drawing.append(
    node("rect", { width, height, fill: paper }),
    node("text", { x: 20, y: 36, "font-size": 20, "font-weight": 700, fill: ink }, title.value.trim() || "Untitled incident"),
    node("text", { x: 20, y: 58, "font-size": 12, fill: muted }, plural(events.length, "event") + (span ? ", " + span + " from the first to the last" : "")),
  );
  const body = node("g", { transform: "translate(0 " + top + ")" });
  body.append(node("line", { x1: plan.spine, x2: plan.spine, y1: 8, y2: plan.height - 8, stroke: kit.color("base-content", 0.15), "stroke-width": 2 }));
  for (const day of plan.days) {
    body.append(node("text", { x: plan.textX, y: day.y, fill: muted, "font-weight": 600, "font-size": 12 }, dayLabel(day.label)));
  }
  for (const row of plan.rows) {
    const tone = lookOf(row.event.kind).tone;
    body.append(
      node("text", { x: plan.spine - 14, y: row.y + 5, "text-anchor": "end", fill: muted, "font-family": "Consolas, Menlo, monospace", "font-size": 13 }, row.clock),
      node("circle", { cx: plan.spine, cy: row.y, r: 6, fill: kit.color(tone), stroke: paper, "stroke-width": 3 }),
    );
    const head = node("text", { x: plan.textX, y: row.y + 5, fill: ink, "font-weight": 600 }, row.who);
    head.append(node("tspan", { dx: 8, fill: kit.color("color-mix(in oklab, " + tone + " 75%, var(--color-base-content))"), "font-weight": 500 }, kindOf(row.event.kind).label));
    body.append(head);
    row.lines.forEach((line, index) => body.append(node("text", { x: plan.textX, y: row.y + 5 + plan.lineHeight * (index + 1), fill: ink }, line)));
  }
  drawing.append(body);
  return new XMLSerializer().serializeToString(drawing);
}

const EXPORTS = {
  markdown: (events) => {
    const name = fileName(title.value);
    download(name, postmortem(title.value.trim(), events), "text/markdown;charset=utf-8");
    return "Downloaded " + name + ". The questions in it are for you to answer.";
  },
  copy: async (events) => {
    try {
      await navigator.clipboard.writeText(postmortem(title.value.trim(), events));
    } catch (error) {
      throw new Error("The browser did not allow copying. Use the download instead.");
    }
    return "The post-mortem skeleton is on the clipboard.";
  },
  picture: (events) => {
    const name = fileName(title.value).replace(/^post-mortem/, "timeline").replace(/\.md$/, ".svg");
    download(name, picture(events), "image/svg+xml;charset=utf-8");
    return "Downloaded " + name + ", a picture to paste into the incident channel.";
  },
};

for (const choice of document.querySelectorAll("[data-export]")) {
  choice.addEventListener("click", async () => {
    if ($("export-menu").hidePopover) $("export-menu").hidePopover();
    if (state.editing) closeEditor(state.editing, { focus: false });
    const problem = firstProblem(false) || (state.events.length === 0 ? { message: "There are no events to export yet." } : null);
    if (problem) {
      stopAt(problem);
      return;
    }
    try {
      const done = await kit.busy($("export-button"), async () => EXPORTS[choice.dataset.export](cleanEvents()));
      kit.toast(done, { kind: "success" });
    } catch (error) {
      kit.toast(error.message, { kind: "error" });
    }
  });
}

// --- The saved timelines ---------------------------------------------------------------------------

function fillSaved(item, timeline) {
  const current = timeline.id === state.id;
  item.classList.toggle("is-current", current);
  const when = new Date(timeline.saved_at);
  item.replaceChildren(
    h(
      "button.it-saved-open",
      { type: "button", "aria-current": current ? "true" : null, onclick: (event) => openSaved(timeline.id, event.currentTarget) },
      h("span.it-saved-icon", { "aria-hidden": "true" }, kit.icon(current ? "file-pen-line" : "file-clock", "size-4")),
      h(
        "span.min-w-0.grow",
        {},
        h("span.it-saved-title", { text: timeline.title }),
        h("span.it-saved-meta", {}, plural(timeline.events, "event") + (Number.isNaN(when.getTime()) ? "" : " \u00b7 saved " + kit.timeAgo(when))),
      ),
      current ? h("span.badge.badge-xs.badge-soft.badge-primary", {}, "Open") : null,
    ),
    h(
      "button.btn.btn-ghost.btn-xs.btn-square.it-saved-delete",
      { type: "button", "aria-label": "Delete " + timeline.title, title: "Delete", onclick: (event) => deleteSaved(timeline, event.currentTarget) },
      kit.icon("trash-2", "size-3.5"),
    ),
  );
}

function showSaved(flashId) {
  const list = $("saved");
  if (!state.signedIn || !state.savedLoaded) return;
  if (list.querySelector(".skeleton")) list.replaceChildren();
  const seen = new Set();
  state.saved.forEach((timeline, index) => {
    seen.add(timeline.id);
    if (!savedItems.has(timeline.id)) savedItems.set(timeline.id, h("li.it-saved-item"));
    const item = savedItems.get(timeline.id);
    fillSaved(item, timeline);
    if (list.children[index] !== item) list.insertBefore(item, list.children[index] || null);
  });
  for (const [id, item] of savedItems) {
    if (!seen.has(id)) {
      savedItems.delete(id);
      item.remove();
    }
  }
  const none = state.saved.length === 0;
  $("saved-empty").hidden = !none;
  if (none) {
    $("saved-empty").replaceChildren(
      h("span.efp-empty-icon", {}, kit.icon("archive")),
      h("strong", {}, "Nothing kept yet"),
      h("span.text-sm", {}, "Save a timeline to find it here. What you save is yours alone: other members do not see it."),
    );
  }
  $("saved-status").textContent = none ? "Only you see them" : plural(state.saved.length, "timeline") + " of at most " + state.savedLimit + ". Only you see them.";
  if (flashId && savedItems.has(flashId)) kit.flash(savedItems.get(flashId));
}

async function loadSaved(button, flashId) {
  $("refresh-button").hidden = !state.signedIn;
  if (!state.signedIn) {
    $("saved").replaceChildren();
    $("saved-status").textContent = "You are not signed in";
    $("saved-empty").hidden = false;
    $("saved-empty").replaceChildren(
      h("span.efp-empty-icon", {}, kit.icon("lock")),
      h("strong", {}, "Nothing is kept for a guest"),
      h("span.text-sm", {}, "A timeline can be built and exported here, but not kept: sign in to save one."),
    );
    return;
  }
  try {
    const work = () => answer(fetch("api/timelines"));
    const found = await (button ? kit.busy(button, work) : work());
    state.saved = found.timelines;
    state.savedLimit = found.limit;
    state.savedLoaded = true;
    showSaved(flashId);
  } catch (error) {
    if ($("saved").querySelector(".skeleton")) $("saved").replaceChildren();
    $("saved-status").textContent = error.message;
  }
}

async function openSaved(id, button) {
  if (state.id === id && !state.dirty) {
    title.focus();
    intoView($("board"));
    return;
  }
  if (!(await mayReplace())) return;
  try {
    const timeline = await kit.busy(button, () => answer(fetch("api/timelines/" + encodeURIComponent(id))), { tick: false });
    if (!timeline) return; // a second click while the first was loading
    show(timeline, false, { drop: true });
    intoView($("board"));
  } catch (error) {
    kit.toast(error.message, { kind: "error" });
    loadSaved();
  }
}

async function deleteSaved(timeline, button) {
  const sure = await ask('Delete "' + timeline.title + '"?', "It is gone for good: nobody can open it again.", "Delete", { danger: true, icon: "trash-2" });
  if (!sure) return;
  try {
    await kit.busy(button, () => answer(fetch("api/timelines/" + encodeURIComponent(timeline.id), { method: "DELETE" })), { tick: false });
    if (timeline.id === state.id) {
      // What is on the page stays, as a timeline that is not saved.
      state.id = null;
      state.version = 0;
      state.dirty = true;
      refresh();
      kit.toast("Deleted from your timelines. What you see is not saved any more: save it to keep it.", { kind: "info", timeout: 7000 });
    } else {
      kit.toast('"' + timeline.title + '" is deleted.', { kind: "success" });
    }
    state.saved = state.saved.filter((kept) => kept.id !== timeline.id);
    showSaved();
  } catch (error) {
    kit.toast(error.message, { kind: "error" });
  }
}

$("refresh-button").addEventListener("click", (event) => loadSaved(event.currentTarget));

// --- Start --------------------------------------------------------------------------------------

async function loadState() {
  try {
    const found = await answer(fetch("api/state"));
    state.limits = found.limits;
    state.signedIn = found.signed_in;
    state.modelUsable = found.model.usable;
    if (found.name) $("me").replaceChildren(kit.avatar(found.name, { size: "2rem" }));
    const pill = $("model");
    pill.hidden = false;
    pill.className = "it-model badge badge-soft gap-1.5 " + (!found.model.usable ? "badge-neutral" : found.model.message ? "badge-warning" : "badge-success");
    pill.replaceChildren(
      kit.icon(found.model.usable ? "sparkles" : "plug-zap", "size-3.5"),
      !found.model.usable ? "Model off" : found.model.message ? "Budget used up" : "Model on",
    );
    if (found.model.message) {
      // The model is off, the app has no token yet, or today's budget is spent.
      alertIn($("notice"), found.model.usable ? found.model.message : found.model.message + " You can still build a timeline by hand.", found.model.usable ? "warning" : "info");
    }
  } catch (error) {
    alertIn($("notice"), error.message, "error");
  }
  $("extract-button").disabled = !state.modelUsable;
  showNotes();
  loadSaved();
}

// The drawing of a time that passed ("saved 3 minutes ago") is refreshed when the page is looked at again.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    refresh();
    showSaved();
  }
});

// Leaving with changes that are not saved: the browser asks first.
window.addEventListener("beforeunload", (event) => {
  if (state.dirty && state.events.length > 0) {
    event.preventDefault();
    event.returnValue = ""; // older browsers ask only when this is set
  }
});

await kit.ready();
kit.themeSwitch($("theme"));
kit.animateList($("saved")); // a timeline saved or deleted slides in or out of the list
loadState();
