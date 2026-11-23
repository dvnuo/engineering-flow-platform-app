// The page: the board, its tools, and the gallery.
//
// The board is two canvases. "kept" holds every finished stroke and is never
// shown; the visible board is a copy of it plus the stroke that is under the
// pointer right now. A stroke in progress is therefore repainted many times
// a second without touching what is already there. Both stay transparent:
// the paper is a background of the page, and it is painted into the picture
// only when the picture is saved.
//
// The paper and the inks, decided on purpose. The paper is the theme's
// (--wb-paper: light in the light theme, dark in the dark one), so the board
// sits in the page instead of glaring out of it, and a saved picture is the
// board as it was seen, paper and all. The inks are chosen for that:
// - "Ink" is the theme's text colour: near black on light paper, near white
//   on dark paper. A stroke keeps the word "ink" rather than a colour, so the
//   board can be drawn again in the other theme and stay readable; on a saved
//   picture that is continued, it is whichever of the theme's two colours
//   stands out from that picture's paper.
// - Primary and Secondary are the theme's own, kept by name the same way: a
//   forked app with another data-theme draws in its colours.
// - Red, orange, green and blue (COLORS in shapes.js) are fixed middle tones
//   that read on white and on dark paper alike (3.5 to 1 or more), so a
//   picture keeps its meaning whoever opens it. The theme's accent is left
//   out on purpose: in the light theme it is too pale for white paper.
//
// The look comes from the EFP kit (/_efp/lib/, docs/UI_KIT.md). Motion is
// kept for what the eye should follow: the pill behind the tool in use
// slides to the next one, the brush under the pointer shows the line before
// it is drawn, a saved picture flies from the board into the gallery, and a
// wiped board is swept away.
//
// Nothing asks the server on a timer: the gallery is loaded when the page
// opens, after a save or a delete, and on "Refresh".

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import * as api from "./api.js";
import { FONT_FAMILY, paintAll, paintStroke } from "./paint.js";
import { BOARD, COLORS, WIDTHS, boardPoint, createHistory, extendStroke, fontSize, isWorthKeeping, startStroke } from "./shapes.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const board = $("board");
const paper = $("paper");
const view = board.getContext("2d");
const kept = document.createElement("canvas");
kept.width = BOARD.width;
kept.height = BOARD.height;
const keptContext = kept.getContext("2d");
const textEntry = $("text-entry");
const brush = $("brush");
const gallery = $("gallery");
const viewer = $("viewer");

const SHORTCUTS = { p: "pen", e: "eraser", l: "line", r: "rectangle", o: "ellipse", a: "arrow", t: "text" };
const WIDTH_NAMES = ["Thin", "Medium", "Thick", "Very thick"];
const DOT_SIZES = [4, 7, 11, 17]; // pixels of the width buttons' dots
const INKS = [
  { token: "ink", name: "Ink: dark on light paper, light on dark paper" },
  { token: "primary", name: "The theme's primary colour" },
  { token: "secondary", name: "The theme's secondary colour" },
  ...COLORS.map((value, index) => ({ token: value, name: ["Red", "Orange", "Green", "Blue"][index] || value })),
];

const strokes = createHistory();
let tool = "pen";
let color = "ink"; // a token of INKS, or a colour from the picker
let width = WIDTHS[1];
let current = null; // the stroke under the pointer
let drawingPointer = null; // the one pointer that draws; a second finger is ignored
let framePending = false;
let unsaved = false;
let textAt = null; // where the text that is being typed will go
let session = { signedIn: false, limits: { bytes: 2097152, per_member: 24, title: 80 } };
let opened = null; // the drawing in the viewer
let theme = {}; // the theme's colours as rgb(), read by readTheme()
let inks = {}; // token -> the colour it paints in right now

function lightness(rgb) {
  const [r, g, b] = (rgb.match(/\d+(\.\d+)?/g) || [0, 0, 0]).map(Number);
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}

function readTheme() {
  theme = {
    paper: kit.color("var(--wb-paper)"),
    content: kit.color("base-content"),
    primary: kit.color("primary"),
    secondary: kit.color("secondary"),
  };
}

