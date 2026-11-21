// A static app: this page draws whatever services.json says. To change the
// map, change that file, push and press Deploy; there is no backend to
// restart and nothing to migrate.
//
// Every URL is relative ("services.json", "./lib/graph.js"): the app is
// served under /{slug}/ once deployed.
//
// The thinking is in lib/ (graph.js: checking the file, reachability,
// cycles; layout.js: the forces; view.js: pan and zoom), free of the DOM and
// tested with node. This file is the drawing and the pointer handling.

import { buildIndex, cycleEdges, findCycles, levels, matches, neighbourhood, validate } from "./lib/graph.js";
import { bounds, createLayout, reheat, settle, step } from "./lib/layout.js";
import { fitView, pan, pinch, toMap, zoomAt } from "./lib/view.js";

const SVG = "http://www.w3.org/2000/svg"; // a name, not an address: nothing is fetched from it
const RADIUS = { 1: 20, 2: 16, 3: 13 };
const HEAD = 9; // length of an arrow head
const TEAM_COLOURS = 8;
const DRAG_THRESHOLD = 4; // pixels a pointer may wander and still count as a click
const ARROW = " \u2192 ";

const byId = (id) => document.getElementById(id);
const svg = byId("map");
const viewport = byId("viewport");
const edgeLayer = byId("edges");
const nodeLayer = byId("nodes");
const panel = byId("panel");
const summary = byId("summary");
const status = byId("status");
const empty = byId("empty");
const search = byId("search");
const teamSelect = byId("team");
const tierSelect = byId("tier");

// Visitors who asked their system for less motion get the finished layout at once.
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

let services = [];
let index = null;
let teams = [];
let cycles = [];
let layout = null;
let nodes = []; // the <g> of each service, in the order of ``services``
let edges = []; // {from, to, group, line, head, cycle}
let view = { x: 0, y: 0, k: 1 };
let chosen = ""; // the name of the chosen service
let shownCycle = null; // or the cycle picked from the list
let tookOver = false; // the visitor moved the view: stop fitting it to the layout
let running = false;

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

function shape(tag, attributes = {}, text = "") {
  const node = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) {
    node.setAttribute(name, value);
  }
  node.textContent = text;
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
const teamClass = (team) => "team-" + (teams.indexOf(team) % TEAM_COLOURS);

// --- Drawing ------------------------------------------------------------------------------------

function build() {
  const number = new Map(services.map((service, position) => [service.name, position]));
  const onCycle = cycleEdges(cycles);
  const level = levels(index);
  edges = [];
  edgeLayer.replaceChildren();
  for (const service of services) {
    for (const target of service.depends_on) {
      const group = shape("g");
      const line = shape("line");
      const head = shape("path", { class: "head" });
      group.append(line, head);
      edgeLayer.append(group);
      edges.push({ from: number.get(service.name), to: number.get(target), group, line, head, cycle: onCycle.has(service.name + ">" + target) });
    }
  }
  nodes = services.map((service, position) => {
    const radius = RADIUS[service.tier];
    const group = shape("g", {
      tabindex: "0",
      role: "button",
      "data-index": position,
      "aria-label": service.name + ", team " + service.team + ", tier " + service.tier,
    });
    group.append(
      shape("title", {}, service.description || service.name),
      shape("circle", { r: radius }),
      shape("text", { class: "tier", dy: "0.35em" }, String(service.tier)),
      shape("text", { class: "name", y: radius + 15 }, service.name),
    );
    return group;
  });
  nodeLayer.replaceChildren(...nodes);
  layout = createLayout(
    services.length,
    edges.map((edge) => [edge.from, edge.to]),
    { levels: services.map((service) => level.get(service.name)) },
  );
}

// Put every node and arrow where the layout has it.
function draw() {
  layout.nodes.forEach((node, position) => {
    nodes[position].setAttribute("transform", "translate(" + node.x.toFixed(1) + " " + node.y.toFixed(1) + ")");
  });
  for (const edge of edges) {
    const a = layout.nodes[edge.from];
    const b = layout.nodes[edge.to];
    const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    const ux = (b.x - a.x) / length;
    const uy = (b.y - a.y) / length;
    // From the rim of one circle to the rim of the other, the head touching the dependency.
    const startGap = RADIUS[services[edge.from].tier] + 2;
    const tipX = b.x - ux * (RADIUS[services[edge.to].tier] + 3);
    const tipY = b.y - uy * (RADIUS[services[edge.to].tier] + 3);
    const baseX = tipX - ux * HEAD;
    const baseY = tipY - uy * HEAD;
    edge.line.setAttribute("x1", (a.x + ux * startGap).toFixed(1));
    edge.line.setAttribute("y1", (a.y + uy * startGap).toFixed(1));
    edge.line.setAttribute("x2", baseX.toFixed(1));
    edge.line.setAttribute("y2", baseY.toFixed(1));
    const wing = HEAD / 2;
    const corner = (side) => (baseX - uy * wing * side).toFixed(1) + " " + (baseY + ux * wing * side).toFixed(1);
    edge.head.setAttribute("d", "M" + tipX.toFixed(1) + " " + tipY.toFixed(1) + " L" + corner(1) + " L" + corner(-1) + " Z");
  }
}

