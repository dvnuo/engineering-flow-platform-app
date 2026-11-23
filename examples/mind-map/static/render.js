// Draws a layout as SVG. Every colour, font and size is written as an
// attribute of the element it belongs to, not as a style sheet rule: that
// way the drawing is complete in itself and the exported file looks the same
// as the page.
//
// The colours are not decided here. The page reads them from its theme
// (themeColors in app.js) and hands them over with useColors(), again each
// time the visitor switches between light and dark or the app gets another
// data-theme. The values below are only what a drawing gets without a page
// around it, for example in the tests.

import { SIZES, edgePath } from "./layout.js";
import { branchColor, find } from "./tree.js";

const SVG = "http://www.w3.org/2000/svg";
export const FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif';
export const FONT_SIZE = 14;
const ROOT_FONT_SIZE = 16;
const ROOT_FILL = "mm-root-fill"; // the id of the centre's gradient

// A branch colour: the tint that fills its nodes and the line around them.
export const PALETTE = {
  blue: { fill: "#dbe7ff", line: "#2f5fd0" },
  green: { fill: "#d7f0dc", line: "#2e7d4f" },
  amber: { fill: "#ffe9b8", line: "#a86a00" },
  red: { fill: "#ffdad6", line: "#b3261e" },
  purple: { fill: "#eadcff", line: "#6f42c1" },
  teal: { fill: "#cfeeee", line: "#0f7b82" },
  pink: { fill: "#ffd8ea", line: "#b0326d" },
  gray: { fill: "#e6e8eb", line: "#5f6b7a" },
};

// Everything else: the sheet under the map, the text, a node of no colour,
// and the centre, which is filled with a gradient unless it has a colour.
export const THEME = {
  sheet: "#ffffff",
  text: "#1c2024",
  plain: { fill: "#f3f5f8", line: "#7a8696" },
  root: { from: "#3d5bd9", to: "#8a3ffc", text: "#ffffff" },
};

// The theme's colours, from the page: { palette: {blue: {fill, line}, ...}, sheet, text, plain, root }.
export function useColors(colors) {
  Object.assign(PALETTE, colors.palette || {});
  for (const key of ["sheet", "text", "plain", "root"]) {
    if (colors[key]) {
      THEME[key] = colors[key];
    }
  }
}

let ruler = null;

// The width of a piece of text, measured on a canvas: no layout of the page
// is needed for it, so measuring five hundred nodes is quick.
export function measureText(text, isRoot) {
  if (!ruler) {
    ruler = document.createElement("canvas").getContext("2d");
  }
  ruler.font = (isRoot ? "600 " + ROOT_FONT_SIZE : FONT_SIZE) + "px " + FONT_FAMILY;
  return ruler.measureText(text).width;
}

function element(name, attributes) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes || {})) {
    node.setAttribute(key, String(value));
  }
  return node;
}

function colorsOf(nodes, id) {
  return PALETTE[branchColor(nodes, id)] || THEME.plain;
}

// The gradient of the centre. It goes into every drawing that uses it.
export function definitions() {
  const gradient = element("linearGradient", { id: ROOT_FILL, x1: 0, y1: 0, x2: 1, y2: 1 });
  gradient.append(
    element("stop", { offset: 0, "stop-color": THEME.root.from }),
    element("stop", { offset: 1, "stop-color": THEME.root.to })
  );
  const defs = element("defs");
  defs.append(gradient);
  return defs;
}

// The line into a node: the colour of its branch, thinner the further out it is.
export function edgeStyle(nodes, box) {
  return { stroke: colorsOf(nodes, box.id).line, width: Math.max(1.5, 3.25 - box.depth * 0.5) };
}

// Everything that decides how a node looks. Two equal keys draw the same
// node, so the page can keep the one it has (see scene.js).
export function nodeKey(box, nodes) {
  const node = find(nodes, box.id) || {};
  const colors = colorsOf(nodes, box.id);
  return [
    box.width,
    box.height,
    box.hidden,
    box.depth === 0,
    node.color || "",
    colors.fill,
    colors.line,
    THEME.text,
    THEME.sheet,
    THEME.root.from,
    THEME.root.to,
    node.text ? "" : "empty",
    box.lines.join("\n"),
  ].join("|");
}

