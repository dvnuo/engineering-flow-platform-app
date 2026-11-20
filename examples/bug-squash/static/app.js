// The page: the loop, the input and the talk with the server. The rules are
// in game.js and the drawing in render.js.
//
// Every URL in this file is relative ("api/runs", not "/api/runs"): the app
// is served under /{slug}/ once deployed. Nothing here asks the server on a
// timer: the leaderboard is loaded when the page opens, after a run and on
// "Refresh".

import { accuracy, hitAt, hitKey, multiplier, newGame, step, summary } from "./game.js";
import { EFFECT_MS, drawSample, drawScene } from "./render.js";

const canvas = document.getElementById("board");
const context = canvas.getContext("2d");
const stage = document.getElementById("stage");
const who = document.getElementById("who");
const scoreText = document.getElementById("score");
const comboText = document.getElementById("combo");
const streakText = document.getElementById("streak");
const timeText = document.getElementById("time");
const timeBar = document.getElementById("timebar");
const overlay = document.getElementById("overlay");
const overlayTitle = document.getElementById("overlay-title");
const overlayText = document.getElementById("overlay-text");
const startButton = document.getElementById("start");
const options = document.getElementById("options");
const calmBox = document.getElementById("calm");
const calmLabel = document.getElementById("calm-label");
const lettersBox = document.getElementById("letters");
const status = document.getElementById("status");
const summarySection = document.getElementById("summary");
const summaryRows = document.getElementById("summary-rows");
const verdict = document.getElementById("verdict");
const legend = document.getElementById("legend");
const notes = document.getElementById("notes");
const boardNote = document.getElementById("board-note");
const leaders = document.getElementById("leaders");
const leaderRows = document.getElementById("leader-rows");
const refreshButton = document.getElementById("refresh");

const darkTheme = window.matchMedia("(prefers-color-scheme: dark)");
const lessMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const SAMPLE = 56; // the side of a drawing in the legend, in CSS pixels

let rules = null;
let signedIn = false;
let game = null; // the run on the board
let run = null; // {id, seed} from the server when the run counts for the leaderboard
let phase = "loading"; // loading, ready, starting, countdown, running, paused, over
let pausedFrom = "running";
let countdown = 0; // milliseconds until the run starts
let last = 0; // the time of the frame before this one
let effects = [];
let view = { width: 0, height: 0, unit: 20, ratio: 1 };

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

// --- What the page shows -----------------------------------------------------------------

function write(element, text) {
  if (element.textContent !== text) {
    element.textContent = text; // the loop calls this sixty times a second: touch the page only for a change
  }
}

function showOverlay(title, text, button) {
  overlay.classList.toggle("count", button === null && phase === "countdown");
  overlayTitle.textContent = title;
  overlayText.textContent = text;
  startButton.hidden = button === null;
  if (button !== null) {
    startButton.textContent = button;
  }
  overlay.hidden = false;
}

function hud() {
  const runMs = rules.run_seconds * 1000;
  const left = game ? Math.max(0, runMs - game.time) : runMs;
  const seconds = Math.ceil(left / 1000);
  write(scoreText, (game ? game.score : 0).toLocaleString());
  write(comboText, "x" + (game ? multiplier(rules, game.combo) : 1));
  write(streakText, game && game.combo > 1 ? game.combo + " in a row" : "");
  write(timeText, String(seconds));
  // Calm mode moves the bar once a second instead of every frame.
  const share = game && game.calm ? seconds / rules.run_seconds : left / runMs;
  const scale = "scaleX(" + share.toFixed(3) + ")";
  if (timeBar.style.transform !== scale) {
    timeBar.style.transform = scale;
  }
}

function paint() {
  if (!view.width) {
    return;
  }
  // A paused board is drawn empty: a pause is not a way to study it.
  const shown = phase === "paused" || phase === "countdown" ? null : game;
  drawScene(context, view, shown, effects, { dark: darkTheme.matches, letters: lettersBox.checked });
}

function fit() {
  const box = stage.getBoundingClientRect();
  if (!box.width || !box.height) {
    return;
  }
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(box.width * ratio);
  canvas.height = Math.round(box.height * ratio);
  view = {
    width: box.width,
    height: box.height,
    ratio,
    unit: Math.min(30, Math.max(15, Math.min(box.width, box.height) * 0.055)), // the radius of a bug of size 1
  };
  paint();
}

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

