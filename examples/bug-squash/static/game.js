// The rules of the game and nothing else: no page, no canvas, no clock of its
// own. The page (app.js) feeds it time and clicks and draws what it holds
// (render.js); the tests run it under node.
//
// Every number comes from rules.json, which the server reads too. The server
// derives its limits (how many bugs a run has, the highest score they can
// give) from the same file with the same arithmetic, so the two cannot drift
// apart: see spawn_count() and max_score() in main.py.

const KEYS = "ASDFJKLGHQWERUIOP"; // the letters bugs carry, for playing with the keyboard
const CALM_LIFE = 0.55; // calm mode: a bug that sits still stays for this share of its crossing time
const EDGE = 0.07; // a crawl starts and ends this far outside the board

// How a kind strays from its straight line after `seconds` on the board, in
// shares of the board. A position is a function of time alone, so the game
// plays the same at 30 and at 144 frames a second.
const STRAY = {
  typo: (seconds, phase) => ({ along: 0, across: 0.02 * Math.sin(2.2 * seconds + phase) }),
  flaky: (seconds, phase) => ({
    along: 0.035 * Math.sin(6.1 * seconds + phase),
    across: 0.085 * Math.sin(3.6 * seconds + phase) + 0.035 * Math.sin(8.3 * seconds + 2 * phase),
  }),
  legacy: () => ({ along: 0, across: 0 }),
  feature: (seconds, phase) => ({ along: 0, across: 0.03 * Math.sin(1.4 * seconds + phase) }),
};

// A small seeded generator (mulberry32). The same seed gives the same run,
// which is what makes the game testable; the server hands out the seed.
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

// What a squash is worth after `combo` squashes in a row.
export function multiplier(rules, combo) {
  return Math.min(rules.combo.max_multiplier, 1 + Math.floor(combo / rules.combo.step));
}

// When something enters the board, in milliseconds from the start. The gap
// shrinks as the run goes on. No chance is involved, so every run has the
// same number of arrivals and the server can know that number.
export function spawnTimes(rules) {
  const runMs = rules.run_seconds * 1000;
  const gap = rules.spawn_gap_ms;
  const times = [];
  let at = 0;
  while (at < runMs) {
    times.push(at);
    at += gap.start + ((gap.end - gap.start) * at) / runMs;
  }
  return times;
}

// The score of a run in which everything that arrives is the most valuable
// bug and all of them are squashed in one unbroken combo. Nobody plays that
// run; it is the line no honest score can cross.
export function maxScore(rules) {
  const best = Math.max(...Object.values(rules.kinds).map((kind) => kind.points));
  const arrivals = spawnTimes(rules).length;
  let total = 0;
  for (let squashed = 0; squashed < arrivals; squashed += 1) {
    total += best * multiplier(rules, squashed);
  }
  return total;
}

// A new run. `options.calm` is the variant without movement; `options.axis`
// is "x" when bugs cross from side to side and "y" on a tall, narrow screen.
export function newGame(rules, seed, options = {}) {
  const squashed = {};
  for (const [name, kind] of Object.entries(rules.kinds)) {
    if (!kind.friend) {
      squashed[name] = 0;
    }
  }
  return {
    rules,
    random: randomSource(seed),
    calm: Boolean(options.calm),
    axis: options.axis === "y" ? "y" : "x",
    time: 0, // milliseconds played; a pause does not count
    over: false,
    schedule: spawnTimes(rules),
    spawned: 0,
    bugs: [], // on the board, oldest first
    score: 0,
    combo: 0,
    bestCombo: 0,
    counts: { squashed, featuresSquashed: 0, shipped: 0, escaped: 0, misses: 0, landed: 0 },
    events: [], // what happened without a click (an escape, a shipped feature): the page shows and empties it
  };
}

function runMs(state) {
  return state.rules.run_seconds * 1000;
}

function pickKind(state, at) {
  const progress = at / runMs(state);
  const weights = Object.entries(state.rules.kinds).map(([name, kind]) => [
    name,
    kind.weight[0] + (kind.weight[1] - kind.weight[0]) * progress,
  ]);
  let roll = state.random() * weights.reduce((sum, [, weight]) => sum + weight, 0);
  for (const [name, weight] of weights) {
    roll -= weight;
    if (roll < 0) {
      return name;
    }
  }
  return weights[weights.length - 1][0];
}

function spawn(state, at) {
  const name = pickKind(state, at);
  const kind = state.rules.kinds[name];
  const speed = 1 + (state.rules.speed_up - 1) * (at / runMs(state));
  // Letters of bugs that are due to leave are free again, however long this step is.
  const taken = new Set(state.bugs.filter((bug) => at - bug.bornAt < bug.life).map((bug) => bug.key));
  // The same four draws in both variants: a seed gives the same bugs with and without movement.
  state.bugs.push({
    id: state.spawned,
    kind: name,
    bornAt: at,
    life: ((kind.cross_seconds * 1000) / speed) * (state.calm ? CALM_LIFE : 1),
    hitsLeft: kind.hits,
    size: kind.size,
    forward: state.random() < 0.5, // which edge it comes from
    lane: 0.12 + state.random() * 0.76, // where across the board it travels
    spot: 0.1 + state.random() * 0.8, // calm mode: where along the board it sits
    phase: state.random() * Math.PI * 2,
    key: [...KEYS].find((letter) => !taken.has(letter)) || null,
  });
}

