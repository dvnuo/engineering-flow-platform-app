// The page: the board, its tools, and the gallery.
//
// The board is two canvases. "kept" holds every finished stroke and is never
// shown; the visible board is a copy of it plus the stroke that is under the
// pointer right now. A stroke in progress is therefore repainted many times
// a second without touching what is already there. Both stay transparent:
// the white paper is a background of the page, and it is painted into the
// picture only when the picture is saved.
//
// Nothing asks the server on a timer: the gallery is loaded when the page
// opens, after a save or a delete, and on "Refresh".

import * as api from "./api.js";
import { FONT_FAMILY, paintAll, paintStroke } from "./paint.js";
import { BOARD, COLORS, WIDTHS, boardPoint, createHistory, extendStroke, fontSize, isWorthKeeping, startStroke } from "./shapes.js";

const $ = (id) => document.getElementById(id);
const board = $("board");
const view = board.getContext("2d");
const kept = document.createElement("canvas");
kept.width = BOARD.width;
kept.height = BOARD.height;
const keptContext = kept.getContext("2d");
const textEntry = $("text-entry");
const status = $("status");
const viewer = $("viewer");

const SHORTCUTS = { p: "pen", e: "eraser", l: "line", r: "rectangle", o: "ellipse", a: "arrow", t: "text" };
const WIDTH_NAMES = ["Thin", "Medium", "Thick", "Very thick"];

const strokes = createHistory();
let tool = "pen";
let color = COLORS[0];
let width = WIDTHS[1];
let current = null; // the stroke under the pointer
let drawingPointer = null; // the one pointer that draws; a second finger is ignored
let framePending = false;
let unsaved = false;
let textAt = null; // where the text that is being typed will go
let session = { signedIn: false, limits: { bytes: 2097152, per_member: 24, title: 80 } };
let opened = null; // the drawing in the viewer

function say(target, text, isProblem) {
  target.textContent = text || "";
  target.classList.toggle("problem", Boolean(isProblem));
}

// --- Painting ---------------------------------------------------------------------------

function show() {
  framePending = false;
  view.clearRect(0, 0, BOARD.width, BOARD.height);
  view.drawImage(kept, 0, 0);
  if (current) {
    paintStroke(view, current);
  }
}

// At most one repaint per frame of the screen, however fast the pointer reports.
function showSoon() {
  if (!framePending) {
    framePending = true;
    requestAnimationFrame(show);
  }
}

function updateButtons() {
  $("undo").disabled = !strokes.canUndo();
  $("redo").disabled = !strokes.canRedo();
  $("clear").disabled = strokes.isEmpty();
}

// Everything again from the list of strokes: after an undo, a redo or a wipe.
function repaint() {
  paintAll(keptContext, strokes.visible());
  show();
  updateButtons();
}

function keep(stroke) {
  strokes.add(stroke);
  unsaved = true;
  if (stroke.tool === "clear") {
    repaint();
  } else {
    paintStroke(keptContext, stroke); // one more stroke on top: no need to paint the others again
    show();
    updateButtons();
  }
}

// --- The pointer: mouse, touch and pen alike -------------------------------------------------

function pointOf(event) {
  return boardPoint(board.getBoundingClientRect(), event.clientX, event.clientY);
}

board.addEventListener("pointerdown", (event) => {
  if (tool === "text" || drawingPointer !== null || (event.pointerType === "mouse" && event.button !== 0)) {
    return;
  }
  drawingPointer = event.pointerId;
  board.setPointerCapture(event.pointerId); // the stroke goes on when the pointer leaves the board
  current = startStroke(tool, color, width, pointOf(event));
  showSoon();
});

board.addEventListener("pointermove", (event) => {
  if (event.pointerId !== drawingPointer || !current) {
    return;
  }
  // A pen or a fast mouse reports several positions per frame. The browser
  // hands over the last one and keeps the others: take them all, or a quick
  // curve becomes a polygon.
  const samples = event.getCoalescedEvents ? event.getCoalescedEvents() : [];
  for (const sample of samples.length ? samples : [event]) {
    extendStroke(current, pointOf(sample));
  }
  showSoon();
});

function endStroke(event, finished) {
  if (event.pointerId !== drawingPointer) {
    return;
  }
  drawingPointer = null;
  const stroke = current;
  current = null;
  if (finished && stroke && isWorthKeeping(stroke)) {
    keep(stroke);
  } else {
    show();
  }
}

board.addEventListener("pointerup", (event) => endStroke(event, true));
board.addEventListener("pointercancel", (event) => endStroke(event, false));

// --- Text: a field on the board, at the place that was clicked ---------------------------------

function closeTextEntry(keepIt) {
  if (!textAt) {
    return;
  }
  const stroke = { tool: "text", color, width, at: textAt, text: textEntry.value };
  textAt = null;
  textEntry.hidden = true;
  if (keepIt && isWorthKeeping(stroke)) {
    keep(stroke);
  }
}

