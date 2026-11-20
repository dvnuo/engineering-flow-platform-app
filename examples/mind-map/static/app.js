// The page: the list of maps, and the editor of one map.
//
// The address after "#" says which of the two is shown ("#12/0a1b2c3d4e5f" is
// the map 0a1b2c3d4e5f of member 12), so a map has a link and the back button
// works. Nothing is kept in the browser's storage and nothing asks the server
// on a timer: the lists are loaded when the page opens and on "Refresh", a
// map when it is opened, and a save goes out when the visitor saves.

import * as api from "./api.js";
import { exportMarkdown, exportPng, exportSvg } from "./exporting.js";
import { fitView, layoutTree, neighbour, zoomAt } from "./layout.js";
import { PALETTE, draw, measureText } from "./render.js";
import * as tree from "./tree.js";

const $ = (id) => document.getElementById(id);
const listView = $("list-view");
const editorView = $("editor-view");
const listStatus = $("list-status");
const stage = $("stage");
const canvas = $("canvas");
const viewport = $("viewport");
const nodeEditor = $("node-editor");
const titleInput = $("title");
const saveStatus = $("save-status");
const hintLine = $("hint");
const notice = $("notice");
const dialog = $("dialog");

const ARROWS = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
const HINT_EDIT = "Tab adds a child, Enter a sibling, F2 renames. Drag the background to move around.";
const HINT_LOOK = "Drag the background to move around; Ctrl + wheel or the buttons zoom.";

let session = { signedIn: false, limits: { maps: 30, nodes: 500, title: 80, text: 200 } };
let editor = null; // the open map, see openEditor()

function say(target, text, isProblem) {
  target.textContent = text || "";
  target.classList.toggle("problem", Boolean(isProblem));
}

function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

function day(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { dateStyle: "medium" });
}

function button(label, onClick, className) {
  const made = document.createElement("button");
  made.type = "button";
  made.textContent = label;
  made.className = className || "";
  made.addEventListener("click", onClick);
  return made;
}

// --- A question in a dialog: a new title, or "are you sure" -----------------------

function ask({ title, text, value, ok, danger }) {
  const input = $("dialog-input");
  const wantsText = value !== undefined;
  $("dialog-title").textContent = title;
  $("dialog-text").textContent = text || "";
  $("dialog-text").hidden = !text;
  input.hidden = !wantsText;
  input.value = wantsText ? value : "";
  $("dialog-ok").textContent = ok || "OK";
  $("dialog-ok").classList.toggle("danger", Boolean(danger));
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

function mapCard(found, own) {
  const item = document.createElement("li");
  item.className = "card";
  const link = document.createElement("a");
  link.className = "card-title";
  link.href = "#" + found.owner.id + "/" + found.id;
  link.textContent = found.title;
  const facts = document.createElement("p");
  facts.className = "muted";
  facts.textContent =
    (own ? "" : "By " + found.owner.name + ". ") + plural(found.nodes, "node") + ", changed " + day(found.updated_at) + ".";
  item.append(link, facts);
  if (!own) {
    return item;
  }
  if (found.shared) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = "Visible to everyone";
    facts.append(" ", badge);
  }
  const change = async (work) => {
    try {
      await work();
      await loadList();
    } catch (error) {
      await loadList();
      say(listStatus, error.message, true);
    }
  };
  const actions = document.createElement("div");
  actions.className = "group";
  actions.append(
    button("Rename", async () => {
      const title = await ask({ title: "Rename the map", value: found.title, ok: "Rename" });
      if (title && title !== found.title) {
        change(() => api.changeMap(found.id, { title, version: found.version }));
      }
    }),
    button(found.shared ? "Stop sharing" : "Share", () =>
      change(() => api.changeMap(found.id, { shared: !found.shared, version: found.version }))
    ),
    button(
      "Delete",
      async () => {
        const sure = await ask({
          title: "Delete this map?",
          text: '"' + found.title + '" with its ' + plural(found.nodes, "node") + " will be gone for good.",
          ok: "Delete",
          danger: true,
        });
        if (sure) {
          change(() => api.deleteMap(found.id));
        }
      },
      "danger"
    )
  );
  item.append(actions);
  return item;
}

async function loadList(message, isProblem) {
  say(listStatus, message || "Loading...", isProblem);
  let found;
  try {
    found = await api.listMaps();
  } catch (error) {
    say(listStatus, error.message, true);
    return;
  }
  session = { signedIn: found.signed_in, limits: found.limits };
  $("who").textContent = found.signed_in
    ? "Signed in as " + found.me.name + ". A map is yours alone until you share it."
    : "You are not signed in: you can look at shared maps, not draw your own.";
  $("new-form").hidden = !found.signed_in;
  $("mine-section").hidden = !found.signed_in;
  $("mine").replaceChildren(...found.mine.map((item) => mapCard(item, true)));
  $("mine-empty").hidden = found.mine.length > 0;
  $("mine-count").textContent = found.mine.length + " of " + found.limits.maps;
  $("shared").replaceChildren(...found.shared.map((item) => mapCard(item, false)));
  $("shared-empty").hidden = found.shared.length > 0;
  say(listStatus, message || "", isProblem);
}

