// The page: the list of maps, and the editor of one map.
//
// The address after "#" says which of the two is shown ("#12/0a1b2c3d4e5f" is
// the map 0a1b2c3d4e5f of member 12), so a map has a link and the back button
// works. Nothing is kept in the browser's storage and nothing asks the server
// on a timer: the lists are loaded when the page opens and on "Refresh", a
// map when it is opened, and a save goes out when the visitor saves.
//
// The look comes from the EFP kit (/_efp/lib/, docs/UI_KIT.md). Motion is
// kept for what the eye should follow: a map opens out of its card and grows
// from its centre; a new node grows out of its parent while the others glide
// to their new places, and the ring around the selected node slides along
// (scene.js); the view glides when it has to move to show a node.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import * as api from "./api.js";
import { exportMarkdown, exportPng, exportSvg, fileName } from "./exporting.js";
import { fitView, layoutTree, neighbour, zoomAt } from "./layout.js";
import { PALETTE, measureText, useColors } from "./render.js";
import { createScene } from "./scene.js";
import * as tree from "./tree.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const listView = $("list-view");
const editorView = $("editor-view");
const stage = $("stage");
const canvas = $("canvas");
const viewport = $("viewport");
const nodeEditor = $("node-editor");
const titleInput = $("title");
const saveStatus = $("save-status");
const notice = $("notice");
const dialog = $("dialog");
const themeButton = $("theme");

const ARROWS = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
const COLOR_NAMES = {
  "": "Like the branch above",
  blue: "Blue",
  green: "Green",
  amber: "Amber",
  red: "Red",
  purple: "Purple",
  teal: "Teal",
  pink: "Pink",
  gray: "Gray",
};
const BRANCH_ORDER = ["blue", "green", "amber", "purple", "teal", "pink", "red", "gray"];
const SAVE_STATES = {
  readonly: ["eye", "Read only"],
  saving: [null, "Saving..."],
  dirty: ["circle-dot", "Not saved yet"],
  saved: ["cloud-check", "Saved"],
  conflict: ["triangle-alert", "Changed elsewhere"],
  error: ["cloud-off", "Not saved"],
};

const scene = createScene(viewport, { still: kit.reducedMotion });
let session = { signedIn: false, limits: { maps: 30, nodes: 500, title: 80, text: 200 } };
let editor = null; // the open map, see openEditor()

function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

function svg(name, attributes) {
  const made = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes || {})) {
    made.setAttribute(key, String(value));
  }
  return made;
}

// The colours of the drawing, read from the theme. The eight branch colours
// are variables of style.css (--mm-blue ...); a node is a tint of its colour
// on the paper, with a line a little closer to the text colour, so that a
// light colour (amber) keeps a visible edge on white and on dark paper.
// render.js writes the results into the SVG as plain colours.
function themeColors() {
  const palette = {};
  for (const name of tree.COLORS) {
    palette[name] = {
      fill: kit.color(`color-mix(in oklab, var(--mm-${name}) 16%, var(--mm-paper))`),
      line: kit.color(`color-mix(in oklab, var(--mm-${name}) 78%, var(--color-base-content))`),
    };
  }
  useColors({
    palette,
    sheet: kit.color("var(--mm-paper)"),
    text: kit.color("base-content"),
    plain: {
      fill: kit.color("color-mix(in oklab, var(--color-base-content) 5%, var(--mm-paper))"),
      line: kit.color("color-mix(in oklab, var(--color-base-content) 38%, var(--mm-paper))"),
    },
    root: { from: kit.color("primary"), to: kit.color("secondary"), text: kit.color("primary-content") },
  });
}

// --- A question in a dialog: a new title, or "are you sure" -----------------------

function ask({ title, text, value, ok, danger, icon }) {
  const input = $("dialog-input");
  const wantsText = value !== undefined;
  $("dialog-title").textContent = title;
  $("dialog-text").textContent = text || "";
  $("dialog-text").hidden = !text;
  input.hidden = !wantsText;
  input.value = wantsText ? value : "";
  $("dialog-ok").textContent = ok || "OK";
  $("dialog-ok").className = "btn " + (danger ? "btn-error" : "btn-primary");
  $("dialog-icon").replaceChildren(kit.icon(icon || (danger ? "trash-2" : "pencil-line")));
  $("dialog-icon").classList.toggle("is-danger", Boolean(danger));
  dialog.returnValue = "";
  dialog.showModal();
  if (wantsText) {
    input.select();
  }
  return new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => resolve(dialog.returnValue === "ok" ? (wantsText ? input.value.trim() : true) : null),
      { once: true }
    );
  });
}

$("dialog-cancel").addEventListener("click", () => dialog.close("cancel"));

// --- The list of maps ---------------------------------------------------------------

const cards = new Map(); // "mine:<id>" or "shared:<id>" -> its <li>, kept between two loads
const animated = new Set(); // the lists that animate their changes already

// A small picture of a map, made from its id and its size alone: the list
// does not fetch any nodes. A bigger map has more branches, in the colours
// the editor gives new branches; a map of one node waits for its first.
function glyph(found) {
  const seed = kit.hue(found.id);
  const branches = Math.min(6, Math.max(0, found.nodes - 1));
  const picture = svg("svg", { viewBox: "0 0 168 84", class: "mm-glyph", "aria-hidden": "true" });
  const middle = 42;
  const spread = branches > 1 ? Math.min(13, 66 / (branches - 1)) : 0;
  if (!branches) {
    picture.append(
      svg("path", { class: "mm-glyph-ghost", d: "M60 " + middle + " L92 " + middle }),
      svg("rect", { class: "mm-glyph-ghost", x: 92, y: middle - 7, width: 40, height: 14, rx: 7 })
    );
  }
  for (let index = 0; index < branches; index += 1) {
    const y = middle + (index - (branches - 1) / 2) * spread;
    const tone = "tone-" + BRANCH_ORDER[index];
    const width = 30 + ((seed >> index) % 3) * 9;
    picture.append(
      svg("path", { class: "mm-link " + tone, d: "M60 " + middle + " C76 " + middle + " 76 " + y + " 92 " + y, "stroke-width": 1.75 }),
      svg("rect", { class: "mm-shape " + tone, x: 92, y: y - 5, width, height: 10, rx: 5, "stroke-width": 1.25 })
    );
    if (found.nodes > 7 && index < 2) {
      picture.append(svg("rect", { class: "mm-shape " + tone, x: 98 + width, y: y - 3.5, width: 18, height: 7, rx: 3.5, "stroke-width": 1 }));
    }
  }
  picture.append(svg("rect", { class: "mm-glyph-root", x: 14, y: middle - 10, width: 46, height: 20, rx: 8 }));
  return picture;
}

