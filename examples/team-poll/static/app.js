// Every URL in this file is relative ("api/poll", not "/api/poll"): the app
// is served under /{slug}/ once deployed. The one exception is the EFP kit at
// /_efp/lib/, which every gateway serves to every app (docs/UI_KIT.md).
//
// The result is loaded when the page opens and when the visitor asks for it.
// There is no timer: a page that asks every few seconds would keep the app
// from ever being idle, and an idle app is what the host stops to save memory.
//
// Each answer is the whole poll. The page keeps one row per option and
// compares the answer with the one before, so that only what changed moves:
// bars glide to their new length, numbers count, a row that somebody else's
// vote changed lights up.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const optionsList = $("options");
const rows = new Map(); // option id -> the parts of its row
let poll = null; // the poll as the server last sent it

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function errorMessage(response) {
  if (response.status === 401) {
    return "Your session has ended. Reload the page to sign in again.";
  }
  try {
    const body = await response.json();
    return (body.error && body.error.message) || "The app answered with an error (" + response.status + ").";
  } catch (error) {
    return "The app answered with an error (" + response.status + ").";
  }
}

function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

// One request; the answer is the whole poll, or an Error with a sentence.
async function call(url, request) {
  let response;
  try {
    response = await fetch(url, request);
  } catch (error) {
    throw new Error("The app cannot be reached right now.");
  }
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  return response.json();
}

// Run one request and show what it answered; true when it worked. The button
// that started it shows that it is working (kit.busy) and shakes when it failed.
async function run(work, button) {
  try {
    show(await (button ? kit.busy(button, work) : work()));
    return true;
  } catch (error) {
    if (poll) {
      kit.toast(error.message, { kind: "error" });
    } else {
      $("problem").hidden = false;
      $("problem").replaceChildren(h("div.alert.alert-soft.alert-error", { role: "alert" }, kit.icon("circle-alert", "size-5"), h("span", { text: error.message })));
      optionsList.replaceChildren();
    }
    return false;
  }
}

let voting = false; // one vote at a time: another click waits for the answer

// The number of the chosen row turns into a spinner, then a tick (kit.busy
// on the number, so that the rest of the row stays readable).
async function vote(option) {
  if (!poll || !poll.signed_in || voting) return;
  const row = rows.get(option.id);
  if (poll.your_vote === option.id) {
    kit.pop(row.key); // it is your vote already
    return;
  }
  voting = true;
  row.button.classList.add("is-choosing");
  row.button.setAttribute("aria-busy", "true");
  await run(
    () =>
      call("api/vote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ option: option.id }),
      }),
    row.key,
  );
  row.button.classList.remove("is-choosing");
  row.button.removeAttribute("aria-busy");
  voting = false;
}

// The options keep the order of poll.json: a row that moved under the
// pointer would be clicked by mistake. Only their insides change.
function makeRow(option, index) {
  const parts = {
    fill: h("span.tp-fill", { "aria-hidden": "true" }),
    key: h("span.tp-key", { "aria-hidden": "true" }, String(index + 1)),
    crown: h("span.tp-crown", { title: "Leading", hidden: true }, kit.icon("crown", "size-4")),
    count: h("span.tp-count.efp-number", {}, "0"),
    percent: h("span.tp-percent.efp-number", {}, "0%"),
  };
  parts.button = h(
    "button.tp-option",
    { type: "button", onclick: () => vote(option) },
    parts.fill,
    parts.key,
    h("span.tp-label", { text: option.label }),
    parts.crown,
    h("span.tp-numbers", {}, parts.count, parts.percent),
  );
  parts.item = h("li", {}, parts.button);
  return parts;
}

