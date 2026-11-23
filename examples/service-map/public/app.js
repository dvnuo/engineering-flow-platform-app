// A static app: this page draws whatever services.json says. To change the
// map, change that file, push and press Deploy; there is no backend to
// restart and nothing to migrate.
//
// Every URL is relative ("services.json", "./lib/graph.js"): the app is
// served under /{slug}/ once deployed. The one exception is the EFP kit, at
// /_efp/lib/, which every gateway serves to every app (docs/UI_KIT.md).
//
// The thinking is in lib/ (graph.js: checking the file, reachability,
// cycles; layout.js: the forces; view.js: pan and zoom), free of the DOM and
// tested with node. This file draws, listens and moves the camera.
//
// Motion here always says something. The map grows out of a spiral into
// its levels (that is the simulation itself, entry points on top), a hover
// lights up the first ring around a service, a choice sends out a ripple
// and runs particles along its arrows in the direction of the dependency,
// and a search flies the camera to what was found. With reduced motion the
// layout is worked out at once and every move jumps to its end.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import { buildIndex, cycleEdges, findCycles, levels, matches, neighbourhood, validate } from "./lib/graph.js";
import { bounds, createLayout, reheat, settle, step } from "./lib/layout.js";
import { clampZoom, fitView, pan, pinch, toMap, zoomAt } from "./lib/view.js";

const { h } = kit;
const SVG = "http://www.w3.org/2000/svg"; // a name, not an address: nothing is fetched from it
const RADIUS = { 1: 20, 2: 16, 3: 13 };
const HEAD = 8; // length of an arrow head
const TEAM_COLOURS = 8;
const DRAG_THRESHOLD = 4; // pixels a pointer may wander and still count as a click
const ARROW = " \u2192 ";
const DOT = " \u00b7 ";
const MAX_RESULTS = 8;
const WIDE = window.matchMedia("(min-width: 64rem)"); // from here on the panel lies over the map

const byId = (id) => document.getElementById(id);
const svg = byId("map");
const viewport = byId("viewport");
const edgeLayer = byId("edges");
const nodeLayer = byId("nodes");
const canvas = byId("canvas");
const panel = byId("panel");
const summary = byId("summary");
const status = byId("status");
const search = byId("search");
const results = byId("results");
const teamSelect = byId("team");
const tierSelect = byId("tier");

let services = [];
let byName = new Map(); // name -> service
let numberOf = new Map(); // name -> its position in ``services``
let index = null;
let teams = [];
let cycles = [];
let layout = null;
let nodes = []; // {group, body} of each service, in the order of ``services``
let edges = []; // {from, to, group, line, flow, head, cycle}
let view = { x: 0, y: 0, k: 1 };
let chosen = ""; // the name of the chosen service
let shownCycle = null; // or the cycle picked from the list
let hovered = ""; // the service under the pointer or the keyboard focus: a preview
let tookOver = false; // the visitor moved the view: stop fitting it to the layout
let running = false;
let flight = null; // the camera move under way; a new one or a drag ends it
let hits = []; // the services the search lists
let activeResult = 0;

function shape(tag, attributes = {}, ...children) {
  const node = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) {
    node.setAttribute(name, value);
  }
  node.append(...children);
  return node;
}