async function listAct(button, work, done) {
  try {
    await kit.busy(button, work, { tick: false });
    if (done) {
      kit.toast(done, { kind: "success" });
    }
  } catch (error) {
    kit.toast(error.message, { kind: "error" });
  }
  await loadList();
}

function cardTool(icon, label, onClick, className) {
  return h(
    "button.btn.btn-ghost.btn-xs.btn-square",
    { type: "button", class: className, "aria-label": label, title: label, onclick: onClick },
    kit.icon(icon, "size-3.5")
  );
}

function fillCard(item, found, own) {
  item.className = "mm-card efp-lift";
  item.dataset.map = found.id;
  const facts = plural(found.nodes, "node") + " \u00b7 changed " + kit.timeAgo(found.updated_at);
  const link = h(
    "a.mm-card-link",
    { href: "#" + found.owner.id + "/" + found.id },
    h("div.mm-thumb", {}, glyph(found)),
    h("div.mm-card-body", {}, h("h3.mm-card-title", { text: found.title }), h("p.text-xs.text-base-content/60.mt-0.5", { text: facts }))
  );
  const foot = h("div.mm-card-foot");
  if (!own) {
    foot.append(
      h(
        "span.inline-flex.items-center.gap-1.5.text-xs.text-base-content/70.min-w-0",
        {},
        kit.avatar(found.owner.name, { size: "1.35rem" }),
        h("span.truncate", { text: "By " + found.owner.name })
      )
    );
    item.replaceChildren(link, foot);
    return;
  }
  foot.append(
    found.shared
      ? h("span.badge.badge-soft.badge-primary.badge-sm.gap-1", {}, kit.icon("globe", "size-3"), "Visible to everyone")
      : h("span.badge.badge-ghost.badge-sm.gap-1", {}, kit.icon("lock", "size-3"), "Private"),
    h(
      "span.mm-card-tools",
      {},
      cardTool("pencil", "Rename", async (event) => {
        const button = event.currentTarget;
        const title = await ask({ title: "Rename the map", value: found.title, ok: "Rename" });
        if (title && title !== found.title) {
          listAct(button, () => api.changeMap(found.id, { title, version: found.version }), "Renamed.");
        }
      }),
      cardTool(found.shared ? "lock" : "globe", found.shared ? "Make it private" : "Show it to everyone", (event) =>
        listAct(
          event.currentTarget,
          () => api.changeMap(found.id, { shared: !found.shared, version: found.version }),
          found.shared ? "Private again: only you can open it." : "Visible to everyone who can open the app."
        )
      ),
      cardTool(
        "trash-2",
        "Delete",
        async (event) => {
          const button = event.currentTarget;
          const sure = await ask({
            title: "Delete this map?",
            text: '"' + found.title + '" with its ' + plural(found.nodes, "node") + " will be gone for good.",
            ok: "Delete",
            danger: true,
          });
          if (sure) {
            listAct(button, () => api.deleteMap(found.id), "The map is deleted.");
          }
        },
        "mm-danger"
      )
    )
  );
  item.replaceChildren(link, foot);
}

// Cards are kept by map id, so a renamed map changes in place and one that
// moves to the top (it was just changed) glides there (kit.animateList).
function drawCards(list, maps, own, prefix) {
  const first = !animated.has(list);
  if (first) {
    list.replaceChildren(); // the skeletons
  }
  const seen = new Set();
  maps.forEach((found, index) => {
    const key = prefix + found.id;
    seen.add(key);
    let item = cards.get(key);
    if (!item) {
      item = h("li");
      cards.set(key, item);
    }
    fillCard(item, found, own);
    if (list.children[index] !== item) {
      list.insertBefore(item, list.children[index] || null);
    }
  });
  for (const [key, item] of cards) {
    if (key.startsWith(prefix) && !seen.has(key)) {
      cards.delete(key);
      item.remove();
    }
  }
  if (first) {
    animated.add(list);
    kit.rise(list.children, { stagger: 60 }).then(() => kit.animateList(list));
  }
}

function cardOf(mapId) {
  for (const prefix of ["mine:", "shared:"]) {
    const item = cards.get(prefix + mapId);
    if (item && item.isConnected) {
      return item;
    }
  }
  return null;
}

function listProblem(text) {
  const status = $("list-status");
  status.hidden = !text;
  status.replaceChildren(text ? h("div.alert.alert-soft.alert-error", {}, kit.icon("circle-alert", "size-5"), h("span", { text })) : "");
}

async function loadList() {
  let found;
  try {
    found = await api.listMaps();
  } catch (error) {
    listProblem(error.message);
    for (const list of [$("mine"), $("shared")]) {
      if (!animated.has(list)) {
        list.replaceChildren();
      }
    }
    return;
  }
  listProblem("");
  session = { signedIn: found.signed_in, limits: found.limits };
  $("who").textContent = found.signed_in
    ? "Signed in as " + found.me.name + ". A map is yours alone until you share it."
    : "You are not signed in: you can open the maps others share.";
  if (found.signed_in && !$("me").firstChild) {
    $("me").append(kit.avatar(found.me.name, { size: "2rem" }));
  }
  $("new-form").hidden = !found.signed_in;
  $("signed-out").hidden = found.signed_in;
  $("mine-section").hidden = !found.signed_in;
  $("mine-count").hidden = !found.signed_in;
  $("mine-count").textContent = found.mine.length + " of " + found.limits.maps;
  drawCards($("mine"), found.mine, true, "mine:");
  $("mine").hidden = found.mine.length === 0;
  $("mine-empty").hidden = found.mine.length > 0;
  drawCards($("shared"), found.shared, false, "shared:");
  $("shared").hidden = found.shared.length === 0;
  $("shared-empty").hidden = found.shared.length > 0;
}

