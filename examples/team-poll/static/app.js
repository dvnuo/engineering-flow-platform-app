// Every URL in this file is relative ("api/poll", not "/api/poll"): the app
// is served under /{slug}/ once deployed.
//
// The result is loaded when the page opens and when the visitor asks for it.
// There is no timer: a page that asks every few seconds would keep the app
// from ever being idle, and an idle app is what the host stops to save memory.

const form = document.getElementById("poll");
const who = document.getElementById("who");
const question = document.getElementById("question");
const options = document.getElementById("options");
const voteButton = document.getElementById("vote");
const withdrawButton = document.getElementById("withdraw");
const refreshButton = document.getElementById("refresh");
const status = document.getElementById("status");

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

function show(poll) {
  question.textContent = poll.question;
  who.textContent = poll.signed_in
    ? "Every member has one vote. You can change yours."
    : "You are not signed in: you can see the result, not vote.";
  options.replaceChildren();
  for (const option of poll.options) {
    const row = document.createElement("label");
    row.className = "option";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "option";
    radio.value = option.id;
    radio.checked = option.id === poll.your_vote;
    radio.disabled = !poll.signed_in;
    const label = document.createElement("span");
    label.textContent = option.label;
    const count = document.createElement("span");
    count.className = "muted";
    count.textContent = plural(option.votes, "vote");
    const bar = document.createElement("span");
    bar.className = "bar";
    const fill = document.createElement("span");
    fill.style.width = (poll.total ? Math.round((option.votes / poll.total) * 100) : 0) + "%";
    bar.append(fill);
    row.append(radio, label, count, bar);
    options.append(row);
  }
  voteButton.disabled = !poll.signed_in;
  withdrawButton.hidden = !poll.your_vote;
  status.textContent = plural(poll.total, "vote") + " in total." + (poll.your_vote ? " Yours is counted." : "");
}

async function call(url, request) {
  status.textContent = "";
  try {
    const response = await fetch(url, request);
    if (!response.ok) {
      status.textContent = await errorMessage(response);
      return;
    }
    show(await response.json());
  } catch (error) {
    status.textContent = "The app cannot be reached right now.";
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const chosen = new FormData(form).get("option");
  if (!chosen) {
    status.textContent = "Choose an option first.";
    return;
  }
  call("api/vote", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ option: chosen }),
  });
});

withdrawButton.addEventListener("click", () => call("api/vote", { method: "DELETE" }));
refreshButton.addEventListener("click", () => call("api/poll"));

call("api/poll");