// Only web addresses become links; anything else in the data file is shown as text.
function webAddress(url) {
  try {
    const parsed = new URL(url, document.baseURI);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch (error) {
    return null;
  }
}

const plural = (count, one, many = one + "s") => count + " " + (count === 1 ? one : many);
// A team's colour is one of eight turns of the theme's primary colour (style.css), so a new theme recolours the teams too.
const teamColour = (team) => "var(--team-" + (teams.indexOf(team) % TEAM_COLOURS) + ")";
const dot = (team) => h("span.sm-dot", { style: { "--team": teamColour(team) } });

// --- Drawing ------------------------------------------------------------------------------------

function build() {
  const onCycle = cycleEdges(cycles);
  const level = levels(index);
  edges = [];
  for (const service of services) {
    for (const target of service.depends_on) {
      const parts = { line: shape("path", { class: "line" }), flow: shape("path", { class: "flow" }), head: shape("path", { class: "head" }) };
      edges.push({
        from: numberOf.get(service.name),
        to: numberOf.get(target),
        group: shape("g", { style: "--level: " + level.get(service.name) }, parts.line, parts.flow, parts.head),
        ...parts,
        cycle: onCycle.has(service.name + ">" + target),
      });
    }
  }
  edgeLayer.replaceChildren(...edges.map((edge) => edge.group));
  nodes = services.map((service, position) => {
    const radius = RADIUS[service.tier];
    const body = shape(
      "g",
      { class: "body" },
      shape("circle", { class: "halo", r: radius + 6 }),
      shape("circle", { class: "disc", r: radius }),
      shape("text", { class: "tier", dy: "0.35em" }, String(service.tier)),
      shape("text", { class: "name", y: radius + 16 }, service.name),
    );
    const group = shape(
      "g",
      {
        tabindex: "0",
        role: "button",
        "data-index": position,
        "aria-label": service.name + ", team " + service.team + ", tier " + service.tier,
        // The level staggers the entrance from the top down: the map grows the way it is read.
        style: "--team: " + teamColour(service.team) + "; --level: " + level.get(service.name),
      },
      shape("title", {}, service.description || service.name),
      body,
    );
    return { group, body };
  });
  nodeLayer.replaceChildren(...nodes.map((node) => node.group));
  layout = createLayout(
    services.length,
    edges.map((edge) => [edge.from, edge.to]),
    { levels: services.map((service) => level.get(service.name)) },
  );
}

const fixed = (number) => number.toFixed(1);

// Put every node and arrow where the layout has it.
function draw() {
  layout.nodes.forEach((node, position) => {
    nodes[position].group.setAttribute("transform", "translate(" + fixed(node.x) + " " + fixed(node.y) + ")");
  });
  for (const edge of edges) {
    const a = layout.nodes[edge.from];
    const b = layout.nodes[edge.to];
    const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    // A slight bend to the left of the way the arrow points: two services
    // that need each other get two arrows side by side instead of one line.
    const bend = Math.min(24, length * 0.11);
    const cx = (a.x + b.x) / 2 + ((a.y - b.y) / length) * bend;
    const cy = (a.y + b.y) / 2 + ((b.x - a.x) / length) * bend;
    const out = Math.hypot(cx - a.x, cy - a.y) || 1;
    const into = Math.hypot(b.x - cx, b.y - cy) || 1;
    const ox = (cx - a.x) / out;
    const oy = (cy - a.y) / out;
    const ix = (b.x - cx) / into;
    const iy = (b.y - cy) / into;
    // From the rim of one circle to the rim of the other, the head touching the dependency.
    const start = RADIUS[services[edge.from].tier] + 3;
    const end = RADIUS[services[edge.to].tier] + 4;
    const tipX = b.x - ix * end;
    const tipY = b.y - iy * end;
    const baseX = tipX - ix * HEAD;
    const baseY = tipY - iy * HEAD;
    const path = "M" + fixed(a.x + ox * start) + " " + fixed(a.y + oy * start) + " Q" + fixed(cx) + " " + fixed(cy) + " " + fixed(baseX) + " " + fixed(baseY);
    edge.line.setAttribute("d", path);
    edge.flow.setAttribute("d", path);
    const wing = HEAD * 0.55;
    const corner = (side) => fixed(baseX - iy * wing * side) + " " + fixed(baseY + ix * wing * side);
    edge.head.setAttribute("d", "M" + fixed(tipX) + " " + fixed(tipY) + " L" + corner(1) + " L" + corner(-1) + " Z");
  }
}

function applyView() {
  viewport.setAttribute("transform", "translate(" + fixed(view.x) + " " + fixed(view.y) + ") scale(" + view.k.toFixed(4) + ")");
  // The dots behind the map move and grow with it but keep their density: a sense of space, not a pattern.
  let grid = 28 * view.k;
  while (grid < 18) grid *= 2;
  while (grid > 56) grid /= 2;
  svg.style.setProperty("--grid", grid.toFixed(2) + "px");
  svg.style.setProperty("--grid-x", fixed(view.x % grid) + "px");
  svg.style.setProperty("--grid-y", fixed(view.y % grid) + "px");
}

const panelOpen = () => Boolean(chosen || shownCycle);

// The part of the map the visitor can see: on a wide screen the open panel covers its right side.
function area() {
  const box = svg.getBoundingClientRect();
  const covered = WIDE.matches && panelOpen() ? panel.offsetWidth + 24 : 0;
  return { width: Math.max(200, (box.width || 800) - covered), height: box.height || 500 };
}

function shownFlags() {
  const filters = { team: teamSelect.value, tier: Number(tierSelect.value) };
  return services.map((service) => matches(service, filters));
}

function framing(shown, maxZoom) {
  const { width, height } = area();
  return fitView(bounds(layout, shown.length ? shown : null), width, height, 32, maxZoom);
}

function fit({ fly = false } = {}) {
  const flags = shownFlags();
  const target = framing(services.map((_, position) => position).filter((position) => flags[position]));
  if (fly) {
    flyTo(target);
  } else {
    flight = null;
    view = target;
    applyView();
  }
}

// The camera glides to ``target``: the middle of the screen travels in a
// straight line, the zoom changes evenly, and a long way out pulls back a
// little in between, so the visitor sees where it goes.
function flyTo(target, duration = 720) {
  if (kit.reducedMotion()) {
    flight = null;
    view = target;
    applyView();
    return;
  }
  const from = view;
  const { width, height } = area();
  const start = toMap(from, width / 2, height / 2);
  const end = toMap(target, width / 2, height / 2);
  const dip = Math.min(0.3, (Math.hypot(end.x - start.x, end.y - start.y) * Math.min(from.k, target.k)) / 2400);
  const began = performance.now();
  const mine = (flight = { target });
  const tick = (now) => {
    if (flight !== mine) return;
    const t = Math.min(1, (now - began) / duration);
    const eased = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    const k = Math.exp(Math.log(from.k) + (Math.log(target.k) - Math.log(from.k)) * eased) * (1 - dip * Math.sin(Math.PI * t));
    const x = start.x + (end.x - start.x) * eased;
    const y = start.y + (end.y - start.y) * eased;
    view = { x: width / 2 - x * k, y: height / 2 - y * k, k };
    applyView();
    if (t < 1) requestAnimationFrame(tick);
    else flight = null;
  };
  requestAnimationFrame(tick);
}

function focusOn(name) {
  const node = layout.nodes[numberOf.get(name)];
  const { width, height } = area();
  const k = clampZoom(Math.min(1.3, Math.max(view.k, 1)));
  tookOver = true;
  flyTo({ x: width / 2 - node.x * k, y: height / 2 - node.y * k, k });
}

// The chosen service needs the camera when it is out of sight, or too small to read (a whole map on a phone).
const needsCamera = (name) => !inSight(name) || view.k < 0.7;

function inSight(name) {
  const node = layout.nodes[numberOf.get(name)];
  const { width, height } = area();
  const x = view.x + node.x * view.k;
  const y = view.y + node.y * view.k;
  return x > 48 && x < width - 48 && y > 40 && y < height - 72;
}

function zoomBy(factor) {
  const { width, height } = area();
  tookOver = true;
  flyTo(zoomAt(flight ? flight.target || view : view, factor, width / 2, height / 2), 260);
}

// Classes say what a node or an arrow is to the service in focus; the style
// sheet turns them into colours AND line styles, so colour is never the only sign.
function paint() {
  const flags = shownFlags();
  const query = search.value.trim().toLowerCase();
  const found = services.map((service, position) => flags[position] && query !== "" && matches(service, { query }));
  const focus = hovered || chosen;
  const peek = hovered !== "" && hovered !== chosen; // a hover shows the first ring around a service, a choice all of it
  const role = new Map();
  let needs = new Set();
  let users = new Set();
  let cycleKeys = new Set();
  if (focus) {
    const near = neighbourhood(index, focus);
    const far = (names) => (peek ? [] : names);
    needs = new Set([...near.dependencies.direct, ...far(near.dependencies.transitive)]);
    users = new Set([...near.dependents.direct, ...far(near.dependents.transitive)]);
    far(near.dependents.transitive).forEach((name) => role.set(name, "users far"));
    near.dependents.direct.forEach((name) => role.set(name, "users direct"));
    far(near.dependencies.transitive).forEach((name) => role.set(name, "needs far"));
    near.dependencies.direct.forEach((name) => role.set(name, "needs direct"));
    role.set(focus, "is-focus");
  } else if (shownCycle) {
    shownCycle.forEach((name) => role.set(name, "in-cycle"));
    cycleKeys = cycleEdges([shownCycle]);
  }
  const focused = focus !== "" || shownCycle !== null;

  services.forEach((service, position) => {
    const classes = ["node"];
    if (!flags[position]) classes.push("is-hidden");
    if (focused) classes.push(role.get(service.name) || "is-dimmed");
    else if (query && !found[position]) classes.push("is-dimmed");
    if (found[position]) classes.push("is-match");
    if (service.name === chosen) classes.push("is-chosen");
    nodes[position].group.setAttribute("class", classes.join(" "));
    nodes[position].group.setAttribute("aria-pressed", String(service.name === chosen));
  });

  for (const edge of edges) {
    const from = services[edge.from].name;
    const to = services[edge.to].name;
    const classes = ["edge"];
    if (edge.cycle) classes.push("cycle");
    if (!flags[edge.from] || !flags[edge.to]) classes.push("is-hidden");
    if (focus) {
      // An arrow belongs to "needs" when it starts at the service in focus or at something it needs.
      if (from === focus) classes.push("needs direct");
      else if (to === focus) classes.push("users direct");
      else if (!peek && needs.has(from)) classes.push("needs far");
      else if (!peek && users.has(to)) classes.push("users far");
      else classes.push("is-dimmed");
    } else if (shownCycle) {
      classes.push(cycleKeys.has(from + ">" + to) ? "in-cycle" : "is-dimmed");
    }
    edge.group.setAttribute("class", classes.join(" "));
  }

  const shown = flags.filter(Boolean).length;
  const matched = found.filter(Boolean).length;
  byId("empty").hidden = shown > 0 || !services.length;
  byId("shown").hidden = shown === services.length;
  byId("shown").textContent = shown + " of " + services.length + " shown";
  status.textContent = query ? (matched ? plural(matched, "service matches", "services match") + ' "' + query + '".' : 'No service is named like "' + query + '".') : "";
}

function peekAt(name) {
  if (hovered !== name) {
    hovered = name;
    paint();
  }
}

// A ring that runs out of the chosen service once: "this one".
function ripple(name) {
  if (kit.reducedMotion()) return;
  const ring = shape("circle", { class: "ripple", r: RADIUS[byName.get(name).tier] + 3 });
  nodes[numberOf.get(name)].body.prepend(ring);
  const gone = () => ring.remove();
  ring.animate([{ transform: "scale(0.9)", opacity: 0.75 }, { transform: "scale(2.6)", opacity: 0 }], { duration: 950, easing: kit.EASE }).finished.then(gone, gone);
}

// --- The side panel -----------------------------------------------------------------------------

// A service as a button: it chooses the service, and a hover shows where it is on the map.
function chip(name, far) {
  return h(
    "li",
    {},
    h(
      "button",
      {
        type: "button",
        class: "sm-chip" + (far ? " is-far" : ""),
        onclick: () => choose(name, { fly: true }),
        onpointerenter: () => peekAt(name),
        onpointerleave: () => peekAt(""),
        onfocus: () => peekAt(name),
        onblur: () => peekAt(""),
      },
      dot(byName.get(name).team),
      name,
    ),
  );
}

function counted(value) {
  const number = h("dd", { class: "efp-number" }, "0");
  kit.countUp(number, value, { from: 0, duration: 800 });
  return number;
}

function panelHead(title, meta, colour, icon) {
  return h(
    "div.sm-panel-head",
    {},
    h("span.sm-panel-mark", { style: { "--team": colour } }, kit.icon(icon, "size-4")),
    h("div.min-w-0.grow", {}, h("h2.sm-panel-title", { text: title, tabindex: "-1" }), h("p", { class: "text-xs text-base-content/60", text: meta })),
    h("button", { type: "button", class: "btn btn-ghost btn-sm btn-square", "aria-label": "Close the details", title: "Close (Esc)", onclick: () => choose("") }, kit.icon("x", "size-4")),
  );
}

function relatives(title, found, note, kind) {
  const total = found.direct.length + found.transitive.length;
  const block = h("section", { class: "sm-rel " + kind }, h("h3", {}, h("span.sm-rel-bar"), title, h("span", { class: "badge badge-sm badge-ghost efp-number", text: String(total) })));
  if (!total) {
    block.append(h("p", { class: "text-sm text-base-content/55", text: kind === "needs" ? "Nothing: it stands on its own." : "Nothing depends on it." }));
    return block;
  }
  block.append(h("p", { class: "text-xs text-base-content/60", text: note }));
  const lists = [
    ["Directly", found.direct, false],
    ["Through others", found.transitive, true],
  ];
  for (const [label, names, far] of lists) {
    if (names.length) {
      block.append(h("h4", { text: label + " (" + names.length + ")" }), h("ul.sm-chips", {}, names.map((name) => chip(name, far))));
    }
  }
  return block;
}

function serviceDetails(service) {
  const near = neighbourhood(index, service.name);
  const links = service.links.map((link) => {
    const address = webAddress(link.url);
    if (!address) return h("li", {}, h("span", { class: "badge badge-ghost", text: link.title }));
    const item = h("a", { class: "btn btn-xs btn-soft efp-press", target: "_blank" }, kit.icon("external-link", "size-3.5"), link.title);
    item.href = address;
    item.rel = "noopener noreferrer";
    return h("li", {}, item);
  });
  return [
    panelHead(service.name, service.team + DOT + "tier " + service.tier, teamColour(service.team), "box"),
    h("p", { class: "text-sm text-base-content/75", text: service.description || "No description in the data file." }),
    links.length ? h("ul.sm-links", {}, links) : null,
    ...cycles
      .filter((cycle) => cycle.includes(service.name))
      .map((cycle) => h("div", { class: "alert alert-soft alert-error text-sm py-2" }, kit.icon("refresh-ccw", "size-4"), h("span", { text: "In a dependency cycle: " + cycle.concat(cycle[0]).join(ARROW) }))),
    h(
      "dl.sm-counts",
      {},
      h("div.needs", {}, h("dt", { text: "Needs" }), counted(near.dependencies.direct.length + near.dependencies.transitive.length)),
      h("div.users", {}, h("dt", { text: "Used by" }), counted(near.dependents.direct.length + near.dependents.transitive.length)),
    ),
    relatives("Depends on", near.dependencies, "What it needs to work.", "needs"),
    relatives("Used by", near.dependents, "What is affected when it fails.", "users"),
  ].filter(Boolean);
}

function cycleDetails(cycle) {
  const loop = [];
  cycle.concat(cycle[0]).forEach((name, position) => {
    if (position) loop.push(h("li.sm-loop-arrow", { "aria-hidden": "true" }, kit.icon("arrow-right", "size-3.5")));
    loop.push(chip(name, false));
  });
  return [
    panelHead("A dependency cycle", plural(cycle.length, "service") + " that need each other", "var(--color-error)", "refresh-ccw"),
    h("ul.sm-chips.sm-loop", { "aria-label": "The cycle, in the direction of its dependencies" }, loop),
    h("p", { class: "text-sm text-base-content/75", text: "Each of these needs the next one, and the last needs the first. None of them can start, be deployed or recover on its own: break the cycle where the dependency is weakest." }),
  ];
}

function describe() {
  const open = panelOpen();
  const opening = open && !canvas.hasAttribute("data-panel");
  canvas.toggleAttribute("data-panel", open);
  panel.inert = !open;
  if (!open) return; // the content stays while the panel slides away
  const hadFocus = panel.contains(document.activeElement);
  panel.replaceChildren(...(chosen ? serviceDetails(byName.get(chosen)) : cycleDetails(shownCycle)));
  panel.scrollTop = 0;
  kit.rise(panel.children, { stagger: 35, distance: opening ? 12 : 6, duration: opening ? 620 : 380 });
  // A keyboard that pressed a service in the panel lands on the new title, not at the top of the page.
  if (hadFocus) panel.querySelector("h2").focus({ preventScroll: true });
}

// Choose a service ("" for none). The choice is kept in the address (#checkout), so it can be linked.
function choose(name, { fromAddress = false, fly = false } = {}) {
  chosen = byName.has(name) ? name : "";
  shownCycle = null;
  hovered = "";
  if (!fromAddress) {
    history.replaceState(null, "", chosen ? "#" + chosen : location.pathname + location.search);
  }
  paint();
  describe();
  if (!chosen || !layout) return;
  ripple(chosen);
  if (!fly && (fromAddress || running)) return; // a layout that still moves brings it into view when it settles
  if (fly || needsCamera(chosen)) focusOn(chosen);
}

function showCycle(cycle) {
  chosen = "";
  hovered = "";
  shownCycle = cycle;
  history.replaceState(null, "", location.pathname + location.search);
  paint();
  describe();
  tookOver = true;
  flyTo(framing(cycle.map((name) => numberOf.get(name)), 1.4));
  canvas.scrollIntoView({ block: "nearest", behavior: kit.reducedMotion() ? "auto" : "smooth" });
}

// --- Animation ----------------------------------------------------------------------------------

function settled() {
  running = false; // no frame is requested until something moves again
  if (chosen && needsCamera(chosen)) focusOn(chosen);
}

function frame() {
  const moving = step(layout);
  draw();
  if (!tookOver && !flight) fit();
  if (moving) {
    requestAnimationFrame(frame);
  } else {
    settled();
  }
}

function animate() {
  if (kit.reducedMotion()) {
    settle(layout); // no animation: work the layout out and draw it once
    draw();
    if (!tookOver) fit();
    settled();
  } else if (!running) {
    running = true;
    requestAnimationFrame(frame);
  }
}

// Somebody who turns "reduce motion" on while the map still moves gets its end at once.
window.matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", () => layout && animate());

// --- Pointer, wheel and keys --------------------------------------------------------------------

const pointers = new Map(); // every finger or button that is down, by pointer id
let gesture = null;

function place(event) {
  const box = svg.getBoundingClientRect();
  return { x: event.clientX - box.left, y: event.clientY - box.top };
}

svg.addEventListener("pointerdown", (event) => {
  if (event.pointerType === "mouse" && event.button !== 0) {
    return; // the right button opens the browser's menu, as everywhere
  }
  flight = null; // the visitor takes the camera back
  const at = place(event);
  pointers.set(event.pointerId, at);
  svg.setPointerCapture(event.pointerId);
  if (pointers.size === 2) {
    gesture = { kind: "pinch", before: Array.from(pointers.values()) };
    return;
  }
  const node = event.target.closest ? event.target.closest(".node") : null;
  gesture = { kind: node ? "node" : "pan", position: node ? Number(node.getAttribute("data-index")) : -1, start: at, last: at, moved: false };
});

svg.addEventListener("pointermove", (event) => {
  if (!gesture || !pointers.has(event.pointerId)) {
    return;
  }
  const at = place(event);
  pointers.set(event.pointerId, at);
  if (gesture.kind === "pinch") {
    const after = Array.from(pointers.values());
    view = pinch(view, gesture.before, after);
    gesture.before = after;
    tookOver = true;
    applyView();
    return;
  }
  if (!gesture.moved && Math.hypot(at.x - gesture.start.x, at.y - gesture.start.y) < DRAG_THRESHOLD) {
    return;
  }
  gesture.moved = true;
  svg.classList.add("is-dragging");
  if (gesture.kind === "node") {
    // The node follows the pointer and stays where it is dropped; the others make room.
    const node = layout.nodes[gesture.position];
    const spot = toMap(view, at.x, at.y);
    node.x = spot.x;
    node.y = spot.y;
    node.fixed = true;
    tookOver = true;
    if (kit.reducedMotion()) {
      draw();
    } else {
      reheat(layout);
      animate();
    }
  } else {
    view = pan(view, at.x - gesture.last.x, at.y - gesture.last.y);
    tookOver = true;
    applyView();
  }
  gesture.last = at;
});

function release(event) {
  if (!pointers.delete(event.pointerId) || !gesture) {
    return;
  }
  svg.classList.remove("is-dragging");
  if (gesture.kind === "pinch") {
    // One finger is left: it goes on panning.
    const [rest] = Array.from(pointers.values());
    gesture = rest ? { kind: "pan", position: -1, start: rest, last: rest, moved: true } : null;
    return;
  }
  if (!gesture.moved && event.type === "pointerup") {
    choose(gesture.kind === "node" ? services[gesture.position].name : ""); // a click: on a service, or on the background
  }
  gesture = null;
}
svg.addEventListener("pointerup", release);
svg.addEventListener("pointercancel", release);

svg.addEventListener(
  "wheel",
  (event) => {
    event.preventDefault(); // the map zooms; the page does not scroll under the pointer
    flight = null;
    const at = place(event);
    const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
    view = zoomAt(view, Math.exp(-delta * 0.0015), at.x, at.y);
    tookOver = true;
    applyView();
  },
  { passive: false },
);

// A hover (or the keyboard focus) on a service previews its first ring. Not on touch: a finger has no hover.
const nodeAt = (target) => (target && target.closest ? target.closest(".node") : null);
const nameOf = (node) => services[Number(node.getAttribute("data-index"))].name;
nodeLayer.addEventListener("pointerover", (event) => {
  const node = nodeAt(event.target);
  if (!gesture && node && event.pointerType !== "touch") peekAt(nameOf(node));
});
nodeLayer.addEventListener("pointerout", (event) => {
  if (!gesture && !nodeAt(event.relatedTarget)) peekAt("");
});
nodeLayer.addEventListener("focusin", (event) => {
  const node = nodeAt(event.target);
  if (node) peekAt(nameOf(node));
});
nodeLayer.addEventListener("focusout", () => peekAt(""));

svg.addEventListener("keydown", (event) => {
  const node = nodeAt(event.target);
  if (node && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    choose(nameOf(node));
    return;
  }
  const moves = { ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40] };
  if (moves[event.key]) {
    event.preventDefault();
    flight = null;
    view = pan(view, ...moves[event.key]);
    tookOver = true;
    applyView();
  }
});

