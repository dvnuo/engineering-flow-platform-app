// The page: the loop, the input and the talk with the server. The rules are
// in game.js and the drawing in render.js.
//
// Every URL in this file is relative ("api/runs", not "/api/runs"): the app
// is served under /{slug}/ once deployed. Nothing here asks the server on a
// timer: the leaderboard is loaded when the page opens, after a run and on
// "Refresh".
//
// The look comes from the EFP kit (/_efp/lib/, docs/UI_KIT.md). The game is
// meant to feel good to play, so every event answers: a squash splatters and
// the score pops, a higher combo announces itself, squashing a feature shakes
// the board, the last seconds turn red. Calm mode and "reduce motion" keep the
// same game without any of that movement.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import { accuracy, hitAt, hitKey, multiplier, newGame, step, summary } from "./game.js";
import { EFFECT_MS, drawSample, drawScene } from "./render.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const canvas = $("board");
const context = canvas.getContext("2d");
const stage = $("stage");
const scoreText = $("score");
const comboText = $("combo");
const streakText = $("streak");
const timeText = $("time");
const timeBar = $("timebar");
const overlay = $("overlay");
const overlayTitle = $("overlay-title");
const startButton = $("start");
const options = $("options");
const calmBox = $("calm");
const lettersBox = $("letters");
const refreshButton = $("refresh");

const darkTheme = window.matchMedia("(prefers-color-scheme: dark)");
const lessMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const SAMPLE = 56; // the side of a drawing in the legend, in CSS pixels
const LOW_SECONDS = 5; // the last seconds of a run, in red

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
let palette = null; // the theme's colours for the canvas
let level = 1; // the combo multiplier shown, to notice when it changes
let shownSeconds = -1;

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
    return true;
  }
  return false;
}

// Movement that only decorates: not in calm mode, not for "reduce motion".
function lively() {
  return !lessMotion.matches && !(game && game.calm);
}

function say(text) {
  const status = $("status");
  status.hidden = !text;
  status.replaceChildren(text ? h("div", { class: "alert alert-soft alert-warning text-sm" }, kit.icon("info", "size-4"), h("span", { text })) : "");
}

// The canvas cannot read CSS variables: the theme's colours, as rgb(), by the names render.js uses.
function readPalette() {
  palette = {
    board: kit.color("color-mix(in oklab, var(--color-base-200) 45%, var(--color-base-100))"),
    grid: kit.color("base-content", 0.16),
    ink: kit.color("base-content"),
    pale: kit.color("base-100"),
    typo: kit.color("error"),
    flaky: kit.color("warning"),
    legacy: kit.color("secondary"),
    feature: kit.color("success"),
    good: kit.color("success"),
    bad: kit.color("error"),
    plain: kit.color("base-content", 0.6),
    accent: kit.color("primary"),
  };
}

// The screen on top of the board: the start, the countdown, a pause, the end of a run.
function showOverlay(mode, { kicker = "", title = "", text = "", button = null } = {}) {
  overlay.classList.toggle("is-count", mode === "count");
  overlay.classList.toggle("is-over", mode === "over");
  $("overlay-kicker").textContent = kicker;
  overlayTitle.textContent = title;
  $("overlay-text").textContent = text;
  $("result").hidden = mode !== "over";
  $("keys").hidden = mode !== "ready" && mode !== "paused";
  if (mode !== "over") $("verdict").textContent = "";
  startButton.hidden = button === null;
  if (button !== null) {
    startButton.textContent = button;
  }
  overlay.hidden = false;
  if (mode !== "count") kit.appear($("panel"), { duration: 520 });
}

function hud() {
  const runMs = rules.run_seconds * 1000;
  const left = game ? Math.max(0, runMs - game.time) : runMs;
  const seconds = Math.ceil(left / 1000);
  write(scoreText, (game ? game.score : 0).toLocaleString("en"));
  const now = game ? multiplier(rules, game.combo) : 1;
  write(comboText, "x" + now);
  comboText.dataset.level = String(now);
  write(streakText, game && game.combo > 1 ? game.combo + " in a row" : "");
  write(timeText, String(seconds));
  // The last seconds: red, and each one counted with a beat.
  const low = phase === "running" && seconds <= LOW_SECONDS;
  $("time-stat").classList.toggle("is-low", low);
  timeBar.parentElement.classList.toggle("is-low", low);
  if (low && seconds !== shownSeconds && lively()) kit.pop(timeText, { scale: 1.25, duration: 420 });
  shownSeconds = seconds;
  // Calm mode moves the bar once a second instead of every frame.
  const share = game && game.calm ? seconds / rules.run_seconds : left / runMs;
  const scale = "scaleX(" + share.toFixed(3) + ")";
  if (timeBar.style.transform !== scale) {
    timeBar.style.transform = scale;
  }
}

