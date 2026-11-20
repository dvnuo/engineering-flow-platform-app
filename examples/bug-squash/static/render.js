// Everything on the canvas is drawn here from lines, circles and ellipses:
// there is no image file in this app. This file knows nothing of the rules;
// it draws what game.js holds.
//
// The four kinds differ in outline, not only in colour, so they can be told
// apart without seeing colour: an oval with spots, three beads with a dashed
// ring, a wide shell with rivets, and a diamond that has no legs at all.

import { position } from "./game.js";

export const EFFECT_MS = 700; // how long "+25" stays on the board

// A canvas does not read CSS variables, so the two themes are spelled out here.
const PALETTES = {
  light: {
    board: "#f6f7fa",
    grid: "#d3d9e3",
    ink: "#1f2633",
    pale: "#ffffff",
    typo: "#c8553d",
    flaky: "#c58a1b",
    legacy: "#6a5fa3",
    feature: "#1f8f63",
    good: "#1d7a46",
    bad: "#b3261e",
    plain: "#5b6676",
  },
  dark: {
    board: "#151920",
    grid: "#2c3440",
    ink: "#e9ecf2",
    pale: "#12161c",
    typo: "#f08a6e",
    flaky: "#f0c25a",
    legacy: "#a79be0",
    feature: "#63d3a2",
    good: "#6fd29a",
    bad: "#f2b8b5",
    plain: "#a3adbd",
  },
};

function oval(ctx, x, y, radiusX, radiusY) {
  ctx.beginPath();
  ctx.ellipse(x, y, radiusX, radiusY, 0, 0, Math.PI * 2);
}

function dot(ctx, x, y, radius) {
  oval(ctx, x, y, radius, radius);
  ctx.fill();
}

// Three pairs of legs; `swing` moves them, pair after pair.
function legs(ctx, unit, reach, swing) {
  ctx.beginPath();
  for (let pair = 0; pair < 3; pair += 1) {
    const x = unit * (0.35 - pair * 0.4);
    const kick = Math.sin(swing + pair * 2.1) * unit * 0.2;
    for (const side of [-1, 1]) {
      ctx.moveTo(x, side * unit * 0.3);
      ctx.lineTo(x + kick * side, side * unit * reach);
    }
  }
  ctx.stroke();
}

// Each painter draws around (0, 0), facing right, within about 1.3 units.

function paintTypo(ctx, unit, colors, look) {
  ctx.strokeStyle = colors.ink;
  ctx.lineWidth = Math.max(1.5, unit * 0.1);
  legs(ctx, unit, 0.95, look.swing);
  ctx.beginPath();
  for (const side of [-1, 1]) {
    ctx.moveTo(unit * 0.8, side * unit * 0.12);
    ctx.quadraticCurveTo(unit * 1.15, side * unit * 0.2, unit * 1.3, side * unit * 0.5);
  }
  ctx.stroke();
  ctx.fillStyle = colors.typo;
  oval(ctx, -unit * 0.1, 0, unit * 0.8, unit * 0.56);
  ctx.fill();
  ctx.fillStyle = colors.ink;
  dot(ctx, unit * 0.72, 0, unit * 0.3);
  ctx.fillStyle = colors.pale;
  for (const x of [-0.5, -0.1, 0.3]) {
    dot(ctx, unit * x, 0, unit * 0.1);
  }
}

function paintFlaky(ctx, unit, colors, look) {
  ctx.globalAlpha *= look.flicker; // now you see it
  ctx.strokeStyle = colors.ink;
  ctx.lineWidth = Math.max(1.2, unit * 0.08);
  legs(ctx, unit, 1, look.swing * 1.6);
  ctx.beginPath();
  for (const side of [-1, 1]) {
    ctx.moveTo(unit * 0.75, side * unit * 0.1);
    ctx.lineTo(unit * 1.0, side * unit * 0.42);
    ctx.lineTo(unit * 1.05, side * unit * 0.16);
    ctx.lineTo(unit * 1.32, side * unit * 0.5);
  }
  ctx.stroke();
  ctx.fillStyle = colors.flaky;
  for (const [x, radius] of [[-0.55, 0.42], [0.05, 0.3], [0.56, 0.28]]) {
    dot(ctx, unit * x, 0, unit * radius);
  }
  ctx.setLineDash([unit * 0.2, unit * 0.16]);
  oval(ctx, -unit * 0.55, 0, unit * 0.58, unit * 0.58);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = colors.ink;
  for (const side of [-1, 1]) {
    dot(ctx, unit * 0.66, side * unit * 0.12, unit * 0.07);
  }
}

