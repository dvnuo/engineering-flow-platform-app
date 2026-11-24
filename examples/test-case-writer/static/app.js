// Every URL in this file is relative ("api/write", not "/api/write"): the
// app is served under /{slug}/ once deployed. The one exception is the EFP
// kit at /_efp/lib/, which every gateway serves to every app.
//
// The scenarios are streamed as lines of JSON (scenarios.js reads them). While
// the model writes, each piece is shown as it arrives; when it is done, the
// finished text is shown again in its form: Gherkin with its keywords and
// tags marked, or the table as a table. Everything the model wrote stays
// text: elements are built with kit.h, never from HTML.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import { LineReader, counter, emptyView, gherkinLine, issueKeyOf, outcome, reduce, tableOf, tally, tokens } from "./scenarios.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const form = $("write-form");
const story = $("story");
const keyField = $("key");
const answer = $("answer");

const MAX_SENT = 100000; // the backend reads the first 6,000 characters anyway; this keeps the request small
const page = {
  limits: { story: 6000, min: 3, max: 12, default: 6 },
  modelUsable: true,
  controller: null, // aborts the answer that is being written
  view: emptyView(),
  style: "gherkin", // of the answer on show, which may differ from the choice made since
};

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

async function post(url, body, signal) {
  let response;
  try {
    response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw { code: "unreachable", message: "The app cannot be reached right now." };
  }
  if (!response.ok) throw await failure(response);
  return response;
}

function chosenStyle() {
  return form.querySelector('input[name="style"]:checked').value;
}

// --- The story -------------------------------------------------------------------------------

function showSize() {
  const size = counter(story.value, page.limits.story);
  $("clear").hidden = size.length === 0;
  $("size").classList.toggle("is-over", size.over);
  $("size").replaceChildren(...(size.length === 0 ? [] : [size.over ? kit.icon("scissors", "size-3.5") : null, size.label].filter(Boolean)));
  $("size").title = size.over ? "Only the first " + page.limits.story.toLocaleString("en") + " characters go to the model" : "";
}

function showCount() {
  $("count-value").textContent = $("count").value;
}

function issueStatus(kind, text) {
  const icons = { info: "info", done: "circle-check", problem: "circle-alert" };
  const line = $("issue-status");
  line.dataset.kind = kind;
  line.replaceChildren(kit.icon(icons[kind], "size-3.5"), h("span", { text }));
}

async function readIssue() {
  const key = issueKeyOf(keyField.value);
  if (!key) {
    // Nothing that is not a key is sent: the server would refuse it anyway.
    issueStatus("problem", "Enter an issue key such as ABC-123.");
    kit.shake(keyField.parentElement);
    keyField.focus();
    return;
  }
  keyField.value = key;
  try {
    const read = await kit.busy($("read-button"), async () => (await post("api/issue", { key })).json());
    const before = story.value;
    story.value = read.story;
    showSize();
    kit.flash(story);
    issueStatus("done", "Read " + read.key + ". Check the text, shorten it if you like, then write.");
    if (before.trim() && before !== read.story) {
      kit.toast("The story was replaced by " + read.key + ".", { action: { label: "Undo", onClick: () => ((story.value = before), showSize()) } });
    }
  } catch (problem) {
    issueStatus("problem", problem.message);
  }
}

// --- The answer ------------------------------------------------------------------------------

function setState(icon, text, live) {
  $("state").replaceChildren(live ? h("span.efp-live.tc-live") : kit.icon(icon, "size-3.5"), h("span", { text }));
}

function showProblem(problem) {
  const notSetUp = ["not_configured", "llm_disabled"].includes(problem.code);
  $("thinking").hidden = true;
  $("problem").hidden = false;
  $("problem").replaceChildren(
    notSetUp
      ? h("div.efp-empty", {}, h("span.efp-empty-icon", {}, kit.icon("plug-zap")), h("strong", {}, "Not set up yet"), h("span.text-sm.max-w-sm", { text: problem.message }))
      : h(
          "div.alert.alert-soft",
          { class: problem.code === "budget_exceeded" ? "alert-warning" : "alert-error", role: "alert" },
          kit.icon(problem.code === "budget_exceeded" ? "coins" : "circle-alert", "size-5"),
          h("span", { text: problem.message }),
        ),
  );
  setState(notSetUp ? "plug-zap" : "circle-alert", notSetUp ? "Not set up" : "Not written");
}

// One piece of the answer, faded in where the caret is.
function append(piece) {
  if (!piece) return;
  if (answer.hidden) {
    $("thinking").hidden = true;
    answer.hidden = false;
  }
  answer.insertBefore(h("span.tc-piece", {}, piece), answer.lastChild);
}