function paint() {
  if (!view.width || !palette) {
    return;
  }
  // A paused board is drawn empty: a pause is not a way to study it.
  const shown = phase === "paused" || phase === "countdown" ? null : game;
  drawScene(context, view, shown, effects, { dark: darkTheme.matches, letters: lettersBox.checked, palette });
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

function showLegend() {
  const ratio = window.devicePixelRatio || 1;
  $("legend").replaceChildren(
    ...Object.entries(rules.kinds).map(([name, kind]) => {
      const sample = h("canvas", { width: Math.round(SAMPLE * ratio), height: Math.round(SAMPLE * ratio), "aria-hidden": "true" });
      drawSample(sample.getContext("2d"), name, SAMPLE, ratio, darkTheme.matches, palette);
      let worth = kind.points + " points" + (kind.hits > 1 ? ", " + kind.hits + " hits" : "");
      if (kind.friend) {
        worth = kind.points + " if you squash it, +" + kind.ship_bonus + " when it ships";
      }
      return h(
        "li",
        {},
        sample,
        h(
          "div",
          { class: "min-w-0 grid" },
          h("strong", { text: kind.label }),
          h("span", { class: "bs-worth" + (kind.friend ? " is-bad" : ""), text: worth }),
          h("span", { class: "text-xs text-base-content/60", text: kind.note }),
        ),
      );
    }),
  );
  $("notes").replaceChildren(
    h(
      "li",
      {},
      "Every " + rules.combo.step + " squashes in a row raise what the next ones are worth, up to x" +
        rules.combo.max_multiplier + ". A click on the empty board breaks the row; so does squashing a feature.",
    ),
    h("li", {}, "A bug that gets across the board costs " + rules.escape_penalty + " points."),
    h("li", {}, "The run lasts " + rules.run_seconds + " seconds and gets busier. Esc pauses it; so does leaving the tab."),
  );
}

function showSummary() {
  const result = summary(game);
  const hit = accuracy(game);
  const rows = [["Score", game.score.toLocaleString("en"), ""]];
  for (const [name, kind] of Object.entries(rules.kinds)) {
    if (kind.friend) {
      rows.push([kind.label + "s squashed", String(result.features_squashed), kind.points + " each, and the combo"]);
      rows.push([kind.label + "s shipped", String(result.shipped), "+" + kind.ship_bonus + " each"]);
    } else {
      rows.push([kind.label + "s squashed", String(result.squashed[name]), kind.points + " each, times the combo"]);
    }
  }
  rows.push(["Bugs that got away", String(result.escaped), "-" + rules.escape_penalty + " each"]);
  rows.push(["Clicks on nothing", String(result.misses), ""]);
  rows.push(["Accuracy", hit === null ? "-" : hit + "%", "clicks that struck a bug"]);
  rows.push(["Best combo", String(result.best_combo), "squashes in a row"]);
  $("summary-rows").replaceChildren(
    ...rows.map(([name, value, note]) =>
      h(
        "tr",
        {},
        h("th", { scope: "row", class: "font-normal whitespace-nowrap", text: name }),
        h("td", { class: "text-right font-semibold efp-number whitespace-nowrap", text: value }),
        h("td", { class: "w-full text-base-content/55 text-xs", text: note }),
      ),
    ),
  );
  $("summary").hidden = false;
}

// --- The leaderboard ---------------------------------------------------------------------------

const leaderItems = new Map(); // a name (and how often it came before) -> its <li>

function showBoard(board, { fresh = false } = {}) {
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
          h("span", { class: "bs-rank efp-number" }),
          kit.avatar(entry.name || "?", { size: "1.85rem" }),
          h("span", { class: "min-w-0 flex items-center gap-1.5" }, h("span", { class: "truncate font-medium", text: entry.name })),
          h("span", { class: "bs-points" }),
        ),
      );
    }
    const item = leaderItems.get(key);
    const rank = item.querySelector(".bs-rank");
    rank.dataset.place = String(entry.rank);
    rank.replaceChildren(entry.rank === 1 ? kit.icon("crown", "size-4") : String(entry.rank));
    rank.title = "Rank " + entry.rank;
    item.querySelector(".bs-points").textContent = entry.score.toLocaleString("en");
    item.classList.toggle("is-you", entry.you);
    const name = item.children[2];
    if (entry.you && name.children.length === 1) name.append(h("span", { class: "badge badge-xs badge-primary badge-soft", text: "you" }));
    if (!entry.you && name.children.length > 1) name.lastElementChild.remove();
    return item;
  });
  const list = $("leaders");
  items.forEach((item, index) => {
    if (list.children[index] !== item) list.insertBefore(item, list.children[index] || null);
  });
  while (list.children.length > items.length) list.lastElementChild.remove();
  $("leaders-empty").hidden = board.top.length > 0;
  list.hidden = board.top.length === 0;
  const note = $("board-note");
  if (board.top.length === 0) {
    note.textContent = "";
  } else if (board.you) {
    note.textContent = "Your best is " + board.you.score.toLocaleString("en") + " points: rank " + board.you.rank + " of " + board.players + ".";
  } else if (board.signed_in) {
    note.textContent = "You have no ranked run yet. " + board.players + (board.players === 1 ? " member has" : " members have") + " played.";
  } else {
    note.textContent = "You are not signed in: you can play, and the board lists the members who are.";
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
    showBoard(answer.data);
  } else {
    $("board-note").textContent = answer.message;
    $("leaders").hidden = true;
  }
}