function applyView() {
  viewport.setAttribute("transform", "translate(" + view.x.toFixed(1) + " " + view.y.toFixed(1) + ") scale(" + view.k.toFixed(4) + ")");
}

function shownFlags() {
  const filters = { team: teamSelect.value, tier: Number(tierSelect.value) };
  return services.map((service) => matches(service, filters));
}

function fit() {
  const flags = shownFlags();
  const shown = services.map((_, position) => position).filter((position) => flags[position]);
  const area = svg.getBoundingClientRect();
  view = fitView(bounds(layout, shown.length ? shown : null), area.width || 800, area.height || 500);
  applyView();
}

// Classes say what a node or an arrow is to the chosen service; the style
// sheet turns them into colours AND line styles, so colour is never the only sign.
function paint() {
  const flags = shownFlags();
  const query = search.value.trim().toLowerCase();
  const found = services.map((service, position) => flags[position] && query !== "" && matches(service, { query }));
  const role = new Map();
  let needs = new Set();
  let users = new Set();
  let cycleKeys = new Set();
  if (chosen) {
    const around = neighbourhood(index, chosen);
    needs = new Set([...around.dependencies.direct, ...around.dependencies.transitive]);
    users = new Set([...around.dependents.direct, ...around.dependents.transitive]);
    around.dependents.transitive.forEach((name) => role.set(name, "users far"));
    around.dependents.direct.forEach((name) => role.set(name, "users direct"));
    around.dependencies.transitive.forEach((name) => role.set(name, "needs far"));
    around.dependencies.direct.forEach((name) => role.set(name, "needs direct"));
    role.set(chosen, "is-chosen");
  } else if (shownCycle) {
    shownCycle.forEach((name) => role.set(name, "in-cycle"));
    cycleKeys = cycleEdges([shownCycle]);
  }
  const focused = chosen !== "" || shownCycle !== null;

  services.forEach((service, position) => {
    const classes = ["node", teamClass(service.team)];
    if (!flags[position]) {
      classes.push("is-hidden");
    }
    if (focused) {
      classes.push(role.get(service.name) || "is-dimmed");
    } else if (query && !found[position]) {
      classes.push("is-dimmed");
    }
    if (found[position]) {
      classes.push("is-match");
    }
    nodes[position].setAttribute("class", classes.join(" "));
    nodes[position].setAttribute("aria-pressed", String(service.name === chosen));
  });

  for (const edge of edges) {
    const from = services[edge.from].name;
    const to = services[edge.to].name;
    const classes = ["edge"];
    if (edge.cycle) {
      classes.push("cycle");
    }
    if (!flags[edge.from] || !flags[edge.to]) {
      classes.push("is-hidden");
    }
    if (chosen) {
      // An arrow belongs to "needs" when it starts at the chosen service or at something it needs.
      if (from === chosen) {
        classes.push("needs direct");
      } else if (to === chosen) {
        classes.push("users direct");
      } else if (needs.has(from)) {
        classes.push("needs far");
      } else if (users.has(to)) {
        classes.push("users far");
      } else {
        classes.push("is-dimmed");
      }
    } else if (shownCycle) {
      classes.push(cycleKeys.has(from + ">" + to) ? "in-cycle" : "is-dimmed");
    }
    edge.group.setAttribute("class", classes.join(" "));
  }

  const shown = flags.filter(Boolean).length;
  const matched = found.filter(Boolean).length;
  empty.hidden = shown > 0;
  if (query) {
    status.textContent = matched ? plural(matched, "service matches", "services match") + ' "' + query + '". Press Enter to choose the first.' : 'No service is named like "' + query + '".';
  } else {
    status.textContent = shown < services.length ? shown + " of " + plural(services.length, "service") + " shown." : "";
  }
}

// --- The side panel -----------------------------------------------------------------------------