// The paper under the pen: the theme's, or that of a saved picture that was put on the board.
function paperUnderPen() {
  const pictures = strokes.visible().filter((stroke) => stroke.tool === "picture");
  return pictures.length ? pictures[pictures.length - 1].paper || theme.paper : theme.paper;
}

function refreshInks() {
  const under = lightness(paperUnderPen());
  const standsOut = Math.abs(lightness(theme.content) - under) >= Math.abs(lightness(theme.paper) - under);
  inks = { ink: standsOut ? theme.content : theme.paper, primary: theme.primary, secondary: theme.secondary };
  $("studio").style.setProperty("--wb-ink", inkOf(color));
  // The swatch of "Ink" shows what it paints in now, which on a continued picture is not always the text colour.
  const swatch = $("colors").querySelector('[data-token="ink"]');
  if (swatch) {
    swatch.style.setProperty("--swatch", inks.ink);
  }
}

function inkOf(token) {
  return inks[token] || token;
}

// A stroke as it is painted: with the colour its token stands for now.
function resolve(stroke) {
  return stroke.color && inks[stroke.color] ? { ...stroke, color: inks[stroke.color] } : stroke;
}

function kilobytes(bytes) {
  return Math.max(1, Math.round(bytes / 1024)) + " KB";
}

// --- Painting ---------------------------------------------------------------------------