$("new-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = $("new-title").value.trim();
  if (!title) {
    return;
  }
  try {
    const made = await api.createMap(title);
    $("new-title").value = "";
    location.hash = "#" + made.owner.id + "/" + made.id;
  } catch (error) {
    say(listStatus, error.message, true);
  }
});

$("refresh").addEventListener("click", () => loadList());

// --- The editor: state -----------------------------------------------------------------

function openEditor(found) {
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
      "A map of " + found.owner.name + ". You can look around and export it.",
      session.signedIn ? [button("Make a copy of my own", copyAsMine)] : []
    );
  }
  listView.hidden = true;
  editorView.hidden = false;
  document.title = found.title + " - Mind map";
  relayout();
  fit();
  select(tree.rootOf(editor.nodes).id);
  hint("");
  showSaveState();
  canvas.focus({ preventScroll: true });
}

function showNotice(text, actions) {
  $("notice-text").textContent = text;
  $("notice-actions").replaceChildren(...actions);
  notice.hidden = false;
}

function hint(text, isProblem) {
  say(hintLine, text || (editor && editor.editable ? HINT_EDIT : HINT_LOOK), isProblem);
}

function showSaveState(problem) {
  if (!editor.editable) {
    say(saveStatus, "Read only");
  } else if (problem) {
    say(saveStatus, problem, true);
  } else {
    say(saveStatus, editor.saving ? "Saving..." : editor.dirty ? "Not saved yet" : "Saved");
  }
}

function applyView() {
  const { x, y, k } = editor.view;
  viewport.setAttribute("transform", "translate(" + x + " " + y + ") scale(" + k + ")");
  placeNodeEditor();
}

// Lay the map out again and draw it. The centre keeps its place on the
// screen: without that, the whole map would jump whenever a branch grows.
function relayout() {
  const rootId = tree.rootOf(editor.nodes).id;
  const before = editor.layout && editor.layout.boxes.get(rootId);
  editor.layout = layoutTree(editor.nodes, measureText);
  if (before) {
    editor.view.y += (before.y - editor.layout.boxes.get(rootId).y) * editor.view.k;
  }
  draw(viewport, editor.nodes, editor.layout);
  if (editor.selected && !editor.layout.boxes.has(editor.selected)) {
    editor.selected = null; // it was removed, or folded away
  }
  markNodes();
  applyView();
  updateTools();
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
  $("toggle-node").disabled = !node || !editor.layout.boxes.get(node.id).hasChildren;
  $("toggle-node").textContent = node && node.collapsed ? "Unfold" : "Fold";
  $("undo").disabled = editor.past.length === 0;
  $("redo").disabled = editor.future.length === 0;
  for (const swatch of $("colors").children) {
    swatch.disabled = !node;
    swatch.setAttribute("aria-pressed", String(Boolean(node) && (node.color || "") === swatch.dataset.color));
  }
}

// Move the view just far enough for a node to be on the stage.
function reveal(id) {
  const box = editor.layout.boxes.get(id);
  if (!box) {
    return;
  }
  const { x, y, k } = editor.view;
  const margin = 24;
  const left = x + box.x * k;
  const top = y + box.y * k;
  const right = left + box.width * k;
  const bottom = top + box.height * k;
  if (left < margin) {
    editor.view.x += margin - left;
  } else if (right > stage.clientWidth - margin) {
    editor.view.x -= right - (stage.clientWidth - margin);
  }
  if (top < margin) {
    editor.view.y += margin - top;
  } else if (bottom > stage.clientHeight - margin) {
    editor.view.y -= bottom - (stage.clientHeight - margin);
  }
  applyView();
}

function select(id) {
  editor.selected = id && editor.layout.boxes.has(id) ? id : null;
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

function fit() {
  editor.view = fitView(editor.layout, stage.clientWidth, stage.clientHeight);
  applyView();
}

function zoomBy(factor) {
  editor.view = zoomAt(editor.view, factor, stage.clientWidth / 2, stage.clientHeight / 2);
  applyView();
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
  hint("A map has at most " + session.limits.nodes + " nodes.", true);
  return false;
}

function add(make) {
  finishEditing(true);
  if (!editor.editable || !editor.selected || !roomForOneMore()) {
    return;
  }
  const made = make(editor.nodes, editor.selected, "");
  if (made.id) {
    change(made.nodes, made.id);
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
  }
}

function startMove() {
  const node = editor.selected && tree.find(editor.nodes, editor.selected);
  if (!editor.editable || !node || node.parent === null) {
    return;
  }
  editor.moving = node.id;
  markNodes();
  hint("Choose the new parent: click it, or walk there with the arrow keys and press Enter. Esc cancels.");
}

function endMove(targetId) {
  const id = editor.moving;
  editor.moving = null;
  if (!targetId || targetId === id) {
    hint("");
    select(id);
    return;
  }
  const reason = tree.moveProblem(editor.nodes, id, targetId);
  if (reason) {
    markNodes();
    select(id);
    hint(reason, true);
    return;
  }
  hint("");
  change(tree.moveNode(editor.nodes, id, targetId), id);
}

// --- The editor: typing the text of a node -------------------------------------------------

function placeNodeEditor() {
  const box = editor.editing && editor.layout.boxes.get(editor.editing.id);
  if (!box) {
    return;
  }
  const { x, y, k } = editor.view;
  nodeEditor.style.left = x + box.x * k + "px";
  nodeEditor.style.top = y + box.y * k + "px";
  nodeEditor.style.width = Math.max(180, box.width * k) + "px";
  nodeEditor.style.height = Math.max(32, box.height * k) + "px";
  nodeEditor.style.fontSize = Math.max(13, 14 * k) + "px";
}

function startEditing(id, created) {
  const node = tree.find(editor.nodes, id);
  if (!editor.editable || !node || !editor.layout.boxes.has(id)) {
    return;
  }
  editor.editing = { id, created: Boolean(created) };
  nodeEditor.value = node.text;
  nodeEditor.hidden = false;
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
    // A new node that was given no text is taken back.
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
    const target = nodeAt(event);
    markNodes(target && !tree.moveProblem(editor.nodes, gesture.id, target) ? target : null);
  }
});