function showLegend() {
  const ratio = window.devicePixelRatio || 1;
  legend.replaceChildren();
  for (const [name, kind] of Object.entries(rules.kinds)) {
    const item = element("li");
    const sample = element("canvas");
    sample.width = Math.round(SAMPLE * ratio);
    sample.height = Math.round(SAMPLE * ratio);
    sample.setAttribute("aria-hidden", "true");
    drawSample(sample.getContext("2d"), name, SAMPLE, ratio, darkTheme.matches);
    const text = element("div", undefined, "text");
    let worth = kind.points + " points" + (kind.hits > 1 ? ", " + kind.hits + " hits" : "");
    if (kind.friend) {
      worth = kind.points + " if you squash it, +" + kind.ship_bonus + " when it ships";
    }
    text.append(element("strong", kind.label), element("span", worth, kind.friend ? "worth bad" : "worth"), element("span", kind.note));
    item.append(sample, text);
    legend.append(item);
  }
  notes.replaceChildren(
    element(
      "li",
      "Every " + rules.combo.step + " squashes in a row raise what the next ones are worth, up to x" +
        rules.combo.max_multiplier + ". A click on the empty board breaks the row; so does squashing a feature."
    ),
    element("li", "A bug that gets across the board costs " + rules.escape_penalty + " points."),
    element("li", "The run lasts " + rules.run_seconds + " seconds and gets busier. Esc pauses it; so does leaving the tab.")
  );
}

function summaryRow(name, value, note) {
  const row = element("tr");
  const head = element("th", name);
  head.scope = "row";
  row.append(head, element("td", value, "number"), element("td", note || "", "note"));
  summaryRows.append(row);
}

function showSummary() {
  const result = summary(game);
  const hit = accuracy(game);
  summaryRows.replaceChildren();
  summaryRow("Score", game.score.toLocaleString());
  for (const [name, kind] of Object.entries(rules.kinds)) {
    if (kind.friend) {
      summaryRow(kind.label + "s squashed", String(result.features_squashed), kind.points + " each, and the combo");
      summaryRow(kind.label + "s shipped", String(result.shipped), "+" + kind.ship_bonus + " each");
    } else {
      summaryRow(kind.label + "s squashed", String(result.squashed[name]), kind.points + " each, times the combo");
    }
  }
  summaryRow("Bugs that got away", String(result.escaped), "-" + rules.escape_penalty + " each");
  summaryRow("Clicks on nothing", String(result.misses));
  summaryRow("Accuracy", hit === null ? "-" : hit + "%", "clicks that struck a bug");
  summaryRow("Best combo", String(result.best_combo), "squashes in a row");
  summarySection.hidden = false;
}

