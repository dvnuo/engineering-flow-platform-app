// The logic of the page, as functions without side effects: no DOM, no
// network, nothing that runs at import time. app.js draws with them, and the
// tests run them with node.

export const KINDS = [
  { id: "detection", label: "Detection" },
  { id: "action", label: "Action" },
  { id: "escalation", label: "Escalation" },
  { id: "resolution", label: "Resolution" },
  { id: "note", label: "Note" },
];

const TIME = /^(?:(\d{4})-(\d{2})-(\d{2})[ T])?(\d{2}):(\d{2})(?::(\d{2}))?Z?$/;

// A time in the form the server keeps ("2026-11-12 14:05" or "14:05", with
// seconds when given), or null. The same rule as clean_time() in main.py:
// the server decides, the page only says early what the server would say.
export function cleanTime(value) {
  const match = TIME.exec(String(value === null || value === undefined ? "" : value).trim());
  if (!match) {
    return null;
  }
  const [, year, month, day, hour, minute, second] = match;
  if (Number(hour) > 23 || Number(minute) > 59 || Number(second || 0) > 59) {
    return null;
  }
  if (year) {
    const date = new Date(Date.UTC(2000, Number(month) - 1, Number(day)));
    date.setUTCFullYear(Number(year));
    const exists =
      Number(year) >= 1 && date.getUTCMonth() === Number(month) - 1 && date.getUTCDate() === Number(day);
    if (!exists) {
      return null; // the 30th of February
    }
  }
  const clock = hour + ":" + minute + (second ? ":" + second : "");
  return year ? year + "-" + month + "-" + day + " " + clock : clock;
}

// What keeps an event from being saved, as a sentence; null when it is fine.
export function eventProblem(event, limits) {
  if (cleanTime(event.time) === null) {
    return "the time must look like 14:05 or 2026-11-12 14:05";
  }
  const text = String(event.text || "").trim();
  if (!text) {
    return "say what happened";
  }
  if (text.length > limits.text) {
    return "the text is longer than " + limits.text + " characters";
  }
  if (String(event.actor || "").trim().length > limits.actor) {
    return "the name is longer than " + limits.actor + " characters";
  }
  if (!KINDS.some((kind) => kind.id === event.kind)) {
    return "choose a kind";
  }
  return null;
}

// "2026-11-12 14:05" -> ["2026-11-12", "14:05"]; "14:05" -> ["", "14:05"].
export function splitTime(time) {
  const text = String(time || "");
  const space = text.lastIndexOf(" ");
  return space === -1 ? ["", text] : [text.slice(0, space), text.slice(space + 1)];
}

function sortKey(event) {
  const [day, clock] = splitTime(event.time);
  return day + " " + (clock.length === 8 ? clock : clock + ":00");
}

// A copy in the order of time. The sort is stable: events of the same minute
// stay in the order they were told. Events without a day sort as one day.
export function sortEvents(events) {
  return events
    .map((event, index) => ({ event, index, key: sortKey(event) }))
    .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : a.index - b.index))
    .map((item) => item.event);
}

// --- Drawing: where things go, decided without a browser --------------------

// The width of a text in em, roughly: SVG does not wrap text by itself, so
// the lines are cut here. Chinese, Japanese and Korean characters are about
// twice as wide as Latin letters.
function units(text) {
  let total = 0;
  for (const character of text) {
    total += character.codePointAt(0) > 0x2e7f ? 1 : 0.56;
  }
  return total;
}

// The text as lines of at most `limit` em. Words stay whole where they fit;
// a word longer than a line (and text without spaces) is cut by character.
export function wrap(text, limit, maxLines = 6) {
  const lines = [];
  let line = "";
  const push = () => {
    lines.push(line);
    line = "";
  };
  for (const word of String(text || "").split(/\s+/).filter(Boolean)) {
    if (line && units(line + " " + word) <= limit) {
      line += " " + word;
      continue;
    }
    if (line) {
      push();
    }
    for (const character of word) {
      if (line && units(line + character) > limit) {
        push();
      }
      line += character;
    }
  }
  if (line) {
    push();
  }
  if (lines.length > maxLines) {
    lines.length = maxLines;
    lines[maxLines - 1] = lines[maxLines - 1].replace(/.{0,3}$/u, "...");
  }
  return lines;
}

// Everything the drawing needs for `events` in a box `width` pixels wide:
// one row per event in the order of time, a heading where the day changes.
export function layout(events, width) {
  const narrow = width < 520;
  const spine = narrow ? 62 : 92; // x of the vertical line; the clock stands left of it
  const textX = spine + 18;
  const lineHeight = 20;
  const perLine = Math.max(10, (width - textX - 8) / 14); // em of a 14px font
  const rows = [];
  const days = [];
  let y = 14;
  let day = null;
  for (const event of sortEvents(events)) {
    const [eventDay, clock] = splitTime(event.time);
    if (eventDay && eventDay !== day) {
      days.push({ label: eventDay, y: y + 12 });
      y += 34;
    }
    day = eventDay || day;
    const lines = wrap(event.text, perLine);
    // The name shares its line with the kind, so it gets one line and less room.
    const who = wrap(event.actor || "Unknown", Math.max(6, perLine - 7), 1)[0] || "Unknown";
    rows.push({ event, clock, who, y: y + 8, lines });
    y += lineHeight * (1 + lines.length) + 16;
  }
  return { width, height: Math.max(y, 40), spine, textX, lineHeight, rows, days };
}

