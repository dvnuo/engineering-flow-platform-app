// Every URL in this file is relative ("api/extract", not "/api/extract"):
// the app is served under /{slug}/ once deployed.
//
// Nothing here asks the server on a timer: the saved timelines are loaded
// when the page opens and after something the visitor did. Whatever came
// from notes or from the model is put into the page as text (textContent,
// SVG text nodes, input values), never as markup.

import {
  KINDS,
  cleanTime,
  eventProblem,
  extractSummary,
  fileName,
  layout,
  postmortem,
  sortEvents,
  suggestedTitle,
} from "./timeline.js";

const notice = document.getElementById("notice");
const notesForm = document.getElementById("notes-form");
const notes = document.getElementById("notes");
const notesCount = document.getElementById("notes-count");
const extractButton = document.getElementById("extract-button");
const blankButton = document.getElementById("blank-button");
const extractStatus = document.getElementById("extract-status");
const timelineSection = document.getElementById("timeline-section");
const title = document.getElementById("title");
const legend = document.getElementById("legend");
const drawing = document.getElementById("drawing");
const empty = document.getElementById("empty");
const eventsList = document.getElementById("events");
const addButton = document.getElementById("add-button");
const undoButton = document.getElementById("undo-button");
const saveButton = document.getElementById("save-button");
const exportButton = document.getElementById("export-button");
const copyButton = document.getElementById("copy-button");
const timelineStatus = document.getElementById("timeline-status");
const savedStatus = document.getElementById("saved-status");
const savedList = document.getElementById("saved");
const refreshButton = document.getElementById("refresh-button");

// The name of the SVG namespace: an identifier, not an address that is fetched.
const SVG = "http://www.w3.org/2000/svg";

const state = {
  limits: { notes: 16000, events: 100, actor: 60, text: 200, title: 120 }, // replaced by what the server says
  signedIn: false,
  modelUsable: false,
  events: [], // in the order of the rows of the editor
  id: null, // of the saved timeline that is open, if any
  version: 0, // the version of it that this page opened
  dirty: false,
  removed: null, // the event removed last, for "Undo"
};
const rows = new Map(); // event -> the controls of its row
let drawQueued = false;
let drawnWidth = 0;

// --- Small helpers -----------------------------------------------------------

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

function say(element, text, isProblem) {
  element.textContent = text;
  element.classList.toggle("problem", Boolean(isProblem));
}

function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