const KIND_OF_TAG = { "tag-main": "tc-tag tc-main", "tag-negative": "tc-tag tc-negative", "tag-edge": "tc-tag tc-edge" };

// The finished Gherkin, a line at a time, with its keywords and tags marked.
function gherkin(text) {
  const lines = text.split("\n");
  return lines.flatMap((line, index) => [
    ...gherkinLine(line).map(([kind, piece]) => (kind === "plain" ? piece : h("span", { class: KIND_OF_TAG[kind] || "tc-g-" + kind, text: piece }))),
    // Not null for "no newline": replaceChildren() would write it as the text "null".
    ...(index < lines.length - 1 ? ["\n"] : []),
  ]);
}

// The finished table as a table; the Type column as a coloured label.
function table(found) {
  const typeColumn = found.header.findIndex((name) => /^type$/i.test(name));
  const cell = (text, index) => {
    const kind = text.toLowerCase();
    return index === typeColumn && ["main", "negative", "edge"].includes(kind) ? h("td", {}, h("span", { class: "tc-tag tc-" + kind, text })) : h("td", { text });
  };
  return h(
    "div.tc-table-wrap",
    {},
    h(
      "table.table.table-sm.tc-table",
      {},
      h("thead", {}, h("tr", {}, found.header.map((name) => h("th", { text: name })))),
      h("tbody", {}, found.rows.map((row) => h("tr", {}, row.map(cell)))),
    ),
  );
}

function showFinished(view, style) {
  const found = style === "table" ? tableOf(view.text) : null;
  answer.classList.toggle("is-table", Boolean(found));
  answer.replaceChildren(...(found ? [table(found)] : style === "gherkin" ? gherkin(view.text) : [view.text]));
  const counts = tally(view.text, style);
  const chips = [
    ["main", "main path", "tc-main"],
    ["negative", "negative", "tc-negative"],
    ["edge", "edge", "tc-edge"],
  ].filter(([kind]) => counts[kind] > 0);
  $("tally").hidden = chips.length === 0;
  $("tally").replaceChildren(...chips.map(([kind, label, className]) => h("li", { class: "tc-chip " + className }, h("span.tc-dot"), h("b.efp-number", { text: String(counts[kind]) }), " " + label)));
  if (chips.length) kit.rise($("tally").children, { distance: 6 });
}

async function write() {
  if (page.controller || $("write-button").classList.contains("efp-busy")) return;
  if (!page.modelUsable) {
    kit.shake($("notice"));
    return;
  }
  const text = story.value.trim();
  if (!text) {
    // Nothing to do: no request, nothing spent.
    issueStatus("problem", "Paste a story first, or enter an issue key and read the issue.");
    kit.shake(story);
    story.focus();
    return;
  }
  const style = chosenStyle();
  const count = Number($("count").value);
  page.controller = new AbortController();
  page.style = style;
  let view = (page.view = emptyView());
  const started = performance.now();
  $("empty").hidden = true;
  $("problem").hidden = true;
  $("outcome").hidden = true;
  $("tally").hidden = true;
  $("thinking").hidden = false;
  for (const id of ["copy", "download"]) $(id).hidden = true;
  $("stop").hidden = false;
  answer.hidden = true;
  answer.classList.remove("is-table");
  answer.replaceChildren(h("span.tc-caret", { "aria-hidden": "true" }));
  setState("", "Writing " + count + (style === "table" ? " test cases..." : " scenarios..."), true);

  let how = "done";
  try {
    how = await kit.busy(
      $("write-button"),
      async () => {
        try {
          const response = await post("api/write", { story: text.slice(0, MAX_SENT), count, style, key: keyField.value }, page.controller.signal);
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          const lines = new LineReader();
          for (;;) {
            const { value, done } = await reader.read();
            for (const event of done ? lines.end() : lines.push(decoder.decode(value, { stream: true }))) {
              const before = view;
              view = page.view = reduce(view, event);
              if (!before.done && !view.done && view.text.length > before.text.length) append(view.text.slice(before.text.length));
            }
            if (done) return "done";
          }
        } catch (error) {
          if (error && error.name === "AbortError") return "stopped";
          if (error instanceof TypeError) return "lost"; // the stream broke off
          throw error;
        }
      },
      { tick: false },
    );
  } catch (problem) {
    page.controller = null;
    $("stop").hidden = true;
    answer.hidden = true;
    showProblem(problem); // refused before any text: the budget, the model off, nothing to write
    return;
  }

  page.controller = null;
  $("stop").hidden = true;
  if (answer.lastChild) answer.lastChild.remove(); // the caret
  const end = outcome(view, how === "stopped" ? "stopped" : "done", style);
  if (!view.text.trim()) {
    answer.hidden = true;
    showProblem({ code: view.error ? view.error.code : "", message: end.text });
    return;
  }
  $("thinking").hidden = true;
  answer.hidden = false;
  if (view.done) showFinished(view, style);
  $("copy").hidden = false;
  $("download").hidden = !view.done;
  $("download-name").textContent = view.done ? view.done.file : "Download";
  $("outcome").hidden = false;
  $("outcome").dataset.kind = end.kind;
  const used = tokens(view.done);
  $("outcome").replaceChildren(
    kit.icon(end.kind === "done" ? "circle-check" : end.kind === "warning" ? "triangle-alert" : "circle-alert", "size-4"),
    h("span", { text: end.text }),
    ...(used ? [h("span.tc-used.efp-number", { text: used })] : []), // no counts after Stop or a lost stream
  );
  const seconds = ((performance.now() - started) / 1000).toFixed(1);
  if (end.kind === "problem") setState("circle-alert", "Interrupted");
  else if (how === "stopped") setState("circle-pause", "Stopped");
  else setState("circle-check", "Written in " + seconds + " s");
  if (end.kind === "done") kit.toast(end.text, { kind: "success", timeout: 2600 });
}

