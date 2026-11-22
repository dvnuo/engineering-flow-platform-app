// A static app: this script, its modules and the style sheet are all there
// is. No request leaves the page after it has loaded, so what a visitor
// pastes (a token, a config file, a log) stays in the tab.
//
// Every URL is relative ("./tools/json.js", "style.css"): the app is served
// under /{slug}/ once deployed, and a leading slash would point outside it.
// The one exception is the EFP kit, at /_efp/lib/, which every gateway
// serves to every app (docs/UI_KIT.md).
//
// Each tool is a module in tools/ with {id, title, blurb, icon, keywords,
// mount(root)}; its logic is in lib/, free of the DOM, where node can test
// it. To add a tool, write the two files and add one line to TOOLS.
//
// The shell around the tools: a list with a filter and a highlight that
// glides to the open tool, a command palette (Ctrl+K), and keys to jump
// between tools. The open tool is named by the address, never by storage.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import { provide } from "./ui.js";
import base64 from "./tools/base64.js";
import cron from "./tools/cron.js";
import diff from "./tools/diff.js";
import hash from "./tools/hash.js";
import json from "./tools/json.js";
import jwt from "./tools/jwt.js";
import regex from "./tools/regex.js";
import time from "./tools/time.js";
import url from "./tools/url.js";
import uuid from "./tools/uuid.js";

const TOOLS = [json, base64, url, jwt, time, uuid, hash, regex, diff, cron];

const { h } = kit;
const $ = (id) => document.getElementById(id);
const list = $("tools");
const stage = $("stage");
const filter = $("filter");
const indicator = $("indicator");
const palette = $("palette");
const paletteInput = $("palette-input");
const paletteList = $("palette-list");
const links = new Map(); // tool id -> its <a> in the list
const items = new Map(); // tool id -> its <li>, kept while the filter hides it
const panels = new Map(); // tool id -> its section, built the first time it opens
let current = null;
let focusOnShow = false; // a tool opened from the keyboard takes the focus to its title
let hits = []; // the tools the palette lists
let active = 0;

// The tool is named by the address (#jwt), so a tool can be linked and the
// back button works. Nothing is remembered in the browser's storage: all
// apps share one origin, and storage of one is readable by every other.
function chosen() {
  const id = location.hash.slice(1);
  return TOOLS.find((tool) => tool.id === id) || TOOLS[0]; // an unknown name opens the first tool
}

// Tools whose title starts with the words come first, then those that mention them.
function find(query) {
  const wanted = query.trim().toLowerCase();
  if (!wanted) return TOOLS.slice();
  const first = [];
  const then = [];
  for (const tool of TOOLS) {
    if (tool.title.toLowerCase().startsWith(wanted) || tool.id.startsWith(wanted)) first.push(tool);
    else if ((tool.title + " " + tool.id + " " + tool.keywords + " " + tool.blurb).toLowerCase().includes(wanted)) then.push(tool);
  }
  return first.concat(then);
}

function open(tool, { focus = false } = {}) {
  focusOnShow = focus;
  if (location.hash === "#" + tool.id) show(tool);
  else location.hash = tool.id; // "hashchange" shows it
}

// --- The tools ----------------------------------------------------------------------------------

function panelFor(tool) {
  if (panels.has(tool.id)) return panels.get(tool.id);
  // A tool is built the first time it is opened and then kept, input and all, until the tab closes.
  const body = h("div", { class: "dt-tool-body" });
  const panel = h(
    "section",
    { class: "dt-tool", "aria-labelledby": "title-" + tool.id },
    h(
      "header",
      { class: "dt-tool-head" },
      h("span", { class: "dt-tool-icon" }, kit.icon(tool.icon, "size-6")),
      h("div", { class: "min-w-0 grow" }, h("h2", { id: "title-" + tool.id, tabindex: "-1", text: tool.title }), h("p", { text: tool.blurb })),
    ),
    body,
  );
  try {
    tool.mount(body);
  } catch (error) {
    body.append(h("div", { class: "alert alert-soft alert-error" }, kit.icon("circle-alert", "size-5"), h("span", { text: "This tool could not start in this browser." })));
  }
  panels.set(tool.id, panel);
  stage.append(panel);
  return panel;
}

function show(tool) {
  const panel = panelFor(tool);
  stage.querySelector(".dt-loading")?.remove();
  for (const [id, other] of panels) {
    other.hidden = id !== tool.id;
  }
  if (tool !== current) kit.rise(panel, { distance: 10, duration: 460 }); // the new tool slides up into its place
  current = tool;
  for (const [id, link] of links) {
    if (id === tool.id) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  moveIndicator();
  links.get(tool.id).scrollIntoView({ block: "nearest", inline: "center", behavior: kit.reducedMotion() ? "auto" : "smooth" }); // the strip of a phone centres it
  document.title = tool.title + " - Dev toolbox";
  if (focusOnShow) panel.querySelector("h2").focus({ preventScroll: true });
  focusOnShow = false;
}

// --- The list: a highlight that glides, a filter, arrow keys ------------------------------------

// The highlight behind the open tool moves to it (a CSS transition on its
// transform), so the eye follows the choice instead of looking for it.
function moveIndicator() {
  const link = current && links.get(current.id);
  if (!link || !link.isConnected || !link.offsetParent) {
    indicator.style.opacity = "0";
    return;
  }
  indicator.style.opacity = "1";
  indicator.style.transform = "translate(" + link.offsetLeft + "px, " + link.offsetTop + "px)";
  indicator.style.width = link.offsetWidth + "px";
  indicator.style.height = link.offsetHeight + "px";
}

TOOLS.forEach((tool, position) => {
  // Plain links: reachable with Tab, opened with Enter, and the address does the rest.
  const link = document.createElement("a");
  link.href = "#" + tool.id;
  link.className = "dt-link";
  link.append(
    h("span", { class: "dt-link-icon" }, kit.icon(tool.icon, "size-4")),
    h("span", { class: "dt-link-title", text: tool.title }),
    h("kbd", { class: "dt-link-key", "aria-hidden": "true", text: String((position + 1) % 10) }),
  );
  link.title = tool.blurb;
  links.set(tool.id, link);
  items.set(tool.id, h("li", {}, link));
});
list.replaceChildren(...items.values());

function applyFilter() {
  const found = new Set(find(filter.value).map((tool) => tool.id));
  // The list keeps its order; what leaves or comes back animates (kit.animateList below).
  list.replaceChildren(...TOOLS.filter((tool) => found.has(tool.id)).map((tool) => items.get(tool.id)));
  $("no-tool").hidden = found.size > 0;
  moveIndicator();
}

filter.addEventListener("input", applyFilter);
filter.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    const first = find(filter.value)[0];
    if (first) open(first, { focus: true });
  } else if (event.key === "Escape" && filter.value) {
    event.preventDefault();
    filter.value = "";
    applyFilter();
  } else if (event.key === "ArrowDown") {
    event.preventDefault();
    list.querySelector("a")?.focus();
  }
});

