// The logic of the page, as functions without side effects: no DOM, no
// network, nothing that runs at import time. app.js uses them, and the tests
// run them with node.

// --- What the visitor types --------------------------------------------------

// The same check as ISSUE_KEY in main.py. The server checks again: this one
// only spares a request that would be refused.
const ISSUE_KEY = /^[A-Z][A-Z0-9_]{1,19}-[1-9][0-9]{0,8}$/;

export function issueKeyOf(text) {
  const key = String(text || "").trim().toUpperCase();
  return ISSUE_KEY.test(key) ? key : null;
}

// "1,234 / 6,000" and whether the model will read only the beginning.
export function counter(text, limit) {
  const length = String(text || "").trim().length;
  return { length, over: length > limit, label: length.toLocaleString("en") + " / " + limit.toLocaleString("en") };
}

// --- The answer stream -------------------------------------------------------
//
// "api/write" answers with lines of JSON: {"start": {...}} first, then
// {"text": "..."} for every piece, and {"done": true, ...} or {"error": {...}}
// at the end. The network cuts the stream into pieces wherever it likes, so
// a piece may end in the middle of a line.

const UNREADABLE = { error: { code: "bad_line", message: "The answer could not be read. Please try again." } };

function parseLine(line) {
  try {
    const event = JSON.parse(line);
    return event !== null && typeof event === "object" ? event : UNREADABLE;
  } catch (error) {
    return UNREADABLE;
  }
}

export class LineReader {
  constructor() {
    this.rest = "";
  }

  // The events of the lines that are complete now.
  push(text) {
    const lines = (this.rest + text).split("\n");
    this.rest = lines.pop();
    return lines.filter((line) => line.trim()).map(parseLine);
  }

  // The stream has ended: what is left is a line, or the half of one.
  end() {
    const rest = this.rest;
    this.rest = "";
    return rest.trim() ? [parseLine(rest)] : [];
  }
}

export function emptyView() {
  return { started: null, text: "", done: null, error: null };
}

// The view after one event. A view is never changed in place.
export function reduce(view, event) {
  if (view.done || view.error) return view; // nothing counts after the end
  if (event.start && typeof event.start === "object") return { ...view, started: event.start };
  if (typeof event.text === "string" && !event.done) return { ...view, text: view.text + event.text };
  if (event.done === true) {
    return {
      ...view,
      // The finished text, without a fence the model put around it: what Copy and Download take.
      text: typeof event.text === "string" ? event.text : view.text,
      done: {
        found: Number(event.found) || 0,
        asked: Number(event.asked) || 0,
        lengthLimit: Boolean(event.length_limit),
        file: typeof event.file === "string" && event.file ? event.file : "scenarios.txt",
        inputTokens: Number.isFinite(event.input_tokens) ? event.input_tokens : null,
        outputTokens: Number.isFinite(event.output_tokens) ? event.output_tokens : null,
      },
    };
  }
  if (event.error && typeof event.error === "object") {
    return { ...view, error: { code: String(event.error.code || ""), message: String(event.error.message || "The answer could not be read.") } };
  }
  return view; // a line of a kind this page does not know: skipped
}

const WHAT = { gherkin: "scenarios", table: "test cases" };

// How an answer ended, as a sentence: { kind: "done" | "warning" | "problem", text }.
// "how" is "done" when the stream ended by itself, "stopped" when the visitor stopped it.
export function outcome(view, how, style) {
  const what = WHAT[style] || "scenarios";
  const incomplete = view.text ? " The text above is incomplete." : "";
  if (view.error) return { kind: "problem", text: view.error.message + incomplete };
  if (how === "stopped") return { kind: "warning", text: "Stopped." + incomplete };
  if (!view.done) return { kind: "problem", text: "The connection was lost." + incomplete };
  if (!view.text.trim()) return { kind: "problem", text: "The model wrote nothing. Try again, or ask for fewer " + what + "." };
  const { found, asked, lengthLimit } = view.done;
  let text = found === asked ? "Done: " + found + " " + what + "." : "Done: " + found + " " + what + ", although " + asked + " were asked for.";
  if (lengthLimit) text += " The answer reached its length limit, so the end may be missing: ask for fewer.";
  return { kind: found === asked && !lengthLimit ? "done" : "warning", text };
}

