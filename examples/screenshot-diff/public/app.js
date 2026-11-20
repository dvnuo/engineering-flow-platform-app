// A static app: the comparison runs in this tab. The images are read with
// the File API and a canvas and are never sent anywhere; there is no
// backend to send them to.
//
// Every URL is relative ("./lib/diff.js", "style.css"): the app is served
// under /{slug}/ once deployed.
//
// The comparison itself is lib/diff.js (typed arrays in, numbers out, no
// DOM, tested with node). This file reads the images, runs the comparison
// in slices so the tab stays responsive, and shows the result.

import { createComparison, percentText, regions, sizeProblem, step, summarize } from "./lib/diff.js";
import { clamp, fitZoom, stepZoom, zoomText } from "./lib/view.js";
import { drawSample } from "./sample.js";

const SVG = "http://www.w3.org/2000/svg"; // a name, not an address: nothing is fetched from it
const SLICE_MS = 12; // work this long, then let the browser draw a frame
const MAX_LISTED = 40;
const MAX_OUTLINED = 300;
const SLOTS = ["expected", "actual"];

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

const images = { expected: null, actual: null }; // each {name, width, height, data, canvas}
let result = null; // {summary, regions, canvas} of the last finished comparison
let run = 0; // a comparison that is overtaken by a newer one stops
let zoom = 1;
let fitted = true; // the zoom follows the size of the window until the visitor picks one
let current = -1; // the region picked from the list

function el(tag, properties = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(properties)) {
    if (name === "text") {
      node.textContent = value;
    } else {
      node.setAttribute(name, value);
    }
  }
  node.append(...children);
  return node;
}

function say(text, problem = false) {
  status.textContent = text;
  status.classList.toggle("problem", problem);
}

const count = (number) => number.toLocaleString("en");
const size = (image) => image.width + " x " + image.height;
const nextFrame = () => new Promise((resolve) => requestAnimationFrame(resolve));

// --- Reading the images -------------------------------------------------------------------------

function fromCanvas(canvas, name) {
  const context = canvas.getContext("2d", { willReadFrequently: true });
  return { name, width: canvas.width, height: canvas.height, data: context.getImageData(0, 0, canvas.width, canvas.height).data, canvas };
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

function setImage(slot, image) {
  images[slot] = image;
  byId("info-" + slot).textContent = image ? image.name + ": " + size(image) + " pixels" : slot === "expected" ? "The baseline: how the screen should look." : "What the test run produced.";
  if (!image) {
    byId("file-" + slot).value = "";
  }
}

async function take(slot, file) {
  if (!file) {
    return;
  }
  say("Reading " + (file.name || "the image") + "...");
  try {
    setImage(slot, await read(file));
  } catch (error) {
    byId("file-" + slot).value = "";
    say(error.message, true);
    return;
  }
  compare();
}

// --- Comparing ----------------------------------------------------------------------------------

async function compare() {
  const mine = (run += 1);
  const { expected, actual } = images;
  if (!expected || !actual) {
    result = null;
    workbench.hidden = true;
    progress.hidden = true;
    say(expected || actual ? "Now choose the other image." : "Choose two images to compare.");
    return;
  }
  const state = createComparison(expected, actual, { tolerance: Number(tolerance.value), ignoreAntialiasing: antialiasing.checked });
  progress.max = state.height;
  progress.value = 0;
  progress.hidden = false;
  say("Comparing...");
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
  const canvas = document.createElement("canvas");
  canvas.width = state.width;
  canvas.height = state.height;
  canvas.getContext("2d").putImageData(new ImageData(state.picture, state.width, state.height), 0, 0);
  result = { summary: summarize(state), regions: regions(state), canvas };
  current = -1;
  workbench.hidden = false;
  report();
  render();
}

let pending = 0;
function compareSoon() {
  clearTimeout(pending);
  pending = setTimeout(compare, 120); // while a slider is dragged, compare when it rests
}

// --- Showing the result -------------------------------------------------------------------------

function report() {
  const { summary } = result;
  const { expected, actual } = images;
  const rows = [
    ["Changed pixels", count(summary.changed) + " (" + percentText(summary.percent) + " of the compared area)"],
    ["Expected", size(expected) + " pixels"],
    ["Actual", size(actual) + " pixels"],
    ["Compared area", count(summary.compared) + " pixels"],
  ];
  if (antialiasing.checked) {
    rows.splice(1, 0, ["Ignored as anti-aliasing", count(summary.antialiased) + " pixels"]);
  }
  if (!summary.sameSize) {
    rows.push(["In one image only", count(summary.onlyOne) + " pixels (striped in the diff)"]);
  }
  const stats = byId("stats");
  stats.replaceChildren();
  for (const [name, value] of rows) {
    stats.append(el("dt", { text: name }), el("dd", { text: value }));
  }

  const list = byId("regions");
  list.replaceChildren();
  result.regions.slice(0, MAX_LISTED).forEach((region, position) => {
    const button = el("button", {
      type: "button",
      class: "link",
      text: region.width + " x " + region.height + " at " + region.x + ", " + region.y + " (" + count(region.pixels) + " px)",
    });
    button.addEventListener("click", () => {
      current = position;
      if (!outline.checked) {
        outline.checked = true;
      }
      render();
      const box = stage.querySelector("rect.current");
      if (box) {
        box.scrollIntoView({ block: "center", inline: "center" });
      }
    });
    list.append(el("li", {}, button));
  });
  const more = result.regions.length - MAX_LISTED;
  byId("regions-note").textContent = result.regions.length
    ? "Largest first. Changes within a dozen pixels of each other form one region." + (more > 0 ? " " + count(more) + " smaller ones are not listed." : "")
    : "None.";

  const mismatch = summary.sameSize
    ? ""
    : " The images differ in size (expected " + size(expected) + ", actual " + size(actual) + "): the part both cover was compared and the rest is striped.";
  if (summary.changed === 0) {
    say((summary.sameSize ? "No difference: every pixel is within the tolerance." : "No difference in the part both images cover.") + mismatch, !summary.sameSize);
  } else {
    say(
      count(summary.changed) + (summary.changed === 1 ? " pixel differs" : " pixels differ") + " (" + percentText(summary.percent) + "), in " +
        count(result.regions.length) + (result.regions.length === 1 ? " region." : " regions.") + mismatch,
    );
  }
}

// Boxes around the changed regions, laid over an image. They are SVG, so they stay sharp at any zoom.
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
    if (position === current) {
      box.setAttribute("class", "current");
    }
    layer.append(box);
  });
  return layer;
}

