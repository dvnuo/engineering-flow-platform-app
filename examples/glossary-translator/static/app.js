// The page of the glossary translator: a text on the left, its translation
// streaming in on the right, the glossary terms marked in both, and the
// glossary itself below, which only the owner of the app changes.
//
// Every URL in this file is relative ("api/translate", not "/api/translate"):
// the app is served under /{slug}/ once deployed. The one exception is the
// EFP kit at /_efp/lib/, which every gateway serves (docs/UI_KIT.md).
//
// Nothing here asks the server on a timer. Text that came from the model or
// from the glossary is put into the page as text (kit.h, textContent, input
// values), never as markup: a marked term is an element with text in it.
// The rules (the answer stream, the terms in a text, what a glossary may
// hold) are in glossary.js, which the tests run with node.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import { LineReader, counter, emptyView, firstProblem, highlight, importSummary, matches, outcome, reduce } from "./glossary.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const text = $("text");
const output = $("output");
const source = $("source");
const target = $("target");

const page = {
  languages: [], // [{id, label}], from the server
  limits: { text: 3000, entries: 500, term: 80, note: 200, csv: 524288 }, // replaced by what the server says
  owner: false,
  modelUsable: false,
  busy: null, // the AbortController of a translation that is running
  view: null, // the translation on the page, as glossary.js reduced it
  saved: [], // the glossary as the server has it: what a translation will use
  version: 0, // of the glossary this page loaded
  entries: [], // the glossary as the list shows it, with changes not saved yet
  sample: false,
  dirty: false,
  editing: null, // the entry whose row is open for editing
};
const rows = new Map(); // entry -> its <li>

// --- Small helpers ----------------------------------------------------------------------

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function errorMessage(response) {
  try {
    const body = await response.json();
    if (body.error && body.error.message) return body.error.message;
  } catch (error) {
    // not JSON
  }
  return response.status === 401 ? "Your session has ended. Reload the page to sign in again." : "The app answered with an error (" + response.status + ").";
}

// What a request answered: its JSON, or an Error that carries the sentence to show.
async function answer(request) {
  let response;
  try {
    response = await request;
  } catch (error) {
    throw new Error("The app cannot be reached right now.");
  }
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

function languageIds() {
  return page.languages.map((language) => language.id);
}

function labelOf(id) {
  const language = page.languages.find((each) => each.id === id);
  return language ? language.label : "Note";
}

// An alert in place of `holder`; an empty text hides it.
function alertIn(holder, message, kind = "info") {
  const icons = { info: "info", success: "circle-check", warning: "triangle-alert", error: "circle-alert" };
  holder.hidden = !message;
  holder.replaceChildren(message ? h("div", { class: "alert alert-soft text-sm alert-" + kind }, kit.icon(icons[kind], "size-5"), h("span", { text: message })) : "");
  if (message) kit.rise(holder.firstChild, { distance: 6, duration: 420 });
}

// A question before something is lost. Resolves to true for "yes".
function ask(heading, message, ok, { danger = false, icon = "triangle-alert" } = {}) {
  const dialog = $("confirm");
  $("confirm-title").textContent = heading;
  $("confirm-text").textContent = message;
  $("confirm-ok").textContent = ok;
  $("confirm-ok").className = "btn " + (danger ? "btn-error" : "btn-primary");
  $("confirm-icon").replaceChildren(kit.icon(icon));
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true }));
}

$("confirm-cancel").addEventListener("click", () => $("confirm").close("cancel"));

// Text with its marked terms as elements: the same pieces drawn behind the text area and in the translation.
function marked(pieces, keys, className) {
  return pieces.map((piece) => (piece.term < 0 ? piece.text : h("mark", { class: className, dataset: { key: keys[piece.term] } }, piece.text)));
}

// --- The text and its terms -----------------------------------------------------------------

// The glossary terms this text will carry along, as the server will choose
// them: entries with both languages, found in the prose of the text.
function candidates() {
  return page.saved.filter((entry) => entry[source.value] && entry[target.value]);
}

let drawQueued = false;

