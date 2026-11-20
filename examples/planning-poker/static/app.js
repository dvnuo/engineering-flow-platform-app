// The page: the lobby, or one room when the address names one (?room=...).
//
// In a room nothing is asked on a timer. The server pushes the room over a
// WebSocket whenever it changes, and the page draws what it was sent. What
// the others picked is not in it before the reveal (see main.py), so there
// is nothing here to keep hidden.

import * as api from "./api.js";
import { memberStatus, number, picksLine, plural, roundLine, safeLink, spreadRows, verdict } from "./format.js";
import { openRoom } from "./socket.js";

const $ = (id) => document.getElementById(id);

function say(target, text, isProblem) {
  target.textContent = text || "";
  target.classList.toggle("problem", Boolean(isProblem));
}

function make(tag, className, text) {
  const made = document.createElement(tag);
  if (className) {
    made.className = className;
  }
  if (text !== undefined) {
    made.textContent = text;
  }
  return made;
}

// --- The lobby ------------------------------------------------------------------------

async function loadLobby() {
  const status = $("lobby-status");
  let found;
  try {
    found = await api.listRooms();
  } catch (error) {
    say(status, error.message, true);
    return;
  }
  $("who").textContent = found.signed_in
    ? "Signed in as " + found.me.name + ". Open a room for a story and send its link to the team."
    : "You are not signed in. Sign in to open or join a room.";
  $("new-room").hidden = !found.signed_in;
  $("rooms-section").hidden = !found.signed_in;
  $("deck-preview").textContent = "The cards: " + found.deck.join(", ") + ".";
  $("rooms").replaceChildren(
    ...found.rooms.map((room) => {
      const item = make("li", "room-row");
      const link = make("a", "", room.title);
      link.href = "?room=" + encodeURIComponent(room.id); // relative: it stays inside the app
      const date = new Date(room.updated_at);
      const day = Number.isNaN(date.getTime()) ? "" : ", last used " + date.toLocaleDateString(undefined, { dateStyle: "medium" });
      item.append(link, make("span", "muted", "Round " + room.round + ", " + plural(room.rounds_done, "round") + " finished" + day));
      return item;
    })
  );
  $("rooms-empty").hidden = found.rooms.length > 0;
  say(status, "");
}

function showLobby() {
  $("lobby").hidden = false;
  $("new-room").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const made = await api.createRoom($("new-title").value.trim(), $("new-link").value.trim());
      location.assign("?room=" + encodeURIComponent(made.id));
    } catch (error) {
      say($("lobby-status"), error.message, true);
    }
  });
  $("refresh").addEventListener("click", loadLobby);
  loadLobby();
}

// --- A room -----------------------------------------------------------------------------

const CONNECTION = {
  connecting: "Connecting...",
  connected: "Connected",
  paused: "Paused while this tab is in the background",
  stopped: "Not connected",
  "signed-out": "Not signed in",
  missing: "No such room",
};