// A click, not a pointerdown: by then the browser has finished moving the
// focus, so the field that is focused here keeps it.
board.addEventListener("click", (event) => {
  if (tool !== "text") {
    return;
  }
  closeTextEntry(true);
  const frame = board.getBoundingClientRect();
  textAt = pointOf(event);
  textEntry.value = "";
  textEntry.hidden = false;
  textEntry.style.left = event.clientX - frame.left + "px";
  textEntry.style.top = event.clientY - frame.top + "px";
  // The size the text will have on the board, at the board's size on the screen.
  textEntry.style.font = Math.max(10, (fontSize(width) * frame.width) / BOARD.width) + "px " + FONT_FAMILY;
  textEntry.style.color = color;
  textEntry.focus();
});

textEntry.addEventListener("keydown", (event) => {
  if (event.isComposing) {
    return; // Enter is choosing a character in an input method, not finishing the text
  }
  if (event.key === "Enter" || event.key === "Escape") {
    event.preventDefault();
    closeTextEntry(event.key === "Enter");
  }
});
textEntry.addEventListener("blur", () => closeTextEntry(true));

// --- Tools, colours, widths --------------------------------------------------------------------

function press(group, chosen) {
  for (const candidate of group.querySelectorAll("button")) {
    candidate.setAttribute("aria-pressed", String(candidate === chosen));
  }
}

function chooseTool(name) {
  closeTextEntry(true);
  tool = name;
  press($("tools"), $("tools").querySelector('[data-tool="' + name + '"]'));
  board.dataset.tool = name;
}

for (const candidate of $("tools").querySelectorAll("button")) {
  candidate.addEventListener("click", () => chooseTool(candidate.dataset.tool));
}

COLORS.forEach((value, index) => {
  const swatch = document.createElement("button");
  swatch.type = "button";
  swatch.className = "swatch";
  swatch.style.background = value;
  swatch.setAttribute("aria-label", "Colour " + (index + 1));
  swatch.setAttribute("aria-pressed", String(index === 0));
  swatch.addEventListener("click", () => {
    color = value;
    press($("colors"), swatch);
  });
  $("custom-color").before(swatch);
});

$("custom-color").addEventListener("input", () => {
  color = $("custom-color").value;
  press($("colors"), null);
});

WIDTHS.forEach((value, index) => {
  const choice = document.createElement("button");
  choice.type = "button";
  choice.className = "width";
  choice.title = WIDTH_NAMES[index];
  choice.setAttribute("aria-label", WIDTH_NAMES[index]);
  choice.setAttribute("aria-pressed", String(value === width));
  const dot = document.createElement("span");
  dot.style.width = 4 + index * 4 + "px";
  dot.style.height = 4 + index * 4 + "px";
  choice.append(dot);
  choice.addEventListener("click", () => {
    width = value;
    press($("widths"), choice);
  });
  $("widths").append(choice);
});

function undo() {
  closeTextEntry(false);
  strokes.undo();
  unsaved = true;
  repaint();
}

function redo() {
  strokes.redo();
  unsaved = true;
  repaint();
}

$("undo").addEventListener("click", undo);
$("redo").addEventListener("click", redo);
$("clear").addEventListener("click", () => {
  closeTextEntry(false);
  if (!strokes.isEmpty()) {
    keep({ tool: "clear" });
  }
});

document.addEventListener("keydown", (event) => {
  // Keys are shortcuts only while nothing is being typed and no dialog is open.
  if (event.target.closest && event.target.closest("input, textarea, select, dialog")) {
    return;
  }
  const letter = event.key.toLowerCase();
  if (event.ctrlKey || event.metaKey) {
    if (letter === "z" || letter === "y") {
      event.preventDefault();
      if (letter === "y" || event.shiftKey) {
        redo();
      } else {
        undo();
      }
    }
  } else if (!event.altKey && SHORTCUTS[letter]) {
    chooseTool(SHORTCUTS[letter]);
  }
});

// --- The picture: save and download --------------------------------------------------------------

// The board on white paper, as PNG bytes.
function picture() {
  const sheet = document.createElement("canvas");
  sheet.width = BOARD.width;
  sheet.height = BOARD.height;
  const context = sheet.getContext("2d");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, BOARD.width, BOARD.height);
  context.drawImage(kept, 0, 0);
  return new Promise((resolve, reject) => {
    sheet.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("The picture could not be made."))), "image/png");
  });
}

function kilobytes(bytes) {
  return Math.max(1, Math.round(bytes / 1024)) + " KB";
}

$("save-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  closeTextEntry(true);
  if (strokes.isEmpty()) {
    say(status, "The board is empty: draw something first.");
    return;
  }
  $("save").disabled = true;
  say(status, "Saving...");
  try {
    const blob = await picture();
    // The server checks the size again; this only saves the visitor a wasted upload.
    if (blob.size > session.limits.bytes) {
      throw new Error("This drawing is " + kilobytes(blob.size) + ", more than the gallery takes. Download it instead.");
    }
    const saved = await api.saveDrawing($("title").value.trim(), blob);
    unsaved = false;
    $("title").value = "";
    await loadGallery();
    say(status, '"' + saved.title + '" is in the gallery (' + kilobytes(saved.bytes) + ").");
  } catch (error) {
    say(status, error.message, true);
  }
  $("save").disabled = !session.signedIn;
});

