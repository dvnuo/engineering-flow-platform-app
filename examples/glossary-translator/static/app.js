// Every URL in this file is relative ("api/translate", not "/api/translate"):
// the app is served under /{slug}/ once deployed.
//
// Nothing here asks the server on a timer. Text that came from the model or
// from the glossary is put into the page as text (textContent, input
// values), never as markup.

import { LineReader, counter, emptyView, firstProblem, importSummary, matches, outcome, reduce } from "./glossary.js";

const notice = document.getElementById("notice");
const form = document.getElementById("translate-form");
const source = document.getElementById("source");
const target = document.getElementById("target");
const swapButton = document.getElementById("swap-button");
const text = document.getElementById("text");
const translateButton = document.getElementById("translate-button");
const stopButton = document.getElementById("stop-button");
const count = document.getElementById("count");
const status = document.getElementById("status");
const result = document.getElementById("result");
const output = document.getElementById("output");
const copyButton = document.getElementById("copy-button");
const termsNote = document.getElementById("terms-note");
const termsList = document.getElementById("terms");
const glossaryFacts = document.getElementById("glossary-facts");
const filter = document.getElementById("filter");
const glossaryHead = document.getElementById("glossary-head");
const glossaryRows = document.getElementById("glossary-rows");
const glossaryEmpty = document.getElementById("glossary-empty");
const ownerTools = document.getElementById("owner-tools");
const addButton = document.getElementById("add-button");
const importButton = document.getElementById("import-button");
const importFile = document.getElementById("import-file");
const saveButton = document.getElementById("save-button");
const glossaryStatus = document.getElementById("glossary-status");

const ARROW = " \u2192 "; // an arrow, written as an escape: every file of the examples is ASCII

const page = {
  languages: [], // [{id, label}], from the server
  limits: { text: 3000, entries: 500, term: 80, note: 200, csv: 524288 }, // replaced by what the server says
  owner: false,
  modelUsable: false,
  busy: null, // the AbortController of a translation that is running
  complete: false, // whether the translation on the page arrived whole
  version: 0, // of the glossary this page loaded
  entries: [], // the glossary as the table shows it
  rows: new Map(), // entry -> its table row
  dirty: false,
};

// --- Small helpers -----------------------------------------------------------

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function errorMessage(response) {
  try {
    const body = await response.json();
    if (body.error && body.error.message) {
      return body.error.message;
    }
  } catch (error) {
    // not JSON
  }
  return response.status === 401
    ? "Your session has ended. Reload the page to sign in again."
    : "The app answered with an error (" + response.status + ").";
}

function say(element, message, isProblem) {
  element.textContent = message;
  element.classList.toggle("problem", Boolean(isProblem));
}

function languageIds() {
  return page.languages.map((language) => language.id);
}

function plural(number, word) {
  return number + " " + word + (number === 1 ? "" : "s");
}

// --- Translating -------------------------------------------------------------

function showCount() {
  const state = counter(text.value, page.limits.text);
  count.textContent = state.text + (state.over ? ": too long for one translation, translate it in parts" : "");
  count.classList.toggle("problem", state.over);
  translateButton.disabled = !page.modelUsable || page.busy !== null || state.over;
}

function showLanguages() {
  // The browser picks glyphs by language: the same code point is drawn
  // differently for Simplified Chinese and for Hong Kong.
  text.lang = source.value;
  output.lang = target.value;
}

function showTranslation(view, ended) {
  output.textContent = view.text;
  termsList.replaceChildren();
  if (view.terms === null) {
    termsNote.textContent = "";
    return;
  }
  termsNote.textContent =
    view.terms.length === 0
      ? "No term of the glossary occurs in this text, so none was sent along."
      : "Only these were sent along with the text. After the translation each is checked: was it used?";
  for (const term of view.terms) {
    const item = document.createElement("li");
    item.append(term.source + ARROW + term.target);
    if (ended && view.done) {
      const missed = view.missed.includes(term.source);
      item.className = missed ? "missed" : "used";
      const mark = document.createElement("span");
      mark.className = "mark";
      mark.textContent = missed ? "not found in the translation" : "used";
      item.append(mark);
    }
    if (term.note) {
      item.title = term.note;
    }
    termsList.append(item);
  }
}

async function translate() {
  const wanted = text.value.trim();
  if (!wanted) {
    say(status, "Type or paste the text to translate first.", true);
    text.focus();
    return;
  }
  if (source.value === target.value) {
    say(status, "Choose two different languages.", true);
    return;
  }
  page.busy = new AbortController();
  page.complete = false;
  translateButton.disabled = true;
  stopButton.hidden = false;
  let view = emptyView();
  result.hidden = false;
  showLanguages();
  showTranslation(view, false);
  say(status, "Translating...");
  try {
    const response = await fetch("api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: wanted, source: source.value, target: target.value }),
      signal: page.busy.signal,
    });
    if (!response.ok) {
      // Refused before any text: the budget, the model switched off, a text that is too long.
      result.hidden = true;
      say(status, await errorMessage(response), true);
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const lines = new LineReader();
    for (;;) {
      const { value, done } = await reader.read();
      const events = done ? lines.end() : lines.push(decoder.decode(value, { stream: true }));
      for (const event of events) {
        view = reduce(view, event);
      }
      showTranslation(view, done);
      if (done) {
        break;
      }
    }
    const end = outcome(view);
    page.complete = view.done && !view.cut;
    say(status, end.text, end.problem);
  } catch (error) {
    showTranslation(view, false);
    if (error && error.name === "AbortError") {
      say(status, "Stopped." + (view.text ? " The translation above is incomplete." : ""));
    } else {
      say(status, "The connection was lost." + (view.text ? " The translation above is incomplete." : ""), true);
    }
  } finally {
    page.busy = null;
    stopButton.hidden = true;
    showCount();
  }
}

