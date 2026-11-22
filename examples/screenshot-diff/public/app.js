// A static app: the comparison runs in this tab. The images are read with
// the File API and a canvas and are never sent anywhere; there is no
// backend to send them to.
//
// Every URL is relative ("./lib/diff.js", "style.css"): the app is served
// under /{slug}/ once deployed. The one exception is the EFP kit, at
// /_efp/lib/, which every gateway serves to every app (docs/UI_KIT.md).
//
// The comparison itself is lib/diff.js (typed arrays in, numbers out, no
// DOM, tested with node). This file reads the images, runs the comparison
// in slices so the tab stays responsive, and shows the result.
//
// Motion here answers a question each time. The boxes say where a dropped
// file goes; swapped images trade places; the share of changed pixels counts
// to its value; the outlines draw themselves around what changed; the swipe
// divider sweeps once across the pair so the eye catches what moves; the
// onion skin fades the actual screen in over the expected one.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import { createComparison, percentText, regions, sizeProblem, step, summarize } from "./lib/diff.js";
import { clamp, fitZoom, stepZoom, zoomText } from "./lib/view.js";
import { drawSample } from "./sample.js";

const { h } = kit;
const SVG = "http://www.w3.org/2000/svg"; // a name, not an address: nothing is fetched from it
const SLICE_MS = 12; // work this long, then let the browser draw a frame
const MAX_LISTED = 40;
const MAX_OUTLINED = 300;
const SLOTS = ["expected", "actual"];
const HINTS = { expected: "The baseline: how the screen should look.", actual: "What the test run produced." };
const DOT = " \u00b7 ";

const byId = (id) => document.getElementById(id);
const status = byId("status");
const progress = byId("progress");
const workbench = byId("workbench");
const stage = byId("stage");
const tolerance = byId("tolerance");
const antialiasing = byId("antialiasing");
const outline = byId("outline");
const divider = byId("divider");
const opacity = byId("opacity");
const views = Array.from(document.querySelectorAll('input[name="view"]'));

const images = { expected: null, actual: null }; // each {name, width, height, data, canvas, id}
let result = null; // {summary, regions, canvas} of the last finished comparison
let run = 0; // a comparison that is overtaken by a newer one stops
let zoom = 1;
let fitted = true; // the zoom follows the size of the window until the visitor picks one
let current = -1; // the region picked from the list
let shownMode = ""; // the view on the screen, to animate only a view that is new
let made = 0; // numbers the images, so a thumbnail keeps its name when it changes box

const count = (number) => number.toLocaleString("en");
const size = (image) => image.width + " x " + image.height;
const plural = (number, word) => count(number) + " " + word + (number === 1 ? "" : "s");
const nextFrame = () => new Promise((resolve) => requestAnimationFrame(resolve));
const mode = () => views.find((view) => view.checked).value;
const other = (slot) => (slot === "expected" ? "actual" : "expected");

// A line under the boxes: a plain note, or a problem in an alert where it happened.
function say(text, problem = false) {
  status.replaceChildren(
    problem
      ? h("div", { class: "alert alert-soft alert-error text-sm py-2" }, kit.icon("circle-alert", "size-4"), h("span", { text }))
      : h("span", { text }),
  );
}

// --- Reading the images -------------------------------------------------------------------------

function fromCanvas(canvas, name) {
  const context = canvas.getContext("2d", { willReadFrequently: true });
  made += 1;
  return { name, width: canvas.width, height: canvas.height, data: context.getImageData(0, 0, canvas.width, canvas.height).data, canvas, id: made };
}

// A file as pixels, or a sentence that says why not.
async function read(file) {
  if (!file.type.startsWith("image/")) {
    throw new Error((file.name || "This file") + " is not an image.");
  }
  let bitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch (error) {
    throw new Error((file.name || "This file") + " could not be read as an image. PNG, JPEG, WebP, GIF and BMP work.");
  }
  const problem = sizeProblem(bitmap.width, bitmap.height);
  if (problem) {
    bitmap.close();
    throw new Error(problem);
  }
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  canvas.getContext("2d", { willReadFrequently: true }).drawImage(bitmap, 0, 0);
  bitmap.close();
  return fromCanvas(canvas, file.name || "pasted image");
}

