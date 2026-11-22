// The map on the page, kept by node id from one layout to the next.
//
// render.js draws a whole map at once, which is what an export needs. On the
// page a map changes one node at a time, and the eye should be able to follow
// the change: a new node grows out of its parent, the others glide to their
// new places, a folded branch is drawn back into the node that hides it, and
// the ring around the selected node slides from one node to the next. So the
// page keeps one SVG group per node and one path per line, and moves them
// from where they are on the screen to where the new layout puts them.
//
// Only positions (and the size of what arrives or leaves) are animated, in
// the map's own units: a frame writes one transform per node and one path per
// line. With still() true (the visitor asked for less motion) the same code
// runs with no time in between.

import { edgePath } from "./layout.js";
import { definitions, edgeStyle, nodeKey, nodeShape } from "./render.js";

const SVG = "http://www.w3.org/2000/svg";
const GLIDE = 460; // ms for a node to reach its new place
const RING = 260; // ms for the selection ring to reach another node
const STAGGER = 70; // ms per level when a map blooms out of its centre

const easeOut = (t) => 1 - Math.pow(1 - t, 4);
const easeBack = (t) => 1 + 2.4 * Math.pow(t - 1, 3) + 1.4 * Math.pow(t - 1, 2); // overshoots a little, then settles
const lerp = (from, to, t) => from + (to - from) * t;

function make(name, attributes) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes || {})) {
    node.setAttribute(key, String(value));
  }
  return node;
}

function mix(from, to, t) {
  return {
    x: lerp(from.x, to.x, t),
    y: lerp(from.y, to.y, t),
    width: lerp(from.width, to.width, t),
    height: lerp(from.height, to.height, t),
  };
}

function moved(box, dx, dy) {
  return { ...box, x: box.x + dx, y: box.y + dy };
}

// A node drawn at a place, at a size (scale around its centre) and opacity.
function place(group, box, scale, opacity) {
  if (scale === 1) {
    group.setAttribute("transform", "translate(" + box.x + " " + box.y + ")");
  } else {
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;
    group.setAttribute(
      "transform",
      "translate(" + cx + " " + cy + ") scale(" + Math.max(0.01, scale) + ") translate(" + -box.width / 2 + " " + -box.height / 2 + ")"
    );
  }
  if (opacity < 1) {
    group.setAttribute("opacity", String(Math.max(0, opacity)));
  } else {
    group.removeAttribute("opacity");
  }
}