function show(next) {
  const before = poll;
  poll = next;
  $("problem").hidden = true;
  $("question").textContent = poll.question;
  $("who").textContent = poll.signed_in
    ? "Every member has one vote. You can change yours."
    : "You are not signed in: you can see the result, not vote.";

  const fresh = rows.size !== poll.options.length || poll.options.some((option) => !rows.has(option.id));
  if (fresh) {
    rows.clear();
    poll.options.forEach((option, index) => rows.set(option.id, makeRow(option, index)));
    optionsList.replaceChildren(...[...rows.values()].map((row) => row.item));
    kit.rise(optionsList.children, { stagger: 70 });
  }

  const top = Math.max(0, ...poll.options.map((option) => option.votes));
  const mineMoved = before && before.your_vote !== poll.your_vote;
  poll.options.forEach((option, index) => {
    const row = rows.get(option.id);
    const was = before ? before.options.find((other) => other.id === option.id) : null;
    const share = poll.total ? option.votes / poll.total : 0;
    const mine = option.id === poll.your_vote;
    const leading = top > 0 && option.votes === top;

    // Two frames: a new row is drawn at zero first, so that its bar grows in.
    requestAnimationFrame(() => requestAnimationFrame(() => row.fill.style.setProperty("width", share * 100 + "%")));
    kit.countUp(row.count, option.votes);
    kit.countUp(row.percent, Math.round(share * 100), { format: (value) => Math.round(value) + "%" });
    row.button.setAttribute("aria-pressed", String(mine));
    row.button.disabled = !poll.signed_in;
    row.button.setAttribute("aria-label", option.label + ": " + plural(option.votes, "vote") + (mine ? ", your vote" : ""));
    row.key.replaceChildren(mine ? kit.icon("check", "size-4") : String(index + 1));
    row.item.classList.toggle("is-leading", leading);
    const wasLeading = row.crown.hidden === false;
    row.crown.hidden = !leading;

    if (!before) return;
    if (mine && before.your_vote !== option.id) {
      kit.appear(row.key); // your vote has landed here
    } else if (was && was.votes !== option.votes && !(mineMoved && (before.your_vote === option.id || mine))) {
      kit.flash(row.button); // somebody else's vote changed this one
    }
    if (leading && !wasLeading) kit.appear(row.crown);
  });

  kit.countUp($("total"), poll.total);
  $("total-label").textContent = (poll.total === 1 ? "vote" : "votes") + " so far";
  $("withdraw").hidden = !poll.your_vote;
  $("note").replaceChildren(...note());

  // The first vote of this visit: a small burst from where it landed.
  if (before && !before.your_vote && poll.your_vote) {
    kit.celebrate({ from: rows.get(poll.your_vote).key, particles: 60 });
  }
}

// The line under the options: what to do next, with the keys that do it.
function note() {
  if (!poll.signed_in) return ["Sign in to vote."];
  const keys = [" Or press ", h("kbd.kbd.kbd-xs", {}, "1"), " to ", h("kbd.kbd.kbd-xs", {}, String(poll.options.length)), "."];
  const hint = h("span.hidden.sm:inline", {}, keys);
  if (poll.your_vote) {
    const chosen = poll.options.find((option) => option.id === poll.your_vote);
    return ["Your vote: ", h("strong", { text: chosen ? chosen.label : poll.your_vote }), ". Pick another option to change it."];
  }
  return [poll.total ? "Pick an option to vote." : "No votes yet. Be the first: pick an option.", hint];
}

// Keys 1 to 9 vote for that option, as long as no field has the focus.
document.addEventListener("keydown", (event) => {
  if (!poll || event.ctrlKey || event.metaKey || event.altKey || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
  const option = poll.options[Number(event.key) - 1];
  if (option && /^[1-9]$/.test(event.key)) vote(option);
});

$("withdraw").addEventListener("click", (event) => run(() => call("api/vote", { method: "DELETE" }), event.currentTarget));

$("refresh").addEventListener("click", async (event) => {
  const before = poll ? JSON.stringify(poll) : "";
  const worked = await run(() => call("api/poll"), event.currentTarget);
  if (worked && before === JSON.stringify(poll)) kit.toast("No new votes since you last looked.", { timeout: 2600 });
});

// Ask for the poll while the icons are drawn, not after.
const first = call("api/poll");
first.catch(() => {}); // run() below reports a failure
await kit.ready();
kit.themeSwitch($("theme"));
run(() => first);