// The mirror behind the text area holds the same text with the terms marked;
// the counter and the meter of the limit follow every keystroke.
function showText() {
  drawQueued = false;
  const terms = candidates();
  const keys = terms.map((entry) => entry[source.value].toLowerCase());
  const pieces = highlight(text.value, terms.map((entry) => entry[source.value]), source.value);
  const missed = new Set(page.view && page.view.done ? page.view.missed.map((term) => term.toLowerCase()) : []);
  const marks = marked(pieces, keys, "gt-mark");
  // A last line break needs a character after it, or the mirror is a line shorter than the text area.
  $("mirror").replaceChildren(...marks, text.value.endsWith("\n") ? " " : "");
  for (const mark of $("mirror").children) mark.classList.toggle("is-missed", missed.has(mark.dataset.key));
  const found = new Set(pieces.filter((piece) => piece.term >= 0).map((piece) => piece.term)).size;
  $("found").replaceChildren(
    found ? h("span.gt-found-chip", {}, kit.icon("book-a", "size-3.5"), plural(found, "glossary term") + (found > 60 ? " (the 60 longest are sent)" : "")) : "",
  );
  const state = counter(text.value, page.limits.text);
  const share = Array.from(text.value.trim()).length / page.limits.text;
  $("count").textContent = state.text;
  $("count").classList.toggle("text-error", state.over);
  $("meter").style.setProperty("--share", String(Math.min(1, share)));
  $("meter").dataset.level = state.over ? "over" : share > 0.85 ? "near" : "";
  $("clear-button").hidden = !text.value;
  $("translate-button").disabled = !page.modelUsable || state.over;
  $("translate-button").title = state.over ? "Too long for one translation: translate it in parts" : "Translate (Ctrl+Enter)";
}

function queueText() {
  if (!drawQueued) {
    drawQueued = true;
    requestAnimationFrame(showText);
  }
}

text.addEventListener("input", queueText);
text.addEventListener("scroll", () => {
  $("mirror").scrollTop = text.scrollTop;
});
text.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    $("translate-form").requestSubmit();
  }
});

$("clear-button").addEventListener("click", () => {
  const before = text.value;
  text.value = "";
  showText();
  text.focus();
  kit.toast("The text is cleared.", { action: { label: "Undo", onClick: () => ((text.value = before), showText()) } });
});

// --- The languages ----------------------------------------------------------------------------

function showLanguages() {
  // The browser picks glyphs by language: the same code point is drawn
  // differently for Simplified Chinese and for Hong Kong.
  text.lang = $("mirror").lang = source.value;
  output.lang = target.value;
  queueText();
}

// The two pickers trade places: each glides from where the other one was.
function cross() {
  if (kit.reducedMotion()) return;
  const from = $("source-wrap").getBoundingClientRect();
  const to = $("target-wrap").getBoundingClientRect();
  const dx = to.left - from.left;
  $("source-wrap").animate([{ transform: `translateX(${dx}px)`, opacity: 0.4 }, { transform: "none", opacity: 1 }], { duration: 520, easing: kit.SPRING });
  $("target-wrap").animate([{ transform: `translateX(${-dx}px)`, opacity: 0.4 }, { transform: "none", opacity: 1 }], { duration: 520, easing: kit.SPRING });
  $("swap-button").animate([{ transform: "rotate(0deg)" }, { transform: "rotate(180deg)" }], { duration: 520, easing: kit.EASE });
}

function swap() {
  if (page.busy) return;
  const from = source.value;
  source.value = target.value;
  target.value = from;
  cross();
  // A finished translation goes over to the left, to be translated back or onwards: it flies there.
  if (page.view && page.view.done && !page.view.cut && !page.view.error && page.view.text) {
    const start = output.getBoundingClientRect();
    const end = $("editor").getBoundingClientRect();
    text.value = page.view.text;
    clearTranslation();
    if (!kit.reducedMotion()) {
      $("editor").animate([{ transform: `translate(${start.left - end.left}px, ${start.top - end.top}px)`, opacity: 0.3 }, { transform: "none", opacity: 1 }], {
        duration: 640,
        easing: kit.SPRING,
      });
    }
  }
  showLanguages();
}

$("swap-button").addEventListener("click", swap);

// Choosing the language of the other side swaps the two.
for (const [changed, other] of [
  [source, target],
  [target, source],
]) {
  changed.addEventListener("change", () => {
    if (changed.value === other.value) {
      const free = page.languages.find((language) => language.id !== changed.value);
      if (free) other.value = free.id;
      cross();
    }
    showLanguages();
  });
}

// --- Translating ------------------------------------------------------------------------------