function show() {
  framePending = false;
  view.clearRect(0, 0, BOARD.width, BOARD.height);
  view.drawImage(kept, 0, 0);
  if (current) {
    paintStroke(view, resolve(current));
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
  paper.classList.toggle("is-empty", strokes.isEmpty() && !current && !textAt);
}

// Everything again from the list of strokes: after an undo, a redo, a wipe or a new theme.
function repaint() {
  refreshInks();
  paintAll(keptContext, strokes.visible().map(resolve));
  show();
  updateButtons();
}

function keep(stroke) {
  strokes.add(stroke);
  unsaved = true;
  if (stroke.tool === "clear" || stroke.tool === "picture") {
    repaint();
  } else {
    paintStroke(keptContext, resolve(stroke)); // one more stroke on top: no need to paint the others again
    show();
    updateButtons();
  }
}

// --- The pointer: mouse, touch and pen alike -------------------------------------------------

function pointOf(event) {
  return boardPoint(board.getBoundingClientRect(), event.clientX, event.clientY);
}

// The brush: a ring as wide as the line will be, where the line will go.
function moveBrush(event) {
  if (event.pointerType === "touch" || tool === "text") {
    brush.hidden = true;
    return;
  }
  const frame = board.getBoundingClientRect();
  const size = Math.max(5, ((tool === "eraser" ? width * 3 : width) * frame.width) / BOARD.width);
  brush.hidden = false;
  brush.dataset.tool = tool;
  brush.style.width = size + "px";
  brush.style.height = size + "px";
  brush.style.transform = "translate(" + (event.clientX - frame.left - size / 2) + "px, " + (event.clientY - frame.top - size / 2) + "px)";
}

board.addEventListener("pointerleave", () => {
  brush.hidden = true;
});

board.addEventListener("pointerdown", (event) => {
  if (tool === "text" || drawingPointer !== null || (event.pointerType === "mouse" && event.button !== 0)) {
    return;
  }
  drawingPointer = event.pointerId;
  board.setPointerCapture(event.pointerId); // the stroke goes on when the pointer leaves the board
  current = startStroke(tool, color, width, pointOf(event));
  updateButtons();
  showSoon();
});

board.addEventListener("pointermove", (event) => {
  moveBrush(event);
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
    updateButtons();
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
  } else {
    updateButtons();
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
  textEntry.style.color = inkOf(color);
  updateButtons();
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
  for (const candidate of group.querySelectorAll("[aria-pressed]")) {
    candidate.setAttribute("aria-pressed", String(candidate === chosen));
  }
}

// The pill behind the tool in use slides to the one that was chosen.
function placePill() {
  const chosen = $("tools").querySelector('button[aria-pressed="true"]');
  const pill = $("tool-pill");
  if (!chosen || !chosen.offsetWidth) {
    return;
  }
  pill.style.width = chosen.offsetWidth + "px";
  pill.style.height = chosen.offsetHeight + "px";
  pill.style.transform = "translate(" + chosen.offsetLeft + "px, " + chosen.offsetTop + "px)";
}

function chooseTool(name) {
  closeTextEntry(true);
  tool = name;
  press($("tools"), $("tools").querySelector('[data-tool="' + name + '"]'));
  board.dataset.tool = name;
  brush.hidden = true;
  placePill();
}

for (const candidate of $("tools").querySelectorAll("button")) {
  candidate.addEventListener("click", () => chooseTool(candidate.dataset.tool));
}

function chooseColor(token, swatch) {
  color = token;
  press($("colors"), swatch);
  $("studio").style.setProperty("--wb-ink", inkOf(color));
  if (textAt) {
    textEntry.style.color = inkOf(color);
  }
}

for (const ink of INKS) {
  const isTheme = !ink.token.startsWith("#");
  const swatch = h("button.wb-swatch", {
    type: "button",
    class: isTheme ? "is-theme" : "",
    dataset: { token: ink.token },
    title: ink.name,
    "aria-label": ink.name,
    "aria-pressed": String(ink.token === color),
    // The theme's colours are CSS variables here, so the swatch follows a new theme by itself.
    style: { "--swatch": isTheme ? (ink.token === "ink" ? "var(--color-base-content)" : "var(--color-" + ink.token + ")") : ink.token },
  });
  swatch.addEventListener("click", () => chooseColor(ink.token, swatch));
  $("colors").insertBefore(swatch, $("colors").lastElementChild);
}

const customLabel = $("custom-color").closest("label");
customLabel.setAttribute("aria-pressed", "false");
$("custom-color").addEventListener("input", () => chooseColor($("custom-color").value, customLabel));

WIDTHS.forEach((value, index) => {
  const choice = h(
    "button.wb-width",
    { type: "button", title: WIDTH_NAMES[index], "aria-label": WIDTH_NAMES[index], "aria-pressed": String(value === width) },
    h("span", { style: { width: DOT_SIZES[index] + "px", height: DOT_SIZES[index] + "px" } })
  );
  choice.addEventListener("click", () => chooseWidth(index));
  $("widths").append(choice);
});

function chooseWidth(index) {
  const choice = $("widths").children[index];
  width = WIDTHS[index];
  press($("widths"), choice);
  kit.pop(choice.firstChild, { scale: 1.3 });
}

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

// Wiping is a stroke like any other (it can be undone). What was on the
// board is swept away from left to right instead of vanishing at once.
function wipe() {
  closeTextEntry(false);
  if (strokes.isEmpty()) {
    return;
  }
  let sweep = null;
  if (!kit.reducedMotion()) {
    const copy = h("canvas.wb-wipe", { width: BOARD.width, height: BOARD.height });
    copy.getContext("2d").drawImage(board, 0, 0);
    const squeegee = h("span.wb-squeegee");
    paper.append(copy, squeegee);
    paper.classList.add("is-wiping"); // the invitation of an empty board waits for the sweep
    const timing = { duration: 620, easing: kit.EASE, fill: "forwards" };
    sweep = Promise.all([
      copy.animate([{ clipPath: "inset(0 0 0 0)" }, { clipPath: "inset(0 0 0 100%)" }], timing).finished,
      squeegee.animate([{ left: "0%", opacity: 1 }, { left: "100%", opacity: 0.6 }], timing).finished,
    ]).finally(() => {
      copy.remove();
      squeegee.remove();
      paper.classList.remove("is-wiping");
    });
  }
  keep({ tool: "clear" });
  return sweep;
}

$("undo").addEventListener("click", undo);
$("redo").addEventListener("click", redo);
$("clear").addEventListener("click", wipe);

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
  } else if (event.key === "[" || event.key === "]") {
    const index = WIDTHS.indexOf(width) + (event.key === "]" ? 1 : -1);
    if (index >= 0 && index < WIDTHS.length) {
      chooseWidth(index);
    }
  } else if (!event.altKey && SHORTCUTS[letter]) {
    chooseTool(SHORTCUTS[letter]);
  }
});

