// The tree of a mind map as plain data, and every change to it as a function
// that returns a new list. Nothing here touches the page, so the same code
// runs in the browser and in the tests (node).
//
// A node is {id, parent, text, color, collapsed}. The order of the list is
// the order of siblings, and exactly one node (the centre) has parent null.
// The server checks the same rules again before it stores a map.

export const COLORS = ["blue", "green", "amber", "red", "purple", "teal", "pink", "gray"];

export function rootOf(nodes) {
  return nodes.find((node) => node.parent === null) || null;
}

export function find(nodes, id) {
  return nodes.find((node) => node.id === id) || null;
}

// Map of parent id -> its children, in sibling order.
export function childrenOf(nodes) {
  const children = new Map();
  for (const node of nodes) {
    children.set(node.id, children.get(node.id) || []);
    if (node.parent !== null) {
      const siblings = children.get(node.parent) || [];
      siblings.push(node);
      children.set(node.parent, siblings);
    }
  }
  return children;
}

// The ids of a node and of everything below it.
export function branchIds(nodes, id) {
  const children = childrenOf(nodes);
  const found = new Set();
  const waiting = [id];
  while (waiting.length) {
    const current = waiting.pop();
    if (found.has(current)) {
      continue;
    }
    found.add(current);
    for (const child of children.get(current) || []) {
      waiting.push(child.id);
    }
  }
  return found;
}

export function newId(nodes) {
  const taken = new Set(nodes.map((node) => node.id));
  for (;;) {
    const id = "n" + Math.random().toString(36).slice(2, 10);
    if (id.length > 1 && !taken.has(id)) {
      return id;
    }
  }
}

function blank(id, parent, text) {
  return { id, parent, text: text || "", color: null, collapsed: false };
}

// A new last child. The parent opens up, or the child would be invisible.
export function addChild(nodes, parentId, text) {
  if (!find(nodes, parentId)) {
    return { nodes, id: null };
  }
  const id = newId(nodes);
  const next = nodes.map((node) => (node.id === parentId && node.collapsed ? { ...node, collapsed: false } : node));
  next.push(blank(id, parentId, text));
  return { nodes: next, id };
}

// A new node right below this one, under the same parent. The centre has no
// siblings: there the new node becomes a child.
export function addSibling(nodes, id, text) {
  const node = find(nodes, id);
  if (!node) {
    return { nodes, id: null };
  }
  if (node.parent === null) {
    return addChild(nodes, id, text);
  }
  const created = newId(nodes);
  const next = nodes.slice();
  next.splice(nodes.indexOf(node) + 1, 0, blank(created, node.parent, text));
  return { nodes: next, id: created };
}

// A change that changes nothing returns the list it was given, so the
// caller can tell (and does not record an empty step to undo).
function update(nodes, id, field, value) {
  const node = find(nodes, id);
  if (!node || node[field] === value) {
    return nodes;
  }
  return nodes.map((other) => (other === node ? { ...node, [field]: value } : other));
}

export function rename(nodes, id, text) {
  return update(nodes, id, "text", text);
}

export function setColor(nodes, id, color) {
  return update(nodes, id, "color", COLORS.includes(color) ? color : null);
}

// A leaf has nothing to fold.
export function toggleCollapsed(nodes, id) {
  const node = find(nodes, id);
  const hasChildren = nodes.some((other) => other.parent === id);
  return node && hasChildren ? update(nodes, id, "collapsed", !node.collapsed) : nodes;
}

// Remove a node with everything below it. The centre stays. "select" is the
// node to select afterwards: the next sibling, else the previous, else the parent.
export function removeBranch(nodes, id) {
  const node = find(nodes, id);
  if (!node || node.parent === null) {
    return { nodes, select: id };
  }
  const siblings = nodes.filter((other) => other.parent === node.parent);
  const position = siblings.indexOf(node);
  const neighbour = siblings[position + 1] || siblings[position - 1];
  const gone = branchIds(nodes, id);
  return { nodes: nodes.filter((other) => !gone.has(other.id)), select: neighbour ? neighbour.id : node.parent };
}

// Why a node cannot be moved below another one, or "" when it can.
export function moveProblem(nodes, id, parentId) {
  const node = find(nodes, id);
  if (!node || !find(nodes, parentId)) {
    return "That node is no longer in the map.";
  }
  if (node.parent === null) {
    return "The centre of the map stays where it is.";
  }
  if (branchIds(nodes, id).has(parentId)) {
    return "A node cannot move into its own branch.";
  }
  if (node.parent === parentId) {
    return "It is already there.";
  }
  return "";
}

// Hang a node, with its branch, below another parent as its last child.
export function moveNode(nodes, id, parentId) {
  if (moveProblem(nodes, id, parentId)) {
    return nodes;
  }
  const moved = { ...find(nodes, id), parent: parentId };
  const next = nodes
    .filter((node) => node.id !== id)
    .map((node) => (node.id === parentId && node.collapsed ? { ...node, collapsed: false } : node));
  next.push(moved);
  return next;
}

// Swap a node with the sibling before (-1) or after (+1) it.
export function reorder(nodes, id, step) {
  const node = find(nodes, id);
  if (!node || node.parent === null) {
    return nodes;
  }
  const siblings = nodes.filter((other) => other.parent === node.parent);
  const other = siblings[siblings.indexOf(node) + step];
  if (!other) {
    return nodes;
  }
  const next = nodes.slice();
  const from = nodes.indexOf(node);
  const to = nodes.indexOf(other);
  next[from] = other;
  next[to] = node;
  return next;
}

// The colour a node is drawn in: its own, else the nearest ancestor's. That
// is what makes a colour the colour of a whole branch.
export function branchColor(nodes, id) {
  let node = find(nodes, id);
  for (let steps = 0; node && steps <= nodes.length; steps += 1) {
    if (node.color) {
      return node.color;
    }
    node = node.parent === null ? null : find(nodes, node.parent);
  }
  return null;
}

// The map as a Markdown outline: the centre is the heading, the rest a nested list.
export function toMarkdown(nodes) {
  const root = rootOf(nodes);
  if (!root) {
    return "";
  }
  const children = childrenOf(nodes);
  const lines = ["# " + (root.text || "Untitled"), ""];
  const walk = (node, depth) => {
    lines.push("  ".repeat(depth) + "- " + (node.text || "(empty)"));
    for (const child of children.get(node.id) || []) {
      walk(child, depth + 1);
    }
  };
  for (const child of children.get(root.id) || []) {
    walk(child, 0);
  }
  return lines.join("\n").trimEnd() + "\n";
}