function paintLegacy(ctx, unit, colors, look) {
  ctx.strokeStyle = colors.ink;
  ctx.lineWidth = Math.max(2, unit * 0.14);
  legs(ctx, unit, 0.92, look.swing * 0.5);
  ctx.fillStyle = colors.ink;
  oval(ctx, unit * 0.78, 0, unit * 0.26, unit * 0.3);
  ctx.fill();
  ctx.fillStyle = colors.legacy;
  oval(ctx, -unit * 0.05, 0, unit * 0.82, unit * 0.66);
  ctx.fill();
  ctx.lineWidth = Math.max(1.2, unit * 0.07);
  ctx.beginPath();
  ctx.moveTo(-unit * 0.85, 0);
  ctx.lineTo(unit * 0.42, 0);
  ctx.moveTo(unit * 0.5, -unit * 0.5);
  ctx.quadraticCurveTo(unit * 0.3, 0, unit * 0.5, unit * 0.5);
  ctx.stroke();
  ctx.fillStyle = colors.pale;
  for (const [x, y] of [[-0.45, -0.32], [-0.45, 0.32], [0.1, -0.36], [0.1, 0.36]]) {
    dot(ctx, unit * x, unit * y, unit * 0.07);
  }
  if (look.cracked) {
    ctx.strokeStyle = colors.pale;
    ctx.lineWidth = Math.max(1.5, unit * 0.1);
    ctx.lineJoin = "miter";
    ctx.beginPath();
    ctx.moveTo(-unit * 0.62, -unit * 0.46);
    ctx.lineTo(-unit * 0.22, -unit * 0.1);
    ctx.lineTo(-unit * 0.42, unit * 0.12);
    ctx.lineTo(-unit * 0.02, unit * 0.6);
    ctx.stroke();
  }
}

function paintFeature(ctx, unit, colors, look) {
  ctx.strokeStyle = colors.feature;
  ctx.lineWidth = Math.max(1.5, unit * 0.08);
  ctx.save();
  ctx.globalAlpha *= 0.45;
  oval(ctx, 0, 0, unit * look.halo, unit * look.halo);
  ctx.stroke();
  ctx.restore();
  // A diamond; the thick outline in its own colour rounds the corners.
  ctx.fillStyle = colors.feature;
  ctx.lineJoin = "round";
  ctx.lineWidth = unit * 0.3;
  ctx.beginPath();
  ctx.moveTo(0, -unit * 0.68);
  ctx.lineTo(unit * 0.68, 0);
  ctx.lineTo(0, unit * 0.68);
  ctx.lineTo(-unit * 0.68, 0);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  // A four-pointed spark.
  ctx.fillStyle = colors.pale;
  ctx.beginPath();
  for (let point = 0; point < 8; point += 1) {
    const radius = unit * (point % 2 ? 0.14 : 0.5);
    const angle = (point * Math.PI) / 4 - Math.PI / 2;
    if (point === 0) {
      ctx.moveTo(Math.cos(angle) * radius, Math.sin(angle) * radius);
    } else {
      ctx.lineTo(Math.cos(angle) * radius, Math.sin(angle) * radius);
    }
  }
  ctx.closePath();
  ctx.fill();
}

// `upright` kinds do not turn with their direction of travel.
const PAINTERS = {
  typo: { paint: paintTypo },
  flaky: { paint: paintFlaky },
  legacy: { paint: paintLegacy },
  feature: { paint: paintFeature, upright: true },
};

function letterBadge(ctx, x, y, unit, letter, colors) {
  const radius = Math.max(9, unit * 0.42);
  const top = Math.max(radius + 2, y);
  ctx.fillStyle = colors.ink;
  dot(ctx, x, top, radius);
  ctx.fillStyle = colors.board;
  ctx.font = "700 " + Math.round(radius * 1.25) + "px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(letter, x, top + radius * 0.08);
}