// A small copy for the box. Its view-transition-name follows the image, so a swap is seen as the two trading places.
function thumbnail(image) {
  const scale = Math.min(1, 560 / image.width, 360 / image.height);
  const small = h("canvas", { width: Math.max(1, Math.round(image.width * scale)), height: Math.max(1, Math.round(image.height * scale)), "aria-hidden": "true" });
  small.getContext("2d").drawImage(image.canvas, 0, 0, small.width, small.height);
  small.style.viewTransitionName = "sd-image-" + image.id;
  return small;
}

function setImage(slot, image, { animate = true } = {}) {
  images[slot] = image;
  const zone = byId("drop-" + slot);
  zone.toggleAttribute("data-filled", Boolean(image));
  byId("info-" + slot).textContent = image ? image.name + DOT + size(image) + " pixels" : HINTS[slot];
  byId("thumb-" + slot).replaceChildren(image ? thumbnail(image) : "");
  if (image && animate) kit.appear(byId("thumb-" + slot).firstChild, { duration: 520 });
  if (!image) byId("file-" + slot).value = "";
  byId("clear").disabled = !images.expected && !images.actual;
  byId("swap").disabled = !images.expected && !images.actual;
}

async function take(slot, file) {
  if (!file) {
    return;
  }
  const zone = byId("drop-" + slot);
  zone.classList.add("is-reading");
  say("Reading " + (file.name || "the image") + "...");
  try {
    setImage(slot, await read(file));
  } catch (error) {
    byId("file-" + slot).value = "";
    say(error.message, true);
    kit.shake(zone);
    return;
  } finally {
    zone.classList.remove("is-reading");
  }
  compare();
}

// --- Comparing ----------------------------------------------------------------------------------

async function compare() {
  const mine = (run += 1);
  const { expected, actual } = images;
  if (!expected || !actual) {
    result = null;
    shownMode = "";
    workbench.hidden = true;
    progress.hidden = true;
    say(expected || actual ? "Now the other image: drop it on its box, choose it, or paste it." : "");
    return;
  }
  const state = createComparison(expected, actual, { tolerance: Number(tolerance.value), ignoreAntialiasing: antialiasing.checked });
  progress.max = state.height;
  progress.value = 0;
  progress.hidden = false;
  workbench.classList.add("is-stale"); // the last result stays, dimmed, until the new one is there
  say("Comparing " + plural(state.width * state.height, "pixel") + "...");
  for (;;) {
    // A slice of rows, then a frame for the browser: a 20-megapixel pair takes seconds, and the tab must not freeze.
    const started = performance.now();
    let done = false;
    while (!done && performance.now() - started < SLICE_MS) {
      done = step(state, 16);
    }
    progress.value = state.row;
    if (done) {
      break;
    }
    await nextFrame();
    if (mine !== run) {
      return; // the images or the settings changed meanwhile
    }
  }
  progress.hidden = true;
  workbench.classList.remove("is-stale");
  const canvas = document.createElement("canvas");
  canvas.width = state.width;
  canvas.height = state.height;
  canvas.getContext("2d").putImageData(new ImageData(state.picture, state.width, state.height), 0, 0);
  result = { summary: summarize(state), regions: regions(state), canvas };
  current = -1;
  const first = workbench.hidden;
  workbench.hidden = false;
  report();
  render();
  if (first) {
    // The result arrives: it rises into place and the page brings it into view.
    kit.rise(workbench.children, { stagger: 90 });
    workbench.scrollIntoView({ behavior: kit.reducedMotion() ? "auto" : "smooth", block: "start" });
  }
}

let pending = 0;
function compareSoon() {
  clearTimeout(pending);
  pending = setTimeout(compare, 120); // while a slider is dragged, compare when it rests
}

// --- The verdict and the list of regions --------------------------------------------------------