$("new-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("new-title");
  const title = input.value.trim();
  if (!title) {
    kit.shake(input.closest("label"));
    input.focus();
    return;
  }
  try {
    const made = await kit.busy($("create"), () => api.createMap(title), { tick: false });
    input.value = "";
    location.hash = "#" + made.owner.id + "/" + made.id;
  } catch (error) {
    kit.toast(error.message, { kind: "error" });
  }
});

$("refresh").addEventListener("click", (event) => kit.busy(event.currentTarget, () => loadList(), { tick: false }));

// --- The editor: what is shown around the map --------------------------------------------

function showNotice(kind, icon, text, actions) {
  const wasHidden = notice.hidden;
  notice.className = "alert alert-soft mm-notice alert-" + kind;
  $("notice-icon").replaceChildren(kit.icon(icon, "size-5"));
  $("notice-text").textContent = text;
  $("notice-actions").replaceChildren(...actions);
  notice.hidden = false;
  if (wasHidden) {
    kit.rise(notice, { distance: -10 });
  } else {
    kit.flash(notice);
  }
}

function noticeButton(label, onClick, primary) {
  const made = h("button.btn.btn-sm", { type: "button", class: primary ? "btn-primary" : "", text: label });
  made.addEventListener("click", () => kit.busy(made, onClick, { tick: false }).catch(() => {}));
  return made;
}

// A sentence for the moment over the canvas (moving a node, a map still
// without branches), or none.
function message(text, icon = "move") {
  const hint = $("hint");
  const shown = !hint.hidden && hint.textContent === text;
  hint.hidden = !text;
  if (!text || shown) {
    return;
  }
  hint.replaceChildren(kit.icon(icon, "size-4"), h("span", { text }));
  kit.appear(hint);
}

// A map that is only its centre says how it grows.
function firstBranchHint() {
  if (editor.editable && editor.nodes.length === 1 && !editor.moving) {
    const touch = matchMedia("(hover: none)").matches;
    message(touch ? "Tap the first button below to grow the first branch." : "Press Tab to grow the first branch.", "sprout");
  } else if (!editor.moving) {
    message("");
  }
}

// Something that did not work: said once, in the corner.
function problem(text) {
  kit.toast(text, { kind: "warning" });
}

function drawLegend() {
  const row = (keys, text) => h("span.mm-legend-row", {}, ...keys.map((key) => h("kbd.kbd.kbd-xs", { text: key })), h("span", { text }));
  $("legend").replaceChildren(
    ...(editor.editable
      ? [row(["Tab"], "add a child"), row(["Enter"], "add a sibling"), row(["F2"], "rename"), row(["Space"], "fold"), row(["?"], "all keys")]
      : [row(["\u2190\u2192"], "walk"), row(["Space"], "fold"), row(["?"], "all keys")])
  );
}

function showSaveState(failure) {
  if (!editor) {
    return;
  }
  const state = !editor.editable
    ? "readonly"
    : editor.saving
      ? "saving"
      : failure
        ? failure.code === "stale"
          ? "conflict"
          : "error"
        : editor.dirty
          ? "dirty"
          : "saved";
  const before = saveStatus.dataset.state;
  const [icon, text] = SAVE_STATES[state];
  saveStatus.title = failure ? failure.message : text;
  if (before === state && saveStatus.firstChild) {
    return;
  }
  saveStatus.dataset.state = state;
  saveStatus.replaceChildren(
    icon ? kit.icon(icon, "size-3.5") : h("span.loading.loading-spinner.loading-xs"),
    h("span.mm-status-text", { text })
  );
  if (state === "saved" && before === "saving") {
    kit.pop(saveStatus, { scale: 1.1 });
  }
}

// --- The editor: the view (pan and zoom) ---------------------------------------------------

let glide = null; // {from, target, start, ms}: the view on its way somewhere
let glideFrame = 0;

function applyView() {
  const { x, y, k } = editor.view;
  viewport.setAttribute("transform", "translate(" + x + " " + y + ") scale(" + k + ")");
  // The dots move and scale with the map, doubling or halving their spacing
  // so that they stay a texture and never turn into a carpet.
  let step = 24 * k;
  while (step < 14) {
    step *= 2;
  }
  while (step > 56) {
    step /= 2;
  }
  stage.style.setProperty("--grid-size", step + "px");
  stage.style.setProperty("--grid-x", x + "px");
  stage.style.setProperty("--grid-y", y + "px");
  $("zoom-level").textContent = Math.round(k * 100) + "%";
  placeNodeEditor();
}

function stopGlide() {
  cancelAnimationFrame(glideFrame);
  glide = null;
}

// Move the view to "target" over a moment, so the eye keeps its place.
function glideView(target, ms = 380) {
  stopGlide();
  if (kit.reducedMotion()) {
    editor.view = target;
    applyView();
    return;
  }
  glide = { from: { ...editor.view }, target, start: performance.now(), ms };
  const open = editor;
  const step = (now) => {
    if (!glide || editor !== open) {
      return;
    }
    const t = Math.min(1, (now - glide.start) / glide.ms);
    const e = 1 - Math.pow(1 - t, 4);
    const { from, target: to } = glide;
    editor.view = { x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e, k: from.k + (to.k - from.k) * e };
    applyView();
    if (t < 1) {
      glideFrame = requestAnimationFrame(step);
    } else {
      glide = null;
    }
  };
  glideFrame = requestAnimationFrame(step);
}

function fit(animate = true) {
  const target = fitView(editor.layout, stage.clientWidth, stage.clientHeight);
  if (animate) {
    glideView(target);
  } else {
    stopGlide();
    editor.view = target;
    applyView();
  }
}

function zoomBy(factor) {
  const base = glide ? glide.target : editor.view; // a second press adds to the first
  glideView(zoomAt(base, factor, stage.clientWidth / 2, stage.clientHeight / 2), 240);
}

