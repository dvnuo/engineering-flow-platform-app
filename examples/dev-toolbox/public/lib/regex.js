// Running a visitor's regular expression without letting it freeze the tab.
//
// A pattern such as (a+)+$ doubles its work with every letter it is given:
// thirty letters take many seconds, forty take hours. And JavaScript cannot
// interrupt a regular expression that is running. So the page never runs
// one itself: regex-worker.js does, in a Web Worker, on a bounded text, and
// createRunner() ends the worker when the answer has not come within the
// time budget.
//
// No DOM in this file: the worker is created by the caller and handed in.

export const MAX_TEXT = 100000;
export const MAX_MATCHES = 1000;
export const BUDGET_MS = 1000;
const FLAGS = "dgimsuvy";

// At most ``max`` characters of the text.
export function boundText(text, max = MAX_TEXT) {
  return text.length > max ? { text: text.slice(0, max), cut: true } : { text, cut: false };
}

// The names of the capturing groups in order, null for an unnamed one:
// "(?<year>\d+)-(\d+)" gives ["year", null].
export function groupNames(pattern) {
  const names = [];
  let inClass = false;
  for (let at = 0; at < pattern.length; at += 1) {
    const character = pattern[at];
    if (character === "\\") {
      at += 1;
    } else if (inClass) {
      inClass = character !== "]";
    } else if (character === "[") {
      inClass = true;
    } else if (character === "(") {
      if (pattern[at + 1] !== "?") {
        names.push(null);
      } else {
        const named = /^\(\?<([A-Za-z_$][A-Za-z0-9_$]*)>/.exec(pattern.slice(at, at + 80));
        if (named) {
          names.push(named[1]);
        }
      }
    }
  }
  return names;
}

function reason(error, pattern) {
  // V8 says "Invalid regular expression: /(/g: Unterminated group"; keep what follows the pattern.
  const message = String((error && error.message) || error).replace(/^Invalid regular expression: /, "");
  const quoted = "/" + pattern + "/";
  if (!message.startsWith(quoted)) {
    return message;
  }
  const rest = message.slice(quoted.length);
  const colon = rest.indexOf(": ");
  return colon === -1 ? message : rest.slice(colon + 2);
}

// Every match of the pattern in the text:
// {ok: true, matches: [{index, end, text, groups}], names, more, outOfTime} or {ok: false, message}.
// Without the g (or y) flag there is one match at most, as in JavaScript.
export function findMatches(pattern, flags, text, { maxMatches = MAX_MATCHES, budgetMs = BUDGET_MS, now = Date.now } = {}) {
  const unknown = Array.from(flags).find((flag, index) => !FLAGS.includes(flag) || flags.indexOf(flag) !== index);
  if (unknown !== undefined) {
    return { ok: false, message: "'" + unknown + "' is not a flag, or is given twice. The flags are " + Array.from(FLAGS).join(", ") + "." };
  }
  let expression;
  try {
    expression = new RegExp(pattern, flags);
  } catch (error) {
    return { ok: false, message: "The pattern is not valid: " + reason(error, pattern).replace(/\.$/, "") + "." };
  }
  const repeat = expression.global || expression.sticky;
  const started = now();
  const matches = [];
  let more = false;
  let outOfTime = false;
  let found;
  while ((found = expression.exec(text)) !== null) {
    if (matches.length >= maxMatches) {
      more = true;
      break;
    }
    matches.push({
      index: found.index,
      end: found.index + found[0].length,
      text: found[0],
      groups: found.slice(1).map((group) => (group === undefined ? null : group)),
    });
    if (!repeat) {
      break;
    }
    if (found[0] === "") {
      // An empty match would be found again at the same place for ever.
      const pair = expression.unicode && text.codePointAt(expression.lastIndex) > 0xffff;
      expression.lastIndex += pair ? 2 : 1;
    }
    if (now() - started > budgetMs) {
      outOfTime = true;
      break;
    }
  }
  return { ok: true, matches, names: groupNames(pattern), more, outOfTime };
}

// The text cut at the matches, for highlighting: [{text, match}] where
// ``match`` is the index of the match or null for the text between two.
export function segments(text, matches) {
  const parts = [];
  let at = 0;
  matches.forEach((match, index) => {
    if (match.index > at) {
      parts.push({ text: text.slice(at, match.index), match: null });
    }
    parts.push({ text: match.text, match: index });
    at = Math.max(at, match.end);
  });
  if (at < text.length) {
    parts.push({ text: text.slice(at), match: null });
  }
  return parts;
}

// Runs requests in a worker, one at a time, each with a time budget.
//
// ``spawn()`` returns a Worker (postMessage, terminate, onmessage, onerror)
// whose script posts {ready: true} once it has loaded and answers a request
// {id, ...} with {id, result}. The budget starts when the request is handed
// to a loaded worker, so a slow download is not mistaken for a slow pattern.
// run() always resolves: {ok: true, result} or {ok: false, reason} with
// "timeout" (the worker was ended), "replaced" (a newer request came) or
// "unavailable" (the worker could not start).
export function createRunner(spawn, budgetMs = BUDGET_MS, startMs = 10000) {
  let worker = null;
  let ready = false;
  let pending = null; // {id, request, resolve, timer, sent}
  let serial = 0;

  const end = () => {
    if (worker) {
      worker.terminate();
      worker = null;
      ready = false;
    }
  };
  const settle = (answer) => {
    if (pending) {
      const { resolve, timer } = pending;
      pending = null;
      clearTimeout(timer);
      resolve(answer);
    }
  };
  const giveUpAfter = (wait, reason) =>
    setTimeout(() => {
      end();
      settle({ ok: false, reason });
    }, wait);
  const send = () => {
    clearTimeout(pending.timer);
    pending.timer = giveUpAfter(budgetMs, "timeout");
    pending.sent = true;
    worker.postMessage({ id: pending.id, ...pending.request });
  };

  return {
    run(request) {
      if (pending) {
        if (pending.sent) {
          end(); // it may be stuck, and only ending the worker stops a regular expression
        }
        settle({ ok: false, reason: "replaced" });
      }
      return new Promise((resolve) => {
        pending = { id: (serial += 1), request, resolve, timer: 0, sent: false };
        if (worker && ready) {
          send();
          return;
        }
        if (!worker) {
          try {
            worker = spawn();
          } catch (error) {
            settle({ ok: false, reason: "unavailable" });
            return;
          }
          worker.onmessage = (event) => {
            const data = event.data || {};
            if (data.ready) {
              ready = true;
              if (pending && !pending.sent) {
                send();
              }
            } else if (pending && data.id === pending.id) {
              settle({ ok: true, result: data.result });
            }
          };
          worker.onerror = () => {
            end();
            settle({ ok: false, reason: "unavailable" });
          };
        }
        pending.timer = giveUpAfter(startMs, "unavailable");
      });
    },
    stop() {
      end();
      settle({ ok: false, reason: "replaced" });
    },
  };
}