function clearTranslation() {
  page.view = null;
  output.replaceChildren();
  $("output-empty").hidden = false;
  $("copy-button").hidden = true;
  $("terms-panel").hidden = true;
  $("status").textContent = "";
  queueText();
}

// The chips of the terms that went along: pending while the text streams,
// then used or missing. Hovering one lights its term up in both texts.
function showTerms(view, checked) {
  $("terms-panel").hidden = view.terms === null;
  if (view.terms === null) return;
  $("terms-note").textContent =
    view.terms.length === 0
      ? "None of the glossary occurs in this text, so none was sent along."
      : checked
        ? "After the translation each was looked for in it."
        : "Only these were sent along with the text.";
  const missed = new Set(view.missed);
  $("terms").replaceChildren(
    ...view.terms.map((term) => {
      const state = !checked ? "pending" : missed.has(term.source) ? "missed" : "used";
      return h(
        "li",
        { class: "gt-chip is-" + state, dataset: { key: term.source.toLowerCase() }, tabindex: "0", title: term.note || null },
        h("span.gt-chip-state", { "aria-hidden": "true" }, state === "pending" ? "" : kit.icon(state === "used" ? "check" : "triangle-alert", "size-3")),
        h("span", { lang: source.value, text: term.source }),
        kit.icon("arrow-right", "size-3.5 opacity-40"),
        h("span", { lang: target.value, text: term.target }),
        state === "missed" ? h("span.sr-only", {}, ": not found in the translation") : state === "used" ? h("span.sr-only", {}, ": used") : null,
      );
    }),
  );
  if (checked && !kit.reducedMotion()) kit.appear($("terms").querySelectorAll(".gt-chip-state"), { stagger: 90, delay: 200 });
}

// The text that has arrived, with a caret while more is coming. A new piece
// fades in where it lands; at the end the text is drawn once more with its marks.
function showOutput(view, piece) {
  $("output-empty").hidden = Boolean(view.text) || page.busy !== null;
  if (piece !== undefined) {
    output.querySelector(".gt-caret")?.remove();
    if (piece) output.append(h("span.gt-piece", {}, piece));
    output.append(h("span.gt-caret", { "aria-hidden": "true" }));
    return;
  }
  const terms = view.terms || [];
  const pieces = highlight(view.text, terms.map((term) => term.target), target.value);
  output.replaceChildren(...marked(pieces, terms.map((term) => term.source.toLowerCase()), "gt-mark"));
  // The terms the model used light up one after the other: the check, made visible.
  if (!kit.reducedMotion()) {
    Array.from(output.querySelectorAll(".gt-mark")).forEach((mark, index) =>
      mark.animate([{ backgroundColor: "transparent", boxShadow: "0 0 0 0 transparent" }, {}], { duration: 700, delay: 120 + index * 110, easing: kit.EASE, fill: "backwards" }),
    );
  }
}

async function translate() {
  const button = $("translate-button");
  if (page.busy || button.classList.contains("efp-busy")) return;
  if (!text.value.trim()) {
    $("status").textContent = "Type or paste the text to translate first.";
    kit.shake($("editor"));
    text.focus();
    return;
  }
  page.busy = new AbortController();
  let view = emptyView();
  page.view = view;
  output.replaceChildren(h("span.gt-caret", { "aria-hidden": "true" }));
  $("output-empty").hidden = true;
  $("copy-button").hidden = true;
  $("stop-button").hidden = false;
  $("status").textContent = "Translating...";
  $("target-pane").classList.add("is-streaming");
  // Read the answer stream line by line; "stopped" when the visitor pressed Stop (not a failure: no shake).
  const work = async () => {
    try {
      const response = await fetch("api/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.value.trim(), source: source.value, target: target.value }),
        signal: page.busy.signal,
      });
      if (!response.ok) throw new Error(await errorMessage(response)); // refused before any text: the budget, the model off
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const lines = new LineReader();
      for (;;) {
        const { value, done } = await reader.read();
        for (const event of done ? lines.end() : lines.push(decoder.decode(value, { stream: true }))) {
          const before = view;
          view = page.view = reduce(view, event);
          if (view.terms !== before.terms) showTerms(view, false);
          if (view.text !== before.text) showOutput(view, view.text.slice(before.text.length));
        }
        if (done) return "done";
      }
    } catch (error) {
      if (error.name === "AbortError") return "stopped";
      throw error instanceof TypeError ? new Error("The connection was lost.") : error;
    }
  };
  try {
    const how = await kit.busy(button, work, { tick: false });
    const end = how === "stopped" ? { problem: false, text: "Stopped." + (view.text ? " The translation above is incomplete." : "") } : outcome(view);
    if (view.text) {
      showOutput(view);
      showTerms(view, view.done);
    } else {
      clearTranslation();
    }
    $("status").textContent = end.text;
    $("status").classList.toggle("text-error", end.problem);
    if (view.done && !view.cut && view.text) {
      kit.toast(view.missed.length ? "Translated. Some glossary terms were not used: they are marked below." : "Translated.", { kind: view.missed.length ? "warning" : "success" });
    }
  } catch (error) {
    showOutput(view);
    if (!view.text) clearTranslation();
    $("status").textContent = error.message + (view.text ? " The translation above is incomplete." : "");
    $("status").classList.add("text-error");
    if (!view.text) kit.toast(error.message, { kind: "error" });
  } finally {
    page.busy = null;
    $("stop-button").hidden = true;
    $("copy-button").hidden = !view.text;
    $("target-pane").classList.remove("is-streaming");
    $("output-empty").hidden = Boolean(view.text);
    showText();
  }
}

