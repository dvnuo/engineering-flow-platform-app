// The page: the lobby, or one room when the address names one (?room=...).
//
// In a room nothing is asked on a timer. The server pushes the room over a
// WebSocket whenever it changes, and the page draws what it was sent. What
// the others picked is not in it before the reveal (see main.py), so there
// is nothing here to keep hidden.
//
// The look comes from the EFP kit (/_efp/lib/, docs/UI_KIT.md): kit.h builds
// elements without HTML strings, kit.icon draws an icon, and the motion
// (rise, appear, flips, confetti) is skipped for a visitor who asked the
// system for less motion. Each state the server sends is compared with the
// one before, so that only what changed moves.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import * as api from "./api.js";
import { memberStatus, number, picksLine, plural, roundLine, safeLink, spreadRows, verdict } from "./format.js";
import { openRoom } from "./socket.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);

// A message above the page: a problem (red) or a plain note. Empty hides it.
function say(target, text, isProblem) {
  target.hidden = !text;
  target.replaceChildren(
    text
      ? h(
          "div",
          { class: "alert alert-soft " + (isProblem ? "alert-error" : "alert-info") },
          kit.icon(isProblem ? "circle-alert" : "info", "size-5"),
          h("span", { text }),
        )
      : "",
  );
}

function cardName(card) {
  return card === "coffee" ? "A break, please" : card === "?" ? "Not sure" : "Card " + card;
}

// The face of a card: its value, or a cup for "coffee"; the value again in the corners.
function fillCard(element, card) {
  if (card === "coffee") {
    element.replaceChildren(kit.icon("coffee"));
    element.removeAttribute("data-corner");
  } else {
    element.textContent = card;
    element.dataset.corner = card;
  }
  return element;
}

const miniCard = (card) => fillCard(h("span", { class: "pp-card pp-mini" }), card);

// --- The lobby ------------------------------------------------------------------------

function roomTile(room) {
  const date = new Date(room.updated_at);
  const used = Number.isNaN(date.getTime()) ? null : "used " + kit.timeAgo(date);
  const link = h(
    "a",
    { class: "efp-surface efp-lift group flex items-center gap-4 p-5 h-full", href: "?room=" + encodeURIComponent(room.id) }, // relative: it stays inside the app
    h("span", { class: "pp-card pp-back pp-mini", "aria-hidden": "true" }, kit.icon("spade")),
    h(
      "span",
      { class: "min-w-0 grow" },
      h("span", { class: "block font-semibold leading-snug line-clamp-2", text: room.title }),
      h(
        "span",
        { class: "mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-base-content/60" },
        h("span", { class: "badge badge-soft badge-primary badge-sm" }, "Round " + room.round),
        h("span", {}, plural(room.rounds_done, "round") + " finished"),
        used ? h("span", { title: date.toLocaleString() }, used) : null,
      ),
    ),
    kit.icon("arrow-right", "size-5 shrink-0 opacity-30 transition duration-300 group-hover:translate-x-1 group-hover:opacity-90"),
  );
  return h("li", {}, link);
}

async function loadLobby(button) {
  const status = $("lobby-status");
  const work = async () => {
    const found = await api.listRooms();
    $("who").textContent = found.signed_in ? "Signed in as " + found.me.name : "Not signed in";
    $("me").replaceChildren(found.signed_in ? kit.avatar(found.me.name, { size: "2rem" }) : "");
    $("new-room").hidden = !found.signed_in;
    $("signed-out").hidden = found.signed_in;
    $("rooms-section").hidden = !found.signed_in;
    $("deck-preview").replaceChildren(...found.deck.map(miniCard));
    $("rooms").replaceChildren(...found.rooms.map(roomTile));
    $("rooms-empty").hidden = found.rooms.length > 0;
    kit.rise($("rooms").children, { stagger: 60 });
  };
  try {
    await (button ? kit.busy(button, work, { tick: false }) : work());
    say(status, "");
  } catch (error) {
    say(status, error.message, true);
  }
}

function showLobby() {
  $("lobby").hidden = false;
  $("new-room").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter || $("new-room").querySelector("[type=submit]");
    try {
      const made = await kit.busy(button, () => api.createRoom($("new-title").value.trim(), $("new-link").value.trim()), { tick: false });
      if (made) {
        location.assign("?room=" + encodeURIComponent(made.id));
      }
    } catch (error) {
      kit.toast(error.message, { kind: "error" });
    }
  });
  $("refresh").addEventListener("click", (event) => loadLobby(event.currentTarget));
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

