// Strokes onto a canvas. The functions take a 2D context and call nothing
// else, so the same code paints the board, the preview of the stroke under
// the pointer and the picture that is saved.

import { BOARD, arrowHead, box, fontSize } from "./shapes.js";

export const FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif';

// A freehand line through its points: curves from midpoint to midpoint, so
// the corners between two samples of the pointer are rounded off.
function freehand(context, points) {
  context.beginPath();
  context.moveTo(points[0].x, points[0].y);
  if (points.length === 1) {
    context.lineTo(points[0].x + 0.01, points[0].y); // a dot: round caps make it one
  }
  for (let index = 1; index < points.length - 1; index += 1) {
    const here = points[index];
    const next = points[index + 1];
    context.quadraticCurveTo(here.x, here.y, (here.x + next.x) / 2, (here.y + next.y) / 2);
  }
  if (points.length > 1) {
    const last = points[points.length - 1];
    context.lineTo(last.x, last.y);
  }
  context.stroke();
}

export function paintStroke(context, stroke) {
  context.save();
  context.lineCap = "round";
  context.lineJoin = "round";
  context.lineWidth = stroke.width || 1;
  context.strokeStyle = stroke.color || "#000000";
  context.fillStyle = stroke.color || "#000000";
  if (stroke.tool === "pen") {
    freehand(context, stroke.points);
  } else if (stroke.tool === "eraser") {
    // The eraser does not paint white: it takes away what is there, so the
    // board stays transparent and the paper shows through.
    context.globalCompositeOperation = "destination-out";
    freehand(context, stroke.points);
  } else if (stroke.tool === "line" || stroke.tool === "arrow") {
    context.beginPath();
    context.moveTo(stroke.from.x, stroke.from.y);
    context.lineTo(stroke.to.x, stroke.to.y);
    if (stroke.tool === "arrow") {
      const [left, right] = arrowHead(stroke.from, stroke.to, stroke.width);
      context.moveTo(left.x, left.y);
      context.lineTo(stroke.to.x, stroke.to.y);
      context.lineTo(right.x, right.y);
    }
    context.stroke();
  } else if (stroke.tool === "rectangle") {
    const area = box(stroke.from, stroke.to);
    context.strokeRect(area.x, area.y, area.width, area.height);
  } else if (stroke.tool === "ellipse") {
    const area = box(stroke.from, stroke.to);
    context.beginPath();
    context.ellipse(area.x + area.width / 2, area.y + area.height / 2, area.width / 2, area.height / 2, 0, 0, 2 * Math.PI);
    context.stroke();
  } else if (stroke.tool === "text") {
    context.font = fontSize(stroke.width) + "px " + FONT_FAMILY;
    context.textBaseline = "top";
    context.fillText(stroke.text, stroke.at.x, stroke.at.y);
  } else if (stroke.tool === "picture") {
    context.drawImage(stroke.image, 0, 0, BOARD.width, BOARD.height);
  }
  context.restore();
}

// Wipe the canvas and paint these strokes in order.
export function paintAll(context, strokes) {
  context.clearRect(0, 0, BOARD.width, BOARD.height);
  for (const stroke of strokes) {
    paintStroke(context, stroke);
  }
}
