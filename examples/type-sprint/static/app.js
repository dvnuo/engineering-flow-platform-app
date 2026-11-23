// The page: the text box, the clock on the screen and the talk with the
// server. The sprint itself is sprint.js; the charts are charts.js.
//
// Every URL in this file is relative ("api/runs", not "/api/runs"): the app
// is served under /{slug}/ once deployed. Nothing here asks the server on a
// timer: the history and the leaderboard are loaded when the page opens,
// after a sprint and on "Refresh".
//
// The look comes from the EFP kit (/_efp/lib/, docs/UI_KIT.md). The motion
// follows the typing: a finished line lifts away and the next one rises, a
// wrong key nudges the line, the last ten seconds turn red, and the result
// counts up, its bars grow and a new best gets confetti. A visitor who asked
// for less motion gets the same page without the movement.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import {
  accuracy,
  elapsedMs,
  localResult,
  marks,
  netChars,
  newSprint,
  randomSource,
  report,
  speedSeries,
  tick,
  type,
  wordsPerMinute,
  worstLine,
} from "./sprint.js";
import { drawHistory, drawSpeed } from "./charts.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const topics = $("topics");
const startButton = $("start");
const againButton = $("again");
const typing = $("typing");
const line = $("line");
const LOW_SECONDS = 10;

let config = null; // what GET api/snippets answered: the lines, the numbers of a sprint, who is visiting
let sprint = null; // the sprint in the text box
let run = null; // its id on the server, when it is saved and ranked
let shownLine = null; // the id of the line whose characters are on the screen
let shownDone = 0; // how many lines were finished when the line on the screen came
let starting = false;