document.addEventListener("keydown", (event) => {
  const typing = event.target.closest && event.target.closest("input, select, textarea");
  if (event.key === "/" && !typing) {
    event.preventDefault();
    search.focus();
    search.select();
  } else if (event.key === "Escape" && (chosen || shownCycle)) {
    choose("");
  } else if (!typing && layout && !event.ctrlKey && !event.metaKey && !event.altKey) {
    if (event.key === "+" || event.key === "=") zoomBy(1.25);
    else if (event.key === "-") zoomBy(0.8);
    else if (event.key === "0") {
      tookOver = false;
      fit({ fly: true });
    }
  }
});

byId("zoom-in").addEventListener("click", () => zoomBy(1.25));
byId("zoom-out").addEventListener("click", () => zoomBy(0.8));
byId("fit").addEventListener("click", () => {
  tookOver = false;
  fit({ fly: true });
});
byId("relayout").addEventListener("click", () => {
  if (services.length) {
    build(); // new elements: the services pop in again, level by level
    tookOver = false;
    paint();
    draw();
    fit();
    animate();
  }
});
byId("show-all").addEventListener("click", () => {
  teamSelect.value = "";
  tierSelect.value = "0";
  refilter();
});

// A new filter is a new picture: the camera glides to what is left.
function refilter() {
  for (const button of byId("teams").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.team === teamSelect.value));
  }
  renderResults();
  paint();
  if (layout) {
    tookOver = false;
    fit({ fly: true });
  }
}
teamSelect.addEventListener("change", refilter);
tierSelect.addEventListener("change", refilter);