function verdictOf(summary) {
  if (summary.changed === 0 && summary.sameSize) return { tone: "success", icon: "circle-check", label: "Identical", headline: "No pixel differs beyond the tolerance." };
  if (summary.changed === 0) return { tone: "warning", icon: "ruler", label: "Sizes differ", headline: "No difference where both images overlap, but they are not the same size." };
  return {
    tone: "error",
    icon: "circle-x",
    label: "Different",
    headline: plural(summary.changed, "pixel") + " differ" + (summary.changed === 1 ? "s" : "") + ", in " + plural(result.regions.length, "region") + ".",
  };
}

function report() {
  const { summary } = result;
  const { expected, actual } = images;
  const said = verdictOf(summary);
  const badge = byId("verdict");
  const changedKind = badge.dataset.tone !== said.tone;
  badge.dataset.tone = said.tone;
  badge.className = "badge badge-lg gap-1.5 badge-" + said.tone;
  badge.replaceChildren(kit.icon(said.icon, "size-4"), said.label);
  if (changedKind) kit.pop(badge, { scale: 1.14 });
  byId("headline").textContent = said.headline;
  workbench.style.setProperty("--tone", "var(--color-" + said.tone + ")");
  // The share counts from the last one to the new one: moving the tolerance is seen as a change of the number.
  kit.countUp(byId("score"), summary.percent, { duration: 900, format: percentText });

  const rows = [
    ["Changed", count(summary.changed) + " px"],
    ["Compared", count(summary.compared) + " px"],
    ["Expected", size(expected)],
    ["Actual", size(actual)],
  ];
  if (antialiasing.checked) rows.splice(1, 0, ["Anti-aliasing", count(summary.antialiased) + " px ignored"]);
  if (!summary.sameSize) rows.push(["One image only", count(summary.onlyOne) + " px, striped"]);
  byId("stats").replaceChildren(...rows.map(([name, value]) => h("div", {}, h("dt", { text: name }), h("dd", { class: "efp-number", text: value }))));

  const list = byId("regions");
  list.replaceChildren(
    ...result.regions.slice(0, MAX_LISTED).map((region, position) =>
      h(
        "li",
        {},
        h(
          "button",
          {
            type: "button",
            class: "sd-region",
            dataset: { region: position },
            onclick: () => pick(position),
            onpointerenter: () => mark(position, true),
            onpointerleave: () => mark(position, false),
          },
          crop(region),
          h(
            "span",
            { class: "min-w-0" },
            h("span", { class: "block text-sm font-medium efp-number", text: region.width + " x " + region.height }),
            h("span", { class: "block text-xs text-base-content/60 efp-number", text: "at " + region.x + ", " + region.y + DOT + plural(region.pixels, "pixel") }),
          ),
        ),
      ),
    ),
  );
  const more = result.regions.length - MAX_LISTED;
  byId("regions-note").textContent = result.regions.length
    ? "Largest first. Changes within a dozen pixels of each other form one region." + (more > 0 ? " " + count(more) + " smaller ones are not listed." : "")
    : "None: there is nothing to outline.";

  const mismatch = summary.sameSize ? "" : " Expected " + size(expected) + ", actual " + size(actual) + ": the part both cover was compared and the rest is striped.";
  byId("headline").textContent = said.headline + mismatch;
  say(""); // the verdict says it now
}

// The changed spot of the actual image, small: a region is recognised before it is read.
function crop(region) {
  const pad = 6;
  const source = images.actual;
  const x = clamp(region.x - pad, 0, source.width - 1);
  const y = clamp(region.y - pad, 0, source.height - 1);
  const width = Math.max(1, Math.min(source.width - x, region.width + pad * 2));
  const height = Math.max(1, Math.min(source.height - y, region.height + pad * 2));
  const scale = Math.min(56 / width, 40 / height, 4);
  const small = h("canvas", { class: "sd-crop", width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)), "aria-hidden": "true" });
  const context = small.getContext("2d");
  context.imageSmoothingEnabled = scale < 1;
  context.drawImage(source.canvas, x, y, width, height, 0, 0, small.width, small.height);
  return h("span", { class: "sd-crop-frame" }, small);
}