$("download").addEventListener("click", async () => {
  closeTextEntry(true);
  try {
    const url = URL.createObjectURL(await picture());
    const link = document.createElement("a");
    link.href = url;
    link.download = "whiteboard.png";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    say(status, error.message, true);
  }
});

window.addEventListener("beforeunload", (event) => {
  if (unsaved && !strokes.isEmpty()) {
    event.preventDefault();
    event.returnValue = ""; // older browsers ask only when this is set
  }
});

// --- The gallery -----------------------------------------------------------------------------------

function facts(drawing) {
  const date = new Date(drawing.created_at);
  const day = Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { dateStyle: "medium" });
  return "By " + drawing.author.name + (day ? ", " + day : "") + ", " + kilobytes(drawing.bytes || 0);
}

function card(drawing) {
  const item = document.createElement("li");
  item.className = "card";
  const open = document.createElement("button");
  open.type = "button";
  open.className = "thumb";
  open.setAttribute("aria-label", "Open " + drawing.title);
  const image = document.createElement("img");
  image.src = api.imageUrl(drawing.author.id, drawing.id);
  image.alt = "";
  image.loading = "lazy"; // a long gallery fetches a picture when it scrolls into view
  image.width = drawing.width || BOARD.width;
  image.height = drawing.height || BOARD.height;
  open.append(image);
  open.addEventListener("click", () => openViewer(drawing));
  const title = document.createElement("p");
  title.className = "card-title";
  title.textContent = drawing.title;
  const meta = document.createElement("p");
  meta.className = "muted";
  meta.textContent = facts(drawing);
  item.append(open, title, meta);
  return item;
}

async function loadGallery() {
  let found;
  try {
    found = await api.loadGallery();
  } catch (error) {
    say($("gallery-status"), error.message, true);
    return;
  }
  session = { signedIn: found.signed_in, limits: found.limits };
  const mine = found.drawings.filter((drawing) => drawing.mine).length;
  $("who").textContent = found.signed_in
    ? "Signed in as " + found.me.name + ". Draw with mouse, touch or pen; what you save is shown to everyone who can open the app."
    : "You are not signed in: you can draw and download, and look at the gallery, but not save to it.";
  $("save").disabled = !found.signed_in;
  $("count").textContent = found.signed_in ? mine + " of " + found.limits.per_member + " are yours" : "";
  $("gallery").replaceChildren(...found.drawings.map(card));
  $("gallery-empty").hidden = found.drawings.length > 0;
  say($("gallery-status"), "");
}

$("refresh").addEventListener("click", loadGallery);

function openViewer(drawing) {
  opened = drawing;
  $("viewer-title").textContent = drawing.title;
  $("viewer-image").src = api.imageUrl(drawing.author.id, drawing.id);
  $("viewer-image").alt = drawing.title;
  $("viewer-facts").textContent = facts(drawing) + ", " + drawing.width + " by " + drawing.height + " pixels";
  $("viewer-delete").hidden = !drawing.can_delete;
  viewer.showModal();
}

$("viewer-close").addEventListener("click", () => viewer.close());

// Put a saved drawing on the board to go on from it. Saving then makes a new
// drawing; the one in the gallery stays as it is.
$("viewer-continue").addEventListener("click", () => {
  const drawing = opened;
  const image = new Image();
  image.onload = () => {
    if (!strokes.isEmpty()) {
      keep({ tool: "clear" });
    }
    keep({ tool: "picture", image });
    viewer.close();
    say(status, '"' + drawing.title + '" is on the board. Saving it makes a new drawing.');
    board.scrollIntoView({ block: "center" });
  };
  image.onerror = () => {
    viewer.close();
    say(status, "That drawing could not be loaded.", true);
  };
  image.src = api.imageUrl(drawing.author.id, drawing.id);
});

$("confirm-cancel").addEventListener("click", () => $("confirm").close("cancel"));

$("viewer-delete").addEventListener("click", () => {
  const drawing = opened;
  const confirm = $("confirm");
  viewer.close();
  $("confirm-text").textContent = '"' + drawing.title + '" will be gone for good, for everyone.';
  confirm.returnValue = "";
  confirm.addEventListener(
    "close",
    async () => {
      if (confirm.returnValue !== "ok") {
        return;
      }
      try {
        await api.deleteDrawing(drawing.author.id, drawing.id);
        await loadGallery();
      } catch (error) {
        await loadGallery();
        say($("gallery-status"), error.message, true);
      }
    },
    { once: true }
  );
  confirm.showModal();
});

chooseTool("pen");
updateButtons();
loadGallery();