// --- The search: a list of matches; Enter or a click flies to one ----------------------------

function closeResults() {
  hits = [];
  results.hidden = true;
  search.setAttribute("aria-expanded", "false");
  search.removeAttribute("aria-activedescendant");
}

function markActive() {
  Array.from(results.children).forEach((item, position) => item.setAttribute("aria-selected", String(position === activeResult && hits.length > 0)));
  if (hits.length) search.setAttribute("aria-activedescendant", "result-" + activeResult);
}

function pick(name) {
  search.value = "";
  closeResults();
  choose(name, { fly: true });
}

function renderResults() {
  const query = search.value.trim().toLowerCase();
  if (!query || !services.length || document.activeElement !== search) {
    closeResults();
    return;
  }
  const flags = shownFlags();
  hits = services.filter((service, position) => flags[position] && matches(service, { query })).slice(0, MAX_RESULTS);
  activeResult = 0;
  const item = (service, position) => {
    const at = service.name.indexOf(query);
    return h(
      "li",
      {
        id: "result-" + position,
        role: "option",
        class: "sm-result",
        onpointerdown: (event) => event.preventDefault(), // keep the focus in the field
        onclick: () => pick(service.name),
        onpointermove: () => {
          activeResult = position;
          markActive();
        },
      },
      dot(service.team),
      h("span.truncate", {}, service.name.slice(0, at), h("mark", { text: service.name.slice(at, at + query.length) }), service.name.slice(at + query.length)),
      h("span", { class: "text-xs text-base-content/55 ml-auto shrink-0", text: service.team + DOT + "tier " + service.tier }),
    );
  };
  results.replaceChildren(...(hits.length ? hits.map(item) : [h("li", { class: "sm-noresult", text: 'No service is named like "' + query + '".' })]));
  results.hidden = false;
  search.setAttribute("aria-expanded", "true");
  markActive();
}

