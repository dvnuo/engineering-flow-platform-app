// A sprint and nothing else: no page, no clock of its own. The page (app.js)
// hands in what the text box holds and what time it is; the tests run this
// file under node.
//
// The server never trusts a speed from here. It gets report(): the ids of the
// lines that were typed, counts of keys, and the characters typed right in
// every five seconds, and works the speed out from the lines it knows itself.

export const WORD = 5; // a "word" is five characters, as everywhere
const MOST_AT_ONCE = 3; // characters one change of the text box may add: more is a paste, not typing

// A small seeded generator (mulberry32): the same seed gives the same order of lines.
export function randomSource(seed) {
  let state = seed >>> 0;
  return function next() {
    state = (state + 0x6d2b79f5) >>> 0;
    let mixed = state;
    mixed = Math.imul(mixed ^ (mixed >>> 15), mixed | 1);
    mixed ^= mixed + Math.imul(mixed ^ (mixed >>> 7), mixed | 61);
    return ((mixed ^ (mixed >>> 14)) >>> 0) / 4294967296;
  };
}

// Lines in a shuffled order. When all were shown they are shuffled again, so
// no line comes twice before every other came once, and never twice in a row.
export function lineQueue(lines, random) {
  let waiting = [];
  let last = null;
  return function next() {
    if (waiting.length === 0) {
      waiting = lines.slice();
      for (let index = waiting.length - 1; index > 0; index -= 1) {
        const other = Math.floor(random() * (index + 1));
        [waiting[index], waiting[other]] = [waiting[other], waiting[index]];
      }
      if (waiting.length > 1 && waiting[waiting.length - 1] === last) {
        waiting.unshift(waiting.pop());
      }
    }
    last = waiting.pop();
    return last;
  };
}

// A sprint of `seconds` over `lines` ({id, text}); `bucketSeconds` is the width of a bar of the speed chart.
export function newSprint(lines, seconds, bucketSeconds, random) {
  const queue = lineQueue(lines, random);
  return {
    seconds,
    bucketSeconds,
    queue,
    current: queue(),
    typed: "", // what stands in the text box
    startedAt: null, // the clock starts with the first key
    over: false,
    completed: [], // ids of the lines typed to the end
    doneChars: 0, // their lengths together
    keystrokes: 0,
    mistakes: 0,
    mistakesByLine: {},
    refusedPaste: false,
    buckets: new Array(Math.round(seconds / bucketSeconds)).fill(0),
  };
}

function rightPrefix(typed, target) {
  let length = 0;
  while (length < typed.length && typed[length] === target[length]) {
    length += 1;
  }
  return length;
}

// Characters that stand right: the finished lines and the right start of the current one.
export function netChars(state) {
  return state.doneChars + rightPrefix(state.typed, state.current.text);
}

export function elapsedMs(state, now) {
  return state.startedAt === null ? 0 : Math.max(0, now - state.startedAt);
}

// Book a change of the right characters in the five seconds it happened in.
// Deleting takes back from the latest bars first, so no bar goes below zero
// and the bars always add up to netChars().
function book(state, change, now) {
  const index = Math.min(state.buckets.length - 1, Math.floor(elapsedMs(state, now) / (state.bucketSeconds * 1000)));
  if (change >= 0) {
    state.buckets[index] += change;
    return;
  }
  let owed = -change;
  for (let at = index; at >= 0 && owed > 0; at -= 1) {
    const taken = Math.min(owed, state.buckets[at]);
    state.buckets[at] -= taken;
    owed -= taken;
  }
}

// The time is up when `now` is past the end; returns whether the sprint is over.
export function tick(state, now) {
  if (!state.over && state.startedAt !== null && elapsedMs(state, now) >= state.seconds * 1000) {
    state.over = true;
  }
  return state.over;
}

// The text box now holds `value`. Returns what it should hold: the same, a
// shorter text (nothing can be typed beyond the end of a line), "" when the
// line was finished, or the text from before when the change was a paste.
export function type(state, value, now) {
  if (state.startedAt === null && value !== "") {
    state.startedAt = now;
  }
  if (tick(state, now) || state.startedAt === null) {
    return state.typed;
  }
  const target = state.current.text;
  const before = netChars(state);
  value = value.slice(0, target.length);
  // What is new is everything after the part the old and the new text share.
  let shared = 0;
  while (shared < value.length && shared < state.typed.length && value[shared] === state.typed[shared]) {
    shared += 1;
  }
  if (value.length - shared > MOST_AT_ONCE) {
    state.refusedPaste = true;
    return state.typed;
  }
  for (let index = shared; index < value.length; index += 1) {
    state.keystrokes += 1;
    if (value[index] !== target[index]) {
      state.mistakes += 1;
      state.mistakesByLine[state.current.id] = (state.mistakesByLine[state.current.id] || 0) + 1;
    }
  }
  state.typed = value;
  if (value === target) {
    // A line counts when every character of it is right: a mistake has to be fixed, not left behind.
    state.completed.push(state.current.id);
    state.doneChars += target.length;
    state.current = state.queue();
    state.typed = "";
  }
  book(state, netChars(state) - before, now);
  return state.typed;
}

// How every character of the current line stands: "right", "wrong", "next" or "ahead".
export function marks(state) {
  const target = state.current.text;
  return Array.from(target, (character, index) => {
    if (index < state.typed.length) {
      return state.typed[index] === character ? "right" : "wrong";
    }
    return index === state.typed.length ? "next" : "ahead";
  });
}

// Rounded to one decimal; the server rounds the same way.
export function tenth(value) {
  return Math.round(value * 10) / 10;
}

export function wordsPerMinute(chars, ms) {
  return ms > 0 ? chars / WORD / (ms / 60000) : 0;
}

// Right keys out of all keys, in percent; null before the first key.
export function accuracy(state) {
  return state.keystrokes ? ((state.keystrokes - state.mistakes) * 100) / state.keystrokes : null;
}

// Words per minute in every bar of the speed chart.
export function speedSeries(buckets, bucketSeconds) {
  return buckets.map((chars) => wordsPerMinute(chars, bucketSeconds * 1000));
}

// The line that drew the most wrong keys: {id, mistakes}, or null for a clean sprint.
export function worstLine(state) {
  let worst = null;
  for (const [id, mistakes] of Object.entries(state.mistakesByLine)) {
    if (!worst || mistakes > worst.mistakes) {
      worst = { id, mistakes };
    }
  }
  return worst;
}

// What goes to the server. No speed and no accuracy: counts it can check.
export function report(state) {
  const partial = rightPrefix(state.typed, state.current.text);
  return {
    completed: state.completed.slice(),
    partial: partial > 0 ? { id: state.current.id, chars: partial } : null,
    keystrokes: state.keystrokes,
    mistakes: state.mistakes,
    buckets: state.buckets.slice(),
  };
}

// The same result the server works out, for a guest whose sprint is not sent.
export function localResult(state, minAccuracy) {
  const right = state.keystrokes - state.mistakes;
  return {
    wpm: tenth(wordsPerMinute(netChars(state), state.seconds * 1000)),
    accuracy: state.keystrokes ? tenth((right * 100) / state.keystrokes) : 0,
    chars: netChars(state),
    mistakes: state.mistakes,
    ranked: state.keystrokes > 0 && right * 100 >= minAccuracy * state.keystrokes,
  };
}