// --- The server ------------------------------------------------------------------------

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function ask(url, body) {
  const request =
    body === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let response;
  try {
    response = await fetch(url, request);
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

// --- Small helpers -----------------------------------------------------------------------

function write(node, text) {
  if (node.textContent !== text) {
    node.textContent = text; // called on every frame: touch the page only for a change
  }
}

function still() {
  return kit.reducedMotion();
}

function say(text) {
  const status = $("status");
  status.hidden = !text;
  status.replaceChildren(text ? h("div", { class: "alert alert-soft alert-warning text-sm" }, kit.icon("info", "size-4"), h("span", { text })) : "");
}

function topicLabel(id) {
  if (id === config.everything) {
    return "Everything";
  }
  const topic = config.topics.find((candidate) => candidate.id === id);
  return topic ? topic.label : String(id || "");
}

function day(at) {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function describe(result) {
  return (
    [day(result.at), topicLabel(result.topic)].filter(Boolean).join(", ") + ": " + result.wpm.toFixed(1) +
    " words a minute, " + result.accuracy.toFixed(1) + "% accurate" + (result.ranked ? "" : " (not ranked)")
  );
}

function linesOf(topicId) {
  const chosen = config.topics.filter((topic) => topicId === config.everything || topic.id === topicId);
  return chosen.flatMap((topic) => topic.snippets);
}

function textOf(lineId) {
  const found = linesOf(config.everything).find((candidate) => candidate.id === lineId);
  return found ? found.text : "";
}

// One of the three cards is on the screen: choosing, typing, or the result.
function showView(name) {
  for (const id of ["setup", "sprint", "result"]) {
    $(id).hidden = id !== name;
  }
  kit.rise($(name), { distance: 10, duration: 480 });
}

// --- Choosing a topic -------------------------------------------------------------------------

let sampleToken = 0;

// A line of the chosen topic types itself out: a taste of what is coming.
function showSample() {
  const lines = linesOf(chosenTopic());
  const text = lines.length ? lines[Math.floor(Math.random() * lines.length)].text : "";
  const sample = $("sample");
  const token = ++sampleToken;
  if (still()) {
    sample.textContent = text;
    return;
  }
  const began = performance.now();
  const step = (now) => {
    if (token !== sampleToken) return; // another topic was chosen meanwhile
    const shown = Math.min(text.length, Math.floor((now - began) / 22));
    sample.textContent = text.slice(0, shown) || "\u00a0";
    if (shown < text.length) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function showTopics() {
  const choices = [{ id: config.everything, label: "Everything", snippets: linesOf(config.everything) }].concat(config.topics);
  topics.replaceChildren(
    h("legend", { class: "sr-only", text: "Topic" }),
    ...choices.map((topic) => {
      const radio = h("input", { type: "radio", name: "topic", value: topic.id });
      radio.checked = topic.id === config.everything;
      radio.addEventListener("change", showSample);
      return h("label", { class: "ts-topic" }, radio, h("span", { text: topic.label }), h("small", { text: String(topic.snippets.length) }));
    }),
  );
  kit.appear(topics.querySelectorAll(".ts-topic"), { stagger: 35 });
  showSample();
}

function chosenTopic() {
  const checked = topics.querySelector("input:checked");
  return checked ? checked.value : config.everything;
}

// --- The sprint on the screen --------------------------------------------------------------------

// The line, one element per character, and how each of them stands.
function showLine() {
  if (shownLine !== sprint.current.id) {
    const finished = shownLine !== null && sprint.completed.length > shownDone;
    if (finished && !still()) {
      // The finished line lifts away while the next one rises into its place.
      const ghost = line.cloneNode(true);
      ghost.removeAttribute("id");
      ghost.classList.add("ts-ghost");
      Array.from(ghost.children).forEach((span) => (span.className = "right"));
      line.parentElement.append(ghost);
      ghost.animate([{ transform: "none", opacity: 1 }, { transform: "translateY(-1.6rem)", opacity: 0 }], { duration: 420, easing: kit.EASE }).finished.then(() => ghost.remove(), () => ghost.remove());
      plus("+" + textOf(sprint.completed[sprint.completed.length - 1]).length);
      kit.pop($("lines"), { scale: 1.3 });
    }
    shownLine = sprint.current.id;
    shownDone = sprint.completed.length;
    line.replaceChildren(...Array.from(sprint.current.text, (character) => h("span", { text: character })));
    typing.setAttribute("aria-label", "Type this line: " + sprint.current.text);
    if (finished && !still()) {
      line.animate([{ transform: "translateY(0.9rem)", opacity: 0 }, { transform: "none", opacity: 1 }], { duration: 420, easing: kit.EASE });
    }
  }
  const states = marks(sprint);
  Array.from(line.children).forEach((span, index) => {
    if (span.className !== states[index]) {
      span.className = states[index];
    }
  });
  write($("lines"), String(sprint.completed.length));
}

// "+34": the characters of a finished line float up from the box.
function plus(text) {
  const chip = h("span", { class: "ts-plus", text });
  typing.parentElement.append(chip);
  chip
    .animate([{ transform: "translateY(0) scale(0.8)", opacity: 0 }, { transform: "translateY(-0.6rem) scale(1.1)", opacity: 1, offset: 0.3 }, { transform: "translateY(-1.8rem)", opacity: 0 }], {
      duration: 900,
      easing: kit.EASE,
    })
    .finished.then(() => chip.remove(), () => chip.remove());
}

function showMeters() {
  const ms = elapsedMs(sprint, performance.now());
  const seconds = Math.ceil(Math.max(0, sprint.seconds * 1000 - ms) / 1000);
  const hit = accuracy(sprint);
  write($("time"), String(seconds));
  // The first two seconds would show a wild number: a few keys divided by almost no time.
  write($("wpm"), ms >= 2000 ? String(Math.round(wordsPerMinute(netChars(sprint), ms))) : "-");
  write($("accuracy"), hit === null ? "-" : Math.round(hit) + "%");
  const low = sprint.startedAt !== null && seconds <= LOW_SECONDS;
  $("time-stat").classList.toggle("is-low", low);
  $("timebar-track").classList.toggle("is-low", low);
  const scale = "scaleX(" + (seconds / sprint.seconds).toFixed(3) + ")"; // moves once a second
  const bar = $("timebar");
  if (bar.style.transform !== scale) {
    bar.style.transform = scale;
  }
}

// --- The result ----------------------------------------------------------------------------

function oneDecimal(value) {
  return (Math.round(value * 10) / 10).toFixed(1);
}

function growBars(svg) {
  if (still()) return;
  svg.querySelectorAll(".bar").forEach((bar, index) => {
    bar.animate([{ transform: "scaleY(0)" }, { transform: "scaleY(1)" }], { duration: 700, delay: 120 + index * 45, easing: kit.SPRING, fill: "backwards" });
  });
  svg.querySelectorAll(".average, .tick.strong").forEach((mark) => mark.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 500, delay: 800, fill: "backwards" }));
}

function showResult(result, done, animate) {
  const from = animate ? 0 : undefined;
  kit.countUp($("result-wpm"), result.wpm, { from, duration: 1300, format: oneDecimal });
  kit.countUp($("result-accuracy"), result.accuracy, { from, duration: 1100, format: (value) => oneDecimal(value) + "%" });
  kit.countUp($("result-chars"), result.chars, { from, duration: 1100 });
  kit.countUp($("result-mistakes"), result.mistakes, { from, duration: 1100 });
  $("headline").textContent = result.wpm.toFixed(1) + " words a minute, " + result.accuracy.toFixed(1) + "% accurate";
  const series = speedSeries(done.buckets, done.bucketSeconds);
  drawSpeed($("speed-chart"), series, done.bucketSeconds, result.wpm);
  if (animate) growBars($("speed-chart"));
  const fastest = Math.round(Math.max(...series));
  const slowest = Math.round(Math.min(...series));
  $("speed-text").textContent =
    "Between " + slowest + " and " + fastest + " words a minute, " + result.chars + " characters right and " + result.mistakes + " wrong keys.";
  const costly = worstLine(done);
  const worst = $("worst");
  worst.replaceChildren();
  if (costly) {
    // A line is text from a file, and still goes in as text.
    worst.append(h("code", { text: textOf(costly.id) }), costly.mistakes + (costly.mistakes === 1 ? " wrong key." : " wrong keys."));
  } else {
    worst.textContent = "None of them: not one wrong key.";
  }
}

function showHistory(runs, best, fresh) {
  $("history").hidden = false;
  $("history-loading").hidden = true;
  $("key-accuracy").textContent = String(config.min_accuracy);
  const chart = $("history-chart");
  chart.toggleAttribute("hidden", runs.length === 0); // an <svg> has no .hidden property, only the attribute
  $("history-key").hidden = runs.length === 0;
  if (runs.length === 0) {
    $("history-note").textContent = "No sprint yet. Your last 30 are drawn here, so you can watch yourself get faster.";
    return;
  }
  const speeds = runs.map((result) => result.wpm);
  $("history-note").textContent =
    (runs.length === 1 ? "One sprint" : runs.length + " sprints, from " + Math.min(...speeds).toFixed(1) + " to " + Math.max(...speeds).toFixed(1) + " words a minute") +
    (best ? ". Your best ranked: " + best.wpm.toFixed(1) + "." : ". None of them is ranked yet.");
  drawHistory(chart, runs, best, describe);
  if (still()) return;
  // The line draws itself from the oldest sprint to the newest; the newest dot lands last.
  const trend = chart.querySelector(".trend");
  if (trend && !fresh) {
    trend.setAttribute("pathLength", "1");
    trend.animate([{ strokeDasharray: "0 1" }, { strokeDasharray: "1 0" }], { duration: 1100, easing: kit.EASE });
  }
  const dots = chart.querySelectorAll(".dot");
  if (fresh) {
    kit.pop(dots[dots.length - 1], { scale: 2.2, duration: 700 });
  } else {
    kit.appear(dots, { stagger: 30, delay: 200 });
  }
}

// --- The leaderboard ---------------------------------------------------------------------------

const leaderItems = new Map(); // a name (and how often it came before) -> its <li>

function showBoard(board, fresh) {
  const seen = new Map();
  const items = board.top.map((entry) => {
    const count = (seen.get(entry.name) || 0) + 1;
    seen.set(entry.name, count);
    const key = entry.name + "#" + count;
    if (!leaderItems.has(key)) {
      leaderItems.set(
        key,
        h(
          "li",
          {},
          h("span", { class: "ts-rank efp-number" }),
          kit.avatar(entry.name || "?", { size: "1.85rem" }),
          h("span", { class: "min-w-0 grid" }, h("span", { class: "flex items-center gap-1.5 min-w-0" }, h("span", { class: "truncate font-medium", text: entry.name })), h("span", { class: "text-xs text-base-content/55 truncate" })),
          h("span", { class: "ts-speed" }, h("span"), h("small", { text: "wpm" })),
        ),
      );
    }
    const item = leaderItems.get(key);
    const rank = item.querySelector(".ts-rank");
    rank.dataset.place = String(entry.rank);
    rank.replaceChildren(entry.rank === 1 ? kit.icon("crown", "size-4") : String(entry.rank));
    rank.title = "Rank " + entry.rank;
    item.querySelector(".ts-speed > span").textContent = Number(entry.wpm).toFixed(1);
    item.querySelector(".grid > .text-xs").textContent =
      (typeof entry.accuracy === "number" ? entry.accuracy.toFixed(1) + "% accurate" : "") + (entry.topic ? ", " + topicLabel(entry.topic) : "");
    item.classList.toggle("is-you", entry.you);
    const name = item.querySelector(".grid > span");
    if (entry.you && name.children.length === 1) name.append(h("span", { class: "badge badge-xs badge-primary badge-soft", text: "you" }));
    if (!entry.you && name.children.length > 1) name.lastElementChild.remove();
    return item;
  });
  const list = $("leaders");
  items.forEach((item, index) => {
    if (list.children[index] !== item) list.insertBefore(item, list.children[index] || null);
  });
  while (list.children.length > items.length) list.lastElementChild.remove();
  list.hidden = board.top.length === 0;
  $("leaders-empty").hidden = board.top.length > 0;
  $("leaders-empty-text").textContent = "No ranked sprint yet: finish one with at least " + config.min_accuracy + "% accuracy and your name is here.";
  const note = $("board-note");
  if (board.top.length === 0) {
    note.textContent = "";
  } else if (board.you) {
    note.textContent = "Your best is rank " + board.you.rank + " of " + board.players + ".";
  } else if (board.signed_in) {
    note.textContent = "You have no ranked sprint yet: finish one with at least " + config.min_accuracy + "% accuracy.";
  } else {
    note.textContent = "You are not signed in: you can type, and the board lists the members who are.";
  }
  if (fresh) {
    const mine = items.find((item) => item.classList.contains("is-you"));
    if (mine) kit.flash(mine);
  }
}

async function loadBoard(button) {
  const work = () => ask("api/leaderboard");
  const answer = await (button ? kit.busy(button, work, { tick: false }) : work());
  if (!answer) return;
  if (answer.ok) {
    showBoard(answer.data, false);
  } else {
    $("board-note").textContent = answer.message;
    $("leaders").hidden = true;
  }
}

// --- The sprint ------------------------------------------------------------------------------

// The clock on the screen. The sprint ends by the time on the clock, not by
// a count of frames, so a tab that was hidden for a while ends it when it
// is looked at again.
function frame() {
  if (!sprint || sprint.startedAt === null) {
    return;
  }
  if (tick(sprint, performance.now())) {
    finish();
    return;
  }
  showMeters();
  requestAnimationFrame(frame);
}

async function start(button) {
  if (starting || sprint) return;
  starting = true;
  say("");
  const topic = chosenTopic();
  run = null;
  if (config.signed_in) {
    // The server notes when the sprint was opened; it will not take a result sooner than a sprint lasts.
    const answer = await kit.busy(button, () => ask("api/runs", { topic }), { tick: false });
    if (answer && answer.ok) {
      run = answer.data.id;
    } else if (answer) {
      say("This sprint will not be saved. " + answer.message);
    }
  }
  starting = false;
  const seed = window.crypto.getRandomValues(new Uint32Array(1))[0];
  sprint = newSprint(linesOf(topic), config.sprint_seconds, config.bucket_seconds, randomSource(seed));
  shownLine = null;
  shownDone = 0;
  showView("sprint");
  typing.disabled = false;
  typing.value = "";
  $("hint").textContent = "The clock starts with your first key. A line counts when all of it is right: fix a mistake with Backspace.";
  showLine();
  showMeters();
  typing.focus();
}

function stop() {
  sprint = null;
  run = null; // left open on the server; the next sprint gives it up
  showView("setup");
  say("Sprint stopped. Nothing was saved.");
  startButton.focus();
}

async function finish() {
  const done = sprint;
  const finished = run;
  sprint = null;
  run = null;
  typing.disabled = true;
  let result = localResult(done, config.min_accuracy);
  showView("result");
  showResult(result, done, true);
  $("result").focus({ preventScroll: true });
  const verdict = $("verdict");
  if (!finished) {
    verdict.textContent = config.signed_in ? "This sprint was not saved." : "You are not signed in, so this sprint is neither saved nor ranked.";
    againButton.focus({ preventScroll: true });
    return;
  }
  verdict.textContent = "Saving...";
  const answer = await kit.busy(againButton, () => ask("api/runs/" + finished + "/finish", report(done)), { tick: false });
  againButton.focus({ preventScroll: true });
  if (!answer || !answer.ok) {
    verdict.textContent = "This sprint was not saved. " + (answer ? answer.message : "");
    return;
  }
  result = answer.data.run; // the numbers the server worked out are the ones that count
  showResult(result, done, false);
  if (!result.ranked) {
    verdict.textContent = "Saved, but not ranked: the board takes sprints with at least " + config.min_accuracy + "% accuracy. Slower is faster.";
  } else if (answer.data.improved) {
    const place = answer.data.board.you;
    verdict.textContent = place.rank === 1 ? "A new best, and the fastest on the board." : "A new best for you: rank " + place.rank + " of " + answer.data.board.players + ".";
    kit.celebrate({ from: $("result-wpm"), particles: place.rank === 1 ? 200 : 140 });
  } else {
    verdict.textContent = "Saved. Your best stays " + answer.data.best.wpm.toFixed(1) + " words a minute.";
  }
  kit.appear(verdict);
  showHistory(answer.data.runs, answer.data.best, true);
  showBoard(answer.data.board, answer.data.improved);
}

// --- Input -----------------------------------------------------------------------------------

typing.addEventListener("input", () => {
  if (!sprint) {
    return;
  }
  const waiting = sprint.startedAt === null;
  const mistakes = sprint.mistakes;
  const kept = type(sprint, typing.value, performance.now());
  if (typing.value !== kept) {
    typing.value = kept;
  }
  if (sprint.refusedPaste) {
    sprint.refusedPaste = false;
    $("hint").textContent = "Pasting is off here: type the line.";
  }
  showLine();
  showMeters();
  // A wrong key nudges the line: no, not that one.
  if (sprint.mistakes > mistakes && !still()) {
    line.animate([0, -3, 3, -2, 0].map((x) => ({ transform: "translateX(" + x + "px)" })), { duration: 220, easing: "ease-out" });
  }
  if (waiting && sprint.startedAt !== null) {
    $("hint").textContent = "";
    requestAnimationFrame(frame);
  }
});

// No paste and no drop: a sprint is typed. sprint.js refuses a change that
// adds a whole word at once as well, for the ways around these three.
for (const name of ["paste", "drop"]) {
  typing.addEventListener(name, (event) => {
    event.preventDefault();
    $("hint").textContent = "Pasting is off here: type the line.";
  });
}
typing.addEventListener("beforeinput", (event) => {
  if (["insertFromPaste", "insertFromDrop", "insertFromYank"].includes(event.inputType)) {
    event.preventDefault();
  }
});

// The cursor stays at the end: Backspace is the only way back. Escape stops the sprint.
typing.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    stop();
  } else if (["ArrowLeft", "ArrowUp", "Home", "PageUp", "Enter"].includes(event.key)) {
    event.preventDefault();
  }
});
for (const name of ["focus", "pointerup", "select"]) {
  typing.addEventListener(name, () => {
    const end = typing.value.length;
    if (typing.selectionStart !== end || typing.selectionEnd !== end) {
      typing.setSelectionRange(end, end);
    }
  });
}

