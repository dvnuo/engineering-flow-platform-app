// A static app: this script, its modules and the style sheet are all there
// is. No request leaves the page after it has loaded, so what a visitor
// pastes (a token, a config file, a log) stays in the tab.
//
// Every URL is relative ("./tools/json.js", "style.css"): the app is served
// under /{slug}/ once deployed, and a leading slash would point outside it.
//
// Each tool is a module in tools/ with {id, title, blurb, mount(root)}; its
// logic is in lib/, free of the DOM, where node can test it. To add a tool,
// write the two files and add one line to TOOLS.

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

const nav = document.getElementById("tools");
const stage = document.getElementById("stage");
const links = new Map();
const panels = new Map();

// The tool is named by the address (#jwt), so a tool can be linked and the
// back button works. Nothing is remembered in the browser's storage: all
// apps share one origin, and storage of one is readable by every other.
function chosen() {
  const id = location.hash.slice(1);
  return TOOLS.find((tool) => tool.id === id) || TOOLS[0]; // an unknown name opens the first tool
}

function show(tool) {
  if (!panels.has(tool.id)) {
    // A tool is built the first time it is opened and then kept, input and all, until the tab closes.
    const panel = document.createElement("section");
    panel.className = "tool";
    panel.setAttribute("aria-labelledby", "title-" + tool.id);
    const title = document.createElement("h2");
    title.id = "title-" + tool.id;
    title.textContent = tool.title;
    const blurb = document.createElement("p");
    blurb.className = "muted";
    blurb.textContent = tool.blurb;
    panel.append(title, blurb);
    try {
      tool.mount(panel);
    } catch (error) {
      const problem = document.createElement("p");
      problem.className = "status problem";
      problem.textContent = "This tool could not start in this browser.";
      panel.append(problem);
    }
    panels.set(tool.id, panel);
    stage.append(panel);
  }
  for (const [id, panel] of panels) {
    panel.hidden = id !== tool.id;
  }
  for (const [id, link] of links) {
    if (id === tool.id) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  }
  document.title = tool.title + " - Dev toolbox";
}

for (const tool of TOOLS) {
  // Plain links: reachable with Tab, opened with Enter, and the address does the rest.
  const link = document.createElement("a");
  link.href = "#" + tool.id;
  link.textContent = tool.title;
  links.set(tool.id, link);
  nav.append(link);
}

stage.replaceChildren(); // the "loading" line
window.addEventListener("hashchange", () => show(chosen()));
show(chosen());