// Move the view just far enough for a node to be on the stage.
function reveal(id) {
  const box = editor.layout.boxes.get(id);
  if (!box) {
    return;
  }
  const view = { ...(glide ? glide.target : editor.view) };
  const margin = 28;
  const bottom = stage.clientHeight - (stage.clientWidth < 640 ? 120 : 84); // the toolbars cover the bottom
  const left = view.x + box.x * view.k;
  const top = view.y + box.y * view.k;
  const right = left + box.width * view.k;
  const below = top + box.height * view.k;
  if (left < margin) {
    view.x += margin - left;
  } else if (right > stage.clientWidth - margin) {
    view.x -= right - (stage.clientWidth - margin);
  }
  if (top < margin + 48) {
    view.y += margin + 48 - top;
  } else if (below > bottom) {
    view.y -= below - bottom;
  }
  if (view.x !== editor.view.x || view.y !== editor.view.y) {
    glideView(view, 320);
  }
}

// --- The editor: drawing ---------------------------------------------------------------------

function openEditor(found) {
  stopGlide();
  editor = {
    map: { id: found.id, owner: found.owner, title: found.title, shared: found.shared, version: found.version },
    editable: found.editable,
    nodes: found.nodes,
    selected: null,
    moving: null, // the node that waits for its new parent
    editing: null, // {id, created} while the text of a node is being typed
    past: [],
    future: [],
    dirty: false,
    saving: false,
    view: { x: 0, y: 0, k: 1 },
    layout: null,
  };
  titleInput.value = found.title;
  titleInput.readOnly = !found.editable;
  $("share-box").checked = found.shared;
  $("share-label").hidden = !found.editable;
  $("edit-tools").hidden = !found.editable;
  $("save").hidden = !found.editable;
  notice.hidden = true;
  if (!found.editable) {
    showNotice(
      "info",
      "eye",
      "A map of " + found.owner.name + ". You can look around, fold branches and export it.",
      session.signedIn ? [noticeButton("Make a copy of my own", copyAsMine, true)] : []
    );
  }
  listView.hidden = true;
  listView.classList.add("mm-settled"); // its entrance played once; coming back it is just there
  editorView.hidden = false;
  $("theme-editor").append(themeButton);
  document.title = found.title + " - Mind map";
  scene.reset();
  relayout({ bloom: true });
  fit(false);
  select(tree.rootOf(editor.nodes).id);
  message("");
  firstBranchHint();
  drawLegend();
  saveStatus.replaceChildren();
  showSaveState();
  canvas.focus({ preventScroll: true });
}

// Lay the map out again and draw it. The centre keeps its place on the
// screen: without that, the whole map would jump whenever a branch grows.
function relayout({ bloom = false } = {}) {
  const rootId = tree.rootOf(editor.nodes).id;
  const before = editor.layout && editor.layout.boxes.get(rootId);
  editor.layout = layoutTree(editor.nodes, measureText);
  const shift = before ? editor.layout.boxes.get(rootId).y - before.y : 0;
  if (shift) {
    editor.view.y -= shift * editor.view.k;
    if (glide) {
      glide.from.y -= shift * glide.from.k;
      glide.target.y -= shift * glide.target.k;
    }
    scene.shift(0, shift); // what is drawn keeps its place on the screen, too
  }
  scene.update(editor.nodes, editor.layout, { bloom });
  if (editor.selected && !editor.layout.boxes.has(editor.selected)) {
    editor.selected = null; // it was removed, or folded away
  }
  markNodes();
  applyView();
  updateTools();
  firstBranchHint();
}

function markNodes(dropTarget) {
  for (const shape of viewport.querySelectorAll(".node")) {
    const id = shape.dataset.id;
    shape.classList.toggle("selected", id === editor.selected);
    shape.classList.toggle("moving", id === editor.moving);
    shape.classList.toggle("drop", id === dropTarget);
  }
}

function updateTools() {
  const node = editor.selected ? tree.find(editor.nodes, editor.selected) : null;
  const isRoot = Boolean(node) && node.parent === null;
  $("add-child").disabled = !node;
  $("add-sibling").disabled = !node;
  $("rename-node").disabled = !node;
  $("move-node").disabled = !node || isRoot;
  $("delete-node").disabled = !node || isRoot;
  $("undo").disabled = editor.past.length === 0;
  $("redo").disabled = editor.future.length === 0;
  const toggle = $("toggle-node");
  toggle.disabled = !node || !editor.layout.boxes.get(node.id).hasChildren;
  const folded = Boolean(node && node.collapsed);
  if (toggle.dataset.folded !== String(folded)) {
    toggle.dataset.folded = String(folded);
    toggle.replaceChildren(kit.icon(folded ? "circle-plus" : "circle-minus"));
    toggle.setAttribute("aria-label", folded ? "Unfold the branch" : "Fold the branch");
  }
  const own = (node && node.color) || "";
  const shown = node ? PALETTE[tree.branchColor(editor.nodes, node.id)] : null;
  const dot = $("color-dot");
  dot.classList.toggle("is-plain", !own);
  dot.style.setProperty("--dot-fill", shown ? shown.fill : "transparent");
  dot.style.setProperty("--dot-line", shown ? shown.line : "");
  $("color-button").classList.toggle("btn-disabled", !node);
  for (const swatch of $("colors").children) {
    swatch.disabled = !node;
    swatch.setAttribute("aria-pressed", String(Boolean(node) && own === swatch.dataset.color));
  }
}

function select(id) {
  editor.selected = id && editor.layout.boxes.has(id) ? id : null;
  scene.select(editor.selected);
  markNodes();
  updateTools();
  if (editor.selected) {
    reveal(editor.selected);
    const node = tree.find(editor.nodes, editor.selected);
    const below = editor.nodes.filter((other) => other.parent === node.id).length;
    $("announce").textContent =
      (node.text || "Empty node") +
      (below ? ", " + below + (below === 1 ? " child" : " children") : "") +
      (node.collapsed ? ", folded" : "");
  }
}

// --- The editor: changes ---------------------------------------------------------------