function mark(position, on) {
  for (const box of stage.querySelectorAll('rect[data-region="' + position + '"]')) {
    box.classList.toggle("is-hover", on);
  }
}

function pick(position) {
  current = position;
  if (!outline.checked) {
    outline.checked = true;
  }
  render();
  const box = stage.querySelector("rect.current");
  if (box) {
    box.scrollIntoView({ block: "center", inline: "center", behavior: kit.reducedMotion() ? "auto" : "smooth" });
  }
  for (const button of byId("regions").querySelectorAll("button")) {
    button.setAttribute("aria-current", String(Number(button.dataset.region) === position));
  }
}

// --- Showing the images -------------------------------------------------------------------------

// Boxes around the changed regions, laid over an image. They are SVG, so they
// stay sharp at any zoom, and each one draws itself, the larger ones first.
function boxes() {
  const { summary } = result;
  const layer = document.createElementNS(SVG, "svg");
  layer.setAttribute("class", "boxes");
  layer.setAttribute("viewBox", "0 0 " + summary.width + " " + summary.height);
  layer.setAttribute("preserveAspectRatio", "none");
  layer.setAttribute("aria-hidden", "true");
  result.regions.slice(0, MAX_OUTLINED).forEach((region, position) => {
    const box = document.createElementNS(SVG, "rect");
    box.setAttribute("x", region.x - 1);
    box.setAttribute("y", region.y - 1);
    box.setAttribute("width", region.width + 2);
    box.setAttribute("height", region.height + 2);
    box.setAttribute("pathLength", "100");
    box.setAttribute("data-region", position);
    box.style.setProperty("--i", Math.min(position, 24));
    if (position === current) {
      box.setAttribute("class", "current");
    }
    layer.append(box);
  });
  return layer;
}

function frame(title, layers, tone) {
  const { summary } = result;
  const inner = h("div", { class: "sd-frame" }, ...layers);
  inner.style.width = Math.round(summary.width * zoom) + "px";
  inner.style.height = Math.round(summary.height * zoom) + "px";
  if (outline.checked) {
    inner.append(boxes());
  }
  return h("figure", { class: "sd-pane" }, h("figcaption", {}, h("span", { class: "sd-tag " + tone, text: title })), inner);
}

function placeDivider() {
  const line = stage.querySelector(".sd-divider");
  if (line && result) {
    const at = Math.round((result.summary.width * zoom * Number(divider.value)) / 100);
    line.style.left = at + "px";
    images.actual.canvas.style.clipPath = "inset(0 0 0 " + at + "px)"; // the actual image shows right of the line
  }
}