function sendJson(url, method, data) {
  return fetch(url, {
    method: method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

function today() {
  const now = new Date();
  const two = (number) => String(number).padStart(2, "0");
  return now.getFullYear() + "-" + two(now.getMonth() + 1) + "-" + two(now.getDate());
}

function svg(name, attributes, text) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes || {})) {
    node.setAttribute(key, value);
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function kindLabel(id) {
  const kind = KINDS.find((item) => item.id === id);
  return kind ? kind.label : id;
}

// --- The drawing -------------------------------------------------------------

function drawable() {
  return state.events.filter((event) => eventProblem(event, state.limits) === null);
}

function showLegend(events) {
  legend.replaceChildren();
  for (const kind of KINDS) {
    const item = document.createElement("li");
    const swatch = document.createElement("span");
    swatch.className = "swatch kind-" + kind.id;
    const count = events.filter((event) => event.kind === kind.id).length;
    item.append(swatch, kind.label + " (" + count + ")");
    legend.append(item);
  }
}

function draw() {
  drawQueued = false;
  const events = drawable();
  showLegend(events);
  empty.hidden = events.length > 0;
  drawing.hidden = events.length === 0;
  if (events.length === 0) {
    drawing.replaceChildren();
    drawnWidth = 0;
    return;
  }
  drawnWidth = Math.max(280, Math.floor(drawing.clientWidth));
  const plan = layout(events, drawnWidth);
  const picture = svg("svg", {
    width: plan.width,
    height: plan.height,
    viewBox: "0 0 " + plan.width + " " + plan.height,
    role: "group",
    "aria-label": "Timeline: " + plural(events.length, "event") + " in the order of time",
  });
  picture.append(svg("line", { class: "spine", x1: plan.spine, x2: plan.spine, y1: 8, y2: plan.height - 8 }));
  for (const day of plan.days) {
    picture.append(svg("text", { class: "day", x: plan.textX, y: day.y }, day.label));
  }
  for (const row of plan.rows) {
    const label = kindLabel(row.event.kind);
    const group = svg("g", {
      class: "event kind-" + row.event.kind,
      tabindex: "0",
      role: "button",
      "aria-label": row.event.time + ", " + row.who + ", " + label + ": " + row.event.text + ". Edit.",
    });
    group.append(
      svg("text", { class: "clock", x: plan.spine - 14, y: row.y + 5 }, row.clock),
      svg("circle", { class: "dot", cx: plan.spine, cy: row.y, r: 7 })
    );
    const head = svg("text", { x: plan.textX, y: row.y + 5 });
    head.append(svg("tspan", { class: "who" }, row.who), svg("tspan", { class: "kind", dx: 8 }, label));
    group.append(head);
    row.lines.forEach((line, index) => {
      group.append(svg("text", { x: plan.textX, y: row.y + 5 + plan.lineHeight * (index + 1) }, line));
    });
    // The drawing is the way into the editor: a click, Enter or Space opens the event's row.
    group.addEventListener("click", () => focusRow(row.event));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        focusRow(row.event);
      }
    });
    picture.append(group);
  }
  drawing.replaceChildren(picture);
}

// Typing redraws at most once per frame.
function queueDraw() {
  if (!drawQueued) {
    drawQueued = true;
    requestAnimationFrame(draw);
  }
}

// --- The editor --------------------------------------------------------------

function textInput(value, label, maxLength) {
  const input = document.createElement("input");
  input.type = "text";
  input.value = value;
  input.maxLength = maxLength;
  input.placeholder = label;
  input.autocomplete = "off";
  input.setAttribute("aria-label", label);
  return input;
}

function checkRow(event) {
  const row = rows.get(event);
  if (!row) {
    return;
  }
  const problem = eventProblem(event, state.limits);
  const text = String(event.text || "").trim();
  row.time.setAttribute("aria-invalid", String(cleanTime(event.time) === null));
  row.text.setAttribute("aria-invalid", String(!text || text.length > state.limits.text));
  row.note.hidden = problem === null;
  row.note.textContent = problem === null ? "" : "Not drawn or saved yet: " + problem + ".";
}

function touched(event) {
  state.dirty = true;
  checkRow(event);
  queueDraw();
}

function editorRow(event) {
  const item = document.createElement("li");
  const time = textInput(event.time, "Time: 14:05 or 2026-11-12 14:05", 20);
  const actor = textInput(event.actor, "Who", state.limits.actor);
  const kind = document.createElement("select");
  kind.setAttribute("aria-label", "Kind");
  for (const choice of KINDS) {
    const option = document.createElement("option");
    option.value = choice.id;
    option.textContent = choice.label;
    kind.append(option);
  }
  kind.value = event.kind;
  const text = textInput(event.text, "What happened", state.limits.text);
  text.className = "what";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "quiet";
  remove.textContent = "Remove";
  const note = document.createElement("p");
  note.className = "row-problem problem";
  note.hidden = true;
  item.append(time, actor, kind, text, remove, note);
  rows.set(event, { item, time, text, note });

  time.addEventListener("input", () => {
    event.time = time.value;
    touched(event);
  });
  // When the field is left (or Enter is pressed), the time takes its kept
  // form and the row moves to its place in the order of time.
  time.addEventListener("change", () => {
    const clean = cleanTime(time.value);
    if (clean !== null) {
      event.time = clean;
      time.value = clean;
      reorder();
    }
  });
  actor.addEventListener("input", () => {
    event.actor = actor.value;
    touched(event);
  });
  kind.addEventListener("change", () => {
    event.kind = kind.value;
    touched(event);
  });
  text.addEventListener("input", () => {
    event.text = text.value;
    touched(event);
  });
  remove.addEventListener("click", () => removeEvent(event));
  return item;
}