search.addEventListener("input", () => {
  renderResults();
  paint();
});
search.addEventListener("focus", renderResults);
search.addEventListener("blur", closeResults);
search.addEventListener("keydown", (event) => {
  if ((event.key === "ArrowDown" || event.key === "ArrowUp") && hits.length) {
    event.preventDefault();
    activeResult = (activeResult + (event.key === "ArrowDown" ? 1 : hits.length - 1)) % hits.length;
    markActive();
  } else if (event.key === "Enter" && hits[activeResult]) {
    event.preventDefault();
    pick(hits[activeResult].name);
  } else if (event.key === "Escape" && search.value) {
    event.preventDefault();
    event.stopPropagation(); // the first Escape empties the field, the next one clears the choice
    search.value = "";
    closeResults();
    paint();
  }
});

window.addEventListener("resize", () => {
  if (layout && !tookOver) fit();
});
window.addEventListener("hashchange", () => choose(decodeName(location.hash), { fromAddress: true, fly: true }));

function decodeName(hash) {
  try {
    return decodeURIComponent(hash.slice(1));
  } catch (error) {
    return "";
  }
}

// --- The legend and the numbers under the map ---------------------------------------------------

// On a phone the key under the map folds away, so the map and the panel come first.
const legend = byId("legend");
const NARROW = window.matchMedia("(max-width: 47.99rem)");
function foldLegend(folded) {
  legend.toggleAttribute("data-folded", folded);
  byId("legend-toggle").setAttribute("aria-expanded", String(!folded));
  byId("legend-body").inert = folded; // folded away, its buttons are out of the Tab order too
}
byId("legend-toggle").addEventListener("click", () => foldLegend(!legend.hasAttribute("data-folded")));
NARROW.addEventListener("change", () => foldLegend(NARROW.matches));
foldLegend(NARROW.matches);

