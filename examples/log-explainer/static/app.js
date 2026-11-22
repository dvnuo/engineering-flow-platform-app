// Every URL in this file is relative ("api/explain", not "/api/explain"):
// the app is served under /{slug}/ once deployed. The one exception is the
// EFP kit at /_efp/lib/, which every gateway serves to every app.
//
// The answer is streamed: the backend sends plain text piece by piece, and
// the page shows each piece as it arrives, with a caret at the end while the
// model is still writing.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const form = $("explain-form");
const button = $("explain-button");
const log = $("log");
const answer = $("answer");

// The backend cuts the log anyway; sending only its end keeps the request small.
const MAX_SENT = 100000;
const MAX_READ = 12000; // what main.py sends to the model (MAX_LOG_CHARS)

let controller = null; // aborts the answer that is being written
let written = ""; // the answer so far, for Copy

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function failure(response) {
  if (response.status === 401) {
    return { code: "sign_in", message: "Your session has ended. Reload the page to sign in again." };
  }
  try {
    const body = await response.json();
    return { code: (body.error && body.error.code) || "", message: (body.error && body.error.message) || "The app answered with an error (" + response.status + ")." };
  } catch (error) {
    return { code: "", message: "The app answered with an error (" + response.status + ")." };
  }
}

function setState(icon, text, live) {
  $("state").replaceChildren(live ? h("span.efp-live.lx-live") : kit.icon(icon, "size-3.5"), h("span", { text }));
}

// What the visitor pasted, and what of it the model will read.
function showSize() {
  const length = log.value.trim().length;
  $("clear").hidden = length === 0;
  $("size").replaceChildren(
    ...(length === 0
      ? []
      : length > MAX_READ
        ? [kit.icon("scissors", "size-3.5"), length.toLocaleString("en") + " characters: the model reads the last " + MAX_READ.toLocaleString("en")]
        : [length.toLocaleString("en") + " characters"]),
  );
}

// A problem instead of the answer: "not set up" is a state to explain, the rest are errors.
function showProblem(problem) {
  const notSetUp = ["not_configured", "llm_disabled"].includes(problem.code);
  $("thinking").hidden = true;
  $("problem").hidden = false;
  $("problem").replaceChildren(
    notSetUp
      ? h(
          "div.efp-empty",
          {},
          h("span.efp-empty-icon", {}, kit.icon("plug-zap")),
          h("strong", {}, "Not set up yet"),
          h("span.text-sm.max-w-sm", { text: problem.message }),
        )
      : h(
          "div.alert.alert-soft",
          { class: problem.code === "budget_exceeded" ? "alert-warning" : "alert-error", role: "alert" },
          kit.icon(problem.code === "budget_exceeded" ? "coins" : "circle-alert", "size-5"),
          h("span", { text: problem.message }),
        ),
  );
  setState(notSetUp ? "plug-zap" : "circle-alert", notSetUp ? "Not set up" : "Not answered");
}

// One piece of the answer, faded in where the caret is.
function append(piece) {
  if (!piece) return;
  if (answer.hidden) {
    $("thinking").hidden = true;
    answer.hidden = false;
    setState("", "Writing...", true);
  }
  written += piece;
  answer.insertBefore(h("span.lx-piece", {}, piece), answer.lastChild);
}

async function explain() {
  const text = log.value.trim();
  if (!text) {
    kit.shake(log);
    log.focus();
    return;
  }
  controller = new AbortController();
  written = "";
  const started = performance.now();
  $("empty").hidden = true;
  $("problem").hidden = true;
  $("thinking").hidden = false;
  $("copy").hidden = true;
  $("stop").hidden = false;
  answer.hidden = true;
  answer.replaceChildren(h("span.lx-caret", { "aria-hidden": "true" }));
  setState("", "Reading the log...", true);

  let ending = "done";
  try {
    await kit.busy(button, async () => {
      let response;
      try {
        response = await fetch("api/explain", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: text.slice(-MAX_SENT) }),
          signal: controller.signal,
        });
      } catch (error) {
        if (error.name === "AbortError") {
          ending = "stopped";
          return;
        }
        throw { code: "unreachable", message: "The app cannot be reached right now." };
      }
      if (!response.ok) throw await failure(response);
      // The answer arrives piece by piece as plain text.
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          append(decoder.decode(value, { stream: true }));
        }
      } catch (error) {
        ending = error.name === "AbortError" ? "stopped" : "lost";
        if (ending === "lost") append("\n[The connection was lost.]");
      }
    });
  } catch (problem) {
    ending = "problem";
    answer.hidden = true;
    showProblem(problem);
  }

  controller = null;
  $("stop").hidden = true;
  if (answer.lastChild) answer.lastChild.remove(); // the caret
  if (ending === "problem") return;
  if (!written) {
    $("thinking").hidden = true;
    showProblem({ code: "", message: ending === "stopped" ? "Stopped before the model wrote anything." : "The model sent an empty answer. Try again." });
    return;
  }
  $("copy").hidden = false;
  const seconds = ((performance.now() - started) / 1000).toFixed(1);
  if (ending === "done") setState("circle-check", "Written in " + seconds + " s");
  else setState("circle-pause", ending === "stopped" ? "Stopped" : "Interrupted");
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  explain();
});

// Ctrl+Enter explains from the log field; Escape stops an answer that is being written.
log.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    form.requestSubmit();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && controller) controller.abort();
});

log.addEventListener("input", showSize);
$("stop").addEventListener("click", () => controller && controller.abort());
$("clear").addEventListener("click", () => {
  log.value = "";
  showSize();
  log.focus();
});
$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(written);
    kit.toast("The explanation is copied.", { kind: "success", timeout: 2500 });
  } catch (error) {
    kit.toast("The browser did not allow copying. Select the text to copy it.", { kind: "warning" });
  }
});

await kit.ready();
kit.themeSwitch($("theme"));
showSize();
