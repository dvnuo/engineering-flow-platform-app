// The page: it draws the wheel, turns it and asks the server. It never
// chooses: "Spin" asks the server who is next and the wheel is turned to that
// name. The geometry is wheel.js.
//
// Every URL in this file is relative ("api/spin", not "/api/spin"): the app
// is served under /{slug}/ once deployed. Nothing here asks the server on a
// timer; somebody who watches presses "Refresh".

import { angleAt, fitLabel, labelSize, landingAngle, sliceColour, sliceSpan, wedgePath } from "./wheel.js";

const SVG = "http://www.w3.org/2000/svg";
const SPIN_MS = 4200;
const TURNS = 5; // whole turns before the wheel comes to rest

const who = document.getElementById("who");
const disc = document.getElementById("disc");
const wheelText = document.getElementById("wheel-text");
const next = document.getElementById("next");
const spinButton = document.getElementById("spin");
const newRoundButton = document.getElementById("new-round");
const refreshButton = document.getElementById("refresh");
const status = document.getElementById("status");
const spokenList = document.getElementById("spoken");
const spokenEmpty = document.getElementById("spoken-empty");
const waitingList = document.getElementById("waiting");
const waitingEmpty = document.getElementById("waiting-empty");
const awayBlock = document.getElementById("away-block");
const awayList = document.getElementById("away");
const historyList = document.getElementById("history");
const historyEmpty = document.getElementById("history-empty");
const ownerSection = document.getElementById("owner");
const ownerNote = document.getElementById("owner-note");
const notOwner = document.getElementById("not-owner");
const teamForm = document.getElementById("team-form");
const teamRows = document.getElementById("team-rows");
const addPersonButton = document.getElementById("add-person");
const saveTeamButton = document.getElementById("save-team");
const teamStatus = document.getElementById("team-status");
const confirmDialog = document.getElementById("confirm");
const confirmText = document.getElementById("confirm-text");

const lessMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

let state = null; // what the server said last
let rotation = 0; // how far the wheel has turned, in degrees
let onWheel = []; // the people on the wheel as it is drawn now
let spinning = false;
let formFor = null; // the list version and round the owner's form was filled from

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

// --- Small helpers -----------------------------------------------------------------------

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) {
    node.textContent = text; // names are text from elsewhere: never markup
  }
  if (className) {
    node.className = className;
  }
  return node;
}