// --- The picture: save and download --------------------------------------------------------------

// The board on its paper, as PNG bytes.
function picture() {
  const sheet = document.createElement("canvas");
  sheet.width = BOARD.width;
  sheet.height = BOARD.height;
  const context = sheet.getContext("2d");
  context.fillStyle = theme.paper;
  context.fillRect(0, 0, BOARD.width, BOARD.height);
  context.drawImage(kept, 0, 0);
  return new Promise((resolve, reject) => {
    sheet.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("The picture could not be made."))), "image/png");
  });
}

$("save-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  closeTextEntry(true);
  if (strokes.isEmpty()) {
    kit.shake(paper);
    kit.toast("The board is empty: draw something first.", { kind: "info" });
    return;
  }
  try {
    const { saved, blob } = await kit.busy($("save"), async () => {
      const made = await picture();
      // The server checks the size again; this only saves the visitor a wasted upload.
      if (made.size > session.limits.bytes) {
        throw new Error("This drawing is " + kilobytes(made.size) + ", more than the gallery takes. Download it instead.");
      }
      return { saved: await api.saveDrawing($("title").value.trim(), made), blob: made };
    });
    unsaved = false;
    $("title").value = "";
    await loadGallery();
    flyIntoGallery(blob, saved.id);
    kit.toast('"' + saved.title + '" is in the gallery (' + kilobytes(saved.bytes) + ").", {
      kind: "success",
      action: { label: "Show", onClick: () => showCard(saved.id) },
    });
  } catch (error) {
    kit.toast(error.message, { kind: "error" });
  }
});

$("download").addEventListener("click", async (event) => {
  closeTextEntry(true);
  try {
    const url = URL.createObjectURL(await kit.busy(event.currentTarget, picture, { tick: false }));
    const link = h("a", { href: url, download: "whiteboard.png" });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    kit.toast("Saved as whiteboard.png.", { kind: "success" });
  } catch (error) {
    kit.toast(error.message, { kind: "error" });
  }
});

window.addEventListener("beforeunload", (event) => {
  if (unsaved && !strokes.isEmpty()) {
    event.preventDefault();
    event.returnValue = ""; // older browsers ask only when this is set
  }
});

// --- The gallery -----------------------------------------------------------------------------------

const cards = new Map(); // drawing id -> its <li>, kept between two loads
let galleryDrawn = false;

function facts(drawing) {
  return [kit.timeAgo(drawing.created_at), kilobytes(drawing.bytes || 0)].join(" \u00b7 ");
}

function fillCard(item, drawing) {
  item.className = "wb-card efp-lift";
  item.dataset.id = drawing.id;
  if (item.dataset.drawn === drawing.id) {
    item.querySelector(".wb-facts").textContent = facts(drawing); // only the time moves on
    return;
  }
  item.dataset.drawn = drawing.id;
  const image = h("img", {
    src: api.imageUrl(drawing.author.id, drawing.id),
    alt: "",
    loading: "lazy", // a long gallery fetches a picture when it scrolls into view
    width: drawing.width || BOARD.width,
    height: drawing.height || BOARD.height,
  });
  image.addEventListener("load", () => image.classList.add("is-loaded"), { once: true });
  const open = h("button.wb-thumb", { type: "button", "aria-label": "Open " + drawing.title, onclick: () => openViewer(drawing) }, image);
  item.replaceChildren(
    open,
    h(
      "div.wb-card-body",
      {},
      h("p.wb-card-title", { text: drawing.title, title: drawing.title }),
      h(
        "p.flex.items-center.gap-1.5.text-xs.text-base-content/60.min-w-0",
        {},
        kit.avatar(drawing.author.name, { size: "1.15rem" }),
        h("span.truncate", { text: drawing.mine ? "You" : drawing.author.name })
      ),
      h("p.wb-facts.text-xs.text-base-content/50.efp-number", { text: facts(drawing) })
    )
  );
}