function showRoom(roomId) {
  const status = $("room-status");
  let room = null; // the room as the server last sent it
  let connected = false;
  $("room").hidden = false;

  function send(message) {
    if (!connection.send(message)) {
      say(status, "There is no connection right now. Your click was not sent.", true);
    }
  }

  // The cards never change while a room is open: the buttons are made once
  // and keep the keyboard focus, only their state is updated.
  function drawDeck() {
    if ($("deck").children.length !== room.deck.length) {
      $("deck").replaceChildren(
        ...room.deck.map((card) => {
          const choice = make("button", "card", card);
          choice.type = "button";
          choice.dataset.card = card;
          // Clicking the chosen card again takes the choice back.
          choice.addEventListener("click", () => send({ type: "pick", card: room.you.card === card ? null : card }));
          return choice;
        })
      );
    }
    for (const choice of $("deck").children) {
      choice.setAttribute("aria-pressed", String(choice.dataset.card === room.you.card));
      choice.disabled = !connected || room.revealed;
    }
  }

  function drawResult() {
    $("result").hidden = !room.result;
    if (!room.result) {
      return;
    }
    $("verdict").textContent = verdict(room.result);
    $("verdict").classList.toggle("agreed", room.result.consensus);
    $("average").textContent = number(room.result.average);
    $("median").textContent = number(room.result.median);
    $("votes").textContent = String(room.result.votes);
    $("spread").replaceChildren(
      ...spreadRows(room.result).map((row) => {
        const item = make("li");
        const bar = make("span", "bar");
        const fill = make("span");
        fill.style.width = row.width + "%";
        bar.append(fill);
        item.append(make("span", "spread-card", row.card), bar, make("span", "muted", plural(row.count, "card")));
        return item;
      })
    );
  }

  function draw() {
    if (!room) {
      return;
    }
    $("table").hidden = false;
    document.title = room.title + " - Planning poker";
    $("story").textContent = room.title;
    $("round").textContent = "Round " + room.round;
    const link = safeLink(room.link);
    $("story-link").hidden = !link;
    if (link) {
      $("story-link").href = link;
      $("story-link").textContent = link;
    }
    drawDeck();
    const picked = room.members.filter((member) => member.picked).length;
    $("reveal").disabled = !connected || room.revealed || picked === 0;
    $("reveal").textContent = room.revealed ? "Revealed" : "Reveal (" + picked + " of " + room.members.length + " picked)";
    $("next").disabled = !connected;
    $("members").replaceChildren(
      ...room.members.map((member) => {
        const item = make("li", member.online ? "" : "away");
        const state = memberStatus(member, room.revealed);
        const chip = make("span", room.revealed && member.picked ? "chip shown" : member.picked ? "chip picked" : "chip", state);
        item.append(make("span", "name", member.name + (member.id === room.you.id ? " (you)" : "")), chip);
        return item;
      })
    );
    drawResult();
    $("history-section").hidden = room.history.length === 0;
    $("history").replaceChildren(
      ...room.history.map((entry) => {
        const item = make("li");
        item.append(make("strong", "", entry.title), make("span", "", roundLine(entry)), make("span", "muted", picksLine(entry)));
        return item;
      })
    );
  }

  const connection = openRoom(roomId, {
    onState(sent) {
      room = sent;
      say(status, "");
      draw();
    },
    onRefusal(message) {
      say(status, message, true);
    },
    onStatus(state, delay) {
      connected = state === "connected";
      const pill = $("connection");
      pill.textContent = state === "waiting" ? "Connection lost. Trying again in " + Math.round(delay / 1000) + " s" : CONNECTION[state];
      pill.dataset.state = connected ? "on" : state === "connecting" || state === "waiting" ? "busy" : "off";
      $("retry").hidden = !(state === "stopped" || state === "waiting" || state === "paused");
      if (state === "signed-out") {
        say(status, "Sign in to join a room. If you were signed in, reload the page.", true);
      } else if (state === "missing") {
        say(status, "This room does not exist any more. Open a new one from the list of rooms.", true);
      } else if (state === "stopped") {
        say(status, "The room cannot be reached. Check your network, then try again.", true);
      }
      draw();
    },
  });

  $("retry").addEventListener("click", () => connection.retryNow());
  $("reveal").addEventListener("click", () => send({ type: "reveal" }));

  $("next").addEventListener("click", () => {
    const pending = room && !room.revealed && room.members.some((member) => member.picked);
    $("next-title").value = room ? room.title : "";
    $("next-link").value = (room && room.link) || "";
    $("next-note").textContent = pending
      ? "The cards of this round have not been revealed. They will be thrown away."
      : "Keep the title to estimate the same story again.";
    $("next-form").hidden = false;
    $("next-title").focus();
    $("next-title").select();
  });
  $("next-cancel").addEventListener("click", () => {
    $("next-form").hidden = true;
    $("next").focus();
  });
  $("next-form").addEventListener("submit", (event) => {
    event.preventDefault();
    send({ type: "new_round", title: $("next-title").value.trim(), link: $("next-link").value.trim() || null });
    $("next-form").hidden = true;
    $("next").focus();
  });

  $("copy-link").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      say(status, "The link is copied. Whoever opens it joins this room.");
    } catch (error) {
      say(status, "Copy this page's address from the address bar to invite the others.");
    }
  });
}

const wanted = new URLSearchParams(location.search).get("room");
if (wanted) {
  showRoom(wanted);
} else {
  showLobby();
}