function frame(title, layers) {
  const { summary } = result;
  const inner = el("div", { class: "frame" }, ...layers);
  inner.style.width = Math.round(summary.width * zoom) + "px";
  inner.style.height = Math.round(summary.height * zoom) + "px";
  if (outline.checked) {
    inner.append(boxes());
  }
  return el("figure", { class: "pane" }, el("figcaption", { text: title }), inner);
}

function placeDivider() {
  const line = stage.querySelector(".divider");
  if (line && result) {
    const at = Math.round((result.summary.width * zoom * Number(divider.value)) / 100);
    line.style.left = at + "px";
    images.actual.canvas.style.clipPath = "inset(0 0 0 " + at + "px)"; // the actual image shows right of the line
  }
}

function render() {
  if (!result) {
    return;
  }
  const { expected, actual } = images;
  const mode = views.find((view) => view.checked).value;
  byId("divider-field").hidden = mode !== "swipe";
  byId("opacity-field").hidden = mode !== "overlay";
  if (fitted) {
    const columns = mode === "side" ? 2 : 1;
    zoom = fitZoom(result.summary.width, result.summary.height, (stage.clientWidth - 24) / columns - 8, window.innerHeight * 0.72);
  }
  for (const [canvas, image] of [[expected.canvas, expected], [actual.canvas, actual], [result.canvas, result.summary]]) {
    canvas.style.width = Math.round(image.width * zoom) + "px";
    canvas.style.height = Math.round(image.height * zoom) + "px";
    canvas.style.clipPath = "";
    canvas.style.opacity = "";
    canvas.setAttribute("class", zoom >= 2 ? "crisp" : ""); // show single pixels as squares when zoomed in
  }
  let panes;
  if (mode === "side") {
    panes = [frame("Expected", [expected.canvas]), frame("Actual", [actual.canvas])];
  } else if (mode === "swipe") {
    const line = el("div", { class: "divider", "aria-hidden": "true" });
    line.addEventListener("pointerdown", (event) => {
      line.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    line.addEventListener("pointermove", (event) => {
      if (line.hasPointerCapture(event.pointerId)) {
        const area = line.parentNode.getBoundingClientRect();
        divider.value = String(Math.round(clamp(((event.clientX - area.left) / area.width) * 100, 0, 100)));
        placeDivider();
      }
    });
    panes = [frame("Expected | Actual", [expected.canvas, actual.canvas, line])];
  } else if (mode === "overlay") {
    actual.canvas.style.opacity = String(Number(opacity.value) / 100);
    panes = [frame("Actual over expected", [expected.canvas, actual.canvas])];
  } else {
    panes = [frame("Differences", [result.canvas])];
  }
  stage.replaceChildren(...panes);
  placeDivider();
  byId("zoom-value").textContent = zoomText(zoom);
}

function download() {
  if (!result) {
    return;
  }
  const { summary } = result;
  const canvas = document.createElement("canvas");
  canvas.width = summary.width;
  canvas.height = summary.height;
  const context = canvas.getContext("2d");
  context.drawImage(result.canvas, 0, 0);
  if (outline.checked) {
    context.strokeStyle = "#1746a2";
    context.lineWidth = Math.max(1, Math.round(Math.max(summary.width, summary.height) / 600));
    for (const region of result.regions.slice(0, MAX_OUTLINED)) {
      context.strokeRect(region.x - 1.5, region.y - 1.5, region.width + 3, region.height + 3);
    }
  }
  canvas.toBlob((blob) => {
    if (!blob) {
      say("The browser could not write the PNG.", true);
      return;
    }
    // The file is made in the tab and handed to the browser's own download: still no upload.
    const address = URL.createObjectURL(blob);
    const link = el("a", { download: "screenshot-diff.png" });
    link.href = address;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(address), 1000);
  }, "image/png");
}

// --- Wiring -------------------------------------------------------------------------------------

for (const slot of SLOTS) {
  const input = byId("file-" + slot);
  const drop = byId("drop-" + slot);
  input.addEventListener("change", () => take(slot, input.files[0]));
  drop.addEventListener("dragover", (event) => {
    event.preventDefault(); // without this the browser would open the file instead
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", async (event) => {
    event.preventDefault();
    drop.classList.remove("over");
    const files = Array.from(event.dataTransfer.files);
    await take(slot, files[0]);
    if (files[1]) {
      await take(slot === "expected" ? "actual" : "expected", files[1]); // two files at once fill both boxes
    }
  });
}

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
byId("swap").addEventListener("click", () => {
  const { expected, actual } = images;
  for (const slot of SLOTS) {
    byId("file-" + slot).value = "";
  }
  setImage("expected", actual);
  setImage("actual", expected);
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
views.forEach((view) => view.addEventListener("change", render));
divider.addEventListener("input", placeDivider);
opacity.addEventListener("input", () => {
  if (images.actual && views.find((view) => view.checked).value === "overlay") {
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
byId("download").addEventListener("click", download);

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