function pointerGone(event) {
  if (!pointers.delete(event.pointerId)) {
    return;
  }
  if (gesture && gesture.kind === "node" && gesture.dragging) {
    canvas.classList.remove("dragging");
    endMove(event.type === "pointerup" ? nodeAt(event) : null);
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
    save();
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
onClick("fit", fit);
onClick("save", () => save());

for (const color of ["", ...tree.COLORS]) {
  const swatch = document.createElement("button");
  swatch.type = "button";
  swatch.className = "swatch";
  swatch.dataset.color = color;
  swatch.title = color ? color.charAt(0).toUpperCase() + color.slice(1) : "The colour of the branch above";
  swatch.setAttribute("aria-label", swatch.title);
  swatch.setAttribute("aria-pressed", "false");
  if (color) {
    swatch.style.background = PALETTE[color].fill;
    swatch.style.borderColor = PALETTE[color].line;
  }
  swatch.addEventListener("click", () => {
    if (editor.editable && editor.selected) {
      change(tree.setColor(editor.nodes, editor.selected, color));
    }
    canvas.focus({ preventScroll: true });
  });
  $("colors").append(swatch);
}

function exporter(id, work) {
  $(id).addEventListener("click", async () => {
    $("export-menu").open = false;
    try {
      await work(editor.nodes, titleInput.value.trim() || editor.map.title);
    } catch (error) {
      hint(error.message, true);
    }
  });
}

exporter("export-svg", exportSvg);
exporter("export-png", exportPng);
exporter("export-md", exportMarkdown);

// The two menus are <details> elements: the browser opens and closes them
// with the mouse and the keyboard. A click elsewhere or Esc closes them too.
function closeMenus(except) {
  for (const menu of document.querySelectorAll("details.menu[open]")) {
    if (menu !== except) {
      menu.open = false;
    }
  }
}
document.addEventListener("click", (event) => closeMenus(event.target.closest ? event.target.closest("details.menu") : null));
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
    save();
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
  let problem = null;
  try {
    const saved = await api.saveMap(open.map.id, sent, whileLeaving);
    open.map.version = saved.version;
    open.map.title = saved.title;
    if (open.nodes === sent.nodes && open.map.shared === sent.shared) {
      open.dirty = false; // nothing was changed while the request was under way
    }
  } catch (error) {
    problem = error;
    if (error.code === "stale" && open === editor) {
      showNotice(error.message, conflictChoices());
    }
  }
  open.saving = false;
  if (open === editor) {
    showSaveState(problem && problem.message);
  }
  return problem;
}

// What can be done about a map that was changed elsewhere: take the stored
// one, or keep this one under a new name. Neither loses work silently.
function conflictChoices() {
  return [button("Reload the saved map", reloadMap), button("Keep mine as a copy", copyAsMine)];
}

async function reloadMap() {
  const { owner, id } = editor.map;
  try {
    openEditor(await api.readMap(owner.id, id));
  } catch (error) {
    hint(error.message, true);
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
  } catch (error) {
    hint(error.message, true);
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

async function route() {
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
      const leave = button("Leave without saving", () => {
        editor.dirty = false;
        location.hash = wantedNext || "#";
      });
      showNotice(
        "Your changes are not saved. " + failed.message,
        failed.code === "stale" ? [...conflictChoices(), leave] : [button("Try again", () => save()), leave]
      );
      return;
    }
    editor = null;
  }
  let problem = "";
  const wanted = /^#(-?\d+)\/([0-9a-f]{12})$/.exec(location.hash);
  if (wanted) {
    try {
      openEditor(await api.readMap(wanted[1], wanted[2]));
      return;
    } catch (error) {
      problem = error.message;
      history.replaceState(null, "", "#"); // the address names a map that cannot be shown
    }
  }
  editorView.hidden = true;
  listView.hidden = false;
  document.title = "Mind map";
  loadList(problem, Boolean(problem));
}

window.addEventListener("hashchange", route);
route();