function shape(name, attributes, text) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) {
    node.setAttribute(key, String(value));
  }
  if (text !== undefined) {
    node.textContent = text;
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

// --- The wheel ---------------------------------------------------------------------------

function turnTo(degrees) {
  rotation = degrees;
  disc.setAttribute("transform", "rotate(" + degrees.toFixed(2) + ")");
}

// One slice per person. `chosen` is the username the wheel has just stopped
// at; `over` draws the wheel of a round in which everybody has spoken.
function drawWheel(people, chosen, over) {
  onWheel = people;
  disc.replaceChildren();
  if (people.length === 0) {
    disc.append(shape("circle", { class: "blank", r: 100 }));
  }
  people.forEach((person, index) => {
    const span = sliceSpan(index, people.length);
    const slice = shape("g", {
      class: "slice c" + sliceColour(index, people.length) + (person.username === chosen ? " chosen" : ""),
    });
    slice.append(
      shape("path", { d: wedgePath(100, span.start, span.end) }),
      // A name runs from the hub to the rim along the middle of its slice.
      shape(
        "text",
        {
          x: 93,
          y: 0,
          "text-anchor": "end",
          "dominant-baseline": "central",
          "font-size": labelSize(people.length),
          transform: "rotate(" + (span.middle - 90).toFixed(2) + ")",
        },
        fitLabel(person.name, people.length)
      )
    );
    disc.append(slice);
  });
  disc.setAttribute("class", chosen ? "landed" : over ? "over" : "");
  turnTo(rotation % 360);
  wheelText.textContent = people.length
    ? "The wheel holds " + listOf(people.map((person) => person.name)) + "."
    : "The wheel is empty.";
}

// Turn from where the wheel is to `to`, slowing down, then call `done`.
// With "reduce motion" the wheel is simply shown at the result.
function turn(to, done) {
  if (lessMotion.matches) {
    turnTo(to);
    done();
    return;
  }
  const from = rotation;
  const began = performance.now();
  function frame(now) {
    const progress = Math.min(1, (now - began) / SPIN_MS);
    turnTo(angleAt(from, to, progress));
    if (progress < 1) {
      requestAnimationFrame(frame);
    } else {
      done();
    }
  }
  requestAnimationFrame(frame);
}

// --- What the page shows -------------------------------------------------------------------

function people(usernames) {
  const byUsername = new Map(state.team.members.map((member) => [member.username, member]));
  return usernames.map((username) => byUsername.get(username) || { username, name: username });
}

function showLists() {
  const round = state.round;
  spokenList.replaceChildren();
  for (const pick of round.picks) {
    const item = element("li", pick.name);
    if (pick.by) {
      item.append(" ", element("small", "spun by " + pick.by));
    }
    spokenList.append(item);
  }
  spokenEmpty.hidden = round.picks.length > 0;

  waitingList.replaceChildren(...people(round.remaining).map((person) => element("li", person.name)));
  waitingEmpty.hidden = round.remaining.length > 0;
  if (state.team.members.length === 0) {
    waitingEmpty.textContent = "Nobody is on the list yet. The owner of this app adds the team.";
  } else {
    waitingEmpty.textContent = round.complete ? "Nobody: everybody has spoken." : "Nobody: everybody is away.";
  }

  const away = state.team.members.filter((member) => member.away);
  awayList.replaceChildren(...away.map((person) => element("li", person.name)));
  awayBlock.hidden = away.length === 0;

  historyList.replaceChildren();
  for (const closed of state.history) {
    const item = element("li");
    item.append(element("strong", "Round " + closed.round), " " + day(closed.closed_at) + ": " + listOf(closed.order) + ".");
    if (closed.left.length > 0) {
      item.append(" ", element("span", "Did not speak: " + listOf(closed.left) + ".", "left"));
    }
    historyList.append(item);
  }
  historyEmpty.hidden = state.history.length > 0;
}

function showButtons() {
  const round = state.round;
  const you = state.you;
  spinButton.disabled = spinning || !you.may_spin || round.remaining.length === 0;
  refreshButton.disabled = spinning;
  newRoundButton.hidden = !you.may_spin || round.picks.length === 0;
  newRoundButton.disabled = spinning;
  // The button that is the next step looks like it.
  spinButton.classList.toggle("primary", !round.complete);
  newRoundButton.classList.toggle("primary", round.complete);
  if (!you.signed_in) {
    who.textContent = "You are not signed in: you can watch.";
  } else if (you.may_spin) {
    who.textContent = "Signed in as " + you.name + ". Spin, and the server picks who speaks next.";
  } else {
    who.textContent = "Signed in as " + you.name + ". You are not on this wheel, so you can watch.";
  }
}

function teamRow(person) {
  const row = element("tr");
  const username = element("input");
  username.type = "text";
  username.name = "username";
  username.value = person.username;
  username.maxLength = 64;
  username.required = true;
  username.autocomplete = "off";
  username.spellcheck = false;
  username.setAttribute("autocapitalize", "off");
  username.setAttribute("aria-label", "Portal username");
  const name = element("input");
  name.type = "text";
  name.name = "name";
  name.value = person.name;
  name.maxLength = 40;
  name.autocomplete = "off";
  name.setAttribute("aria-label", "Name on the wheel");
  const away = element("input");
  away.type = "checkbox";
  away.name = "away";
  away.checked = Boolean(person.away);
  away.setAttribute("aria-label", "Away today");
  const remove = element("button", "Remove");
  remove.type = "button";
  remove.addEventListener("click", () => {
    row.remove();
    teamStatus.textContent = "Removed. Save the list to keep the change.";
  });
  const cells = [username, name, away, remove].map((control, index) => {
    const cell = element("td", undefined, index > 1 ? "narrow" : "");
    cell.append(control);
    return cell;
  });
  row.append(...cells);
  return row;
}

// The owner's form is filled when the list or the round changes, not on
// every refresh: a spin by somebody else must not wipe what is being typed.
function showOwner() {
  ownerSection.hidden = !state.you.owner;
  notOwner.hidden = state.you.owner;
  if (!state.you.owner) {
    return;
  }
  ownerNote.textContent =
    (state.team.starter ? "This is the starter list that ships with the app: replace it with your team. " : "") +
    "The username is the one a person signs in to the Portal with; it decides who may spin. " +
    "\"Away today\" lasts until the next round starts.";
  const filledFrom = state.team.version + ":" + state.round.number;
  if (formFor !== filledFrom) {
    formFor = filledFrom;
    teamRows.replaceChildren(...state.team.members.map(teamRow));
  }
}

// Take in what the server said. The wheel is redrawn unless it has just
// stopped and should keep showing where.
function adopt(fresh, keepWheel) {
  state = fresh;
  showLists();
  showButtons();
  showOwner();
  if (keepWheel) {
    return;
  }
  const round = state.round;
  if (round.complete) {
    drawWheel(round.picks.map((pick) => ({ username: pick.username, name: pick.name })), null, true);
    next.textContent = "Everybody has spoken.";
  } else {
    drawWheel(people(round.remaining), null, false);
    const last = round.picks[round.picks.length - 1];
    next.textContent = last ? "Last picked: " + last.name + "." : "";
  }
}

async function load() {
  refreshButton.disabled = true;
  const answer = await ask("api/state");
  refreshButton.disabled = spinning;
  if (!answer.ok) {
    status.textContent = answer.message;
    return false;
  }
  adopt(answer.data, false);
  return true;
}

// --- Spinning --------------------------------------------------------------------------------

async function spin() {
  if (spinning || !state) {
    return;
  }
  spinning = true;
  showButtons();
  status.textContent = "";
  drawWheel(people(state.round.remaining), null, false); // the wheel holds who is still to speak
  // The page says which pick it asks for. If somebody else asked for the
  // same one a moment sooner, the server answers with theirs.
  const answer = await ask("api/spin", "POST", { round: state.round.number, pick: state.round.picks.length + 1 });
  if (!answer.ok) {
    spinning = false;
    status.textContent = answer.message;
    if (answer.status === 409) {
      await load(); // the round has moved on: show where it stands
    } else {
      showButtons();
    }
    return;
  }
  const pick = answer.data.pick;
  const index = onWheel.findIndex((person) => person.username === pick.username);
  const landed = () => {
    spinning = false;
    if (index >= 0) {
      drawWheel(onWheel, pick.username, false);
    }
    adopt(answer.data.state, index >= 0);
    next.textContent = answer.data.state.round.complete
      ? pick.name + " speaks last. Everybody has spoken."
      : pick.name + " speaks next.";
    if (!answer.data.created) {
      status.textContent = (pick.by || "Somebody") + " spun at the same moment: this is that one spin.";
    }
  };
  if (index < 0) {
    landed(); // this page had an old list: no turn, only the result
    return;
  }
  const offCentre = (Math.random() - 0.5) * 0.7; // where in the slice the wheel halts is only looks
  turn(landingAngle(rotation, index, onWheel.length, TURNS, offCentre), landed);
}

async function closeRound() {
  newRoundButton.disabled = true;
  const number = state.round.number;
  const answer = await ask("api/round/next", "POST", { round: number });
  if (!answer.ok) {
    status.textContent = answer.message;
    showButtons();
    return;
  }
  adopt(answer.data.state, false);
  status.textContent = answer.data.closed
    ? "Round " + number + " is closed. Everybody who is not away is back on the wheel."
    : "That round was closed already.";
}

spinButton.addEventListener("click", spin);
refreshButton.addEventListener("click", () => {
  status.textContent = "";
  load();
});

newRoundButton.addEventListener("click", () => {
  if (state.round.complete) {
    closeRound();
    return;
  }
  const left = people(state.round.remaining).map((person) => person.name);
  confirmText.textContent =
    listOf(left) + (left.length === 1 ? " has" : " have") + " not spoken yet. The round is closed as it is and listed below.";
  confirmDialog.returnValue = "";
  confirmDialog.showModal();
});

confirmDialog.addEventListener("close", () => {
  if (confirmDialog.returnValue === "close") {
    closeRound();
  }
});

// --- The owner's form ------------------------------------------------------------------------

addPersonButton.addEventListener("click", () => {
  const row = teamRow({ username: "", name: "", away: false });
  teamRows.append(row);
  row.querySelector("input").focus();
});

teamForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const members = Array.from(teamRows.querySelectorAll("tr")).map((row) => ({
    username: row.querySelector('input[name="username"]').value,
    name: row.querySelector('input[name="name"]').value,
    away: row.querySelector('input[name="away"]').checked,
  }));
  saveTeamButton.disabled = true;
  status.textContent = "";
  teamStatus.textContent = "Saving...";
  // The version says which list the form was filled from; the server refuses a save over a newer one.
  const answer = await ask("api/team", "PUT", { version: state.team.version, members });
  saveTeamButton.disabled = false;
  if (answer.ok) {
    formFor = null;
    adopt(answer.data, false);
    teamStatus.textContent = "Saved.";
    return;
  }
  teamStatus.textContent = answer.message;
  if (answer.status === 409) {
    formFor = null;
    if (await load()) {
      teamStatus.textContent = answer.message + " The form shows the list as it is now: make your change again.";
    }
  }
});

load();