function download() {
  const view = page.view;
  if (!view.done) return;
  // The file is made in the browser from what is on the page: nothing is asked from the server, nothing is kept.
  const type = page.style === "table" ? "text/markdown" : "text/plain";
  const url = URL.createObjectURL(new Blob([view.text + "\n"], { type: type + ";charset=utf-8" }));
  const link = h("a", { href: url, download: view.done.file });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// --- The state of the app -----------------------------------------------------------------------

function notice(message, kind) {
  $("notice").hidden = false;
  $("notice").replaceChildren(
    h("div.alert.alert-soft.efp-rise", { class: "alert-" + kind, role: "status" }, kit.icon(kind === "warning" ? "coins" : "plug-zap", "size-5"), h("span", { text: message + " You can still read an issue." })),
  );
}

async function loadState() {
  let state;
  try {
    const response = await fetch("api/state");
    if (!response.ok) throw await failure(response);
    state = await response.json();
  } catch (problem) {
    notice(problem.message || "The app cannot be reached right now.", "error");
    return;
  }
  page.limits = state.limits;
  const { min, max } = state.limits;
  Object.assign($("count"), { min, max, value: Math.min(Math.max(Number($("count").value), min), max) });
  showCount();
  $("story-note-text").textContent =
    "The first " + state.limits.story.toLocaleString("en") + " characters go to the model, as data: instructions inside the story are not followed.";
  showSize();

  const model = state.model;
  page.modelUsable = model.usable;
  const pill = $("model");
  pill.hidden = false;
  pill.className = "tc-model badge badge-soft gap-1.5 " + (!model.usable ? "badge-neutral" : model.message ? "badge-warning" : "badge-success");
  pill.replaceChildren(kit.icon(model.usable ? "sparkles" : "plug-zap", "size-3.5"), !model.usable ? "Model off" : model.message ? "Budget used up" : "Model on");
  if (model.message) notice(model.message, model.usable ? "warning" : "info");
  $("write-button").disabled = !model.usable;

  if (state.jira === false) {
    keyField.disabled = true;
    $("read-button").disabled = true;
    issueStatus("info", "Jira is not turned on for this app. Paste the story instead.");
  }
}

// --- Events ----------------------------------------------------------------------------------

form.addEventListener("submit", (event) => {
  event.preventDefault();
  write();
});
story.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    form.requestSubmit();
  }
});
keyField.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault(); // Enter in the key field reads the issue; it does not write
    readIssue();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && page.controller) page.controller.abort();
});

story.addEventListener("input", showSize);
$("count").addEventListener("input", showCount);
$("read-button").addEventListener("click", readIssue);
$("stop").addEventListener("click", () => page.controller && page.controller.abort());
$("clear").addEventListener("click", () => {
  story.value = "";
  showSize();
  story.focus();
});
$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(page.view.text);
    kit.toast("The scenarios are copied.", { kind: "success", timeout: 2500 });
  } catch (error) {
    kit.toast("The browser did not allow copying. Select the text to copy it.", { kind: "warning" });
  }
});
$("download").addEventListener("click", download);

await kit.ready();
kit.themeSwitch($("theme"));
showSize();
showCount();
await loadState();
