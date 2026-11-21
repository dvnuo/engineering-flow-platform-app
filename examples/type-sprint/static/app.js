// The page: the text box, the clock on the screen and the talk with the
// server. The sprint itself is sprint.js; the charts are charts.js.
//
// Every URL in this file is relative ("api/runs", not "/api/runs"): the app
// is served under /{slug}/ once deployed. Nothing here asks the server on a
// timer: the history and the leaderboard are loaded when the page opens,
// after a sprint and on "Refresh".

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

const who = document.getElementById("who");
const topics = document.getElementById("topics");
const startButton = document.getElementById("start");
const status = document.getElementById("status");
const sprintSection = document.getElementById("sprint");
const timeText = document.getElementById("time");
const wpmText = document.getElementById("wpm");
const accuracyText = document.getElementById("accuracy");
const timeBar = document.getElementById("timebar");
const line = document.getElementById("line");
const typing = document.getElementById("typing");
const hint = document.getElementById("hint");
const stopButton = document.getElementById("stop");
const resultSection = document.getElementById("result");
const headline = document.getElementById("headline");
const verdict = document.getElementById("verdict");
const speedChart = document.getElementById("speed-chart");
const speedText = document.getElementById("speed-text");
const worst = document.getElementById("worst");
const historySection = document.getElementById("history");
const historyNote = document.getElementById("history-note");
const historyChart = document.getElementById("history-chart");
const historyKey = document.getElementById("history-key");
const keyAccuracy = document.getElementById("key-accuracy");
const boardNote = document.getElementById("board-note");
const leaders = document.getElementById("leaders");
const leaderRows = document.getElementById("leader-rows");
const refreshButton = document.getElementById("refresh");

let config = null; // what GET api/snippets answered: the lines, the numbers of a sprint, who is visiting
let sprint = null; // the sprint in the text box
let run = null; // its id on the server, when it is saved and ranked
let shownLine = null; // the id of the line whose characters are on the screen

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

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) {
    node.textContent = text;
  }
  if (className) {
    node.className = className;
  }
  return node;
}