// Every change goes through here: it can be undone, and the map is "not saved yet".
function change(nodes, selectId) {
  if (nodes === editor.nodes) {
    return;
  }
  editor.past.push(editor.nodes);
  if (editor.past.length > 200) {
    editor.past.shift();
  }
  editor.future = [];
  editor.nodes = nodes;
  editor.dirty = true;
  if (selectId !== undefined) {
    editor.selected = selectId;
  }
  relayout();
  select(editor.selected);
  showSaveState();
}

function step(from, to) {
  finishEditing(true);
  if (!from.length) {
    return;
  }
  to.push(editor.nodes);
  editor.nodes = from.pop();
  editor.dirty = true;
  relayout();
  select(editor.selected || tree.rootOf(editor.nodes).id);
  showSaveState();
}

function roomForOneMore() {
  if (editor.nodes.length < session.limits.nodes) {
    return true;
  }
  problem("A map has at most " + session.limits.nodes + " nodes.");
  return false;
}

// A new main branch gets a colour of its own, the first one its siblings do
// not wear yet, so that a map is easy to read without any effort. A centre
// with a colour of its own gives that colour to the whole map instead.
function withBranchColor(nodes, id) {
  const node = tree.find(nodes, id);
  const root = tree.rootOf(nodes);
  if (!node || root.color || node.parent !== root.id || node.color) {
    return nodes;
  }
  const taken = nodes.filter((other) => other.parent === root.id && other.color).map((other) => other.color);
  const free = BRANCH_ORDER.find((color) => !taken.includes(color)) || BRANCH_ORDER[taken.length % BRANCH_ORDER.length];
  return tree.setColor(nodes, id, free);
}

function add(make) {
  finishEditing(true);
  if (!editor.editable || !editor.selected || !roomForOneMore()) {
    return;
  }
  const made = make(editor.nodes, editor.selected, "");
  if (made.id) {
    change(withBranchColor(made.nodes, made.id), made.id);
    startEditing(made.id, true);
  }
}

function removeSelected() {
  if (editor.editable && editor.selected) {
    const result = tree.removeBranch(editor.nodes, editor.selected);
    change(result.nodes, result.select);
  }
}

function toggleSelected() {
  if (!editor.selected) {
    return;
  }
  const nodes = tree.toggleCollapsed(editor.nodes, editor.selected);
  if (editor.editable) {
    change(nodes);
  } else if (nodes !== editor.nodes) {
    editor.nodes = nodes; // a visitor folds for themselves; nothing is saved
    relayout();
    select(editor.selected);
  }
}

function startMove() {
  const node = editor.selected && tree.find(editor.nodes, editor.selected);
  if (!editor.editable || !node || node.parent === null) {
    return;
  }
  editor.moving = node.id;
  markNodes();
  message("Choose the new parent: click it, or walk there with the arrow keys and press Enter. Esc cancels.");
}

// Hang the node that is moving below targetId. True when it moved.
function endMove(targetId) {
  const id = editor.moving;
  editor.moving = null;
  message("");
  if (!targetId || targetId === id) {
    markNodes();
    select(id);
    return false;
  }
  const reason = tree.moveProblem(editor.nodes, id, targetId);
  if (reason) {
    markNodes();
    select(id);
    problem(reason);
    return false;
  }
  change(tree.moveNode(editor.nodes, id, targetId), id);
  return true;
}

// --- The editor: typing the text of a node -------------------------------------------------

function placeNodeEditor() {
  const box = editor && editor.editing && editor.layout.boxes.get(editor.editing.id);
  if (!box) {
    return;
  }
  const { x, y, k } = editor.view;
  nodeEditor.style.left = x + box.x * k - 2 + "px";
  nodeEditor.style.top = y + box.y * k - 2 + "px";
  nodeEditor.style.width = Math.max(190, box.width * k + 4) + "px";
  nodeEditor.style.height = Math.max(34, box.height * k + 4) + "px";
  nodeEditor.style.fontSize = Math.max(13, 14 * k) + "px";
}

function startEditing(id, created) {
  const node = tree.find(editor.nodes, id);
  if (!editor.editable || !node || !editor.layout.boxes.has(id)) {
    return;
  }
  editor.editing = { id, created: Boolean(created) };
  nodeEditor.value = node.text;
  nodeEditor.placeholder = created ? "A new idea" : "";
  nodeEditor.hidden = false;
  stage.classList.add("is-typing");
  placeNodeEditor();
  nodeEditor.focus({ preventScroll: true });
  nodeEditor.select();
}

function finishEditing(keep) {
  const editing = editor && editor.editing;
  if (!editing) {
    return;
  }
  editor.editing = null;
  nodeEditor.hidden = true;
  stage.classList.remove("is-typing");
  const text = nodeEditor.value.replace(/\s+/g, " ").trim().slice(0, session.limits.text);
  const node = tree.find(editor.nodes, editing.id);
  if (!node) {
    return;
  }
  if (keep && text) {
    if (editing.created) {
      // Adding the node was the change; its first text belongs to it, so
      // that one "Undo" takes the node away again.
      editor.nodes = tree.rename(editor.nodes, editing.id, text);
      relayout();
      select(editing.id);
    } else {
      change(tree.rename(editor.nodes, editing.id, text), editing.id);
    }
  } else if (editing.created && editor.past.length) {
    // A new node that was given no text is taken back: it shrinks into its parent.
    editor.nodes = editor.past.pop();
    relayout();
    select(node.parent);
  }
}

nodeEditor.addEventListener("keydown", (event) => {
  if (event.isComposing) {
    return; // Enter is choosing a character in an input method, not finishing the text
  }
  if (event.key === "Enter" || event.key === "Escape" || event.key === "Tab") {
    event.preventDefault();
    finishEditing(event.key !== "Escape");
    canvas.focus({ preventScroll: true });
    if (event.key === "Tab" && !event.shiftKey) {
      add(tree.addChild); // keep typing an outline: Tab goes one level deeper
    }
  }
});
nodeEditor.addEventListener("blur", () => finishEditing(true));

// --- The editor: pointer (mouse, touch, pen) -------------------------------------------------