function showBoard(board) {
  leaderRows.replaceChildren();
  for (const entry of board.top) {
    const row = element("tr", undefined, entry.you ? "you" : "");
    row.append(
      element("td", String(entry.rank), "number"),
      element("td", entry.name + (entry.you ? " (you)" : "")), // a name is text from elsewhere: textContent
      element("td", entry.score.toLocaleString(), "number")
    );
    leaderRows.append(row);
  }
  leaders.hidden = board.top.length === 0;
  if (board.top.length === 0) {
    boardNote.textContent = "Nobody has finished a run yet. The first place is free.";
  } else if (board.you) {
    boardNote.textContent =
      "Your best is " + board.you.score.toLocaleString() + " points: rank " + board.you.rank + " of " + board.players + ".";
  } else if (board.signed_in) {
    boardNote.textContent = "You have no ranked run yet.";
  } else {
    boardNote.textContent = "You are not signed in: you can play, and the board lists the members who are.";
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

// --- The run -------------------------------------------------------------------------------

const TONES = { squash: "good", shipped: "good", crack: "plain", miss: "plain", feature: "bad", escaped: "bad" };

// Whatever happened on the board, as a word or a number where it happened.
function addEffect(result) {
  if (!result) {
    return;
  }
  let text = result.type;
  if (result.points) {
    text = (result.points > 0 ? "+" : "") + result.points;
  }
  effects.push({ at: game.time, x: result.x, y: result.y, text, tone: TONES[result.type], ring: result.type === "squash" });
}

function frame(now) {
  if (phase !== "countdown" && phase !== "running") {
    return; // a pause or the end of the run stops the loop; nothing runs while there is nothing to move
  }
  const elapsed = Math.min(100, now - last); // a frame that took long is not played in one jump
  last = now;
  if (phase === "countdown") {
    countdown -= elapsed;
    if (countdown > 0) {
      write(overlayTitle, String(Math.ceil(countdown / 1000)));
    } else {
      phase = "running";
      overlay.hidden = true;
    }
  } else {
    step(game, elapsed);
    game.events.splice(0).forEach(addEffect);
    effects = effects.filter((effect) => game.time - effect.at < EFFECT_MS);
  }
  paint();
  hud();
  if (game.over) {
    finish();
    return;
  }
  requestAnimationFrame(frame);
}

async function start() {
  if (phase !== "ready" && phase !== "over") {
    return;
  }
  phase = "starting";
  startButton.disabled = true;
  options.disabled = true;
  status.textContent = "";
  run = null;
  if (signedIn) {
    // The server notes when the run began and hands out its seed.
    const answer = await ask("api/runs", {});
    if (answer.ok) {
      run = answer.data;
    } else {
      status.textContent = "This run will not be ranked. " + answer.message;
    }
  }
  const seed = run ? run.seed : window.crypto.getRandomValues(new Uint32Array(1))[0];
  // On a tall, narrow screen the bugs cross from top to bottom: the longer way.
  game = newGame(rules, seed, { calm: calmBox.checked, axis: view.height > view.width * 1.15 ? "y" : "x" });
  effects = [];
  summarySection.hidden = true;
  countdown = rules.countdown_seconds * 1000;
  phase = "countdown";
  showOverlay(String(rules.countdown_seconds), "", null);
  hud();
  last = performance.now();
  requestAnimationFrame(frame);
}

function pause() {
  if (phase !== "countdown" && phase !== "running") {
    return;
  }
  pausedFrom = phase;
  phase = "paused";
  paint();
  startButton.disabled = false;
  showOverlay("Paused", "The clock is stopped.", "Resume");
  startButton.focus();
}

function resume() {
  phase = pausedFrom;
  startButton.disabled = true;
  if (phase === "countdown") {
    showOverlay(String(Math.ceil(countdown / 1000)), "", null);
  } else {
    overlay.hidden = true;
  }
  last = performance.now();
  requestAnimationFrame(frame);
}

async function finish() {
  phase = "over";
  const score = game.score;
  const finished = run;
  run = null;
  options.disabled = false;
  showSummary();
  showOverlay(score.toLocaleString() + " points", "The numbers of this run are below the board.", "Play again");
  if (!finished) {
    verdict.textContent = signedIn
      ? "This run was not ranked."
      : "You are not signed in, so this run is not ranked.";
    startButton.disabled = false;
    startButton.focus();
    return;
  }
  // "Play again" waits for the answer: starting a run gives up the one before it.
  verdict.textContent = "Sending your score...";
  const answer = await ask("api/runs/" + finished.id + "/finish", { score, summary: summary(game) });
  startButton.disabled = false;
  startButton.focus();
  if (!answer.ok) {
    verdict.textContent = "This run was not ranked. " + answer.message;
    return;
  }
  const you = answer.data.you;
  verdict.textContent = answer.data.improved
    ? "A new best for you: rank " + you.rank + " of " + answer.data.players + "."
    : "Your best stays " + you.score.toLocaleString() + " points (rank " + you.rank + " of " + answer.data.players + ").";
  showBoard(answer.data);
}

// --- Input -----------------------------------------------------------------------------------

startButton.addEventListener("click", () => {
  if (phase === "paused") {
    resume();
  } else {
    start();
  }
});

// Pointer events cover the mouse, a pen and a finger with one handler.
canvas.addEventListener("pointerdown", (event) => {
  if (phase !== "running") {
    return;
  }
  event.preventDefault();
  const box = canvas.getBoundingClientRect();
  const slack = view.unit * (event.pointerType === "touch" ? 0.7 : 0.2); // a finger is wider than a pointer
  addEffect(hitAt(game, event.clientX - box.left, event.clientY - box.top, view, slack));
});

document.addEventListener("keydown", (event) => {
  if (phase !== "running" || event.ctrlKey || event.metaKey || event.altKey) {
    return;
  }
  if (event.key === "Escape") {
    pause();
  } else if (lettersBox.checked && /^[a-zA-Z]$/.test(event.key)) {
    event.preventDefault();
    addEffect(hitKey(game, event.key.toUpperCase()));
  }
});

// A hidden tab gets no frames. Without the pause the run would stand still
// and then be over, or jump, when the visitor comes back.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    pause();
  }
});

refreshButton.addEventListener("click", loadBoard);
lettersBox.addEventListener("change", paint);
darkTheme.addEventListener("change", () => {
  paint();
  if (rules) {
    showLegend();
  }
});
new ResizeObserver(fit).observe(stage);

// --- Start -------------------------------------------------------------------------------------

async function load() {
  if (lessMotion.matches) {
    calmBox.checked = true;
    calmLabel.textContent += " (on: your system asks for less motion)";
  }
  const answer = await ask("api/rules");
  if (!answer.ok) {
    showOverlay("The game could not be loaded", answer.message, null);
    return;
  }
  rules = answer.data.rules;
  signedIn = answer.data.signed_in;
  who.textContent = signedIn
    ? "Playing as " + answer.data.name + ". Your best run goes on the leaderboard."
    : "You are not signed in: you can play, but your runs are not ranked.";
  showLegend();
  hud();
  phase = "ready";
  showOverlay(
    "Squash the bugs. Spare the features.",
    rules.run_seconds + " seconds. Click or tap; or turn on the letters and type.",
    "Start"
  );
  loadBoard();
}

load();