function write(node, text) {
  if (node.textContent !== text) {
    node.textContent = text; // called on every frame: touch the page only for a change
  }
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

// --- What the page shows -------------------------------------------------------------------

function showTopics() {
  const choices = [{ id: config.everything, label: "Everything", snippets: linesOf(config.everything) }].concat(config.topics);
  for (const topic of choices) {
    const label = element("label");
    const radio = element("input");
    radio.type = "radio";
    radio.name = "topic";
    radio.value = topic.id;
    radio.checked = topic.id === config.everything;
    label.append(radio, element("span", topic.label), element("small", topic.snippets.length + " lines"));
    topics.append(label);
  }
}

function chosenTopic() {
  const checked = topics.querySelector("input:checked");
  return checked ? checked.value : config.everything;
}

// The line, one element per character, and how each of them stands.
function showLine() {
  if (shownLine !== sprint.current.id) {
    shownLine = sprint.current.id;
    line.replaceChildren(...Array.from(sprint.current.text, (character) => element("span", character)));
    typing.setAttribute("aria-label", "Type this line: " + sprint.current.text);
  }
  const states = marks(sprint);
  Array.from(line.children).forEach((span, index) => {
    if (span.className !== states[index]) {
      span.className = states[index];
    }
  });
}

function showMeters() {
  const ms = elapsedMs(sprint, performance.now());
  const seconds = Math.ceil(Math.max(0, sprint.seconds * 1000 - ms) / 1000);
  const hit = accuracy(sprint);
  write(timeText, String(seconds));
  // The first two seconds would show a wild number: a few keys divided by almost no time.
  write(wpmText, ms >= 2000 ? String(Math.round(wordsPerMinute(netChars(sprint), ms))) : "-");
  write(accuracyText, hit === null ? "-" : Math.round(hit) + "%");
  const scale = "scaleX(" + (seconds / sprint.seconds).toFixed(3) + ")"; // moves once a second
  if (timeBar.style.transform !== scale) {
    timeBar.style.transform = scale;
  }
}

function showResult(result, done) {
  headline.textContent = result.wpm.toFixed(1) + " words a minute, " + result.accuracy.toFixed(1) + "% accurate";
  const series = speedSeries(done.buckets, done.bucketSeconds);
  drawSpeed(speedChart, series, done.bucketSeconds, result.wpm);
  const fastest = Math.round(Math.max(...series));
  const slowest = Math.round(Math.min(...series));
  speedText.textContent =
    "Between " + slowest + " and " + fastest + " words a minute, " + result.chars + " characters right and " +
    result.mistakes + " wrong keys.";
  const costly = worstLine(done);
  worst.replaceChildren();
  if (costly) {
    // A line is text from a file, and still goes in as text.
    worst.append(element("code", textOf(costly.id)), costly.mistakes + (costly.mistakes === 1 ? " wrong key." : " wrong keys."));
  } else {
    worst.textContent = "None of them: not one wrong key.";
  }
  resultSection.hidden = false;
}

function showHistory(runs, best) {
  historySection.hidden = false;
  keyAccuracy.textContent = String(config.min_accuracy);
  historyChart.hidden = runs.length === 0;
  historyKey.hidden = runs.length === 0;
  if (runs.length === 0) {
    historyNote.textContent = "No sprint yet. Your last 30 are drawn here, so you can watch yourself get faster.";
    return;
  }
  const speeds = runs.map((result) => result.wpm);
  historyNote.textContent =
    (runs.length === 1 ? "One sprint" : runs.length + " sprints, from " + Math.min(...speeds).toFixed(1) + " to " +
      Math.max(...speeds).toFixed(1) + " words a minute") +
    (best ? ". Your best ranked sprint: " + best.wpm.toFixed(1) + " words a minute." : ". None of them is ranked yet.");
  drawHistory(historyChart, runs, best, describe);
}

function showBoard(board) {
  leaderRows.replaceChildren();
  for (const entry of board.top) {
    const row = element("tr", undefined, entry.you ? "you" : "");
    row.append(
      element("td", String(entry.rank), "number"),
      element("td", entry.name + (entry.you ? " (you)" : "")), // a name is text from elsewhere: textContent
      element("td", Number(entry.wpm).toFixed(1), "number"),
      element("td", typeof entry.accuracy === "number" ? entry.accuracy.toFixed(1) + "%" : "", "number"),
      element("td", topicLabel(entry.topic))
    );
    leaderRows.append(row);
  }
  leaders.hidden = board.top.length === 0;
  if (board.top.length === 0) {
    boardNote.textContent = "No ranked sprint yet. The first place is free.";
  } else if (board.you) {
    boardNote.textContent = "Your best is rank " + board.you.rank + " of " + board.players + ".";
  } else if (board.signed_in) {
    boardNote.textContent = "You have no ranked sprint yet: finish one with at least " + config.min_accuracy + "% accuracy.";
  } else {
    boardNote.textContent = "You are not signed in: you can type, and the board lists the members who are.";
  }
}

async function loadBoard() {
  refreshButton.disabled = true;
  const answer = await ask("api/leaderboard");
  refreshButton.disabled = false;
  if (answer.ok) {
    showBoard(answer.data);
  } else {
    boardNote.textContent = answer.message;
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

async function start() {
  startButton.disabled = true;
  status.textContent = "";
  const topic = chosenTopic();
  run = null;
  if (config.signed_in) {
    // The server notes when the sprint was opened; it will not take a result sooner than a sprint lasts.
    const answer = await ask("api/runs", { topic });
    if (answer.ok) {
      run = answer.data.id;
    } else {
      status.textContent = "This sprint will not be saved. " + answer.message;
    }
  }
  const seed = window.crypto.getRandomValues(new Uint32Array(1))[0];
  sprint = newSprint(linesOf(topic), config.sprint_seconds, config.bucket_seconds, randomSource(seed));
  shownLine = null;
  resultSection.hidden = true;
  sprintSection.hidden = false;
  typing.disabled = false;
  typing.value = "";
  hint.textContent = "The clock starts with your first key. A line counts when all of it is right: fix a mistake with Backspace.";
  showLine();
  showMeters();
  typing.focus();
}

function stop() {
  sprint = null;
  run = null; // left open on the server; the next sprint gives it up
  sprintSection.hidden = true;
  startButton.disabled = false;
  status.textContent = "Sprint stopped. Nothing was saved.";
  startButton.focus();
}

async function finish() {
  const done = sprint;
  const finished = run;
  sprint = null;
  run = null;
  typing.disabled = true;
  sprintSection.hidden = true;
  let result = localResult(done, config.min_accuracy);
  showResult(result, done);
  resultSection.focus();
  if (!finished) {
    verdict.textContent = config.signed_in
      ? "This sprint was not saved."
      : "You are not signed in, so this sprint is neither saved nor ranked.";
    startButton.disabled = false;
    return;
  }
  verdict.textContent = "Saving...";
  const answer = await ask("api/runs/" + finished + "/finish", report(done));
  startButton.disabled = false;
  if (!answer.ok) {
    verdict.textContent = "This sprint was not saved. " + answer.message;
    return;
  }
  result = answer.data.run; // the numbers the server worked out are the ones that count
  showResult(result, done);
  if (!result.ranked) {
    verdict.textContent =
      "Saved, but not ranked: the board takes sprints with at least " + config.min_accuracy + "% accuracy. Slower is faster.";
  } else if (answer.data.improved) {
    verdict.textContent = "A new best for you: rank " + answer.data.board.you.rank + " of " + answer.data.board.players + ".";
  } else {
    verdict.textContent = "Saved. Your best stays " + answer.data.best.wpm.toFixed(1) + " words a minute.";
  }
  showHistory(answer.data.runs, answer.data.best);
  showBoard(answer.data.board);
}

// --- Input -----------------------------------------------------------------------------------

typing.addEventListener("input", () => {
  if (!sprint) {
    return;
  }
  const waiting = sprint.startedAt === null;
  const kept = type(sprint, typing.value, performance.now());
  if (typing.value !== kept) {
    typing.value = kept;
  }
  if (sprint.refusedPaste) {
    sprint.refusedPaste = false;
    hint.textContent = "Pasting is off here: type the line.";
  }
  showLine();
  showMeters();
  if (waiting && sprint.startedAt !== null) {
    hint.textContent = "";
    requestAnimationFrame(frame);
  }
});

// No paste and no drop: a sprint is typed. sprint.js refuses a change that
// adds a whole word at once as well, for the ways around these three.
for (const name of ["paste", "drop"]) {
  typing.addEventListener(name, (event) => {
    event.preventDefault();
    hint.textContent = "Pasting is off here: type the line.";
  });
}
typing.addEventListener("beforeinput", (event) => {
  if (["insertFromPaste", "insertFromDrop", "insertFromYank"].includes(event.inputType)) {
    event.preventDefault();
  }
});

// The cursor stays at the end: Backspace is the only way back.
typing.addEventListener("keydown", (event) => {
  if (["ArrowLeft", "ArrowUp", "Home", "PageUp", "Enter"].includes(event.key)) {
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

startButton.addEventListener("click", start);
stopButton.addEventListener("click", stop);
refreshButton.addEventListener("click", loadBoard);

// --- Start -------------------------------------------------------------------------------------

async function load() {
  const answer = await ask("api/snippets");
  if (!answer.ok) {
    who.textContent = answer.message;
    return;
  }
  config = answer.data;
  who.textContent = config.signed_in
    ? "Typing as " + config.name + ". " + config.sprint_seconds + " seconds of what engineers type; your sprints are kept."
    : "You are not signed in: you can type, but your sprints are not kept or ranked.";
  showTopics();
  startButton.disabled = false;
  loadBoard();
  if (config.signed_in) {
    const history = await ask("api/history");
    if (history.ok) {
      showHistory(history.data.runs, history.data.best);
    }
  }
}

load();