const pointers = new Map(); // the pointers that are down on the stage: id -> {x, y}
let gesture = null; // "pan", "pinch", or "node" (a click on a node that may become a drag)

function stagePoint(event) {
  const frame = stage.getBoundingClientRect();
  return { x: event.clientX - frame.left, y: event.clientY - frame.top };
}

function nodeAt(event) {
  const under = document.elementFromPoint(event.clientX, event.clientY);
  const shape = under && under.closest ? under.closest(".node") : null;
  return shape && viewport.contains(shape) ? shape.dataset.id : null;
}

canvas.addEventListener("pointerdown", (event) => {
  if (event.pointerType === "mouse" && event.button !== 0) {
    return;
  }
  finishEditing(true);
  stopGlide(); // the hand wins over a glide that is under way
  const point = stagePoint(event);
  const id = nodeAt(event);
  pointers.set(event.pointerId, point);
  canvas.setPointerCapture(event.pointerId);
  if (pointers.size > 1) {
    gesture = { kind: "pinch", distance: 0 };
  } else if (id && editor.moving) {
    endMove(id);
    gesture = null;
  } else if (id) {
    select(id);
    // A finger on a node pans like a finger anywhere else; to move a node by
    // touch there is the Move button. A mouse or a pen drags the node.
    const draggable = editor.editable && event.pointerType !== "touch" && id !== tree.rootOf(editor.nodes).id;
    gesture = { kind: "node", id, start: point, draggable, dragging: false };
  } else {
    gesture = { kind: "pan" };
    stage.classList.add("is-panning");
  }
});

canvas.addEventListener("pointermove", (event) => {
  const before = pointers.get(event.pointerId);
  if (!before || !gesture) {
    return;
  }
  const point = stagePoint(event);
  pointers.set(event.pointerId, point);
  if (gesture.kind === "pinch") {
    const [first, second] = [...pointers.values()];
    const distance = Math.hypot(first.x - second.x, first.y - second.y);
    if (gesture.distance > 0 && distance > 0) {
      editor.view = zoomAt(editor.view, distance / gesture.distance, (first.x + second.x) / 2, (first.y + second.y) / 2);
      applyView();
    }
    gesture.distance = distance;
    return;
  }
  if (gesture.kind === "node" && !gesture.dragging) {
    if (Math.hypot(point.x - gesture.start.x, point.y - gesture.start.y) < 6) {
      return; // still a click
    }
    if (!gesture.draggable) {
      gesture = { kind: "pan" };
      stage.classList.add("is-panning");
    } else {
      gesture.dragging = true;
      editor.moving = gesture.id;
      canvas.classList.add("dragging");
    }
  }
  if (gesture.kind === "pan") {
    editor.view.x += point.x - before.x;
    editor.view.y += point.y - before.y;
    applyView();
  } else if (gesture.dragging) {
    // The branch follows the pointer, a little below and to the right of it,
    // so that the node under the pointer stays in sight and can say whether
    // it would take it.
    const k = editor.view.k;
    scene.drag(gesture.id, (point.x - gesture.start.x + 16) / k, (point.y - gesture.start.y + 20) / k);
    const target = nodeAt(event);
    markNodes(target && !tree.moveProblem(editor.nodes, gesture.id, target) ? target : null);
  }
});

function pointerGone(event) {
  if (!pointers.delete(event.pointerId)) {
    return;
  }
  stage.classList.remove("is-panning");
  if (gesture && gesture.kind === "node" && gesture.dragging) {
    canvas.classList.remove("dragging");
    // The dragged node itself lets the pointer through (style.css), so this is the node below it.
    const target = event.type === "pointerup" ? nodeAt(event) : null;
    if (!endMove(target)) {
      scene.settle(); // refused or let go in the open: it glides back
    }
  }
  gesture = pointers.size === 1 ? { kind: "pan" } : null; // after a pinch the finger that stays pans
}

canvas.addEventListener("pointerup", pointerGone);
canvas.addEventListener("pointercancel", pointerGone);

canvas.addEventListener("dblclick", (event) => {
  const id = nodeAt(event);
  if (id) {
    startEditing(id, false);
  }
});

canvas.addEventListener(
  "wheel",
  (event) => {
    event.preventDefault();
    stopGlide();
    const unit = event.deltaMode === 1 ? 16 : 1; // some mice count in lines
    if (event.ctrlKey || event.metaKey) {
      const point = stagePoint(event);
      editor.view = zoomAt(editor.view, Math.exp(-event.deltaY * unit * 0.01), point.x, point.y);
    } else {
      editor.view.x -= event.deltaX * unit;
      editor.view.y -= event.deltaY * unit;
    }
    applyView();
  },
  { passive: false }
);

// --- The editor: keyboard ---------------------------------------------------------------------

canvas.addEventListener("keydown", (event) => {
  if (!editor) {
    return;
  }
  const key = event.key;
  const command = event.ctrlKey || event.metaKey;
  if (command && (key === "z" || key === "Z" || key === "y" || key === "Y")) {
    event.preventDefault();
    if (editor.editable) {
      const redo = key === "y" || key === "Y" || event.shiftKey;
      step(redo ? editor.future : editor.past, redo ? editor.past : editor.future);
    }
    return;
  }
  if (command) {
    return;
  }
  if (key === "?") {
    event.preventDefault();
    $("keys-dialog").showModal();
    return;
  }
  if (ARROWS[key]) {
    event.preventDefault();
    const from = editor.selected || tree.rootOf(editor.nodes).id;
    if (event.altKey && editor.editable && !editor.moving && (key === "ArrowUp" || key === "ArrowDown")) {
      change(tree.reorder(editor.nodes, from, key === "ArrowUp" ? -1 : 1), from);
    } else {
      const node = tree.find(editor.nodes, from);
      if (ARROWS[key] === "right" && node.collapsed) {
        toggleSelected(); // walking into a folded branch opens it
      }
      select(editor.selected ? neighbour(editor.layout, from, ARROWS[key]) || from : from);
    }
    return;
  }
  if (key === "Escape") {
    // With nothing selected the map no longer takes Tab, so the keyboard can leave it.
    if (editor.moving) {
      endMove(null);
    } else {
      select(null);
    }
    return;
  }
  if (key === "+" || key === "=" || key === "-" || key === "0") {
    event.preventDefault();
    if (key === "0") {
      fit();
    } else {
      zoomBy(key === "-" ? 1 / 1.2 : 1.2);
    }
    return;
  }
  if (!editor.selected) {
    return;
  }
  if (editor.moving) {
    if (key === "Enter") {
      event.preventDefault();
      endMove(editor.selected);
    }
    return;
  }
  if (key === " ") {
    event.preventDefault();
    toggleSelected();
    return;
  }
  if (!editor.editable || event.altKey) {
    return;
  }
  const actions = {
    Tab: () => add(tree.addChild),
    Insert: () => add(tree.addChild),
    Enter: () => add(tree.addSibling),
    F2: () => startEditing(editor.selected, false),
    Delete: removeSelected,
    Backspace: removeSelected,
    m: startMove,
    M: startMove,
  };
  if (actions[key] && !(key === "Tab" && event.shiftKey)) {
    event.preventDefault();
    actions[key]();
  }
});