$("translate-form").addEventListener("submit", (event) => {
  event.preventDefault();
  translate();
});

function stop() {
  if (page.busy) page.busy.abort(); // the server stops reading from the model when the connection closes
}

$("stop-button").addEventListener("click", stop);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && page.busy) stop();
});

$("copy-button").addEventListener("click", async (event) => {
  try {
    await kit.busy(event.currentTarget, () => navigator.clipboard.writeText(page.view.text));
    kit.toast("The translation is on the clipboard.", { kind: "success" });
  } catch (error) {
    kit.toast("The browser did not allow copying. Select the text instead.", { kind: "error" });
  }
});

// A term pointed at, in the chips or in the translation, lights up wherever it occurs.
function light(key) {
  for (const element of document.querySelectorAll("[data-key]")) element.classList.toggle("is-lit", key !== null && element.dataset.key === key);
}

for (const area of [$("terms"), output]) {
  area.addEventListener("pointerover", (event) => light(event.target.closest("[data-key]")?.dataset.key ?? null));
  area.addEventListener("pointerleave", () => light(null));
  area.addEventListener("focusin", (event) => light(event.target.closest("[data-key]")?.dataset.key ?? null));
  area.addEventListener("focusout", () => light(null));
}

// --- The glossary -----------------------------------------------------------------------------

function setDirty(dirty) {
  page.dirty = dirty;
  $("save-button").disabled = !dirty;
  if ($("dirty-badge").hidden === dirty) {
    $("dirty-badge").hidden = !dirty;
    if (dirty) kit.appear($("dirty-badge"));
  }
}

function showFacts(glossary) {
  let facts;
  if (glossary.sample) {
    facts = plural(glossary.entries.length, "sample term") + ": the owner of the app has not saved a glossary yet.";
  } else {
    facts = plural(glossary.entries.length, "term") + ".";
    const when = new Date(glossary.updated_at);
    if (glossary.updated_by && !Number.isNaN(when.getTime())) facts += " Saved by " + glossary.updated_by + " " + kit.timeAgo(when) + ".";
  }
  $("glossary-facts").textContent = facts + (page.owner ? " You own this app: you can change it." : " Only the owner of the app can change it.");
  $("sample-badge").hidden = !glossary.sample;
}

function headRow() {
  $("glossary-head").replaceChildren(...page.languages.map((language) => h("span", {}, language.label)), h("span", {}, "Note"), h("span"));
}

function viewOf(entry) {
  const cells = [...languageIds(), "note"].map((field) =>
    h(
      "div.gt-cell",
      { class: field === "note" ? "gt-note" : null, dataset: { field } },
      h("span.gt-cell-label", {}, labelOf(field) + ": "),
      h("span.gt-term", { lang: field === "note" ? null : field, class: entry[field] ? null : "is-empty", text: entry[field] || "\u2014" }),
    ),
  );
  const tools = page.owner
    ? h(
        "div.gt-row-tools",
        {},
        h("button.btn.btn-ghost.btn-xs.btn-square", { type: "button", "aria-label": "Edit " + (entry.en || "the term"), title: "Edit", onclick: () => openRow(entry) }, kit.icon("pencil", "size-3.5")),
        h(
          "button.btn.btn-ghost.btn-xs.btn-square.text-error",
          { type: "button", "aria-label": "Remove " + (entry.en || "the term"), title: "Remove", onclick: () => removeEntry(entry) },
          kit.icon("trash-2", "size-3.5"),
        ),
      )
    : h("div.gt-row-tools");
  return [...cells, tools];
}

