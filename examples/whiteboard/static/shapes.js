// What a drawing is made of, as plain data, and the arithmetic around it.
// Nothing here touches the page, so the tests run it with node.
//
// A drawing is a list of strokes, in the order they were made:
//   {tool: "pen" | "eraser", color, width, points: [{x, y}, ...]}
//   {tool: "line" | "rectangle" | "ellipse" | "arrow", color, width, from, to}
//   {tool: "text", color, width, at, text}
//   {tool: "picture", image}      a saved drawing to continue on
//   {tool: "clear"}               everything before it is wiped
// All coordinates are in the board's own units (BOARD), whatever size the
// board has on the screen.

export const BOARD = { width: 1600, height: 1000 };
export const TOOLS = ["pen", "eraser", "line", "rectangle", "ellipse", "arrow", "text"];
export const WIDTHS = [3, 6, 12, 24];
export const COLORS = ["#1c2024", "#b3261e", "#d97706", "#2e7d4f", "#2f5fd0", "#6f42c1", "#5f6b7a"];

// A position on the screen as a point of the board. "frame" is where the
// board is on the screen (getBoundingClientRect).
export function boardPoint(frame, clientX, clientY) {
  const x = ((clientX - frame.left) / (frame.width || 1)) * BOARD.width;
  const y = ((clientY - frame.top) / (frame.height || 1)) * BOARD.height;
  return {
    x: Math.round(Math.min(BOARD.width, Math.max(0, x)) * 10) / 10,
    y: Math.round(Math.min(BOARD.height, Math.max(0, y)) * 10) / 10,
  };
}

export function distance(first, second) {
  return Math.hypot(first.x - second.x, first.y - second.y);
}

export function startStroke(tool, color, width, point) {
  if (tool === "pen" || tool === "eraser") {
    return { tool, color, width: tool === "eraser" ? width * 3 : width, points: [point] };
  }
  return { tool, color, width, from: point, to: point };
}

// The stroke with the pointer at a new place. A freehand line takes the
// point only when it is a little away from the last one: a slow hand would
// otherwise pile up thousands of points that change nothing.
export function extendStroke(stroke, point) {
  if (stroke.points) {
    const last = stroke.points[stroke.points.length - 1];
    if (distance(last, point) >= 1.5) {
      stroke.points.push(point);
    }
  } else {
    stroke.to = point;
  }
  return stroke;
}

// A click without a drag makes a dot with the pen and nothing with a shape.
export function isWorthKeeping(stroke) {
  if (stroke.tool === "text") {
    return stroke.text.trim() !== "";
  }
  return Boolean(stroke.points) || distance(stroke.from, stroke.to) >= 3;
}

// The corner and size of the box between two points, whichever way it was dragged.
export function box(from, to) {
  return {
    x: Math.min(from.x, to.x),
    y: Math.min(from.y, to.y),
    width: Math.abs(to.x - from.x),
    height: Math.abs(to.y - from.y),
  };
}

// The two ends of an arrow's head: the points left and right of the tip.
export function arrowHead(from, to, width) {
  const size = Math.max(14, width * 4);
  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  const spread = Math.PI / 7;
  return [angle + Math.PI - spread, angle + Math.PI + spread].map((direction) => ({
    x: to.x + size * Math.cos(direction),
    y: to.y + size * Math.sin(direction),
  }));
}

export function fontSize(width) {
  return 14 + width * 2;
}

// --- The list of strokes, with undo and redo --------------------------------------

export function createHistory(limit = 400) {
  const done = [];
  let undone = [];
  return {
    // A new stroke; what was undone before it can no longer be redone.
    add(stroke) {
      done.push(stroke);
      if (done.length > limit) {
        done.shift();
      }
      undone = [];
    },
    undo() {
      if (done.length) {
        undone.push(done.pop());
      }
    },
    redo() {
      if (undone.length) {
        done.push(undone.pop());
      }
    },
    canUndo: () => done.length > 0,
    canRedo: () => undone.length > 0,
    // What is on the board: the strokes since the last "clear". Clearing is
    // a stroke like any other, so it can be undone.
    visible() {
      let start = 0;
      done.forEach((stroke, index) => {
        if (stroke.tool === "clear") {
          start = index + 1;
        }
      });
      return done.slice(start);
    },
    isEmpty() {
      return this.visible().length === 0;
    },
  };
}
