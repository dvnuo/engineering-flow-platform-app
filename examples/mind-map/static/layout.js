// Where every node of a map goes: a tidy tree that grows to the right.
//
// Pure arithmetic. The caller says how wide a piece of text is (measure), so
// the browser can use a canvas for that and the tests a ruler of their own.
//
// Each node owns a horizontal band as tall as everything below it. A node
// sits in the middle of its band and its children share the band from top to
// bottom, so two branches can never overlap.

export const SIZES = {
  gapX: 44, // between a node and its children
  gapY: 12, // between two siblings
  padX: 14,
  padY: 8,
  lineHeight: 20,
  maxTextWidth: 220, // longer text is wrapped
  minWidth: 44,
  badge: 30, // room for the "+3" of a collapsed node
};

// Break text into lines no wider than maxWidth. A single word that is too
// wide is cut, so a long URL cannot make a node endless.
export function wrapLines(text, maxWidth, measure) {
  const lines = [];
  let line = "";
  for (const word of String(text).split(/\s+/).filter(Boolean)) {
    const candidate = line ? line + " " + word : word;
    if (measure(candidate) <= maxWidth) {
      line = candidate;
      continue;
    }
    if (line) {
      lines.push(line);
    }
    line = word;
    while (measure(line) > maxWidth && line.length > 1) {
      let keep = line.length - 1;
      while (keep > 1 && measure(line.slice(0, keep)) > maxWidth) {
        keep -= 1;
      }
      lines.push(line.slice(0, keep));
      line = line.slice(keep);
    }
  }
  if (line || lines.length === 0) {
    lines.push(line);
  }
  return lines;
}

// The layout: a box per visible node, the lines between them, the size of
// the whole drawing. measure(text, isRoot) answers a width in the same units.
export function layoutTree(nodes, measure, sizes = SIZES) {
  const children = new Map(nodes.map((node) => [node.id, []]));
  let root = null;
  for (const node of nodes) {
    if (node.parent === null) {
      root = node;
    } else if (children.has(node.parent)) {
      children.get(node.parent).push(node);
    }
  }
  const boxes = new Map();
  const edges = [];
  const order = [];
  if (!root) {
    return { boxes, edges, order, width: 0, height: 0 };
  }

  const shown = (node) => (node.collapsed ? [] : children.get(node.id));
  const hiddenBelow = (node) => {
    let count = 0;
    const waiting = children.get(node.id).slice();
    while (waiting.length) {
      count += 1;
      waiting.push(...children.get(waiting.pop().id));
    }
    return count;
  };

  // First pass, leaves first: the size of each node and of its band.
  const band = new Map();
  const measureNode = (node, depth) => {
    const isRoot = depth === 0;
    const lines = wrapLines(node.text, sizes.maxTextWidth, (text) => measure(text, isRoot));
    const textWidth = Math.max(0, ...lines.map((line) => measure(line, isRoot)));
    const hidden = node.collapsed ? hiddenBelow(node) : 0;
    const box = {
      id: node.id,
      parent: node.parent,
      depth,
      lines,
      hidden,
      hasChildren: children.get(node.id).length > 0,
      width: Math.ceil(Math.max(sizes.minWidth, textWidth + 2 * sizes.padX)) + (hidden ? sizes.badge : 0),
      height: lines.length * sizes.lineHeight + 2 * sizes.padY,
      x: 0,
      y: 0,
    };
    boxes.set(node.id, box);
    let below = 0;
    const kids = shown(node);
    for (const child of kids) {
      below += measureNode(child, depth + 1);
    }
    below += Math.max(0, kids.length - 1) * sizes.gapY;
    band.set(node.id, Math.max(box.height, below));
    return band.get(node.id);
  };
  measureNode(root, 0);

  // Second pass, root first: positions.
  let width = 0;
  const place = (node, x, top) => {
    const box = boxes.get(node.id);
    const mine = band.get(node.id);
    box.x = x;
    box.y = top + (mine - box.height) / 2;
    order.push(node.id);
    width = Math.max(width, x + box.width);
    const kids = shown(node);
    const below = kids.reduce((sum, child) => sum + band.get(child.id), 0) + Math.max(0, kids.length - 1) * sizes.gapY;
    let childTop = top + (mine - below) / 2;
    for (const child of kids) {
      edges.push({ from: node.id, to: child.id });
      place(child, x + box.width + sizes.gapX, childTop);
      childTop += band.get(child.id) + sizes.gapY;
    }
  };
  place(root, 0, 0);
  return { boxes, edges, order, width, height: band.get(root.id) };
}

// The curve from a parent's right edge to a child's left edge, as SVG path data.
export function edgePath(from, to) {
  const x1 = from.x + from.width;
  const y1 = from.y + from.height / 2;
  const x2 = to.x;
  const y2 = to.y + to.height / 2;
  const middle = (x1 + x2) / 2;
  return "M" + [x1, y1].join(" ") + " C" + [middle, y1, middle, y2, x2, y2].join(" ");
}

// The node an arrow key leads to, or null: left is the parent, right the
// first child, up and down the nearest node of the same depth.
export function neighbour(layout, id, direction) {
  const box = layout.boxes.get(id);
  if (!box) {
    return null;
  }
  if (direction === "left") {
    return box.parent;
  }
  if (direction === "right") {
    const edge = layout.edges.find((candidate) => candidate.from === id);
    return edge ? edge.to : null;
  }
  const level = [...layout.boxes.values()].filter((other) => other.depth === box.depth).sort((a, b) => a.y - b.y);
  const other = level[level.indexOf(box) + (direction === "up" ? -1 : 1)];
  return other ? other.id : null;
}

// The view (pan x, y and zoom k) that shows the whole drawing in a stage of
// this size, never enlarged beyond "most".
export function fitView(layout, stageWidth, stageHeight, margin = 40, most = 1.25) {
  const k = Math.max(
    0.1,
    Math.min(most, (stageWidth - 2 * margin) / (layout.width || 1), (stageHeight - 2 * margin) / (layout.height || 1))
  );
  return { k, x: (stageWidth - layout.width * k) / 2, y: (stageHeight - layout.height * k) / 2 };
}

// Zoom by a factor while the point (px, py) of the stage stays where it is.
export function zoomAt(view, factor, px, py, least = 0.2, most = 3) {
  const k = Math.min(most, Math.max(least, view.k * factor));
  const ratio = k / view.k;
  return { k, x: px - (px - view.x) * ratio, y: py - (py - view.y) * ratio };
}