function serviceButton(name) {
  const button = el("button", { type: "button", class: "link", text: name });
  button.addEventListener("click", () => choose(name));
  return button;
}

function relatives(title, found, note, kind) {
  const total = found.direct.length + found.transitive.length;
  const block = el("section", { class: "relatives " + kind }, el("h3", { text: title + " (" + total + ")" }));
  if (!total) {
    block.append(el("p", { class: "muted", text: "Nothing." }));
    return block;
  }
  block.append(el("p", { class: "muted", text: note }));
  const lists = [
    ["Directly", found.direct],
    ["Through others", found.transitive],
  ];
  for (const [label, names] of lists) {
    if (names.length) {
      const list = el("ul", { class: "names" });
      names.forEach((name) => list.append(el("li", {}, serviceButton(name))));
      block.append(el("h4", { text: label + " (" + names.length + ")" }), list);
    }
  }
  return block;
}

function describe() {
  if (!chosen && shownCycle) {
    panel.replaceChildren(
      el("h2", { text: "A dependency cycle" }),
      el("p", { text: shownCycle.concat(shownCycle[0]).join(ARROW) }),
      el("p", { class: "muted", text: "Each of these needs the next one, and the last needs the first. Break the cycle where the dependency is weakest." }),
      el("ul", { class: "names" }, ...shownCycle.map((name) => el("li", {}, serviceButton(name)))),
    );
    return;
  }
  const service = services.find((candidate) => candidate.name === chosen);
  if (!service) {
    panel.replaceChildren(el("p", { class: "muted", text: "Choose a service on the map to see what it depends on and what depends on it." }));
    return;
  }
  const around = neighbourhood(index, chosen);
  const links = el("ul", { class: "links" });
  for (const link of service.links) {
    const address = webAddress(link.url);
    const item = el(address ? "a" : "span", { text: link.title });
    if (address) {
      item.href = address;
      item.target = "_blank";
      item.rel = "noopener noreferrer";
    }
    links.append(el("li", {}, item));
  }
  const inCycles = cycles.filter((cycle) => cycle.includes(chosen));
  const clear = el("button", { type: "button", text: "Clear the choice" });
  clear.addEventListener("click", () => choose(""));
  panel.replaceChildren(
    el("h2", { text: service.name }),
    el("p", { class: "meta" }, el("span", { class: "swatch " + teamClass(service.team) }), service.team, el("span", { class: "badge", text: "Tier " + service.tier })),
    el("p", { text: service.description || "No description in the data file." }),
    links,
    ...inCycles.map((cycle) => el("p", { class: "warning", text: "In a dependency cycle: " + cycle.concat(cycle[0]).join(ARROW) })),
    relatives("Depends on", around.dependencies, "What it needs to work.", "needs"),
    relatives("Used by", around.dependents, "What is affected when it fails.", "users"),
    clear,
  );
}

// Choose a service ("" for none). The choice is kept in the address (#checkout), so it can be linked.
function choose(name, fromAddress = false) {
  chosen = services.some((service) => service.name === name) ? name : "";
  shownCycle = null;
  if (!fromAddress) {
    history.replaceState(null, "", chosen ? "#" + chosen : location.pathname + location.search);
  }
  paint();
  describe();
}

// --- Animation ----------------------------------------------------------------------------------

function frame() {
  const moving = step(layout);
  draw();
  if (!tookOver) {
    fit();
  }
  if (moving) {
    requestAnimationFrame(frame);
  } else {
    running = false; // settled: no frame is requested until something moves again
  }
}

function animate() {
  if (reducedMotion.matches) {
    settle(layout); // no animation: work the layout out and draw it once
    draw();
    if (!tookOver) {
      fit();
    }
  } else if (!running) {
    running = true;
    requestAnimationFrame(frame);
  }
}

// --- Pointer, wheel and keys --------------------------------------------------------------------

const pointers = new Map(); // every finger or button that is down, by pointer id
let gesture = null;

function place(event) {
  const area = svg.getBoundingClientRect();
  return { x: event.clientX - area.left, y: event.clientY - area.top };
}