function showTeams() {
  byId("teams").replaceChildren(
    ...teams.map((team) =>
      h(
        "li",
        {},
        h(
          "button",
          {
            type: "button",
            class: "sm-team",
            "aria-pressed": "false",
            dataset: { team },
            onclick: () => {
              teamSelect.value = teamSelect.value === team ? "" : team;
              refilter();
            },
          },
          dot(team),
          team,
        ),
      ),
    ),
  );
}

function tile(label, icon, tone, value, note) {
  const number = h("dd", { class: "sm-value efp-number" }, "0");
  kit.countUp(number, value, { from: 0, duration: 1000 });
  return h(
    "div",
    { class: "efp-surface efp-lift sm-tile", style: { "--tone": "var(--color-" + tone + ")" } },
    h("dt", {}, h("span.sm-tile-icon", {}, kit.icon(icon, "size-4")), label),
    number,
    h("dd", { class: "sm-note", text: note }),
  );
}

function showOverview(moreCycles) {
  const dependencies = services.reduce((total, service) => total + service.depends_on.length, 0);
  const entries = Array.from(levels(index).values()).filter((level) => level === 0).length;
  byId("stats").replaceChildren(
    tile("Services", "boxes", "primary", services.length, "in " + plural(teams.length, "team")),
    tile("Dependencies", "spline", "secondary", dependencies, (dependencies / services.length).toFixed(1) + " per service"),
    tile("Entry points", "log-in", "accent", entries, "nothing depends on them"),
    tile("Cycles", cycles.length ? "refresh-ccw" : "circle-check", cycles.length ? "error" : "success", cycles.length, cycles.length ? "services that need each other" : "none, as it should be"),
  );
  kit.rise(byId("stats").children, { stagger: 60 });

  // The blast radius: how much stops when a service stops.
  const ranked = services
    .map((service) => {
      const near = neighbourhood(index, service.name);
      return { service, count: near.dependents.direct.length + near.dependents.transitive.length };
    })
    .filter((row) => row.count > 0)
    .sort((one, two) => two.count - one.count || one.service.name.localeCompare(two.service.name))
    .slice(0, 6);
  const most = ranked.length ? ranked[0].count : 1;
  byId("ranking").replaceChildren(
    ...ranked.map((row, place) => {
      const bar = h("span.sm-bar", { style: { "--team": teamColour(row.service.team), "--i": place } });
      // Two frames later, so the bar grows from nothing instead of appearing at its length.
      requestAnimationFrame(() => requestAnimationFrame(() => bar.style.setProperty("--w", ((100 * row.count) / most).toFixed(1) + "%")));
      return h(
        "li",
        {},
        h(
          "button",
          {
            type: "button",
            class: "sm-rank",
            title: plural(row.count, "service") + " stop when " + row.service.name + " stops",
            onclick: () => {
              choose(row.service.name, { fly: true });
              canvas.scrollIntoView({ block: "nearest", behavior: kit.reducedMotion() ? "auto" : "smooth" });
            },
          },
          h("span.sm-rank-name", {}, dot(row.service.team), h("span.truncate", { text: row.service.name })),
          h("span.sm-track", {}, bar),
          h("span", { class: "efp-number text-sm font-semibold w-6 text-right", text: String(row.count) }),
        ),
      );
    }),
  );
  byId("ranking-card").hidden = !ranked.length;

  const items = cycles.map((cycle) =>
    h("li", {}, h("button", { type: "button", class: "sm-cycle", onclick: () => showCycle(cycle) }, kit.icon("refresh-ccw", "size-4"), h("span", { text: cycle.concat(cycle[0]).join(ARROW) }), kit.icon("chevron-right", "size-4 ml-auto opacity-50"))),
  );
  if (moreCycles) {
    items.push(h("li", { class: "text-sm text-base-content/60", text: "There are more cycles than are listed here." }));
  }
  if (!items.length) {
    items.push(h("li", { class: "sm-nocycle" }, kit.icon("circle-check", "size-5"), h("span", { text: "No cycles: every service can be started and deployed without waiting for itself." })));
  }
  byId("cycle-list").replaceChildren(...items);
  byId("cycles").hidden = false;
  kit.rise([byId("ranking-card"), byId("cycles")], { delay: 200, stagger: 80 });
}