function formOf(entry) {
  const inputs = [...languageIds(), "note"].map((field) => {
    const input = h("input.input.input-sm.w-full", {
      type: "text",
      autocomplete: "off",
      lang: field === "note" ? null : field,
      maxlength: field === "note" ? page.limits.note : page.limits.term,
      placeholder: field === "note" ? "When to use it (optional)" : labelOf(field),
    });
    input.value = entry[field] || "";
    input.addEventListener("input", () => {
      entry[field] = input.value;
      setDirty(true);
    });
    return h("label.gt-field", { class: field === "note" ? "gt-note" : null }, h("span", {}, labelOf(field)), input);
  });
  const form = h(
    "form.gt-edit",
    { "aria-label": "Edit the term" },
    ...inputs,
    h(
      "div.gt-edit-actions",
      {},
      h("span.text-xs.text-base-content/50.hidden.sm:inline", { "aria-hidden": "true" }, h("kbd.kbd.kbd-xs", {}, "Enter"), " or ", h("kbd.kbd.kbd-xs", {}, "Esc"), " closes"),
      h("button.btn.btn-ghost.btn-sm.text-error.ml-auto", { type: "button", onclick: () => removeEntry(entry) }, kit.icon("trash-2", "size-4"), "Remove"),
      h("button.btn.btn-primary.btn-sm", { type: "submit" }, kit.icon("check", "size-4"), "Done"),
    ),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    closeRow(entry);
  });
  form.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeRow(entry);
    }
  });
  return form;
}

// A row's height follows its new content instead of jumping (read <-> edit).
function morph(row, update) {
  const from = row.getBoundingClientRect().height;
  update();
  if (kit.reducedMotion()) return;
  const to = row.getBoundingClientRect().height;
  row.style.overflow = "clip";
  row.animate([{ height: from + "px" }, { height: to + "px" }], { duration: 360, easing: kit.EASE }).finished.catch(() => {}).then(() => (row.style.overflow = ""));
  row.firstElementChild?.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300, delay: 60, fill: "backwards" });
}

function fillRow(entry) {
  const row = rows.get(entry);
  row.classList.toggle("is-editing", page.editing === entry);
  row.replaceChildren(...(page.editing === entry ? [formOf(entry)] : viewOf(entry)));
}

function rowOf(entry) {
  if (!rows.has(entry)) {
    rows.set(entry, h("li.gt-entry"));
    fillRow(entry);
  }
  return rows.get(entry);
}

function openRow(entry) {
  if (page.editing === entry) return;
  if (page.editing) closeRow(page.editing, { focus: false });
  page.editing = entry;
  const row = rows.get(entry);
  morph(row, () => fillRow(entry));
  row.querySelector("input").focus({ preventScroll: true });
}

function closeRow(entry, { focus = true } = {}) {
  if (page.editing !== entry) return;
  page.editing = null;
  const row = rows.get(entry);
  // A row left blank is no term: it goes, as it would on saving.
  if (languageIds().every((field) => !String(entry[field] || "").trim())) {
    removeEntry(entry, { quiet: true });
    return;
  }
  morph(row, () => fillRow(entry));
  if (focus) row.querySelector("button")?.focus({ preventScroll: true });
  showList();
}

// A click outside the open row, or Tab out of it, closes it.
document.addEventListener(
  "pointerdown",
  (event) => {
    if (page.editing && !rows.get(page.editing).contains(event.target)) closeRow(page.editing, { focus: false });
  },
  true,
);
$("glossary-rows").addEventListener("focusout", (event) => {
  if (page.editing && event.relatedTarget && !rows.get(page.editing).contains(event.relatedTarget)) closeRow(page.editing, { focus: false });
});