document.addEventListener("keydown", (event) => {
  if (editor && (event.ctrlKey || event.metaKey) && (event.key === "s" || event.key === "S")) {
    event.preventDefault();
    saveNow();
  }
});

// --- The editor: buttons -----------------------------------------------------------------------

function onClick(id, action) {
  $(id).addEventListener("click", () => {
    action();
    if (!editor || !editor.editing) {
      canvas.focus({ preventScroll: true }); // the keys keep working after a click on a button
    }
  });
}

onClick("add-child", () => add(tree.addChild));
onClick("add-sibling", () => add(tree.addSibling));
onClick("rename-node", () => startEditing(editor.selected, false));
onClick("move-node", startMove);
onClick("delete-node", removeSelected);
onClick("toggle-node", toggleSelected);
onClick("undo", () => step(editor.past, editor.future));
onClick("redo", () => step(editor.future, editor.past));
onClick("zoom-in", () => zoomBy(1.2));
onClick("zoom-out", () => zoomBy(1 / 1.2));
onClick("fit", () => fit());
$("save").addEventListener("click", () => saveNow());
$("keys").addEventListener("click", () => $("keys-dialog").showModal());
$("keys-dialog").addEventListener("close", () => editor && canvas.focus({ preventScroll: true }));

// The swatches show the theme's colours, so they are made again when it changes.
function drawSwatches() {
  $("colors").replaceChildren(
    ...["", ...tree.COLORS].map((color) => {
      const look = PALETTE[color];
      const swatch = h("button.mm-swatch", {
        type: "button",
        class: color ? "" : "is-plain",
        dataset: { color },
        title: COLOR_NAMES[color],
        "aria-label": COLOR_NAMES[color],
        "aria-pressed": "false",
        style: look ? { background: look.fill, borderColor: look.line } : {},
      });
      swatch.addEventListener("click", () => {
        $("color-menu").open = false;
        if (editor && editor.editable && editor.selected) {
          change(tree.setColor(editor.nodes, editor.selected, color));
        }
        canvas.focus({ preventScroll: true });
      });
      return swatch;
    })
  );
  if (editor) {
    updateTools();
  }
}

function exporter(id, work, extension) {
  $(id).addEventListener("click", async () => {
    $("export-menu").open = false;
    const title = titleInput.value.trim() || editor.map.title;
    try {
      await kit.busy($("export-menu").querySelector("summary"), async () => work(editor.nodes, title), { tick: false });
      kit.toast("Saved as " + fileName(title, extension) + ".", { kind: "success" });
    } catch (error) {
      kit.toast(error.message, { kind: "error" });
    }
  });
}

exporter("export-svg", exportSvg, "svg");
exporter("export-png", exportPng, "png");
exporter("export-md", exportMarkdown, "md");

// The two menus are <details> elements: the browser opens and closes them
// with the mouse and the keyboard. A click elsewhere or Esc closes them too.
function closeMenus(except) {
  for (const menu of document.querySelectorAll("details.mm-menu[open]")) {
    if (menu !== except) {
      menu.open = false;
    }
  }
}
document.addEventListener("click", (event) => closeMenus(event.target.closest ? event.target.closest("details.mm-menu") : null));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeMenus(null);
  }
});

titleInput.addEventListener("input", () => {
  if (editor.editable) {
    editor.dirty = true;
    showSaveState();
  }
});
titleInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) {
    saveNow();
  }
});

// Sharing is saved at once: a box that looks ticked while the map is still
// private would be a promise the page does not keep.
$("share-box").addEventListener("change", async () => {
  editor.map.shared = $("share-box").checked;
  editor.dirty = true;
  if (await save()) {
    editor.map.shared = !editor.map.shared;
    $("share-box").checked = editor.map.shared;
  } else {
    kit.toast(editor.map.shared ? "Visible to everyone who can open the app." : "Private again: only you can open it.", { kind: "success" });
  }
});

// --- The editor: saving ------------------------------------------------------------------------

// Send the map with the version it was loaded as. The answer is the new
// version, or 409 when the stored map has moved on in the meantime. Returns
// what went wrong (an error with a sentence as its message), or null.
async function save(whileLeaving) {
  if (!editor || !editor.editable) {
    return null;
  }
  if (editor.saving) {
    return new Error("A save is still under way.");
  }
  finishEditing(true);
  const open = editor;
  const title = titleInput.value.replace(/\s+/g, " ").trim() || open.map.title;
  const sent = { title, nodes: open.nodes, shared: open.map.shared, version: open.map.version };
  open.saving = true;
  showSaveState();
  let failure = null;
  try {
    const saved = await api.saveMap(open.map.id, sent, whileLeaving);
    open.map.version = saved.version;
    open.map.title = saved.title;
    if (open.nodes === sent.nodes && open.map.shared === sent.shared) {
      open.dirty = false; // nothing was changed while the request was under way
    }
  } catch (error) {
    failure = error;
    if (error.code === "stale" && open === editor) {
      showNotice("warning", "triangle-alert", error.message, conflictChoices());
    } else if (!whileLeaving && open === editor) {
      kit.toast(error.message, { kind: "error" });
    }
  }
  open.saving = false;
  if (open === editor) {
    showSaveState(failure);
  }
  return failure;
}