function buildEditor() {
  rows.clear();
  eventsList.replaceChildren();
  for (const event of state.events) {
    eventsList.append(editorRow(event));
    checkRow(event);
  }
}

function reorder() {
  const focused = document.activeElement;
  state.events = sortEvents(state.events);
  for (const event of state.events) {
    eventsList.append(rows.get(event).item); // appending a node that is in the list moves it
  }
  if (focused && focused !== document.activeElement && eventsList.contains(focused)) {
    focused.focus();
  }
}

function focusRow(event) {
  const row = rows.get(event);
  if (row) {
    row.item.scrollIntoView({ block: "center" });
    row.text.focus();
  }
}

function removeEvent(event) {
  const index = state.events.indexOf(event);
  if (index === -1) {
    return;
  }
  state.events.splice(index, 1);
  state.removed = { event, index };
  state.dirty = true;
  rows.get(event).item.remove();
  rows.delete(event);
  undoButton.hidden = false;
  say(timelineStatus, "Removed. It is gone for good when you save.");
  queueDraw();
  undoButton.focus();
}

function show(timeline, dirty) {
  state.events = timeline.events.map((event) => ({
    time: event.time,
    actor: event.actor || "",
    text: event.text,
    kind: event.kind,
  }));
  state.id = timeline.id;
  state.version = timeline.version;
  state.dirty = dirty;
  state.removed = null;
  undoButton.hidden = true;
  title.value = timeline.title;
  timelineSection.hidden = false; // before drawing: a hidden box has no width to draw into
  buildEditor();
  draw();
  saveButton.textContent = state.id ? "Save the changes" : "Save";
}

function mayReplace() {
  return (
    !state.dirty ||
    state.events.length === 0 ||
    window.confirm("Replace the timeline on this page? It has changes that are not saved.")
  );
}

// What keeps the timeline from being saved or exported, as a sentence.
function firstProblem(needsTitle) {
  if (needsTitle && !title.value.trim()) {
    title.focus();
    return "Give the timeline a title first.";
  }
  if (state.events.length === 0) {
    return "There are no events yet.";
  }
  if (state.events.length > state.limits.events) {
    return "A timeline holds at most " + state.limits.events + " events.";
  }
  for (const event of state.events) {
    if (eventProblem(event, state.limits) !== null) {
      focusRow(event);
      return "Correct the marked event first, or remove it.";
    }
  }
  return null;
}

function cleanEvents() {
  return sortEvents(state.events).map((event) => ({
    time: cleanTime(event.time),
    actor: event.actor.trim(),
    text: event.text.trim(),
    kind: event.kind,
  }));
}

// --- Talking to the app ------------------------------------------------------

function showNotice(text) {
  notice.textContent = text;
  notice.hidden = false;
}

function countNotes() {
  const length = notes.value.length;
  notesCount.textContent =
    length > state.limits.notes
      ? "Only the first " + state.limits.notes.toLocaleString("en") + " of these " + length.toLocaleString("en") +
        " characters will be read."
      : "";
}

async function loadState() {
  try {
    const response = await fetch("api/state");
    if (!response.ok) {
      showNotice(await errorMessage(response));
      return;
    }
    const found = await response.json();
    state.limits = found.limits;
    state.signedIn = found.signed_in;
    state.modelUsable = found.model.usable;
    title.maxLength = found.limits.title;
    if (found.model.message) {
      // The model is off, the app has no token yet, or today's budget is spent.
      showNotice(found.model.usable ? found.model.message : found.model.message + " You can still build a timeline by hand.");
    }
  } catch (error) {
    showNotice("The app cannot be reached right now.");
  }
  extractButton.disabled = !state.modelUsable;
  countNotes();
  loadSaved();
}