// One node: its box, its text, and how many nodes it hides when folded.
export function nodeShape(box, nodes) {
  const node = find(nodes, box.id) || { text: "", color: null };
  const isRoot = box.depth === 0;
  const colors = colorsOf(nodes, box.id);
  const group = element("g", { class: "node", "data-id": box.id, transform: "translate(" + box.x + " " + box.y + ")" });
  // A centre without a colour of its own wears the theme's gradient; with
  // one, it is filled with it and its text takes the colour of the sheet.
  const rootFill = node.color ? colors.line : "url(#" + ROOT_FILL + ")";
  const ink = isRoot ? (node.color ? THEME.sheet : THEME.root.text) : THEME.text;
  group.append(
    element("rect", {
      class: "node-body",
      width: box.width,
      height: box.height,
      rx: isRoot ? 14 : 10,
      fill: isRoot ? rootFill : colors.fill,
      stroke: isRoot ? "none" : colors.line,
      "stroke-width": isRoot ? 0 : 1.5,
    })
  );
  const label = element("text", {
    "font-family": FONT_FAMILY,
    "font-size": isRoot ? ROOT_FONT_SIZE : FONT_SIZE,
    "font-weight": isRoot ? 600 : 400,
    fill: ink,
  });
  if (!node.text) {
    label.setAttribute("fill-opacity", "0.45");
  }
  box.lines.forEach((line, index) => {
    const span = element("tspan", { x: SIZES.padX, y: SIZES.padY + SIZES.lineHeight * index + 15 });
    span.textContent = line || (index === 0 && !node.text ? "..." : "");
    label.append(span);
  });
  group.append(label);
  if (box.hidden) {
    // A folded node says how many nodes it hides, on a small pill of its colour.
    const text = "+" + box.hidden;
    const width = Math.max(22, text.length * 6.6 + 10);
    const pill = element("g", { transform: "translate(" + (box.width - 7 - width) + " " + (box.height / 2 - 9) + ")" });
    pill.append(
      element("rect", {
        width,
        height: 18,
        rx: 9,
        fill: isRoot ? THEME.root.text : colors.line,
        "fill-opacity": isRoot ? 0.24 : 1,
      }),
      element("text", {
        x: width / 2,
        y: 12.5,
        "text-anchor": "middle",
        "font-family": FONT_FAMILY,
        "font-size": 11,
        "font-weight": 700,
        fill: isRoot ? THEME.root.text : THEME.sheet,
      })
    );
    pill.lastChild.textContent = text;
    group.append(pill);
  }
  const title = element("title");
  title.textContent = node.text || "Empty node";
  group.append(title);
  return group;
}

// Fill "target" (an SVG group) with the drawing of this layout.
export function draw(target, nodes, layout) {
  const lines = element("g", { fill: "none", "stroke-linecap": "round" });
  const shapes = element("g");
  for (const edge of layout.edges) {
    const to = layout.boxes.get(edge.to);
    const look = edgeStyle(nodes, to);
    lines.append(
      element("path", { d: edgePath(layout.boxes.get(edge.from), to), stroke: look.stroke, "stroke-width": look.width })
    );
  }
  for (const id of layout.order) {
    shapes.append(nodeShape(layout.boxes.get(id), nodes));
  }
  target.replaceChildren(definitions(), lines, shapes);
}

// The drawing as a document of its own: a sheet in the colour of the canvas,
// a little larger than the map. Used for the SVG and the PNG export.
export function standalone(nodes, layout, margin = 24) {
  const width = Math.ceil(layout.width + 2 * margin);
  const height = Math.ceil(layout.height + 2 * margin);
  // No xmlns attribute is set here: the serializer writes the namespace of an
  // element made with createElementNS by itself, and a second one would be an error.
  const svg = element("svg", { width, height, viewBox: "0 0 " + width + " " + height });
  svg.append(element("rect", { width, height, fill: THEME.sheet }));
  const sheet = element("g", { transform: "translate(" + margin + " " + margin + ")" });
  draw(sheet, nodes, layout);
  svg.append(sheet);
  return { svg, width, height };
}