// --- The glossary ------------------------------------------------------------

function showFacts(glossary) {
  let facts;
  if (glossary.sample) {
    facts = plural(glossary.entries.length, "sample term") + ": the owner of the app has not saved a glossary yet.";
  } else {
    facts = plural(glossary.entries.length, "term") + ".";
    const when = new Date(glossary.updated_at);
    if (glossary.updated_by && !Number.isNaN(when.getTime())) {
      facts += " Last saved by " + glossary.updated_by + " on " + when.toLocaleDateString() + ".";
    }
  }
  glossaryFacts.textContent = facts + (page.owner ? " You own this app: you can change it." : " Only the owner of the app can change it.");
}

function showHead() {
  glossaryHead.replaceChildren();
  for (const label of [...page.languages.map((language) => language.label), "Note"]) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    glossaryHead.append(cell);
  }
  if (page.owner) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = "Row";
    glossaryHead.append(cell);
  }
}

function changed() {
  page.dirty = true;
  saveButton.disabled = false;
  for (const row of page.rows.values()) {
    row.classList.remove("marked");
  }
}

function glossaryRow(entry) {
  const row = document.createElement("tr");
  for (const field of [...languageIds(), "note"]) {
    const cell = document.createElement("td");
    if (field !== "note") {
      cell.lang = field;
    }
    if (page.owner) {
      const input = document.createElement("input");
      input.type = "text";
      input.value = entry[field] || "";
      input.maxLength = field === "note" ? page.limits.note : page.limits.term;
      input.autocomplete = "off";
      const label = page.languages.find((language) => language.id === field);
      input.setAttribute("aria-label", label ? label.label : "Note");
      input.addEventListener("input", () => {
        entry[field] = input.value;
        changed();
      });
      cell.append(input);
    } else {
      cell.textContent = entry[field] || "";
    }
    row.append(cell);
  }
  if (page.owner) {
    const cell = document.createElement("td");
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "quiet";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      page.entries.splice(page.entries.indexOf(entry), 1);
      page.rows.delete(entry);
      row.remove();
      changed();
      say(glossaryStatus, "Removed. It is gone for good when you save.");
      showFilter();
      addButton.focus();
    });
    cell.append(remove);
    row.append(cell);
  }
  page.rows.set(entry, row);
  return row;
}

function showFilter() {
  let shown = 0;
  for (const [entry, row] of page.rows) {
    row.hidden = !matches(entry, filter.value, languageIds());
    shown += row.hidden ? 0 : 1;
  }
  glossaryEmpty.hidden = shown > 0;
  glossaryEmpty.textContent = page.entries.length === 0 ? "The glossary is empty." : "No term matches.";
}

function showGlossary(glossary) {
  page.version = glossary.version;
  page.entries = glossary.entries.map((entry) => ({ ...entry }));
  page.rows.clear();
  page.dirty = false;
  saveButton.disabled = true;
  showHead();
  glossaryRows.replaceChildren();
  for (const entry of page.entries) {
    glossaryRows.append(glossaryRow(entry));
  }
  showFilter();
  showFacts(glossary);
}

async function loadGlossary() {
  try {
    const response = await fetch("api/glossary");
    if (!response.ok) {
      say(glossaryFacts, await errorMessage(response), true);
      return;
    }
    showGlossary(await response.json());
  } catch (error) {
    say(glossaryFacts, "The app cannot be reached right now.", true);
  }
}

async function saveGlossary() {
  const problem = firstProblem(page.entries, languageIds(), page.limits);
  if (problem) {
    const row = page.rows.get(page.entries[problem.row - 1]);
    filter.value = "";
    showFilter();
    row.classList.add("marked");
    row.scrollIntoView({ block: "center" });
    say(glossaryStatus, "Row " + problem.row + " cannot be saved: " + problem.reason + ".", true);
    return;
  }
  saveButton.disabled = true;
  say(glossaryStatus, "Saving...");
  try {
    const response = await fetch("api/glossary", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: page.version, entries: page.entries }),
    });
    if (!response.ok) {
      saveButton.disabled = false;
      say(glossaryStatus, await errorMessage(response), true);
      return;
    }
    const saved = await response.json();
    showGlossary(saved);
    say(glossaryStatus, "Saved: " + plural(saved.entries.length, "term") + ". Translations use them from now on.");
  } catch (error) {
    saveButton.disabled = false;
    say(glossaryStatus, "The app cannot be reached right now.", true);
  }
}