export function createScene(viewport, { still = () => false } = {}) {
  const defs = make("g");
  const lines = make("g", { fill: "none", "stroke-linecap": "round" });
  const ring = make("rect", { class: "selection-ring", fill: "none", visibility: "hidden" });
  const shapes = make("g");
  viewport.replaceChildren(defs, lines, ring, shapes);

  const items = new Map(); // id -> {group, path, key, parent, depth, from, to, box, grow, delay}
  let leaving = []; // nodes on their way into a node that stays: {group, path, anchor, from, to}
  let began = 0; // when the current glide started
  let selected = null;
  let ringNow = null; // the ring's rectangle as drawn
  let ringFrom = null;
  let ringBegan = 0;
  let frameAsked = false;
  let held = null; // {id, branch, dx, dy}: a branch that is being dragged

  const duration = (ms) => (still() ? 0 : ms);
  const progress = (now, start, delay, ms) => (ms === 0 ? 1 : Math.min(1, Math.max(0, (now - start - delay) / ms)));

  function ringTarget() {
    const item = selected && items.get(selected);
    if (!item || !item.box) {
      return null;
    }
    const pad = 5;
    const box = item.box;
    return { x: box.x - pad, y: box.y - pad, width: box.width + 2 * pad, height: box.height + 2 * pad, rx: item.depth === 0 ? 18 : 14 };
  }

  function drawRing(now) {
    const target = ringTarget();
    if (!target) {
      ring.setAttribute("visibility", "hidden");
      ringNow = null;
      return false;
    }
    const t = progress(now, ringBegan, 0, duration(RING));
    let rect = target;
    if (ringFrom) {
      rect = { ...mix(ringFrom, target, easeOut(t)), rx: target.rx };
    } else {
      // A ring that was not there snaps on from a little further out.
      const grow = 10 * (1 - easeOut(t));
      rect = { x: target.x - grow, y: target.y - grow, width: target.width + 2 * grow, height: target.height + 2 * grow, rx: target.rx };
      ring.setAttribute("opacity", String(easeOut(t)));
    }
    if (t >= 1) {
      ring.removeAttribute("opacity");
    }
    for (const key of ["x", "y", "width", "height", "rx"]) {
      ring.setAttribute(key, String(rect[key]));
    }
    ring.setAttribute("visibility", "visible");
    ringNow = rect;
    return t < 1;
  }

  function drawLine(item) {
    const parent = item.parent !== null ? items.get(item.parent) : null;
    if (item.path && parent && parent.box) {
      item.path.setAttribute("d", edgePath(parent.box, item.box));
    }
  }

  function frame(now) {
    frameAsked = false;
    let busy = false;
    const glide = duration(GLIDE);
    for (const [id, item] of items) {
      if (held && held.branch.has(id)) {
        item.box = moved(item.to, held.dx, held.dy);
        place(item.group, item.box, 1, 1);
        continue;
      }
      const t = progress(now, began, item.delay, glide);
      busy = busy || t < 1;
      item.box = mix(item.from, item.to, easeOut(t));
      if (item.grow) {
        place(item.group, item.box, lerp(0.2, 1, easeBack(t)), Math.min(1, t * 2.5));
        if (item.path) {
          item.path.setAttribute("opacity", String(Math.min(1, t * 2)));
        }
      } else {
        place(item.group, item.box, 1, 1);
      }
    }
    for (const item of items.values()) {
      drawLine(item);
    }
    leaving = leaving.filter((gone) => {
      const t = progress(now, began, 0, duration(GLIDE * 0.75));
      const box = mix(gone.from, gone.to, easeOut(t));
      place(gone.group, box, 1 - 0.8 * easeOut(t), 1 - t);
      const anchor = items.get(gone.anchor);
      if (gone.path && anchor && anchor.box) {
        gone.path.setAttribute("d", edgePath(anchor.box, box));
        gone.path.setAttribute("opacity", String(1 - t));
      }
      if (t >= 1) {
        gone.group.remove();
        if (gone.path) {
          gone.path.remove();
        }
        return false;
      }
      busy = true;
      return true;
    });
    busy = drawRing(now) || busy;
    if (busy) {
      ask();
    }
  }

  function ask() {
    if (!frameAsked) {
      frameAsked = true;
      requestAnimationFrame(frame);
    }
  }

  // Draw this layout. What stays glides from where it is now; what is new
  // grows out of its parent; what is gone shrinks into its nearest ancestor
  // that is still there. bloom: the whole map grows out of its centre, level
  // after level (a map that was just opened).
  function update(nodes, layout, { bloom = false } = {}) {
    held = null; // a dropped branch starts its glide where it was let go
    for (const gone of leaving) {
      gone.group.remove();
      if (gone.path) {
        gone.path.remove();
      }
    }
    leaving = [];
    defs.replaceChildren(definitions());

    for (const [id, item] of items) {
      if (layout.boxes.has(id)) {
        continue;
      }
      let anchor = item.parent;
      while (anchor !== null && !layout.boxes.has(anchor)) {
        anchor = items.has(anchor) ? items.get(anchor).parent : null;
      }
      const into = anchor !== null ? layout.boxes.get(anchor) : item.box;
      leaving.push({
        group: item.group,
        path: item.path,
        anchor,
        from: item.box,
        to: { ...item.box, x: into.x + into.width / 2 - item.box.width / 2, y: into.y + into.height / 2 - item.box.height / 2 },
      });
      items.delete(id);
    }

    for (const id of layout.order) {
      const box = layout.boxes.get(id);
      let item = items.get(id);
      const arriving = !item || bloom;
      if (!item) {
        item = { group: null, path: null, key: "", box: null };
        items.set(id, item);
      }
      const key = nodeKey(box, nodes);
      if (item.key !== key) {
        const fresh = nodeShape(box, nodes);
        if (item.group) {
          item.group.replaceWith(fresh);
        } else {
          shapes.append(fresh);
        }
        item.group = fresh;
        item.key = key;
      }
      if (box.parent !== null && !item.path) {
        item.path = make("path");
        lines.append(item.path);
      } else if (box.parent === null && item.path) {
        item.path.remove();
        item.path = null;
      }
      if (item.path) {
        const look = edgeStyle(nodes, box);
        item.path.setAttribute("stroke", look.stroke);
        item.path.setAttribute("stroke-width", String(look.width));
        item.path.removeAttribute("opacity");
      }
      item.parent = box.parent;
      item.depth = box.depth;
      item.to = box;
      item.delay = bloom ? box.depth * STAGGER : 0;
      item.grow = arriving;
      if (!arriving) {
        item.from = item.box;
      } else {
        // Out of the right edge of the parent, as the parent is drawn now
        // (the order is root first, so the parent has its start already).
        const parent = box.parent !== null ? items.get(box.parent) : null;
        const origin = parent ? parent.from : box;
        item.from = parent
          ? { x: origin.x + origin.width - box.width / 2, y: origin.y + origin.height / 2 - box.height / 2, width: box.width, height: box.height }
          : box;
      }
      item.box = item.from;
    }
    began = performance.now();
    if (bloom) {
      ringFrom = null;
    }
    frame(began);
  }

  // Select a node (or none): the ring slides there from where it is.
  function select(id) {
    const next = id && items.has(id) ? id : null;
    if (next === selected && ringNow) {
      return;
    }
    ringFrom = ringNow;
    ringBegan = performance.now();
    selected = next;
    frame(ringBegan);
  }

  // Everything that is drawn moves by (dx, dy) in the map's units, without
  // an animation: the view moved the other way, so on the screen nothing
  // moves at all (see relayout in app.js).
  function shift(dx, dy) {
    for (const item of items.values()) {
      item.box = moved(item.box, dx, dy);
      item.from = moved(item.from, dx, dy);
    }
    for (const gone of leaving) {
      gone.from = moved(gone.from, dx, dy);
    }
    if (ringNow) {
      ringNow = { ...ringNow, x: ringNow.x + dx, y: ringNow.y + dy };
    }
    if (ringFrom) {
      ringFrom = { ...ringFrom, x: ringFrom.x + dx, y: ringFrom.y + dy };
    }
  }

  function branchOf(id) {
    const ids = new Set([id]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const [other, item] of items) {
        if (!ids.has(other) && ids.has(item.parent)) {
          ids.add(other);
          grew = true;
        }
      }
    }
    return ids;
  }

  // A node with its branch held under the pointer, (dx, dy) away from its place.
  function drag(id, dx, dy) {
    if (!held || held.id !== id) {
      held = { id, branch: branchOf(id) };
      for (const member of held.branch) {
        shapes.append(items.get(member).group); // above the others while it is carried
      }
    }
    held.dx = dx;
    held.dy = dy;
    frame(performance.now());
  }

  // Everything glides back to where the layout puts it (a drop that was refused).
  function settle() {
    held = null;
    for (const item of items.values()) {
      item.from = item.box;
      item.grow = false;
      item.delay = 0;
    }
    began = performance.now();
    frame(began);
  }

  function reset() {
    items.clear();
    leaving = [];
    held = null;
    selected = null;
    ringNow = null;
    ringFrom = null;
    lines.replaceChildren();
    shapes.replaceChildren();
    ring.setAttribute("visibility", "hidden");
  }

  return { update, select, shift, drag, settle, reset };
}
