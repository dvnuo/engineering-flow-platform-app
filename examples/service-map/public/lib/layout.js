// A force-directed layout, written out by hand.
//
// Four forces act in every step. All nodes push each other apart. An edge
// pulls its two ends towards a comfortable distance. A weak pull keeps the
// whole map near the centre. And each node is drawn towards the height of
// its level (lib/graph.js, levels), which gives the picture a direction:
// entry points on top, databases at the bottom, arrows mostly pointing
// down. The "temperature" (alpha) falls with every step, the moves get
// smaller, and below ALPHA_MIN the layout has settled and step() says so:
// the page stops animating then.
//
// Nothing here is random (the start is a spiral), so one data file always
// gives one picture. No DOM in this file.

export const ALPHA_MIN = 0.005;
const COOLING = 0.025;
const FRICTION = 0.6;
// How strong each force is. Change one and watch the picture change.
export const FORCES = {
  repulsion: 0.03, // in units of spacing squared
  spring: 0.08,
  gravity: 0.035,
  layer: 0.4,
};
const LAYER_HEIGHT = 0.85; // the distance between two levels, in units of spacing
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

// ``edges`` are pairs [from, to] of node numbers: "from depends on to".
// ``levels`` gives each node its level (0 on top); without it there is no direction.
// ``spacing`` is the length an edge would like to have; ``room`` is the box
// a node needs for its circle and the label under it.
export function createLayout(count, edges, { levels = null, spacing = 130, room = { width: 124, height: 72 }, forces = FORCES } = {}) {
  const nodes = [];
  for (let index = 0; index < count; index += 1) {
    // A sunflower spiral: evenly spread, and no two nodes start on the same spot.
    const distance = spacing * 0.55 * Math.sqrt(index + 0.5);
    nodes.push({
      x: Math.cos(index * GOLDEN_ANGLE) * distance,
      y: Math.sin(index * GOLDEN_ANGLE) * distance,
      vx: 0,
      vy: 0,
      fixed: false, // true while the visitor holds the node
    });
  }
  const degree = new Array(count).fill(0);
  for (const [from, to] of edges) {
    degree[from] += 1;
    degree[to] += 1;
  }
  // The height each node is drawn towards, with the middle level at 0.
  const middle = levels ? (Math.min(...levels, 0) + Math.max(...levels, 0)) / 2 : 0;
  const heights = levels ? levels.map((level) => (level - middle) * spacing * LAYER_HEIGHT) : null;
  return { nodes, edges, degree, heights, spacing, room, forces: { ...FORCES, ...forces }, alpha: 1 };
}

// One step of the simulation. True while the layout is still moving; a
// settled layout is left alone until reheat() warms it up again.
export function step(layout) {
  const { nodes, edges, degree, heights, spacing, room, forces } = layout;
  const alpha = layout.alpha;
  if (alpha < ALPHA_MIN) {
    return false;
  }
  const push = forces.repulsion * spacing * spacing * alpha;

  for (let i = 0; i < nodes.length; i += 1) {
    for (let j = i + 1; j < nodes.length; j += 1) {
      let dx = nodes[i].x - nodes[j].x;
      let dy = nodes[i].y - nodes[j].y;
      let squared = dx * dx + dy * dy;
      if (squared < 1) {
        // Two nodes on one spot: push them apart along a direction that depends only on their numbers.
        dx = Math.cos(i + j) || 1;
        dy = Math.sin(i + j);
        squared = 1;
      }
      const force = push / squared; // times (dx, dy): a push that falls off with the distance
      nodes[i].vx += dx * force;
      nodes[i].vy += dy * force;
      nodes[j].vx -= dx * force;
      nodes[j].vy -= dy * force;
    }
  }

  for (const [from, to] of edges) {
    const a = nodes[from];
    const b = nodes[to];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const distance = Math.sqrt(dx * dx + dy * dy) || 1;
    // A hub with many edges would be torn at from all sides: each of its edges pulls less.
    const strength = (forces.spring * alpha) / Math.sqrt(Math.min(degree[from], degree[to]));
    const pull = ((distance - spacing) / distance) * strength;
    a.vx += dx * pull;
    a.vy += dy * pull;
    b.vx -= dx * pull;
    b.vy -= dy * pull;
  }

  nodes.forEach((node, index) => {
    if (heights) {
      node.vy += (heights[index] - node.y) * forces.layer * alpha;
    }
    node.vx = (node.vx - node.x * forces.gravity * alpha) * FRICTION;
    node.vy = (node.vy - node.y * forces.gravity * alpha) * FRICTION;
    if (node.fixed) {
      node.vx = 0;
      node.vy = 0;
    } else {
      node.x += node.vx;
      node.y += node.vy;
    }
  });

  separate(layout);
  layout.alpha = alpha * (1 - COOLING);
  if (layout.alpha >= ALPHA_MIN) {
    return true;
  }
  for (let pass = 0; pass < 12; pass += 1) {
    separate(layout); // a last tidy-up before the picture stands still
  }
  return false;
}

// Whatever the forces say, the boxes of two nodes do not overlap: they are
// pushed apart along the side on which they overlap least.
function separate(layout) {
  const { nodes, room } = layout;
  for (let i = 0; i < nodes.length; i += 1) {
    for (let j = i + 1; j < nodes.length; j += 1) {
      const dx = nodes[i].x - nodes[j].x;
      const dy = nodes[i].y - nodes[j].y;
      const overlapX = room.width - Math.abs(dx);
      const overlapY = room.height - Math.abs(dy);
      if (overlapX > 0 && overlapY > 0) {
        // A node the visitor holds stays; the other one makes all the room.
        const shareI = nodes[i].fixed ? 0 : nodes[j].fixed ? 1 : 0.5;
        const shareJ = nodes[j].fixed ? 0 : nodes[i].fixed ? 1 : 0.5;
        if (overlapX / room.width < overlapY / room.height) {
          const side = dx > 0 || (dx === 0 && i < j) ? 1 : -1;
          nodes[i].x += side * overlapX * shareI;
          nodes[j].x -= side * overlapX * shareJ;
        } else {
          const side = dy > 0 || (dy === 0 && i < j) ? 1 : -1;
          nodes[i].y += side * overlapY * shareI;
          nodes[j].y -= side * overlapY * shareJ;
        }
      }
    }
  }
}

// Run to the end at once: for visitors who asked for reduced motion, and for tests.
// Returns the number of steps taken.
export function settle(layout, maxSteps = 600) {
  let steps = 0;
  while (steps < maxSteps && step(layout)) {
    steps += 1;
  }
  layout.alpha = 0;
  return steps;
}

// Warm the layout up again, after a node was moved by hand.
export function reheat(layout, alpha = 0.3) {
  layout.alpha = Math.max(layout.alpha, alpha);
}

// The box around the nodes numbered in ``shown`` (all nodes when left out), with room for circles and labels.
export function bounds(layout, shown = null) {
  const picked = shown === null ? layout.nodes : shown.map((index) => layout.nodes[index]);
  if (!picked.length) {
    return { left: -100, top: -100, right: 100, bottom: 100 };
  }
  return {
    left: Math.min(...picked.map((node) => node.x)) - layout.room.width / 2,
    top: Math.min(...picked.map((node) => node.y)) - layout.room.height / 2,
    right: Math.max(...picked.map((node) => node.x)) + layout.room.width / 2,
    bottom: Math.max(...picked.map((node) => node.y)) + layout.room.height / 2,
  };
}
