// Draws a layout as SVG. Every colour, font and size is written as an
// attribute of the element it belongs to, not as a style sheet rule: that
// way the drawing is complete in itself and the exported file looks the same
// as the page.

import { SIZES, edgePath } from "./layout.js";
import { branchColor, find } from "./tree.js";

const SVG = "http://www.w3.org/2000/svg";
export const FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif';
export const FONT_SIZE = 14;
const ROOT_FONT_SIZE = 16;
const INK = "#1c2024";

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
const PLAIN = { fill: "#eef1f5", line: "#7a8696" };

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

function drawNode(box, node, colors, isRoot) {
  const group = element("g", { class: "node", "data-id": box.id, transform: "translate(" + box.x + " " + box.y + ")" });
  group.append(
    element("rect", {
      width: box.width,
      height: box.height,
      rx: isRoot ? 14 : 9,
      fill: isRoot ? colors.line : colors.fill,
      stroke: colors.line,
      "stroke-width": 1.5,
    })
  );
  const label = element("text", {
    "font-family": FONT_FAMILY,
    "font-size": isRoot ? ROOT_FONT_SIZE : FONT_SIZE,
    "font-weight": isRoot ? 600 : 400,
    fill: isRoot ? "#ffffff" : INK,
  });
  box.lines.forEach((line, index) => {
    const span = element("tspan", { x: SIZES.padX, y: SIZES.padY + SIZES.lineHeight * index + 15 });
    span.textContent = line || (index === 0 && !node.text ? "..." : "");
    label.append(span);
  });
  group.append(label);
  if (box.hidden) {
    // A collapsed node says how many nodes it hides.
    const badge = element("text", {
      x: box.width - SIZES.padX,
      y: box.height / 2 + 4,
      "text-anchor": "end",
      "font-family": FONT_FAMILY,
      "font-size": 11,
      "font-weight": 600,
      fill: isRoot ? "#ffffff" : colors.line,
    });
    badge.textContent = "+" + box.hidden;
    group.append(badge);
  }
  const title = element("title");
  title.textContent = node.text || "Empty node";
  group.append(title);
  return group;
}

// Fill "target" (an SVG group) with the drawing of this layout.
export function draw(target, nodes, layout) {
  const lines = element("g", { fill: "none", "stroke-width": 2, "stroke-linecap": "round" });
  const shapes = element("g");
  for (const edge of layout.edges) {
    const colors = PALETTE[branchColor(nodes, edge.to)] || PLAIN;
    lines.append(
      element("path", { d: edgePath(layout.boxes.get(edge.from), layout.boxes.get(edge.to)), stroke: colors.line })
    );
  }
  for (const id of layout.order) {
    const box = layout.boxes.get(id);
    const colors = PALETTE[branchColor(nodes, id)] || PLAIN;
    shapes.append(drawNode(box, find(nodes, id), colors, box.depth === 0));
  }
  target.replaceChildren(lines, shapes);
}

// The drawing as a document of its own: a white sheet a little larger than
// the map. Used for the SVG and the PNG export.
export function standalone(nodes, layout, margin = 24) {
  const width = Math.ceil(layout.width + 2 * margin);
  const height = Math.ceil(layout.height + 2 * margin);
  // No xmlns attribute is set here: the serializer writes the namespace of an
  // element made with createElementNS by itself, and a second one would be an error.
  const svg = element("svg", { width, height, viewBox: "0 0 " + width + " " + height });
  svg.append(element("rect", { width, height, fill: "#ffffff" }));
  const sheet = element("g", { transform: "translate(" + margin + " " + margin + ")" });
  draw(sheet, nodes, layout);
  svg.append(sheet);
  return { svg, width, height };
}
