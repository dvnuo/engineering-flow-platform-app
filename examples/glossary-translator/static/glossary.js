// The logic of the page, as functions without side effects: no DOM, no
// network, nothing that runs at import time. app.js uses them, and the tests
// run them with node.

// --- The answer stream ------------------------------------------------------
//
// "api/translate" answers with lines of JSON: {"terms": [...]} first, then
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
  return { terms: null, text: "", done: false, cut: false, missed: [], error: null, inputTokens: null, outputTokens: null };
}

// What the page shows after one more event of the stream. Nothing is taken
// on trust: a field of the wrong type is ignored.
export function reduce(view, event) {
  if (view.done || view.error) {
    return view; // nothing counts after the end
  }
  if (event.error) {
    const message = typeof event.error.message === "string" ? event.error.message : UNREADABLE.error.message;
    return { ...view, error: { code: String(event.error.code || "error"), message } };
  }
  if (Array.isArray(event.terms)) {
    const terms = event.terms
      .filter((term) => term && typeof term.source === "string" && typeof term.target === "string")
      .map((term) => ({ source: term.source, target: term.target, note: typeof term.note === "string" ? term.note : "" }));
    return { ...view, terms };
  }
  if (typeof event.text === "string") {
    return { ...view, text: view.text + event.text };
  }
  if (event.done === true) {
    return {
      ...view,
      done: true,
      cut: event.cut === true,
      missed: Array.isArray(event.missed) ? event.missed.filter((term) => typeof term === "string") : [],
      inputTokens: Number.isFinite(event.input_tokens) ? event.input_tokens : null,
      outputTokens: Number.isFinite(event.output_tokens) ? event.output_tokens : null,
    };
  }
  return view;
}

// The sentence under the translation when the stream has ended.
export function outcome(view) {
  if (view.error) {
    return { problem: true, text: view.error.message + (view.text ? " The translation above is incomplete." : "") };
  }
  if (!view.done) {
    return { problem: true, text: "The answer ended early. The translation above is incomplete: please try again." };
  }
  const parts = [view.cut ? "The answer reached its length limit, so the end is missing: translate the text in parts." : "Done."];
  if (view.terms && view.terms.length > 0) {
    const used = view.terms.length - view.missed.length;
    parts.push(
      view.missed.length === 0
        ? "Every glossary term was used."
        : used + " of " + view.terms.length + " glossary terms were used; check the others below."
    );
  }
  if (view.inputTokens !== null && view.outputTokens !== null) {
    parts.push(view.inputTokens + " tokens in, " + view.outputTokens + " out.");
  }
  return { problem: view.cut, text: parts.join(" ") };
}

// --- The text ---------------------------------------------------------------

// "1,234 / 3,000 characters", and whether that is too many. The server
// counts the same way: characters, not bytes, after trimming.
export function counter(text, limit) {
  const length = Array.from(String(text || "").trim()).length;
  return {
    over: length > limit,
    text: length.toLocaleString("en") + " / " + limit.toLocaleString("en") + " characters",
  };
}

// --- Terms in a text ----------------------------------------------------------

// What is not prose: fenced code, inline code, addresses. The same pattern as
// NOT_PROSE in main.py, which leaves the terms inside them out of the prompt.
const NOT_PROSE = /```[\s\S]*?(?:```|$)|`[^`\n]{0,400}`|https?:\/\/[^\s<>)\]]{1,2000}/g;

function literal(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// The text in pieces, [{ text, term }], where term is the index in `terms` of
// the term a piece is, or -1. The rule of occurs() in main.py: an English term
// counts as whole words in any case, singular or plural; a Chinese one
// anywhere. Terms inside code or an address do not count, and of two terms at
// one place the longer wins ("test case" over "test"). The page marks the
// pieces as elements with text in them, never as markup.
export function highlight(text, terms, language) {
  const value = String(text || "");
  const skipped = Array.from(value.matchAll(NOT_PROSE), (match) => [match.index, match.index + match[0].length]);
  const found = [];
  terms.forEach((term, index) => {
    if (!term) {
      return;
    }
    const pattern =
      language === "en"
        ? new RegExp("(?<![A-Za-z0-9])" + literal(term) + "(?:e?s)?(?![A-Za-z0-9])", "gi")
        : new RegExp(literal(term), "g");
    for (const match of value.matchAll(pattern)) {
      found.push({ start: match.index, end: match.index + match[0].length, term: index });
    }
  });
  found.sort((a, b) => a.start - b.start || b.end - a.end);
  const pieces = [];
  let at = 0;
  for (const hit of found) {
    if (hit.start < at || skipped.some(([from, to]) => hit.start < to && hit.end > from)) {
      continue;
    }
    if (hit.start > at) {
      pieces.push({ text: value.slice(at, hit.start), term: -1 });
    }
    pieces.push({ text: value.slice(hit.start, hit.end), term: hit.term });
    at = hit.end;
  }
  if (at < value.length) {
    pieces.push({ text: value.slice(at), term: -1 });
  }
  return pieces;
}

// --- The glossary -----------------------------------------------------------

function tidy(value) {
  return String(value === null || value === undefined ? "" : value)
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

export function isBlank(entry, languages) {
  return languages.every((language) => !tidy(entry[language]));
}

// Whether an entry shows up for what is typed into "Find": any field, any case.
export function matches(entry, query, languages) {
  const wanted = tidy(query).toLowerCase();
  if (!wanted) {
    return true;
  }
  return [...languages, "note"].some((field) => tidy(entry[field]).toLowerCase().includes(wanted));
}

// The first row that cannot be saved, as {row, reason}, or null. The same
// rules as clean_entries() in main.py: the server decides, the page only
// says early what the server would say. Rows count from 1, blank ones too.
export function firstProblem(entries, languages, limits) {
  const seen = new Map(languages.map((language) => [language, new Map()]));
  let kept = 0;
  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    const row = index + 1;
    if (isBlank(entry, languages)) {
      continue;
    }
    for (const language of languages) {
      if (tidy(entry[language]).length > limits.term) {
        return { row, reason: "a term is longer than " + limits.term + " characters" };
      }
    }
    if (tidy(entry.note).length > limits.note) {
      return { row, reason: "the note is longer than " + limits.note + " characters" };
    }
    if (languages.filter((language) => tidy(entry[language])).length < 2) {
      return { row, reason: "it needs the term in at least two languages" };
    }
    for (const language of languages) {
      const term = tidy(entry[language]);
      if (term && seen.get(language).has(term.toLowerCase())) {
        return { row, reason: 'it repeats "' + term + '" of row ' + seen.get(language).get(term.toLowerCase()) };
      }
    }
    kept += 1;
    if (kept > limits.entries) {
      return { row, reason: "a glossary holds at most " + limits.entries + " terms" };
    }
    for (const language of languages) {
      const term = tidy(entry[language]);
      if (term) {
        seen.get(language).set(term.toLowerCase(), row);
      }
    }
  }
  return null;
}

// What an import brought, for the status line.
export function importSummary(result) {
  const count = result.entries.length;
  let text = count + (count === 1 ? " term" : " terms") + " read from the file.";
  if (result.skipped_rows > 0) {
    const first = result.skipped[0];
    text +=
      " " + result.skipped_rows + (result.skipped_rows === 1 ? " row was" : " rows were") +
      " skipped (row " + first.row + ": " + first.reason + ").";
  }
  return text + (count > 0 ? " Nothing is saved yet: look them over, then save." : "");
}