// --- The run -------------------------------------------------------------------------------

const TONES = { squash: "good", shipped: "good", crack: "plain", miss: "plain", feature: "bad", escaped: "bad" };

// A word in the middle of the board: "Go!", "x3 combo", "combo lost".
function announce(text, tone) {
  effects.push({ at: game.time, x: 0.5, y: 0.2, text, tone, big: true, life: 900 });
}

// Whatever happened on the board, as a word or a number where it happened,
// and the answer of the numbers above it.
function addEffect(result, comboBefore) {
  if (!result) {
    return;
  }
  let text = result.type;
  if (result.points) {
    text = (result.points > 0 ? "+" : "") + result.points;
  }
  const effect = { at: game.time, x: result.x, y: result.y, text, tone: TONES[result.type], ring: result.type === "squash", seed: (game.time % 628) / 100 };
  if (result.type === "squash") effect.splat = result.kind;
  effects.push(effect);

  const now = multiplier(rules, game.combo);
  if (result.type === "squash") {
    if (lively()) kit.pop(scoreText, { scale: 1.12, duration: 320 });
    if (now > level) {
      announce("x" + now + " combo", "accent");
      if (lively()) kit.pop(comboText, { scale: 1.4, duration: 600 });
    }
  } else if (result.type === "feature") {
    if (lively()) kit.shake(stage);
  }
  if (comboBefore >= rules.combo.step && game.combo === 0 && (result.type === "miss" || result.type === "feature")) {
    announce("combo lost", "bad");
    if (lively()) kit.shake(comboText);
  }
  level = now;
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
      // Each second of the countdown lands with a beat.
      if (write(overlayTitle, String(Math.ceil(countdown / 1000))) && !lessMotion.matches) {
        overlayTitle.animate([{ transform: "scale(1.7)", opacity: 0 }, { transform: "scale(1)", opacity: 1 }], { duration: 520, easing: kit.BOUNCE });
      }
    } else {
      phase = "running";
      overlay.hidden = true;
      announce("Go!", "accent");
    }
  } else {
    step(game, elapsed);
    game.events.splice(0).forEach((event) => addEffect(event, game.combo));
    effects = effects.filter((effect) => game.time - effect.at < (effect.life || EFFECT_MS));
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
  options.disabled = true;
  say("");
  run = null;
  if (signedIn) {
    // The server notes when the run began and hands out its seed.
    const answer = await kit.busy(startButton, () => ask("api/runs", {}), { tick: false });
    if (answer && answer.ok) {
      run = answer.data;
    } else if (answer) {
      say("This run will not be ranked. " + answer.message);
    }
  }
  const seed = run ? run.seed : window.crypto.getRandomValues(new Uint32Array(1))[0];
  // On a tall, narrow screen the bugs cross from top to bottom: the longer way.
  game = newGame(rules, seed, { calm: calmBox.checked, axis: view.height > view.width * 1.15 ? "y" : "x" });
  effects = [];
  level = 1;
  $("summary").hidden = true;
  countdown = rules.countdown_seconds * 1000;
  phase = "countdown";
  showOverlay("count", { title: String(rules.countdown_seconds) });
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
  showOverlay("paused", { kicker: "Paused", title: "The clock is stopped", text: "The board is hidden while the run waits for you.", button: "Resume" });
  startButton.focus({ preventScroll: true });
}