const FLIP_STEP = 110; // ms between two cards turning over (pp-flip's transition-delay)
const FLIP_TIME = 760; // ms one card takes

function showRoom(roomId) {
  const status = $("room-status");
  let room = null; // the room as the server last sent it
  let connected = false;
  let landing = false; // our own card is on its way from the hand to the table
  const seats = new Map(); // member id -> the seat's <li>
  $("room").hidden = false;

  function send(message) {
    if (!connection.send(message)) {
      say(status, "There is no connection right now. Your click was not sent.", true);
      return false;
    }
    return true;
  }

  // ---- Your hand. The cards never change while a room is open: the buttons are
  // made once and keep the keyboard focus, only their state is updated.

  function drawDeck() {
    const deck = $("deck");
    if (deck.children.length !== room.deck.length) {
      const middle = (room.deck.length - 1) / 2;
      deck.replaceChildren(
        ...room.deck.map((card, index) => {
          const offset = index - middle;
          const choice = h("button", {
            type: "button",
            class: "pp-card",
            "aria-label": cardName(card),
            dataset: { card },
            style: { "--r": (offset * 2.4).toFixed(2), "--y": (offset * offset * 1.1).toFixed(1) },
          });
          fillCard(choice, card);
          // Clicking the chosen card again takes the choice back.
          choice.addEventListener("click", () => choose(card, choice));
          return choice;
        }),
      );
      kit.rise(deck.children, { stagger: 35, distance: 36, duration: 700 });
    }
    for (const choice of deck.children) {
      choice.setAttribute("aria-pressed", String(choice.dataset.card === room.you.card));
      choice.disabled = !connected || room.revealed;
    }
  }

  function choose(card, button) {
    const taking = room.you.card === card;
    if (send({ type: "pick", card: taking ? null : card }) && !taking && !room.you.card) {
      flyToSeat(button);
    }
  }

  // A card back travels from the hand to your place at the table.
  function flyToSeat(from) {
    const seat = seats.get(room.you.id);
    const slot = seat && seat.querySelector(".pp-slot");
    if (!slot || kit.reducedMotion()) return;
    const a = from.getBoundingClientRect();
    const b = slot.getBoundingClientRect();
    const ghost = h(
      "span",
      { class: "pp-card pp-back pp-flying", style: { left: a.left + "px", top: a.top + "px", width: a.width + "px", height: a.height + "px" } },
      kit.icon("spade"),
    );
    document.body.append(ghost);
    const dx = b.left + b.width / 2 - (a.left + a.width / 2);
    const dy = b.top + b.height / 2 - (a.top + a.height / 2);
    const scale = b.width / a.width;
    landing = true;
    slot.dataset.incoming = "";
    ghost
      .animate(
        [
          { transform: "none" },
          { transform: `translate(${dx * 0.45}px, ${dy * 0.45 - 70}px) rotate(-16deg) scale(${(1 + scale) / 2})`, offset: 0.45 },
          { transform: `translate(${dx}px, ${dy}px) rotate(0deg) scale(${scale})` },
        ],
        { duration: 720, easing: kit.EASE },
      )
      .finished.catch(() => {})
      .finally(() => {
        ghost.remove();
        landing = false;
        delete slot.dataset.incoming;
        kit.pop(slot, { scale: 1.12 });
      });
  }

  // ---- The table: one seat per member, round the table when it is wide enough.

  function makeSeat(member) {
    return h(
      "li",
      { class: "pp-seat", dataset: { id: member.id } },
      h(
        "div",
        { class: "pp-slot", dataset: { state: "empty" } },
        h("div", { class: "pp-flip" }, h("span", { class: "pp-face pp-card pp-back" }, kit.icon("spade")), h("span", { class: "pp-face pp-card pp-front" })),
      ),
      h("div", { class: "pp-person" }, kit.avatar(member.name, { size: "2.1rem" }), h("span", { class: "pp-name" }), h("span", { class: "pp-state" })),
    );
  }

  function updateSeat(seat, member, index, animate) {
    const you = member.id === room.you.id;
    seat.classList.toggle("is-you", you);
    seat.classList.toggle("is-away", !member.online);
    seat.querySelector(".pp-name").textContent = member.name + (you ? " (you)" : "");
    seat.querySelector(".pp-state").textContent = memberStatus(member, room.revealed);
    const slot = seat.querySelector(".pp-slot");
    const flip = slot.querySelector(".pp-flip");
    flip.style.setProperty("--i", index);
    if (room.revealed && member.picked) {
      fillCard(slot.querySelector(".pp-front"), member.card);
    }
    const was = slot.dataset.state;
    const state = !member.picked ? "empty" : room.revealed ? "up" : "down";
    slot.dataset.state = state;
    // Somebody else's card lands on the table (ours is already flying there).
    if (animate && was === "empty" && state === "down" && !(you && landing) && !kit.reducedMotion()) {
      flip.animate(
        [
          { transform: "translateY(-28px) rotate(-14deg) scale(0.6)", opacity: 0 },
          { transform: "none", opacity: 1 },
        ],
        { duration: 640, easing: kit.SPRING },
      );
    }
  }

  function placeSeats() {
    const table = $("members").closest(".pp-table");
    const list = Array.from($("members").children);
    const round = table.clientWidth >= 560 && list.length >= 2 && list.length <= 12;
    table.classList.toggle("is-round", round);
    if (!round) return;
    // You sit at the bottom, the others round the table from there. The
    // ellipse keeps every seat whole inside the table, whatever its size.
    const mine = Math.max(0, list.findIndex((seat) => Number(seat.dataset.id) === room.you.id));
    const pad = 14;
    const radiusX = ((table.clientWidth / 2 - list[0].offsetWidth / 2 - pad) / table.clientWidth) * 100;
    const radiusY = ((table.clientHeight / 2 - list[0].offsetHeight / 2 - pad) / table.clientHeight) * 100;
    list.forEach((seat, index) => {
      const angle = ((90 + (360 * (index - mine)) / list.length) * Math.PI) / 180;
      seat.style.setProperty("--x", (50 + Math.cos(angle) * radiusX).toFixed(2));
      seat.style.setProperty("--y", (50 + Math.sin(angle) * radiusY).toFixed(2));
    });
  }

  function drawSeats(animate) {
    const list = $("members");
    const present = new Set(room.members.map((member) => member.id));
    for (const [id, seat] of seats) {
      if (!present.has(id)) {
        seats.delete(id);
        seat.remove();
      }
    }
    room.members.forEach((member, index) => {
      let seat = seats.get(member.id);
      if (!seat) {
        seat = makeSeat(member);
        seats.set(member.id, seat);
        list.append(seat);
        if (animate) kit.appear(seat);
      }
      updateSeat(seat, member, index, animate);
    });
    placeSeats();
  }

  // The middle of the table: how many have picked, then the average.
  const center = {
    ring: h("div", { class: "radial-progress", role: "progressbar", style: { "--value": 0 } }),
    big: h("span", { class: "pp-big" }),
    small: h("span", { class: "pp-small" }),
  };
  $("table-center").append(center.ring, center.big, center.small);

  function drawCenter(animate) {
    const total = room.members.length;
    const picked = room.members.filter((member) => member.picked).length;
    center.ring.hidden = room.revealed;
    center.big.hidden = !room.revealed;
    if (room.revealed && room.result) {
      center.big.classList.toggle("efp-gradient-text", room.result.consensus);
      center.small.textContent = room.result.consensus ? "Consensus" : "Average";
      if (animate && typeof room.result.average === "number") {
        center.big.textContent = "0";
        setTimeout(() => kit.countUp(center.big, room.result.average, { from: 0, format: number }), revealTime());
      } else {
        center.big.textContent = number(room.result.average);
      }
      return;
    }
    const percent = total ? Math.round((picked * 100) / total) : 0;
    center.ring.style.setProperty("--value", percent);
    center.ring.setAttribute("aria-valuenow", String(percent));
    center.ring.textContent = picked + "/" + total;
    center.small.textContent = total && picked === total ? "Everybody has picked" : "Waiting for " + plural(total - picked, "card");
  }

  // How long the cards take to turn over: the result waits for them.
  function revealTime() {
    return kit.reducedMotion() ? 0 : FLIP_TIME + FLIP_STEP * Math.max(0, room.members.length - 1);
  }

  // ---- The result and the rounds before.

  function drawResult(animate) {
    const result = room.result;
    $("result").hidden = !result;
    $("how").hidden = Boolean(result) || room.history.length > 0;
    if (!result) return;
    $("verdict").textContent = verdict(result);
    const icon = $("verdict-icon");
    icon.classList.toggle("is-agreed", result.consensus);
    icon.replaceChildren(kit.icon(result.consensus ? "party-popper" : result.votes === 1 ? "hourglass" : "messages-square", "size-5"));
    const rows = spreadRows(result);
    $("spread").replaceChildren(
      ...rows.map((row) =>
        h(
          "li",
          { class: "grid grid-cols-[2rem_1fr_4.5rem] items-center gap-3" },
          miniCard(row.card),
          h("div", { class: "pp-bar" }, h("span", { style: { width: animate ? "0%" : row.width + "%" } })),
          h("span", { class: "text-xs text-base-content/60 text-right efp-number", text: plural(row.count, "card") }),
        ),
      ),
    );
    if (!animate) {
      $("average").textContent = number(result.average);
      $("median").textContent = number(result.median);
      $("votes").textContent = String(result.votes);
      return;
    }
    // After the cards have turned: the panel rises, the numbers count, the bars grow.
    $("result").style.visibility = "hidden";
    setTimeout(() => {
      $("result").style.visibility = "";
      kit.rise($("result"));
      kit.appear(icon, { delay: 120 });
      const count = (id, value) =>
        typeof value === "number" ? kit.countUp($(id), value, { from: 0, format: number }) : ($(id).textContent = number(value));
      count("average", result.average);
      count("median", result.median);
      kit.countUp($("votes"), result.votes, { from: 0 });
      Array.from($("spread").querySelectorAll(".pp-bar > span")).forEach((bar, index) => {
        setTimeout(() => (bar.style.width = rows[index].width + "%"), 90 * index + 60);
      });
      if (result.consensus) {
        kit.celebrate({ particles: 160 });
      }
    }, revealTime());
  }

  const historyItems = new Map(); // "round|title" -> its <li>
  kit.animateList($("history"));

  function drawHistory() {
    $("history-section").hidden = room.history.length === 0;
    const items = room.history.map((entry) => {
      const key = entry.round + "|" + entry.title;
      if (!historyItems.has(key)) {
        historyItems.set(
          key,
          h(
            "li",
            { class: "flex gap-3", title: picksLine(entry) },
            h("span", { class: "pp-step" + (entry.result.consensus ? " !bg-success/15 !text-success" : "") }, String(entry.round)),
            h(
              "div",
              { class: "min-w-0 grow" },
              h("p", { class: "font-medium text-sm truncate", text: entry.title }),
              h("p", { class: "text-xs text-base-content/60", text: roundLine(entry) }),
              h(
                "div",
                { class: "flex flex-wrap gap-1 mt-1.5" },
                ...entry.picks.map((chosen) =>
                  h(
                    "span",
                    { class: "badge badge-sm badge-ghost gap-1" },
                    chosen.name,
                    h("strong", {}, chosen.card === "coffee" ? kit.icon("coffee", "size-3") : chosen.card),
                  ),
                ),
              ),
            ),
          ),
        );
      }
      return historyItems.get(key);
    });
    const list = $("history");
    items.forEach((item, index) => {
      if (list.children[index] !== item) list.insertBefore(item, list.children[index] || null);
    });
    while (list.children.length > items.length) list.lastElementChild.remove();
  }

  // ---- Everything, from what the server sent (and what it sent before).

  function draw(previous) {
    if (!room) return;
    const first = !previous;
    const revealedNow = Boolean(previous && !previous.revealed && room.revealed);
    const newRound = Boolean(previous && previous.round !== room.round);
    $("table").hidden = false;
    document.title = room.title + " - Planning poker";
    $("story").textContent = room.title;
    $("round").textContent = "Round " + room.round;
    $("who").textContent = "Signed in as " + room.you.name;
    if (!$("me").firstChild) $("me").append(kit.avatar(room.you.name, { size: "2rem" }));
    const link = safeLink(room.link);
    $("story-link").hidden = !link;
    if (link) {
      $("story-link").href = link;
      $("story-link").querySelector("span").textContent = link;
    }
    drawDeck();
    drawSeats(!first);
    drawCenter(revealedNow);
    const picked = room.members.filter((member) => member.picked).length;
    $("reveal").disabled = !connected || room.revealed || picked === 0;
    $("reveal-text").textContent = room.revealed ? "Revealed" : "Reveal (" + picked + " of " + room.members.length + " picked)";
    $("next").disabled = !connected;
    if (first || revealedNow || newRound || previous.result !== null || room.result !== null) {
      drawResult(revealedNow);
    }
    drawHistory();
    if (first) {
      kit.rise([$("table").querySelector(".pp-table"), $("deck").closest("section"), ...$("table").querySelectorAll("aside > section:not([hidden])")], { stagger: 80 });
    }
    if (newRound) {
      kit.toast("Round " + room.round + ": " + room.title, { kind: "info", timeout: 3200 });
      kit.flash($("story"));
    }
  }

  const connection = openRoom(roomId, {
    onState(sent) {
      const previous = room;
      room = sent;
      say(status, "");
      draw(previous);
    },
    onRefusal(message) {
      say(status, message, true);
    },
    onStatus(state, delay) {
      connected = state === "connected";
      const pill = $("connection");
      $("connection-text").textContent =
        state === "waiting" ? "Connection lost. Trying again in " + Math.round(delay / 1000) + " s" : CONNECTION[state];
      pill.dataset.state = connected ? "on" : state === "connecting" || state === "waiting" ? "busy" : "off";
      $("retry").hidden = !(state === "stopped" || state === "waiting" || state === "paused");
      if (state === "signed-out") {
        say(status, "Sign in to join a room. If you were signed in, reload the page.", true);
      } else if (state === "missing") {
        say(status, "This room does not exist any more. Open a new one from the list of rooms.", true);
      } else if (state === "stopped") {
        say(status, "The room cannot be reached. Check your network, then try again.", true);
      }
      draw(room);
    },
  });

  new ResizeObserver(() => room && placeSeats()).observe($("members").closest(".pp-table"));

  $("retry").addEventListener("click", () => connection.retryNow());
  $("reveal").addEventListener("click", () => send({ type: "reveal" }));

  const dialog = $("next-dialog");
  $("next").addEventListener("click", () => {
    const pending = room && !room.revealed && room.members.some((member) => member.picked);
    $("next-title").value = room ? room.title : "";
    $("next-link").value = (room && room.link) || "";
    $("next-note").textContent = pending
      ? "The cards of this round have not been revealed. They will be thrown away."
      : "Keep the title to estimate the same story again.";
    dialog.showModal();
    $("next-title").select();
  });
  $("next-cancel").addEventListener("click", () => dialog.close());
  $("next-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (send({ type: "new_round", title: $("next-title").value.trim(), link: $("next-link").value.trim() || null })) {
      dialog.close();
    }
  });

  $("copy-link").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    try {
      await navigator.clipboard.writeText(location.href);
      kit.toast("The link is copied. Whoever opens it joins this room.", { kind: "success" });
      const before = button.firstElementChild;
      const done = kit.icon("check");
      button.replaceChild(done, before);
      kit.appear(done);
      setTimeout(() => button.replaceChild(before, done), 1800);
    } catch (error) {
      kit.toast("Copy this page's address from the address bar to invite the others.");
    }
  });

  // Typing a card's value picks it: 5, 1 then 3 for 13, ? and c for coffee.
  let typed = "";
  let typedAt = 0;
  document.addEventListener("keydown", (event) => {
    if (!room || room.revealed || !connected || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest && event.target.closest("input, textarea, select, button, dialog[open]")) return;
    let wanted = null;
    if (event.key === "c") wanted = "coffee";
    else if (event.key === "?") wanted = "?";
    else if (/^[0-9]$/.test(event.key)) {
      typed = (Date.now() - typedAt < 800 ? typed : "") + event.key;
      typedAt = Date.now();
      wanted = room.deck.includes(typed) ? typed : room.deck.includes(event.key) ? event.key : null;
      if (wanted === event.key) typed = event.key;
    }
    const button = wanted && Array.from($("deck").children).find((choice) => choice.dataset.card === wanted);
    if (button && room.you.card !== wanted) {
      event.preventDefault();
      button.click();
      kit.pop(button, { scale: 1.08 });
    }
  });
}

await kit.ready();
kit.themeSwitch($("theme"));

const wanted = new URLSearchParams(location.search).get("room");
if (wanted) {
  showRoom(wanted);
} else {
  showLobby();
}