// Where a bug is, as shares of the board's width and height (0..1, a little
// beyond while it enters or leaves).
export function position(state, bug, time = state.time) {
  let along = bug.spot;
  let across = bug.lane;
  if (!state.calm) {
    const age = (time - bug.bornAt) / bug.life;
    const stray = (STRAY[bug.kind] || STRAY.legacy)((time - bug.bornAt) / 1000, bug.phase);
    along = -EDGE + (1 + 2 * EDGE) * (bug.forward ? age : 1 - age) + stray.along;
    across = Math.min(0.94, Math.max(0.06, across + stray.across));
  }
  return state.axis === "y" ? { x: across, y: along } : { x: along, y: across };
}

function inside(point) {
  return { x: Math.min(0.96, Math.max(0.04, point.x)), y: Math.min(0.94, Math.max(0.08, point.y)) };
}

function leave(state, bug) {
  const kind = state.rules.kinds[bug.kind];
  const where = inside(position(state, bug, bug.bornAt + bug.life));
  if (kind.friend) {
    state.counts.shipped += 1;
    state.score += kind.ship_bonus || 0;
    state.events.push({ type: "shipped", points: kind.ship_bonus || 0, ...where });
  } else {
    state.counts.escaped += 1;
    state.score = Math.max(0, state.score - state.rules.escape_penalty);
    state.events.push({ type: "escaped", points: -state.rules.escape_penalty, ...where });
  }
}

// Move the run on by `elapsedMs`: let in what is due, let out what has crossed.
export function step(state, elapsedMs) {
  if (state.over) {
    return;
  }
  state.time = Math.min(runMs(state), state.time + Math.max(0, elapsedMs));
  while (state.spawned < state.schedule.length && state.schedule[state.spawned] <= state.time) {
    spawn(state, state.schedule[state.spawned]);
    state.spawned += 1;
  }
  const gone = state.bugs.filter((bug) => state.time - bug.bornAt >= bug.life);
  if (gone.length) {
    state.bugs = state.bugs.filter((bug) => !gone.includes(bug));
    // In the order they left, not the order they came: the score is the same at any frame rate.
    gone.sort((one, other) => one.bornAt + one.life - (other.bornAt + other.life));
    gone.forEach((bug) => leave(state, bug));
  }
  if (state.time >= runMs(state)) {
    state.over = true; // what is still on the board neither escaped nor was squashed
  }
}

function remove(state, bug) {
  state.bugs = state.bugs.filter((other) => other !== bug);
}

function strike(state, bug) {
  const kind = state.rules.kinds[bug.kind];
  const where = position(state, bug);
  if (kind.friend) {
    remove(state, bug);
    state.counts.featuresSquashed += 1;
    state.score = Math.max(0, state.score + kind.points);
    state.combo = 0;
    return { type: "feature", kind: bug.kind, points: kind.points, ...where };
  }
  state.counts.landed += 1;
  bug.hitsLeft -= 1;
  if (bug.hitsLeft > 0) {
    // Armour: the combo is kept and not raised. Only a squash raises it, so
    // a run can never hold more combo steps than it has bugs.
    return { type: "crack", kind: bug.kind, points: 0, ...where };
  }
  const times = multiplier(state.rules, state.combo);
  const points = kind.points * times;
  remove(state, bug);
  state.score += points;
  state.combo += 1;
  state.bestCombo = Math.max(state.bestCombo, state.combo);
  state.counts.squashed[bug.kind] += 1;
  return { type: "squash", kind: bug.kind, points, times, ...where };
}

function miss(state, where) {
  state.counts.misses += 1;
  state.combo = 0;
  return { type: "miss", points: 0, ...where };
}

// A click or tap at (x, y) in pixels of a board that is `view.width` by
// `view.height`; `view.unit` is the radius of a bug of size 1. `slack` widens
// every target, for fingers. Of several bugs in reach the nearest is struck.
export function hitAt(state, x, y, view, slack = 0) {
  if (state.over) {
    return null;
  }
  let struck = null;
  let nearest = Infinity;
  for (const bug of state.bugs) {
    const at = position(state, bug);
    const distance = Math.hypot(x - at.x * view.width, y - at.y * view.height);
    if (distance <= bug.size * view.unit + slack && distance < nearest) {
      struck = bug;
      nearest = distance;
    }
  }
  return struck ? strike(state, struck) : miss(state, { x: x / view.width, y: y / view.height });
}

// The keyboard: the letter a bug carries strikes that bug. A letter nobody
// carries is a miss, like a click on the empty board.
export function hitKey(state, letter) {
  if (state.over) {
    return null;
  }
  const struck = state.bugs.find((bug) => bug.key === letter);
  return struck ? strike(state, struck) : miss(state, { x: 0.5, y: 0.12 });
}

// Clicks that struck a bug, out of all clicks, in percent; null before the first click.
export function accuracy(state) {
  const clicks = state.counts.landed + state.counts.featuresSquashed + state.counts.misses;
  return clicks ? Math.round((state.counts.landed / clicks) * 100) : null;
}

// What the page sends to the server with the score. The server checks that
// these numbers can belong together; it cannot know whether they were played.
export function summary(state) {
  return {
    squashed: { ...state.counts.squashed },
    features_squashed: state.counts.featuresSquashed,
    shipped: state.counts.shipped,
    escaped: state.counts.escaped,
    misses: state.counts.misses,
    best_combo: state.bestCombo,
  };
}