// The divider runs across once when the swipe view opens: the eye catches what jumps.
function sweep() {
  const stops = [50, 88, 12, 50];
  const began = performance.now();
  const length = 1500;
  const tick = (now) => {
    if (mode() !== "swipe" || stage.dataset.held) return; // the visitor took the divider
    const t = Math.min(1, (now - began) / length);
    const part = t * (stops.length - 1);
    const index = Math.min(stops.length - 2, Math.floor(part));
    const local = part - index;
    const eased = local < 0.5 ? 2 * local * local : 1 - Math.pow(-2 * local + 2, 2) / 2;
    divider.value = String(Math.round(stops[index] + (stops[index + 1] - stops[index]) * eased));
    placeDivider();
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function render() {
  if (!result) {
    return;
  }
  const { expected, actual } = images;
  const view = mode();
  const entering = view !== shownMode;
  shownMode = view;
  byId("divider-field").hidden = view !== "swipe";
  byId("opacity-field").hidden = view !== "overlay";
  if (fitted) {
    const columns = view === "side" ? 2 : 1;
    zoom = fitZoom(result.summary.width, result.summary.height, (stage.clientWidth - 32) / columns - 12, Math.max(320, window.innerHeight * 0.68));
  }
  for (const [canvas, image] of [[expected.canvas, expected], [actual.canvas, actual], [result.canvas, result.summary]]) {
    canvas.style.width = Math.round(image.width * zoom) + "px";
    canvas.style.height = Math.round(image.height * zoom) + "px";
    canvas.style.clipPath = "";
    canvas.style.opacity = "";
    canvas.setAttribute("class", zoom >= 2 ? "crisp" : ""); // show single pixels as squares when zoomed in
  }
  let panes;
  if (view === "side") {
    panes = [frame("Expected", [expected.canvas], "is-expected"), frame("Actual", [actual.canvas], "is-actual")];
  } else if (view === "swipe") {
    const line = h("div", { class: "sd-divider", "aria-hidden": "true" }, h("span", { class: "sd-knob" }, kit.icon("chevrons-left-right", "size-4")));
    line.addEventListener("pointerdown", (event) => {
      line.setPointerCapture(event.pointerId);
      stage.dataset.held = "yes";
      event.preventDefault();
    });
    line.addEventListener("pointermove", (event) => {
      if (line.hasPointerCapture(event.pointerId)) {
        const area = line.parentNode.getBoundingClientRect();
        divider.value = String(Math.round(clamp(((event.clientX - area.left) / area.width) * 100, 0, 100)));
        placeDivider();
      }
    });
    panes = [frame("Expected | Actual", [expected.canvas, actual.canvas, line], "is-both")];
  } else if (view === "overlay") {
    actual.canvas.style.opacity = String(Number(opacity.value) / 100);
    panes = [frame("Actual over expected", [expected.canvas, actual.canvas], "is-both")];
  } else {
    panes = [frame("Differences", [result.canvas], "is-diff")];
  }
  stage.dataset.mode = view;
  stage.replaceChildren(...panes);
  placeDivider();
  byId("zoom-value").textContent = zoomText(zoom);
  if (!entering || kit.reducedMotion()) return;
  delete stage.dataset.held;
  if (view === "swipe") sweep();
  if (view === "overlay") {
    actual.canvas.animate([{ opacity: 0 }, { opacity: Number(opacity.value) / 100 }], { duration: 700, easing: kit.EASE });
  }
}

function download() {
  return new Promise((resolve, reject) => {
    const { summary } = result;
    const canvas = document.createElement("canvas");
    canvas.width = summary.width;
    canvas.height = summary.height;
    const context = canvas.getContext("2d");
    context.drawImage(result.canvas, 0, 0);
    if (outline.checked) {
      context.strokeStyle = kit.color("primary"); // the theme's colour, as on the page
      context.lineWidth = Math.max(1, Math.round(Math.max(summary.width, summary.height) / 600));
      for (const region of result.regions.slice(0, MAX_OUTLINED)) {
        context.strokeRect(region.x - 1.5, region.y - 1.5, region.width + 3, region.height + 3);
      }
    }
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error("The browser could not write the PNG."));
        return;
      }
      // The file is made in the tab and handed to the browser's own download: still no upload.
      const address = URL.createObjectURL(blob);
      const link = h("a", { download: "screenshot-diff.png", href: address });
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(address), 1000);
      resolve();
    }, "image/png");
  });
}

// --- Wiring -------------------------------------------------------------------------------------

const hasFiles = (event) => Boolean(event.dataTransfer) && Array.from(event.dataTransfer.types || []).includes("Files");
let dragDepth = 0;

function disarm() {
  dragDepth = 0;
  document.body.classList.remove("is-dragging");
  for (const slot of SLOTS) byId("drop-" + slot).classList.remove("over");
}

async function takeAll(files, first) {
  await take(first, files[0]);
  if (files[1]) {
    await take(other(first), files[1]); // two files at once fill both boxes
  }
}

for (const slot of SLOTS) {
  const input = byId("file-" + slot);
  const drop = byId("drop-" + slot);
  input.addEventListener("change", () => take(slot, input.files[0]));
  drop.addEventListener("dragover", (event) => {
    event.preventDefault(); // without this the browser would open the file instead
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", (event) => {
    if (!drop.contains(event.relatedTarget)) drop.classList.remove("over");
  });
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation();
    disarm();
    takeAll(Array.from(event.dataTransfer.files), slot);
  });
}