// The list in the order of page.entries, with the rows that the search hides.
function showList() {
  const list = $("glossary-rows");
  if (list.querySelector(".gt-skeleton")) list.replaceChildren();
  page.entries.forEach((entry, index) => {
    const row = rowOf(entry);
    if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
  });
  for (const [entry, row] of rows) {
    if (!page.entries.includes(entry)) {
      rows.delete(entry);
      row.remove();
    }
  }
  let shown = 0;
  for (const [entry, row] of rows) {
    row.hidden = page.editing !== entry && !matches(entry, $("filter").value, languageIds());
    shown += row.hidden ? 0 : 1;
  }
  const empty = $("glossary-empty");
  empty.hidden = shown > 0;
  $("glossary-head").hidden = shown === 0;
  if (shown > 0) return;
  if (page.entries.length === 0) {
    empty.replaceChildren(
      h("span.efp-empty-icon", {}, kit.icon("book-a")),
      h("strong", {}, "The glossary is empty"),
      h(
        "span.text-sm.max-w-md",
        {},
        "A glossary keeps the team's words the same in every translation. Add a term such as \"deployment\" with its Chinese: whenever a text contains it, the model is told which word to use, and the page checks that it did.",
      ),
      page.owner
        ? h("button.btn.btn-sm.btn-primary.mt-2.efp-press", { type: "button", onclick: addEntry }, kit.icon("plus", "size-4"), "Add the first term")
        : h("span.text-xs.text-base-content/50", {}, "Only the owner of the app can add terms."),
    );
  } else {
    empty.replaceChildren(
      h("span.efp-empty-icon", {}, kit.icon("search-x")),
      h("strong", {}, "No term matches \u201c" + $("filter").value.trim() + "\u201d"),
      h("button.btn.btn-sm.btn-ghost", { type: "button", onclick: () => (($("filter").value = ""), showList()) }, "Show every term"),
    );
  }
}

function showGlossary(glossary, { dirty = false, rise = false } = {}) {
  page.version = glossary.version;
  page.entries = glossary.entries.map((entry) => ({ ...entry }));
  page.sample = glossary.sample;
  page.editing = null;
  rows.clear();
  $("glossary-rows").replaceChildren();
  headRow();
  showList();
  showFacts(glossary);
  setDirty(dirty);
  if (rise) kit.rise(Array.from($("glossary-rows").children).slice(0, 24), { stagger: 22, distance: 8 });
}

async function loadGlossary() {
  try {
    const glossary = await answer(fetch("api/glossary"));
    page.saved = glossary.entries;
    showGlossary(glossary, { rise: true });
    queueText();
  } catch (error) {
    $("glossary-rows").replaceChildren();
    $("glossary-facts").textContent = error.message;
  }
}

function addEntry() {
  if (page.entries.length >= page.limits.entries) {
    kit.toast("A glossary holds at most " + page.limits.entries + " terms.", { kind: "warning" });
    return;
  }
  if (page.editing) closeRow(page.editing, { focus: false });
  const entry = { note: "" };
  for (const language of languageIds()) entry[language] = "";
  // At the top, where it is seen; the search would hide a blank row, so it is cleared.
  page.entries.unshift(entry);
  $("filter").value = "";
  showList();
  const row = rows.get(entry);
  kit.rise(row, { distance: -10 });
  openRow(entry);
  row.scrollIntoView({ block: "nearest", behavior: kit.reducedMotion() ? "auto" : "smooth" });
}

async function removeEntry(entry, { quiet = false } = {}) {
  const index = page.entries.indexOf(entry);
  if (index === -1) return;
  if (page.editing === entry) page.editing = null;
  const row = rows.get(entry);
  page.entries.splice(index, 1);
  setDirty(true);
  if (!kit.reducedMotion()) {
    row.style.overflow = "clip";
    await row
      .animate([{ height: row.offsetHeight + "px", opacity: 1 }, { height: "0px", opacity: 0, paddingBlock: "0px", transform: "translateX(24px)" }], { duration: 320, easing: kit.EASE })
      .finished.catch(() => {});
  }
  showList();
  if (quiet) return;
  $("add-button").focus({ preventScroll: true });
  kit.toast("The term is removed. It is gone for good when you save.", {
    action: {
      label: "Undo",
      onClick: () => {
        page.entries.splice(Math.min(index, page.entries.length), 0, entry);
        showList();
        kit.appear(rows.get(entry));
      },
    },
  });
}