async function extract() {
  if (!notes.value.trim()) {
    say(extractStatus, "Paste the notes of an incident first.", true);
    notes.focus();
    return;
  }
  if (!mayReplace()) {
    return;
  }
  extractButton.disabled = true;
  say(extractStatus, "Reading the notes... This can take half a minute.");
  try {
    // The server reads the beginning anyway; sending less keeps the request small.
    const response = await sendJson("api/extract", "POST", { text: notes.value.slice(0, state.limits.notes * 4) });
    if (!response.ok) {
      say(extractStatus, await errorMessage(response), true);
      return;
    }
    const result = await response.json();
    say(extractStatus, extractSummary(result));
    if (result.events.length > 0) {
      show({ id: null, version: 0, title: suggestedTitle(result.events, today()), events: result.events }, true);
      say(timelineStatus, "Not saved yet.");
      timelineSection.scrollIntoView({ block: "start" });
    }
  } catch (error) {
    say(extractStatus, "The app cannot be reached right now.", true);
  } finally {
    extractButton.disabled = !state.modelUsable;
  }
}

async function save() {
  const problem = firstProblem(true);
  if (problem) {
    say(timelineStatus, problem, true);
    return;
  }
  saveButton.disabled = true;
  say(timelineStatus, "Saving...");
  try {
    const body = { title: title.value.trim(), events: cleanEvents(), version: state.version };
    const response = state.id
      ? await sendJson("api/timelines/" + encodeURIComponent(state.id), "PUT", body)
      : await sendJson("api/timelines", "POST", body);
    if (!response.ok) {
      say(timelineStatus, await errorMessage(response), true);
      return;
    }
    show(await response.json(), false);
    say(timelineStatus, "Saved. Only you can open it.");
    loadSaved();
  } catch (error) {
    say(timelineStatus, "The app cannot be reached right now.", true);
  } finally {
    saveButton.disabled = !state.signedIn;
  }
}

function when(saved) {
  const date = new Date(saved);
  return Number.isNaN(date.getTime()) ? "" : ", saved " + date.toLocaleString();
}

function showSaved(found) {
  savedList.replaceChildren();
  for (const timeline of found.timelines) {
    const item = document.createElement("li");
    const name = document.createElement("button");
    name.type = "button";
    name.className = "name" + (timeline.id === state.id ? " current" : "");
    name.textContent = timeline.title;
    name.addEventListener("click", () => openSaved(timeline.id));
    const facts = document.createElement("span");
    facts.className = "muted";
    facts.textContent = plural(timeline.events, "event") + when(timeline.saved_at) + (timeline.id === state.id ? " (open)" : "");
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "quiet";
    remove.textContent = "Delete";
    remove.setAttribute("aria-label", "Delete " + timeline.title);
    remove.addEventListener("click", () => deleteSaved(timeline));
    item.append(name, facts, remove);
    savedList.append(item);
  }
  say(
    savedStatus,
    found.timelines.length === 0
      ? "Nothing saved yet. What you save is yours alone: other members do not see it."
      : plural(found.timelines.length, "timeline") + " of at most " + found.limit + ". Only you see them."
  );
}

async function loadSaved() {
  refreshButton.hidden = !state.signedIn;
  saveButton.disabled = !state.signedIn;
  if (!state.signedIn) {
    savedList.replaceChildren();
    say(savedStatus, "You are not signed in: a timeline can be built and downloaded here, but not kept.");
    return;
  }
  try {
    const response = await fetch("api/timelines");
    if (!response.ok) {
      say(savedStatus, await errorMessage(response), true);
      return;
    }
    showSaved(await response.json());
  } catch (error) {
    say(savedStatus, "The app cannot be reached right now.", true);
  }
}