function resume() {
  phase = pausedFrom;
  if (phase === "countdown") {
    showOverlay("count", { title: String(Math.ceil(countdown / 1000)) });
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
  $("time-stat").classList.remove("is-low");
  timeBar.parentElement.classList.remove("is-low");
  showSummary();
  const result = summary(game);
  const squashed = Object.values(result.squashed).reduce((sum, count) => sum + count, 0);
  const hit = accuracy(game);
  showOverlay("over", { kicker: "Time is up", title: "0", text: "points", button: "Play again" });
  kit.countUp(overlayTitle, score, { from: 0, duration: 1400, format: (value) => Math.round(value).toLocaleString("en") });
  kit.countUp($("result-squashed"), squashed, { from: 0, duration: 1000 });
  kit.countUp($("result-combo"), result.best_combo, { from: 0, duration: 1000 });
  if (hit === null) $("result-accuracy").textContent = "-";
  else kit.countUp($("result-accuracy"), hit, { from: 0, duration: 1000, format: (value) => Math.round(value) + "%" });
  const verdict = $("verdict");
  if (!finished) {
    verdict.textContent = signedIn ? "This run was not ranked." : "You are not signed in, so this run is not ranked.";
    startButton.focus({ preventScroll: true });
    return;
  }
  // "Play again" waits for the answer: starting a run gives up the one before it.
  verdict.textContent = "Sending your score...";
  const answer = await kit.busy(startButton, () => ask("api/runs/" + finished.id + "/finish", { score, summary: summary(game) }), { tick: false });
  startButton.focus({ preventScroll: true });
  if (!answer || !answer.ok) {
    verdict.textContent = "This run was not ranked. " + (answer ? answer.message : "");
    return;
  }
  const you = answer.data.you;
  if (answer.data.improved) {
    verdict.textContent = you.rank === 1 ? "A new best, and the top of the board." : "A new best for you: rank " + you.rank + " of " + answer.data.players + ".";
    kit.celebrate({ from: $("panel"), particles: you.rank === 1 ? 200 : 130 });
  } else {
    verdict.textContent = "Your best stays " + you.score.toLocaleString("en") + " points (rank " + you.rank + " of " + answer.data.players + ").";
  }
  kit.appear(verdict);
  showBoard(answer.data, { fresh: answer.data.improved });
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
  const before = game.combo;
  addEffect(hitAt(game, event.clientX - box.left, event.clientY - box.top, view, slack), before);
});

document.addEventListener("keydown", (event) => {
  if (phase !== "running" || event.ctrlKey || event.metaKey || event.altKey) {
    return;
  }
  if (event.key === "Escape") {
    pause();
  } else if (lettersBox.checked && /^[a-zA-Z]$/.test(event.key)) {
    event.preventDefault();
    const before = game.combo;
    addEffect(hitKey(game, event.key.toUpperCase()), before);
  }
});

// A hidden tab gets no frames. Without the pause the run would stand still
// and then be over, or jump, when the visitor comes back.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    pause();
  }
});

refreshButton.addEventListener("click", (event) => loadBoard(event.currentTarget));
lettersBox.addEventListener("change", paint);
new ResizeObserver(fit).observe(stage);

// --- Start -------------------------------------------------------------------------------------

async function load() {
  if (lessMotion.matches) {
    calmBox.checked = true;
    $("calm-label").textContent += " (on: your system asks for less motion)";
  }
  const answer = await ask("api/rules");
  if (!answer.ok) {
    showOverlay("error", { kicker: "Sorry", title: "The game could not be loaded", text: answer.message });
    $("who").textContent = "";
    return;
  }
  rules = answer.data.rules;
  signedIn = answer.data.signed_in;
  $("who").textContent = signedIn
    ? "Playing as " + answer.data.name + ". Your best run goes on the leaderboard."
    : "You are not signed in: you can play, but your runs are not ranked.";
  if (signedIn) $("me").append(kit.avatar(answer.data.name, { size: "2rem" }));
  showLegend();
  hud();
  paint();
  phase = "ready";
  showOverlay("ready", {
    kicker: rules.run_seconds + " seconds",
    title: "Squash the bugs. Spare the features.",
    text: "Click or tap a bug; the letters option lets you play with the keyboard.",
    button: "Start",
  });
  startButton.focus({ preventScroll: true });
  loadBoard();
}

await kit.ready();
kit.themeSwitch($("theme"));
readPalette();
// A new theme repaints the board and the drawings of the legend in its colours.
kit.onThemeChange(() => {
  readPalette();
  paint();
  if (rules) showLegend();
});
kit.animateList($("leaders"));
fit();
load();