async function saveGlossary(button) {
  if (page.editing) closeRow(page.editing, { focus: false });
  const problem = firstProblem(page.entries, languageIds(), page.limits);
  if (problem) {
    const entry = page.entries[problem.row - 1];
    $("filter").value = "";
    showList();
    openRow(entry);
    rows.get(entry).scrollIntoView({ block: "center", behavior: kit.reducedMotion() ? "auto" : "smooth" });
    kit.shake(rows.get(entry));
    alertIn($("glossary-status"), "Row " + problem.row + " cannot be saved: " + problem.reason + ".", "error");
    return;
  }
  try {
    const saved = await kit.busy(button, () =>
      answer(fetch("api/glossary", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: page.version, entries: page.entries }) })),
    );
    page.saved = saved.entries;
    showGlossary(saved);
    alertIn($("glossary-status"), "");
    kit.toast("Saved: " + plural(saved.entries.length, "term") + ". Translations use them from now on.", { kind: "success" });
    queueText();
  } catch (error) {
    alertIn($("glossary-status"), error.message, "error");
  }
}

async function importCsv(file) {
  if (file.size > page.limits.csv) {
    alertIn($("glossary-status"), "This file is larger than " + Math.floor(page.limits.csv / 1024) + " KiB.", "error");
    return;
  }
  try {
    // The file goes up as it is; the server reads it and answers with rows for the list. Nothing is saved yet.
    const found = await kit.busy($("import-button"), () => answer(fetch("api/glossary/csv", { method: "POST", headers: { "Content-Type": "text/csv" }, body: file })));
    if (found.entries.length === 0) {
      alertIn($("glossary-status"), importSummary(found), "error");
      return;
    }
    const question = "The " + plural(page.entries.length, "term") + " in the list are replaced by the " + found.entries.length + " of " + file.name + ". Nothing is saved until you save.";
    if (page.entries.length > 0 && !(await ask("Replace the list?", question, "Replace it", { icon: "file-up" }))) return;
    showGlossary({ version: page.version, entries: found.entries, sample: false, updated_at: null, updated_by: null }, { dirty: true, rise: true });
    $("glossary-facts").textContent = plural(found.entries.length, "term") + " from " + file.name + ", not saved yet.";
    alertIn($("glossary-status"), importSummary(found), found.skipped_rows ? "warning" : "success");
    $("save-button").focus();
  } catch (error) {
    alertIn($("glossary-status"), error.message, "error");
  }
}

$("filter").addEventListener("input", showList);
$("add-button").addEventListener("click", addEntry);
$("save-button").addEventListener("click", (event) => saveGlossary(event.currentTarget));
$("import-button").addEventListener("click", () => $("import-file").click());
$("import-file").addEventListener("change", () => {
  const file = $("import-file").files[0];
  $("import-file").value = ""; // the same file can be chosen again
  if (file) importCsv(file);
});

// --- Start --------------------------------------------------------------------------------------

async function start() {
  try {
    const state = await answer(fetch("api/state"));
    page.languages = state.languages;
    page.limits = state.limits;
    page.owner = state.owner;
    page.modelUsable = state.model.usable;
    if (state.name) $("me").replaceChildren(kit.avatar(state.name, { size: "2rem" }));
    const pill = $("model");
    pill.hidden = false;
    pill.className = "gt-model badge badge-soft gap-1.5 " + (!state.model.usable ? "badge-neutral" : state.model.message ? "badge-warning" : "badge-success");
    pill.replaceChildren(kit.icon(state.model.usable ? "sparkles" : "plug-zap", "size-3.5"), !state.model.usable ? "Model off" : state.model.message ? "Budget used up" : "Model on");
    // The model is off, the app has no token yet, or today's budget is spent.
    if (state.model.message) alertIn($("notice"), state.model.message, state.model.usable ? "warning" : "info");
    $("owner-tools").hidden = !page.owner;
    for (const select of [source, target]) {
      select.replaceChildren(...page.languages.map((language) => h("option", { value: language.id }, language.label)));
    }
    if (page.languages.length > 1) {
      source.value = page.languages[0].id;
      target.value = page.languages[1].id;
    }
    showLanguages();
    showText();
    await loadGlossary();
  } catch (error) {
    alertIn($("notice"), error.message, "error");
    $("glossary-rows").replaceChildren();
    $("glossary-facts").textContent = "";
  }
}

// Leaving with glossary changes that are not saved: the browser asks first.
window.addEventListener("beforeunload", (event) => {
  if (page.dirty) {
    event.preventDefault();
    event.returnValue = ""; // older browsers ask only when this is set
  }
});

await kit.ready();
kit.themeSwitch($("theme"));
start();