// Save from the button, from Ctrl+S or from Enter in the title: the button shows it.
function saveNow() {
  if (!editor || !editor.editable) {
    return;
  }
  kit
    .busy($("save"), async () => {
      const failure = await save();
      if (failure) {
        throw failure;
      }
    })
    .catch(() => {}); // already said: the chip, a toast or the notice
}

// What can be done about a map that was changed elsewhere: take the stored
// one, or keep this one under a new name. Neither loses work silently.
function conflictChoices() {
  return [noticeButton("Reload the saved map", reloadMap), noticeButton("Keep mine as a copy", copyAsMine, true)];
}

async function reloadMap() {
  const { owner, id } = editor.map;
  try {
    openEditor(await api.readMap(owner.id, id));
  } catch (error) {
    kit.toast(error.message, { kind: "error" });
  }
}

// A map of my own with these nodes: the way out of a conflict, and the way
// to work on a map somebody else shares.
async function copyAsMine() {
  const title = (titleInput.value.trim() || editor.map.title).slice(0, session.limits.title - 7) + " (copy)";
  try {
    const made = await api.createMap(title, editor.nodes);
    editor.dirty = false;
    location.hash = "#" + made.owner.id + "/" + made.id;
    kit.toast("Your copy is open. It is private until you share it.", { kind: "success" });
  } catch (error) {
    kit.toast(error.message, { kind: "error" });
  }
}

// Leaving with unsaved work: save while the tab goes into the background or
// closes (the request outlives the page), and ask before the page is left.
document.addEventListener("visibilitychange", () => {
  if (document.hidden && editor && editor.dirty) {
    save(true);
  }
});
window.addEventListener("beforeunload", (event) => {
  if (editor && editor.editable && editor.dirty) {
    event.preventDefault();
    event.returnValue = ""; // older browsers ask only when this is set
  }
});

// --- Which of the two views -----------------------------------------------------------------------

// The picture of the card becomes the canvas, or the canvas becomes the
// card again (a View Transition where the browser has them), so the eye
// sees where the map went.
async function morph(from, to, update) {
  if (!from || !to) {
    update();
    return;
  }
  from.style.viewTransitionName = "mm-map";
  await kit.transition(() => {
    from.style.viewTransitionName = "";
    update();
    to.style.viewTransitionName = "mm-map";
  });
  to.style.viewTransitionName = "";
}

// A card says that its map is on the way when that takes a moment.
function markOpening(card) {
  if (!card) {
    return () => {};
  }
  const timer = setTimeout(() => card.classList.add("is-opening"), 150);
  return () => {
    clearTimeout(timer);
    card.classList.remove("is-opening");
  };
}

function showList() {
  editorView.hidden = true;
  listView.hidden = false;
  $("theme-home").append(themeButton);
  document.title = "Mind map";
}

let routes = 0; // the latest route() call; an older one that finishes late gives way
let listLoaded = Promise.resolve();

async function route() {
  const ticket = ++routes;
  let left = null;
  if (editor) {
    const here = "#" + editor.map.owner.id + "/" + editor.map.id;
    const wantedNext = location.hash;
    if (wantedNext === here) {
      return;
    }
    const failed = editor.dirty && editor.editable ? await save() : null;
    if (failed) {
      // Stay: the unsaved work is still on the page, and the visitor decides.
      history.replaceState(null, "", here);
      const leave = noticeButton("Leave without saving", () => {
        editor.dirty = false;
        location.hash = wantedNext || "#";
      });
      showNotice(
        "warning",
        "triangle-alert",
        "Your changes are not saved. " + failed.message,
        failed.code === "stale" ? [...conflictChoices(), leave] : [noticeButton("Try again", () => save(), true), leave]
      );
      return;
    }
    left = editor.map.id;
    editor = null;
    stopGlide();
  }
  let failure = "";
  const wanted = /^#(-?\d+)\/([0-9a-f]{12})$/.exec(location.hash);
  if (wanted) {
    const card = listView.hidden ? null : cardOf(wanted[2]);
    const done = markOpening(card);
    try {
      const [found] = await Promise.all([api.readMap(wanted[1], wanted[2]), listLoaded]);
      done();
      if (ticket === routes) {
        await morph(card && card.querySelector(".mm-thumb"), stage, () => openEditor(found));
      }
      return;
    } catch (error) {
      done();
      failure = error.message;
      history.replaceState(null, "", "#"); // the address names a map that cannot be shown
    }
  }
  if (left) {
    // First, so that the card of a map made just now is there to land on.
    await kit.busy($("back"), () => loadList(), { tick: false });
  }
  if (ticket !== routes) {
    return;
  }
  const card = left ? cardOf(left) : null;
  await morph(listView.hidden && card ? stage : null, card && card.querySelector(".mm-thumb"), showList);
  if (failure) {
    listProblem(failure);
  }
}

await kit.ready();
kit.themeSwitch(themeButton);
themeColors();
drawSwatches();
kit.onThemeChange(() => {
  themeColors();
  drawSwatches();
  if (editor) {
    scene.update(editor.nodes, editor.layout); // the same places, new colours: nothing moves
    markNodes();
  }
});
// A stage that changes size a little (a window resized) keeps the middle of
// the map in the middle; one that changes a lot (a phone turned) shows the
// whole map again.
let stageSize = null;
new ResizeObserver(() => {
  const size = { width: stage.clientWidth, height: stage.clientHeight };
  if (editor && editor.layout && stageSize && stageSize.width && size.width) {
    const ratio = Math.max(size.width / stageSize.width, stageSize.width / size.width);
    if (ratio > 1.25) {
      fit(false);
    } else {
      editor.view.x += (size.width - stageSize.width) / 2;
      editor.view.y += (size.height - stageSize.height) / 2;
      applyView();
    }
  }
  stageSize = size;
}).observe(stage);

listLoaded = loadList();
window.addEventListener("hashchange", route);
route();