// Enter on a topic starts the sprint, as the button does.
topics.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !startButton.disabled) {
    event.preventDefault();
    start(startButton);
  }
});

startButton.addEventListener("click", () => start(startButton));
againButton.addEventListener("click", () => start(againButton));
$("change").addEventListener("click", () => {
  showView("setup");
  (topics.querySelector("input:checked") || startButton).focus();
});
$("stop").addEventListener("click", stop);
$("refresh").addEventListener("click", (event) => loadBoard(event.currentTarget));

// --- Start -------------------------------------------------------------------------------------

async function load() {
  const answer = await ask("api/snippets");
  if (!answer.ok) {
    $("who").textContent = "";
    say(answer.message);
    return;
  }
  config = answer.data;
  $("who").textContent = config.signed_in
    ? "Typing as " + config.name + ". " + config.sprint_seconds + " seconds of what engineers type; your sprints are kept."
    : "You are not signed in: you can type, but your sprints are not kept or ranked.";
  if (config.signed_in) $("me").append(kit.avatar(config.name, { size: "2rem" }));
  showTopics();
  startButton.disabled = false;
  loadBoard();
  // A guest's sprints are not kept: no history to show.
  $("history").hidden = !config.signed_in;
  if (config.signed_in) {
    const history = await ask("api/history");
    if (history.ok) {
      showHistory(history.data.runs, history.data.best, false);
    } else {
      $("history-loading").hidden = true;
      $("history-note").textContent = history.message;
    }
  }
}

await kit.ready();
kit.themeSwitch($("theme"));
kit.animateList($("leaders"));
load();