export function tokens(done) {
  if (!done || done.inputTokens === null || done.outputTokens === null) return "";
  return done.inputTokens.toLocaleString("en") + " tokens in, " + done.outputTokens.toLocaleString("en") + " out";
}

// --- Reading the answer ------------------------------------------------------
//
// What the model wrote is shown as text. These functions only cut it into
// pieces with a kind, which app.js turns into elements with textContent.

const STEP = /^(\s*)(Given|When|Then|And|But)(\s.*)?$/;
const HEADING = /^(\s*)(Feature|Background|Rule|Scenario Outline|Scenario|Examples):(.*)$/;
const KINDS = ["main", "negative", "edge"];

// One line of Gherkin as [kind, text] pieces: keyword, tag, comment, question or plain.
export function gherkinLine(text) {
  const heading = HEADING.exec(text);
  if (heading) return [["plain", heading[1]], ["heading", heading[2] + ":"], ["title", heading[3]]].filter(([, piece]) => piece);
  const step = STEP.exec(text);
  if (step) return [["plain", step[1]], ["keyword", step[2]], ["plain", step[3] || ""]].filter(([, piece]) => piece);
  const trimmed = text.trim();
  if (trimmed.startsWith("#")) return [[/^#\s*open question:/i.test(trimmed) ? "question" : "comment", text]];
  if (trimmed.startsWith("@")) {
    const indent = text.slice(0, text.length - text.trimStart().length);
    const pieces = indent ? [["plain", indent]] : [];
    trimmed.split(/(\s+)/).forEach((word) => {
      if (!word) return;
      const kind = word.slice(1).toLowerCase();
      pieces.push(/^\s+$/.test(word) ? ["plain", word] : [KINDS.includes(kind) ? "tag-" + kind : "tag", word]);
    });
    return pieces;
  }
  return text ? [["plain", text]] : [];
}

function cells(row) {
  let inner = row.trim();
  if (inner.startsWith("|")) inner = inner.slice(1);
  if (inner.endsWith("|")) inner = inner.slice(0, -1);
  // A line break inside a cell, as models write it in a Markdown table.
  return inner.split("|").map((cell) => cell.trim().replace(/<br\s*\/?>/gi, "\n"));
}

// A Markdown table as { header, rows }, or null when the text is not one table.
export function tableOf(text) {
  const rows = String(text || "")
    .split("\n")
    .map((row) => row.trim())
    .filter(Boolean);
  if (rows.length < 2 || !rows.every((row) => row.startsWith("|"))) return null;
  if (!/^\|?[\s:|-]+\|?$/.test(rows[1]) || !rows[1].includes("-")) return null;
  const header = cells(rows[0]);
  const body = rows.slice(2).map((row) => {
    const found = cells(row);
    while (found.length < header.length) found.push("");
    return found.slice(0, header.length);
  });
  return { header, rows: body };
}

// How many main, negative and edge cases an answer holds.
export function tally(text, style) {
  const counts = { main: 0, negative: 0, edge: 0 };
  if (style === "table") {
    const table = tableOf(text);
    const column = table ? table.header.findIndex((name) => /^type$/i.test(name)) : -1;
    if (column < 0) return counts;
    for (const row of table.rows) {
      const kind = row[column].toLowerCase();
      if (kind in counts) counts[kind] += 1;
    }
    return counts;
  }
  for (const word of String(text || "").match(/@(main|negative|edge)\b/gi) || []) counts[word.slice(1).toLowerCase()] += 1;
  return counts;
}