svg.addEventListener("pointerdown", (event) => {
  if (event.pointerType === "mouse" && event.button !== 0) {
    return; // the right button opens the browser's menu, as everywhere
  }
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
  if (gesture.kind === "node") {
    // The node follows the pointer and stays where it is dropped; the others make room.
    const node = layout.nodes[gesture.position];
    const spot = toMap(view, at.x, at.y);
    node.x = spot.x;
    node.y = spot.y;
    node.fixed = true;
    tookOver = true;
    if (reducedMotion.matches) {
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
    const at = place(event);
    const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
    view = zoomAt(view, Math.exp(-delta * 0.0015), at.x, at.y);
    tookOver = true;
    applyView();
  },
  { passive: false },
);

function zoomButton(factor) {
  const area = svg.getBoundingClientRect();
  view = zoomAt(view, factor, area.width / 2, area.height / 2);
  tookOver = true;
  applyView();
}

svg.addEventListener("keydown", (event) => {
  const node = event.target.closest ? event.target.closest(".node") : null;
  if (node && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    choose(services[Number(node.getAttribute("data-index"))].name);
    return;
  }
  const moves = { ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40] };
  if (moves[event.key]) {
    event.preventDefault();
    view = pan(view, ...moves[event.key]);
    tookOver = true;
    applyView();
  } else if (event.key === "+" || event.key === "=") {
    zoomButton(1.25);
  } else if (event.key === "-") {
    zoomButton(0.8);
  } else if (event.key === "0") {
    tookOver = false;
    fit();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && (chosen || shownCycle)) {
    choose("");
  }
});

byId("zoom-in").addEventListener("click", () => zoomButton(1.25));
byId("zoom-out").addEventListener("click", () => zoomButton(0.8));
byId("fit").addEventListener("click", () => {
  tookOver = false;
  fit();
});
byId("relayout").addEventListener("click", () => {
  if (services.length) {
    build();
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

function refilter() {
  paint();
  if (!tookOver) {
    fit();
  }
}
teamSelect.addEventListener("change", refilter);
tierSelect.addEventListener("change", refilter);
search.addEventListener("input", paint);
search.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    const flags = shownFlags();
    const first = services.find((service, position) => flags[position] && search.value.trim() && matches(service, { query: search.value }));
    if (first) {
      choose(first.name);
    }
  }
});
window.addEventListener("resize", () => {
  if (layout && !tookOver) {
    fit();
  }
});
window.addEventListener("hashchange", () => choose(decodeName(location.hash), true));

function decodeName(hash) {
  try {
    return decodeURIComponent(hash.slice(1));
  } catch (error) {
    return "";
  }
}

// --- Loading ------------------------------------------------------------------------------------

function listInto(id, sectionId, items) {
  byId(id).replaceChildren(...items);
  byId(sectionId).hidden = items.length === 0;
}

async function load() {
  let data;
  try {
    const response = await fetch("services.json");
    if (!response.ok) {
      // 401 from the gateway: the visitor's session has ended.
      summary.textContent = response.status === 401 ? "Your session has ended. Reload the page to sign in again." : "services.json could not be loaded (status " + response.status + ").";
      return;
    }
    data = JSON.parse(await response.text());
  } catch (error) {
    summary.textContent = error instanceof SyntaxError ? "services.json is not valid JSON: " + error.message : "services.json could not be loaded.";
    return;
  }

  const checked = validate(data);
  services = checked.services;
  index = buildIndex(services);
  teams = Array.from(new Set(services.map((service) => service.team))).sort();
  const searched = findCycles(index);
  cycles = searched.cycles;

  listInto("problem-list", "problems", checked.problems.map((problem) => el("li", { text: problem })));
  const cycleItems = cycles.map((cycle) => {
    const button = el("button", { type: "button", class: "link", text: cycle.concat(cycle[0]).join(ARROW) });
    button.addEventListener("click", () => {
      choose("");
      shownCycle = cycle;
      paint();
      describe();
      svg.scrollIntoView({ block: "nearest" });
    });
    return el("li", {}, button);
  });
  if (searched.more) {
    cycleItems.push(el("li", { class: "muted", text: "There are more cycles than are listed here." }));
  }
  listInto("cycle-list", "cycles", cycleItems);

  for (const team of teams) {
    teamSelect.append(el("option", { value: team, text: team }));
  }
  byId("teams").replaceChildren(...teams.map((team) => el("li", {}, el("span", { class: "swatch " + teamClass(team) }), team)));

  const dependencies = services.reduce((total, service) => total + service.depends_on.length, 0);
  summary.textContent = services.length
    ? plural(services.length, "service") + ", " + plural(dependencies, "dependency", "dependencies") + ", " + plural(teams.length, "team") + ". Read from services.json."
    : "services.json lists no service that can be drawn.";
  if (!services.length) {
    return;
  }
  build();
  chosen = "";
  choose(decodeName(location.hash), true);
  draw();
  fit();
  animate();
}

load();