async function openSaved(id) {
  if (!mayReplace()) {
    return;
  }
  try {
    const response = await fetch("api/timelines/" + encodeURIComponent(id));
    if (!response.ok) {
      say(savedStatus, await errorMessage(response), true);
      loadSaved();
      return;
    }
    show(await response.json(), false);
    say(timelineStatus, "Opened.");
    loadSaved();
    timelineSection.scrollIntoView({ block: "start" });
    title.focus();
  } catch (error) {
    say(savedStatus, "The app cannot be reached right now.", true);
  }
}

async function deleteSaved(timeline) {
  if (!window.confirm('Delete "' + timeline.title + '"? This cannot be undone.')) {
    return;
  }
  try {
    const response = await fetch("api/timelines/" + encodeURIComponent(timeline.id), { method: "DELETE" });
    if (!response.ok) {
      say(savedStatus, await errorMessage(response), true);
      return;
    }
    if (timeline.id === state.id) {
      // What is on the page stays, as a timeline that is not saved.
      state.id = null;
      state.version = 0;
      state.dirty = true;
      saveButton.textContent = "Save";
      say(timelineStatus, "Deleted from your saved timelines. What you see here is not saved any more.");
    }
    loadSaved();
  } catch (error) {
    say(savedStatus, "The app cannot be reached right now.", true);
  }
}

function markdown() {
  const problem = firstProblem(false);
  if (problem) {
    say(timelineStatus, problem, true);
    return null;
  }
  return postmortem(title.value.trim(), cleanEvents());
}

// --- Events of the page ------------------------------------------------------

notesForm.addEventListener("submit", (event) => {
  event.preventDefault();
  extract();
});

notes.addEventListener("input", countNotes);

blankButton.addEventListener("click", () => {
  if (mayReplace()) {
    show({ id: null, version: 0, title: "", events: [] }, false);
    say(timelineStatus, "");
    title.focus();
  }
});

title.addEventListener("input", () => {
  state.dirty = true;
});

addButton.addEventListener("click", () => {
  if (state.events.length >= state.limits.events) {
    say(timelineStatus, "A timeline holds at most " + state.limits.events + " events.", true);
    return;
  }
  const sorted = sortEvents(drawable());
  // A new event starts at the time of the last one: usually it comes after it.
  const event = { time: sorted.length ? sorted[sorted.length - 1].time : "", actor: "", text: "", kind: "note" };
  state.events.push(event);
  state.dirty = true;
  eventsList.append(editorRow(event));
  checkRow(event);
  const row = rows.get(event);
  (event.time ? row.text : row.time).focus();
});

undoButton.addEventListener("click", () => {
  if (state.removed) {
    state.events.splice(state.removed.index, 0, state.removed.event);
    state.removed = null;
    buildEditor();
    queueDraw();
    say(timelineStatus, "The event is back.");
  }
  undoButton.hidden = true;
  addButton.focus();
});

saveButton.addEventListener("click", save);

exportButton.addEventListener("click", () => {
  const text = markdown();
  if (text === null) {
    return;
  }
  // The file is made in the browser from what is on the page: nothing is asked from the server.
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName(title.value);
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  say(timelineStatus, "Downloaded " + link.download + ". The questions in it are for you to answer.");
});

copyButton.addEventListener("click", async () => {
  const text = markdown();
  if (text === null) {
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    say(timelineStatus, "The post-mortem skeleton is on the clipboard.");
  } catch (error) {
    say(timelineStatus, "The browser did not allow copying. Use the download instead.", true);
  }
});

refreshButton.addEventListener("click", loadSaved);

// The drawing follows the width of the page. An observer, not a timer.
if ("ResizeObserver" in window) {
  new ResizeObserver(() => {
    if (!drawing.hidden && Math.max(280, Math.floor(drawing.clientWidth)) !== drawnWidth) {
      queueDraw();
    }
  }).observe(drawing);
}

// Leaving with changes that are not saved: the browser asks first.
window.addEventListener("beforeunload", (event) => {
  if (state.dirty && state.events.length > 0) {
    event.preventDefault();
  }
});

loadState();