// --- Words about a timeline -------------------------------------------------

function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

function minutesOf(time) {
  const [day, clock] = splitTime(time);
  const [hour, minute, second] = clock.split(":").map(Number);
  const inDay = hour * 60 + minute + (second || 0) / 60;
  if (!day) {
    return { dated: false, minutes: inDay };
  }
  const [year, month, date] = day.split("-").map(Number);
  return { dated: true, minutes: Date.UTC(year, month - 1, date) / 60000 + inDay };
}

// "1 h 25 min" from the first to the last event, or null when that cannot be
// said (no events, or times with and without a day mixed).
export function duration(events) {
  const sorted = sortEvents(events);
  if (sorted.length < 2) {
    return null;
  }
  const first = minutesOf(sorted[0].time);
  const last = minutesOf(sorted[sorted.length - 1].time);
  if (first.dated !== last.dated || last.minutes < first.minutes) {
    return null;
  }
  const total = Math.round(last.minutes - first.minutes);
  const days = Math.floor(total / 1440);
  const hours = Math.floor((total % 1440) / 60);
  const minutes = total % 60;
  const parts = [];
  if (days) {
    parts.push(days + " d");
  }
  if (hours) {
    parts.push(hours + " h");
  }
  if (minutes || parts.length === 0) {
    parts.push(minutes + " min");
  }
  return parts.join(" ");
}

// What an extraction did, for the status line under the notes.
export function extractSummary(result) {
  const parts = [];
  parts.push(
    result.events.length === 0
      ? "The model found no events in these notes."
      : plural(result.events.length, "event") + " found. Check them against your notes: a model can misread."
  );
  if (result.dropped) {
    parts.push(
      plural(result.dropped, "item") + " of the answer did not keep the agreed format and " +
        (result.dropped === 1 ? "was" : "were") + " dropped."
    );
  }
  if (result.incomplete) {
    parts.push("The answer reached its length limit, so later events may be missing: paste the notes in two parts.");
  }
  if (result.cut) {
    parts.push("The notes were too long: only their beginning was read.");
  }
  if (result.masked) {
    parts.push(plural(result.masked, "secret") + " masked before the notes left the app.");
  }
  if (result.repaired) {
    parts.push("The first answer could not be used, so the model was asked a second time.");
  }
  return parts.join(" ");
}

// --- The post-mortem --------------------------------------------------------

// Text from notes and from a model, made harmless for Markdown: no table
// cell ends early, and nothing turns into a link, an image or markup when
// the document is rendered somewhere else.
export function markdownText(text) {
  return String(text === null || text === undefined ? "" : text)
    .replace(/\s+/g, " ")
    .trim()
    .replace(/([\\`*_[\]()<>|#!])/g, "\\$1");
}

function firstOf(events, kind) {
  const found = events.find((event) => event.kind === kind);
  return found ? found.time : null;
}

function lastOf(events, kind) {
  const found = events.filter((event) => event.kind === kind);
  return found.length ? found[found.length - 1].time : null;
}

// The skeleton of a post-mortem as Markdown: the facts the timeline holds
// are filled in, everything that needs a person is left as a question.
export function postmortem(title, events) {
  const sorted = sortEvents(events);
  const people = [];
  for (const event of sorted) {
    const actor = String(event.actor || "").trim();
    if (actor && !people.includes(actor)) {
      people.push(actor);
    }
  }
  const span = duration(sorted);
  const lines = [
    "# Post-mortem: " + (markdownText(title) || "Untitled incident"),
    "",
    "- Status: draft",
    "- First event: " + (sorted.length ? sorted[0].time : "not recorded"),
    "- Detected: " + (firstOf(sorted, "detection") || "not recorded"),
    "- Resolved: " + (lastOf(sorted, "resolution") || "not recorded"),
    "- From the first to the last event: " + (span || "not known"),
    "- Involved: " + (people.length ? people.map(markdownText).join(", ") : "not recorded"),
    "",
    "## Summary",
    "",
    "_What happened, in two or three sentences, for somebody who was not there._",
    "",
    "## Impact",
    "",
    "_Who was affected, for how long, and how badly._",
    "",
    "## Timeline",
    "",
  ];
  if (sorted.length) {
    lines.push("| Time | Who | What | Kind |", "|---|---|---|---|");
    for (const event of sorted) {
      lines.push(
        "| " + [event.time, markdownText(event.actor), markdownText(event.text), event.kind].join(" | ") + " |"
      );
    }
  } else {
    lines.push("_No events yet._");
  }
  lines.push(
    "",
    "## Root cause",
    "",
    "_Why it happened. Ask \"why\" until the answer is something you can change._",
    "",
    "## What went well",
    "",
    "- ",
    "",
    "## What went badly",
    "",
    "- ",
    "",
    "## Action items",
    "",
    "| Action | Owner | Due |",
    "|---|---|---|",
    "|  |  |  |",
    ""
  );
  return lines.join("\n");
}

// A file name for the download: letters and digits of the title, nothing
// that could be a path.
export function fileName(title) {
  const slug = String(title || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .slice(0, 60)
    .replace(/^-+|-+$/g, "");
  return "post-mortem" + (slug ? "-" + slug : "") + ".md";
}

// A title to start from, from the day of the first event when it has one.
export function suggestedTitle(events, today) {
  const sorted = sortEvents(events);
  const day = sorted.length ? splitTime(sorted[0].time)[0] : "";
  return "Incident of " + (day || today);
}