// --- Loading ------------------------------------------------------------------------------------

// The map cannot be drawn: an empty state that says why and what to do.
function nothing(icon, title, text) {
  byId("loading").hidden = true;
  byId("overview").hidden = true;
  byId("legend").hidden = true;
  canvas.toggleAttribute("data-empty", true); // no zoom buttons over a map that is not there
  for (const control of [search, teamSelect, tierSelect]) control.disabled = true;
  byId("nothing").replaceChildren(h("span.efp-empty-icon", {}, kit.icon(icon)), h("strong", { text: title }), h("span", { class: "text-sm max-w-md", text }));
  byId("nothing").hidden = false;
  summary.textContent = title + ".";
}

async function load() {
  let data;
  try {
    const response = await fetch("services.json");
    if (!response.ok) {
      // 401 from the gateway: the visitor's session has ended.
      nothing("cloud-alert", "The map could not be loaded", response.status === 401 ? "Your session has ended. Reload the page to sign in again." : "services.json could not be loaded (status " + response.status + ").");
      return;
    }
    data = JSON.parse(await response.text());
  } catch (error) {
    nothing(
      "file-warning",
      "The map could not be loaded",
      error instanceof SyntaxError ? "services.json is not valid JSON: " + error.message : "services.json could not be loaded. Check the connection and reload the page.",
    );
    return;
  }

  const checked = validate(data);
  services = checked.services;
  byName = new Map(services.map((service) => [service.name, service]));
  numberOf = new Map(services.map((service, position) => [service.name, position]));
  index = buildIndex(services);
  teams = Array.from(new Set(services.map((service) => service.team))).sort();
  const searched = findCycles(index);
  cycles = searched.cycles;

  byId("problem-list").replaceChildren(...checked.problems.map((problem) => h("li", { text: problem })));
  byId("problems").hidden = checked.problems.length === 0;
  if (!services.length) {
    nothing("file-json", "services.json lists no service yet", "Add your services to public/services.json: a name, a team, a tier and what each one depends on. Push, deploy, and the map draws itself.");
    return;
  }

  for (const team of teams) {
    teamSelect.append(h("option", { value: team, text: team }));
  }
  showTeams();
  const dependencies = services.reduce((total, service) => total + service.depends_on.length, 0);
  summary.textContent = plural(services.length, "service") + ", " + plural(dependencies, "dependency", "dependencies") + ", " + plural(teams.length, "team") + ". Read from services.json.";
  byId("loading").hidden = true;
  showOverview(searched.more);
  build();
  choose(decodeName(location.hash), { fromAddress: true });
  draw();
  fit();
  animate();
}

await kit.ready();
kit.themeSwitch(byId("theme"));
load();