async function importCsv(file) {
  if (file.size > page.limits.csv) {
    say(glossaryStatus, "This file is larger than " + Math.floor(page.limits.csv / 1024) + " KiB.", true);
    return;
  }
  say(glossaryStatus, "Reading " + file.name + "...");
  try {
    // The file goes up as it is; the server reads it and answers with rows for the table.
    const response = await fetch("api/glossary/csv", { method: "POST", headers: { "Content-Type": "text/csv" }, body: file });
    if (!response.ok) {
      say(glossaryStatus, await errorMessage(response), true);
      return;
    }
    const found = await response.json();
    if (found.entries.length === 0) {
      say(glossaryStatus, importSummary(found), true);
      return;
    }
    const question =
      "Replace the " + plural(page.entries.length, "term") + " in the table with the " + found.entries.length +
      " from the file? Nothing is saved until you save.";
    if (page.entries.length > 0 && !window.confirm(question)) {
      say(glossaryStatus, "The table was left as it is.");
      return;
    }
    const version = page.version;
    showGlossary({ version: version, entries: found.entries, sample: false, updated_at: null, updated_by: null });
    changed();
    glossaryFacts.textContent = plural(found.entries.length, "term") + " from " + file.name + ", not saved yet.";
    say(glossaryStatus, importSummary(found));
    saveButton.focus();
  } catch (error) {
    say(glossaryStatus, "The app cannot be reached right now.", true);
  }
}

// --- Start -------------------------------------------------------------------

function fillLanguages() {
  for (const select of [source, target]) {
    select.replaceChildren();
    for (const language of page.languages) {
      const option = document.createElement("option");
      option.value = language.id;
      option.textContent = language.label;
      select.append(option);
    }
  }
  if (page.languages.length > 1) {
    source.value = page.languages[0].id;
    target.value = page.languages[1].id;
  }
  showLanguages();
}

async function start() {
  try {
    const response = await fetch("api/state");
    if (!response.ok) {
      notice.textContent = await errorMessage(response);
      notice.hidden = false;
      return;
    }
    const state = await response.json();
    page.languages = state.languages;
    page.limits = state.limits;
    page.owner = state.owner;
    page.modelUsable = state.model.usable;
    if (state.model.message) {
      // The model is off, the app has no token yet, or today's budget is spent.
      notice.textContent = state.model.message;
      notice.hidden = false;
    }
    ownerTools.hidden = !page.owner;
    fillLanguages();
    showCount();
    await loadGlossary();
  } catch (error) {
    notice.textContent = "The app cannot be reached right now.";
    notice.hidden = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (page.busy === null) {
    translate();
  }
});

stopButton.addEventListener("click", () => {
  if (page.busy !== null) {
    page.busy.abort(); // the server stops reading from the model when the connection closes
  }
});

text.addEventListener("input", showCount);

source.addEventListener("change", () => {
  if (source.value === target.value) {
    // Choosing the language of the other side swaps the two.
    const other = page.languages.find((language) => language.id !== source.value);
    target.value = other ? other.id : target.value;
  }
  showLanguages();
});

target.addEventListener("change", () => {
  if (source.value === target.value) {
    const other = page.languages.find((language) => language.id !== target.value);
    source.value = other ? other.id : source.value;
  }
  showLanguages();
});

swapButton.addEventListener("click", () => {
  const from = source.value;
  source.value = target.value;
  target.value = from;
  if (!result.hidden && page.complete && page.busy === null) {
    // Carry a finished translation over, to translate it back or onwards.
    text.value = output.textContent;
    result.hidden = true;
    say(status, "");
  }
  showLanguages();
  showCount();
});

copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(output.textContent);
    say(status, "The translation is on the clipboard.");
  } catch (error) {
    say(status, "The browser did not allow copying. Select the text instead.", true);
  }
});

filter.addEventListener("input", showFilter);

addButton.addEventListener("click", () => {
  if (page.entries.length >= page.limits.entries) {
    say(glossaryStatus, "A glossary holds at most " + page.limits.entries + " terms.", true);
    return;
  }
  const entry = { note: "" };
  for (const language of languageIds()) {
    entry[language] = "";
  }
  page.entries.push(entry);
  filter.value = "";
  const row = glossaryRow(entry);
  glossaryRows.append(row);
  showFilter();
  changed();
  row.scrollIntoView({ block: "center" });
  row.querySelector("input").focus();
});

importButton.addEventListener("click", () => importFile.click());

importFile.addEventListener("change", () => {
  const file = importFile.files[0];
  importFile.value = ""; // the same file can be chosen again
  if (file) {
    importCsv(file);
  }
});

saveButton.addEventListener("click", saveGlossary);

// Leaving with glossary changes that are not saved: the browser asks first.
window.addEventListener("beforeunload", (event) => {
  if (page.dirty) {
    event.preventDefault();
  }
});

start();