list.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  const shown = Array.from(list.querySelectorAll("a"));
  const at = shown.indexOf(document.activeElement);
  if (at < 0) return;
  event.preventDefault();
  shown[(at + (event.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length].focus();
});

new ResizeObserver(moveIndicator).observe(list);

// --- The palette (Ctrl+K) ---------------------------------------------------------------------

function markActive() {
  Array.from(paletteList.children).forEach((item, position) => item.setAttribute("aria-selected", String(position === active)));
  const item = paletteList.children[active];
  if (item && hits.length) {
    paletteInput.setAttribute("aria-activedescendant", item.id);
    item.scrollIntoView({ block: "nearest" });
  }
}

function renderPalette() {
  hits = find(paletteInput.value);
  active = 0;
  const wanted = paletteInput.value.trim().toLowerCase();
  const title = (text) => {
    const at = wanted ? text.toLowerCase().indexOf(wanted) : -1;
    return at < 0 ? text : [text.slice(0, at), h("mark", { text: text.slice(at, at + wanted.length) }), text.slice(at + wanted.length)];
  };
  paletteList.replaceChildren(
    ...(hits.length
      ? hits.map((tool, position) =>
          h(
            "li",
            {
              id: "palette-" + tool.id,
              role: "option",
              class: "dt-option",
              onclick: () => choose(tool),
              onpointermove: () => {
                if (active !== position) {
                  active = position;
                  markActive();
                }
              },
            },
            h("span", { class: "dt-link-icon" }, kit.icon(tool.icon, "size-4")),
            h("span", { class: "min-w-0 grow" }, h("span", { class: "dt-option-title" }, title(tool.title)), h("span", { class: "dt-option-blurb", text: tool.blurb })),
            h("kbd", { class: "kbd kbd-xs", text: "Alt " + ((TOOLS.indexOf(tool) + 1) % 10) }),
          ),
        )
      : [h("li", { class: "dt-option-none" }, kit.icon("search-x", "size-5"), h("span", { text: 'No tool for "' + paletteInput.value.trim() + '".' }))]),
  );
  markActive();
}

function choose(tool) {
  palette.close();
  open(tool, { focus: true });
}

function openPalette() {
  paletteInput.value = "";
  renderPalette();
  palette.showModal();
  paletteInput.focus();
}

$("palette-open").addEventListener("click", openPalette);
paletteInput.addEventListener("input", renderPalette);
paletteInput.addEventListener("keydown", (event) => {
  if ((event.key === "ArrowDown" || event.key === "ArrowUp") && hits.length) {
    event.preventDefault();
    active = (active + (event.key === "ArrowDown" ? 1 : hits.length - 1)) % hits.length;
    markActive();
  } else if (event.key === "Enter" && hits[active]) {
    event.preventDefault();
    choose(hits[active]);
  }
});
// A click beside the box (on the dialog's own backdrop) closes it.
palette.addEventListener("click", (event) => {
  if (event.target === palette) palette.close();
});

// --- Keys for the whole page --------------------------------------------------------------------

document.addEventListener("keydown", (event) => {
  const typing = event.target.closest && event.target.closest("input, textarea, select");
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault(); // the browser's own Ctrl+K would focus its address bar
    if (palette.open) palette.close();
    else openPalette();
  } else if (event.altKey && /^Digit[0-9]$/.test(event.code)) {
    // By the key's place, not its character: Alt+1 types a symbol on some keyboards.
    event.preventDefault();
    open(TOOLS[(Number(event.code.slice(5)) + 9) % 10], { focus: true });
  } else if (event.altKey && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
    event.preventDefault();
    const at = TOOLS.indexOf(current);
    open(TOOLS[(at + (event.key === "ArrowDown" ? 1 : TOOLS.length - 1)) % TOOLS.length], { focus: true });
  } else if (event.key === "/" && !typing && !palette.open) {
    event.preventDefault();
    filter.focus();
  }
});

provide(kit); // the tools build with the kit's icons and motion; they do not import it themselves, so node can load them
await kit.ready();
kit.themeSwitch($("theme"));
kit.animateList(list);
window.addEventListener("hashchange", () => show(chosen()));
show(chosen());
requestAnimationFrame(() => indicator.classList.add("is-ready")); // placed once without gliding, then it glides