function galleryProblem(text) {
  const status = $("gallery-status");
  status.hidden = !text;
  status.replaceChildren(text ? h("div.alert.alert-soft.alert-error.mb-3", {}, kit.icon("circle-alert", "size-5"), h("span", { text })) : "");
}

// The gallery, kept by drawing id: a new picture is the one card that moves.
async function loadGallery() {
  let found;
  try {
    found = await api.loadGallery();
  } catch (error) {
    galleryProblem(error.message);
    if (!galleryDrawn) {
      gallery.replaceChildren();
    }
    return;
  }
  galleryProblem("");
  session = { signedIn: found.signed_in, limits: found.limits };
  const mine = found.drawings.filter((drawing) => drawing.mine).length;
  $("who").textContent = found.signed_in
    ? "Signed in as " + found.me.name + ". Draw with mouse, touch or pen; what you save, everyone who can open the app sees."
    : "You are not signed in: you can draw and download, and look at the gallery, but not save to it.";
  if (found.signed_in && !$("me").firstChild) {
    $("me").append(kit.avatar(found.me.name, { size: "2rem" }));
  }
  $("save").disabled = !found.signed_in;
  $("save").title = found.signed_in ? "" : "Sign in to save to the gallery";
  $("count").hidden = !found.signed_in;
  $("count").textContent = mine + " of " + found.limits.per_member + " yours";
  if (!galleryDrawn) {
    gallery.replaceChildren(); // the skeletons
  }
  const seen = new Set();
  found.drawings.forEach((drawing, index) => {
    seen.add(drawing.id);
    let item = cards.get(drawing.id);
    if (!item) {
      item = h("li");
      cards.set(drawing.id, item);
    }
    fillCard(item, drawing);
    if (gallery.children[index] !== item) {
      gallery.insertBefore(item, gallery.children[index] || null);
    }
  });
  for (const [id, item] of cards) {
    if (!seen.has(id)) {
      cards.delete(id);
      item.remove();
    }
  }
  gallery.hidden = found.drawings.length === 0;
  $("gallery-empty").hidden = found.drawings.length > 0;
  if (!galleryDrawn) {
    galleryDrawn = true;
    kit.rise(gallery.children, { stagger: 55 }).then(() => kit.animateList(gallery));
  }
}

$("refresh").addEventListener("click", (event) => kit.busy(event.currentTarget, loadGallery, { tick: false }));

// The empty gallery points at the board.
$("start").addEventListener("click", () => {
  chooseTool("pen");
  paper.scrollIntoView({ behavior: kit.reducedMotion() ? "auto" : "smooth", block: "center" });
  kit.flash(paper);
});

// After a save the picture flies from the board to its card, which takes it
// over when it lands. Below the screen (a phone) it simply flies off that way.
async function flyIntoGallery(blob, id) {
  const item = cards.get(id);
  if (!item) {
    return;
  }
  const target = item.querySelector(".wb-thumb");
  if (kit.reducedMotion() || !target) {
    return;
  }
  // The card's own picture is fetched while the copy is in the air, so that it is there when it lands.
  const own = target.querySelector("img");
  own.loading = "eager";
  const arrived = own.decode().catch(() => {});
  const from = board.getBoundingClientRect();
  const to = target.getBoundingClientRect();
  const url = URL.createObjectURL(blob);
  const ghost = h("img.wb-ghost", {
    src: url,
    alt: "",
    style: { left: from.left + "px", top: from.top + "px", width: from.width + "px", height: from.height + "px" },
  });
  document.body.append(ghost);
  item.style.visibility = "hidden";
  const dx = to.left - from.left;
  const dy = to.top - from.top;
  const scale = to.width / from.width;
  await ghost
    .animate(
      [
        { transform: "none", opacity: 1 },
        { transform: "translate(" + dx * 0.35 + "px, " + (dy * 0.35 - 40) + "px) scale(" + (1 + scale) / 2.2 + ") rotate(-2deg)", opacity: 1, offset: 0.45 },
        { transform: "translate(" + dx + "px, " + dy + "px) scale(" + scale + ")", opacity: 0.9 },
      ],
      { duration: 880, easing: kit.EASE }
    )
    .finished.catch(() => {});
  await Promise.race([arrived, new Promise((resolve) => setTimeout(resolve, 1200))]);
  item.style.visibility = "";
  ghost.remove();
  URL.revokeObjectURL(url);
  kit.pop(item, { scale: 1.06 });
  kit.flash(item);
}