// Files dragged anywhere over the page: both boxes light up to say where they go.
document.addEventListener("dragenter", (event) => {
  if (hasFiles(event)) {
    dragDepth += 1;
    document.body.classList.add("is-dragging");
  }
});
document.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) disarm();
});
document.addEventListener("dragover", (event) => {
  if (hasFiles(event)) event.preventDefault(); // a file dropped beside the boxes is not opened by the browser
});
document.addEventListener("drop", (event) => {
  if (!hasFiles(event)) return;
  event.preventDefault();
  disarm();
  takeAll(Array.from(event.dataTransfer.files), images.expected && !images.actual ? "actual" : "expected");
});

document.addEventListener("paste", (event) => {
  const file = Array.from(event.clipboardData ? event.clipboardData.files : []).find((candidate) => candidate.type.startsWith("image/"));
  if (file) {
    event.preventDefault();
    take(images.expected ? "actual" : "expected", file); // the first paste is the baseline, every later one the new run
  }
});

byId("sample").addEventListener("click", () => {
  for (const slot of SLOTS) {
    byId("file-" + slot).value = "";
    setImage(slot, fromCanvas(drawSample(slot), "sample-" + slot + ".png"));
  }
  compare();
});
byId("swap").addEventListener("click", (event) => {
  const { expected, actual } = images;
  if (!expected && !actual) return;
  event.currentTarget.classList.toggle("is-turned");
  for (const slot of SLOTS) {
    byId("file-" + slot).value = "";
  }
  kit.transition(() => {
    setImage("expected", actual, { animate: false });
    setImage("actual", expected, { animate: false });
  });
  compare();
});
byId("clear").addEventListener("click", () => {
  SLOTS.forEach((slot) => setImage(slot, null));
  stage.replaceChildren();
  compare();
});

tolerance.addEventListener("input", () => {
  byId("tolerance-value").textContent = tolerance.value;
  compareSoon();
});
antialiasing.addEventListener("change", compare);
outline.addEventListener("change", render);
views.forEach((view) => view.addEventListener("change", () => kit.transition(render)));
divider.addEventListener("input", () => {
  stage.dataset.held = "yes";
  placeDivider();
});
opacity.addEventListener("input", () => {
  if (images.actual && mode() === "overlay") {
    images.actual.canvas.style.opacity = String(Number(opacity.value) / 100);
  }
});

function setZoom(value) {
  fitted = value === null;
  zoom = value === null ? zoom : value;
  render();
}
byId("zoom-in").addEventListener("click", () => setZoom(stepZoom(zoom, 1)));
byId("zoom-out").addEventListener("click", () => setZoom(stepZoom(zoom, -1)));
byId("zoom-fit").addEventListener("click", () => setZoom(null));
byId("zoom-full").addEventListener("click", () => setZoom(1));
byId("download").addEventListener("click", async (event) => {
  if (!result) return;
  try {
    await kit.busy(event.currentTarget, download);
  } catch (error) {
    kit.toast(error.message, { kind: "error" });
  }
});

// 1 to 4 pick the view, + and - zoom, 0 fits: when a result is there and no field has the keys.
document.addEventListener("keydown", (event) => {
  const typing = event.target.closest && event.target.closest("input[type=text], textarea, select");
  if (!result || typing || event.ctrlKey || event.metaKey || event.altKey) return;
  const index = ["1", "2", "3", "4"].indexOf(event.key);
  if (index >= 0) {
    views[index].checked = true;
    kit.transition(render);
  } else if (event.key === "+" || event.key === "=") {
    setZoom(stepZoom(zoom, 1));
  } else if (event.key === "-") {
    setZoom(stepZoom(zoom, -1));
  } else if (event.key === "0") {
    setZoom(null);
  }
});

let resizing = false;
window.addEventListener("resize", () => {
  if (fitted && result && !resizing) {
    resizing = true;
    requestAnimationFrame(() => {
      resizing = false;
      render();
    });
  }
});

await kit.ready();
kit.themeSwitch(byId("theme"));