function drawBug(ctx, state, bug, view, colors, options) {
  const at = position(state, bug);
  const x = at.x * view.width;
  const y = at.y * view.height;
  const unit = view.unit * bug.size;
  const seconds = (state.time - bug.bornAt) / 1000;
  const painter = PAINTERS[bug.kind] || PAINTERS.typo;
  let angle = bug.phase; // calm mode: every bug faces its own way and stays so
  if (!state.calm) {
    const ahead = position(state, bug, state.time + 60);
    angle = Math.atan2((ahead.y - at.y) * view.height, (ahead.x - at.x) * view.width);
  }
  ctx.save();
  ctx.translate(x, y);
  if (!painter.upright) {
    ctx.rotate(angle);
  }
  painter.paint(ctx, unit, colors, {
    swing: state.calm ? 0.6 : seconds * 11,
    flicker: state.calm ? 1 : 0.55 + 0.45 * Math.abs(Math.sin(seconds * 5 + bug.phase)),
    halo: state.calm ? 1.15 : 1.1 + 0.1 * Math.sin(seconds * 3 + bug.phase),
    cracked: bug.hitsLeft < state.rules.kinds[bug.kind].hits,
  });
  ctx.restore();
  if (options.letters && bug.key) {
    letterBadge(ctx, x, y - unit - view.unit * 0.5, view.unit, bug.key, colors);
  }
}

// "+25", "crack", "-30": text that rises and fades, and a ring around a
// squash. In calm mode it neither rises nor fades; it is there, then gone.
function drawEffects(ctx, state, effects, view, colors) {
  const size = Math.round(Math.max(13, view.unit * 0.7));
  ctx.font = "700 " + size + "px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const effect of effects) {
    const age = Math.min(1, Math.max(0, (state.time - effect.at) / EFFECT_MS));
    const x = effect.x * view.width;
    const y = effect.y * view.height;
    const color = colors[effect.tone] || colors.plain;
    ctx.save();
    if (!state.calm) {
      ctx.globalAlpha = 1 - age * age;
      if (effect.ring) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        const radius = view.unit * (0.7 + age * 1.3);
        oval(ctx, x, y, radius, radius);
        ctx.stroke();
      }
    }
    ctx.fillStyle = color;
    const lift = view.unit * (state.calm ? 1.2 : 1.2 + age * 0.9);
    ctx.fillText(effect.text, Math.min(view.width - size * 1.5, Math.max(size * 1.5, x)), Math.max(size, y - lift));
    ctx.restore();
  }
}

// One frame: the board, the bugs, the effects. `state` is null for an empty board.
export function drawScene(ctx, view, state, effects, options) {
  const colors = PALETTES[options.dark ? "dark" : "light"];
  ctx.setTransform(view.ratio, 0, 0, view.ratio, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = colors.board;
  ctx.fillRect(0, 0, view.width, view.height);
  ctx.fillStyle = colors.grid;
  const gap = view.unit * 2.4;
  for (let x = gap / 2; x < view.width; x += gap) {
    for (let y = gap / 2; y < view.height; y += gap) {
      ctx.fillRect(x - 1, y - 1, 2, 2);
    }
  }
  if (!state) {
    return;
  }
  ctx.lineCap = "round";
  for (const bug of state.bugs) {
    drawBug(ctx, state, bug, view, colors, options);
  }
  drawEffects(ctx, state, effects, view, colors);
}

// One bug of a kind in the middle of a small square canvas, for the legend.
export function drawSample(ctx, kind, size, ratio, dark) {
  const colors = PALETTES[dark ? "dark" : "light"];
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, size, size);
  ctx.globalAlpha = 1;
  ctx.lineCap = "round";
  ctx.save();
  ctx.translate(size / 2, size / 2);
  (PAINTERS[kind] || PAINTERS.typo).paint(ctx, size * 0.3, colors, { swing: 0.6, flicker: 1, halo: 1.15, cracked: false });
  ctx.restore();
}