function showCard(id) {
  const item = cards.get(id);
  if (item) {
    item.scrollIntoView({ behavior: kit.reducedMotion() ? "auto" : "smooth", block: "center" });
    kit.flash(item);
  }
}

function openViewer(drawing) {
  opened = drawing;
  $("viewer-title").textContent = drawing.title;
  $("viewer-image").src = api.imageUrl(drawing.author.id, drawing.id);
  $("viewer-image").alt = drawing.title;
  $("viewer-facts").replaceChildren(
    kit.avatar(drawing.author.name, { size: "1.35rem" }),
    h("span", { text: "By " + drawing.author.name }),
    h("span", { text: "\u00b7 " + facts(drawing) + " \u00b7 " + drawing.width + " by " + drawing.height + " pixels" })
  );
  $("viewer-delete").hidden = !drawing.can_delete;
  viewer.showModal();
}

$("viewer-close").addEventListener("click", () => viewer.close());

// Put a saved drawing on the board to go on from it. Saving then makes a new
// drawing; the one in the gallery stays as it is.
$("viewer-continue").addEventListener("click", (event) => {
  const drawing = opened;
  const image = new Image();
  kit.busy(event.currentTarget, () => new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = () => reject(new Error("That drawing could not be loaded."));
    image.src = api.imageUrl(drawing.author.id, drawing.id);
  }), { tick: false }).then(
    () => {
      // Its paper decides which ink stands out on it (see refreshInks).
      const probe = h("canvas", { width: 1, height: 1 }).getContext("2d", { willReadFrequently: true });
      probe.drawImage(image, 2, 2, 1, 1, 0, 0, 1, 1);
      const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
      if (!strokes.isEmpty()) {
        keep({ tool: "clear" });
      }
      keep({ tool: "picture", image, paper: "rgb(" + r + ", " + g + ", " + b + ")" });
      viewer.close();
      paper.scrollIntoView({ behavior: kit.reducedMotion() ? "auto" : "smooth", block: "center" });
      kit.flash(paper);
      kit.toast('"' + drawing.title + '" is on the board. Saving it makes a new drawing.', { kind: "info" });
    },
    (error) => kit.toast(error.message, { kind: "error" })
  );
});

$("confirm-cancel").addEventListener("click", () => $("confirm").close("cancel"));

$("viewer-delete").addEventListener("click", () => {
  const drawing = opened;
  const confirm = $("confirm");
  viewer.close();
  $("confirm-title").textContent = "Delete this drawing?";
  $("confirm-text").textContent = '"' + drawing.title + '" will be gone for good, for everyone.';
  confirm.returnValue = "";
  confirm.addEventListener(
    "close",
    async () => {
      if (confirm.returnValue !== "ok") {
        return;
      }
      const item = cards.get(drawing.id);
      if (item) {
        item.classList.add("is-leaving");
      }
      try {
        await api.deleteDrawing(drawing.author.id, drawing.id);
        kit.toast('"' + drawing.title + '" is deleted.', { kind: "success" });
      } catch (error) {
        kit.toast(error.message, { kind: "error" });
      }
      if (item) {
        item.classList.remove("is-leaving");
      }
      await loadGallery();
    },
    { once: true }
  );
  confirm.showModal();
});

await kit.ready();
kit.themeSwitch($("theme"));
readTheme();
refreshInks();
// A new theme: the paper, the inks that follow it, and so the whole board.
kit.onThemeChange(() => {
  readTheme();
  repaint();
});
chooseTool("pen");
requestAnimationFrame(() => $("tool-pill").classList.add("is-ready")); // from now on the pill slides
new ResizeObserver(placePill).observe($("tools"));
updateButtons();
loadGallery();
