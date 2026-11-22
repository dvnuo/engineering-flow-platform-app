// A static app: this script and links.json are all there is. Every URL is
// relative ("links.json", not "/links.json"), because the app is served under
// /{slug}/ once deployed. The one exception is the EFP kit at /_efp/lib/,
// which every gateway serves to every app (docs/UI_KIT.md).
//
// The cards are made once, when links.json has arrived. Typing only takes
// cards out and puts them back (kit.animateList glides the others into
// place) and marks the words that matched; nothing is drawn anew.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const filter = $("filter");
const groupsElement = $("groups");

// Each group takes a colour of the theme, in this order.
const TONES = ["primary", "secondary", "accent", "info", "success", "warning"];

let groups = []; // [{ section, list, count, entries: [{ link, card, title, note, anchor, text }] }]
let total = 0;
let first = null; // the link that Enter opens: the first one shown

// Only web addresses become links. Anything else in the data file (a
// "javascript:" address, for example) is shown as plain text.
function webAddress(url) {
  try {
    const parsed = new URL(url, document.baseURI);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed : null;
  } catch (error) {
    return null;
  }
}

// The text, with each searched word in a <mark>. Still text: never HTML.
function marked(text, words) {
  if (!words.length) return [text];
  const escaped = words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return text
    .split(new RegExp("(" + escaped.join("|") + ")", "gi"))
    .map((part, index) => (index % 2 ? h("mark.tl-hit", {}, part) : part))
    .filter((part) => part !== "");
}

function card(link) {
  const address = webAddress(link.url);
  const title = h("span.tl-title");
  const note = link.note ? h("span.tl-note") : null;
  let anchor = null;
  if (address) {
    // The whole card is the link (see .tl-anchor::after); its tags stay buttons above it.
    anchor = h("a.tl-anchor", { href: address.href, target: "_blank" }, title);
    anchor.rel = "noopener noreferrer";
  }
  const tags = (link.tags || []).map((tag) =>
    h("button.tl-tag", { type: "button", title: "Show the links tagged " + tag, onclick: () => searchFor(tag) }, tag),
  );
  const element = h(
    "li.tl-card.efp-surface",
    {},
    h("span.tl-tile", { "aria-hidden": "true" }, link.title.trim().charAt(0).toUpperCase()),
    h(
      "div.min-w-0",
      {},
      anchor || h("span.tl-anchor", {}, title),
      note,
      h("span.tl-host", { text: address ? address.host : "not a web address" }),
      tags.length ? h("div.tl-tags", {}, tags) : null,
    ),
    address ? kit.icon("arrow-up-right", "tl-go size-4") : null,
  );
  const text = [link.title, link.note, (link.tags || []).join(" ")].join(" ").toLowerCase();
  return { link, card: element, title, note, anchor, text };
}

function build(data) {
  groups = data.map((group, number) => {
    const count = h("span.tl-count", {}, "0");
    const list = h("ul.tl-list", { "aria-label": group.name });
    const section = h(
      "section.tl-group",
      { style: { "--tl-tone": "var(--color-" + TONES[number % TONES.length] + ")" } },
      h("h2.tl-group-head", {}, h("span.tl-dot", { "aria-hidden": "true" }), h("span", { text: group.name }), count),
      list,
    );
    return { section, list, count, entries: (group.links || []).map(card) };
  });
  total = groups.reduce((sum, group) => sum + group.entries.length, 0);
  groupsElement.replaceChildren();
  render();
  tagChips();
  kit.rise(groupsElement.querySelectorAll(".tl-group-head, .tl-card"), { stagger: 35 });
  // From now on, cards that come and go glide (the first drawing rose instead).
  kit.animateList(groupsElement);
  groups.forEach((group) => kit.animateList(group.list));
}

// Put each matching card in its place and take the others out. Cards that
// stay are not touched, so only what changed moves.
function render() {
  const query = filter.value.trim().toLowerCase();
  const words = query.split(/\s+/).filter(Boolean);
  let shown = 0;
  let place = 0;
  first = null;
  for (const group of groups) {
    let index = 0;
    for (const entry of group.entries) {
      if (!words.every((word) => entry.text.includes(word))) {
        entry.card.remove();
        continue;
      }
      entry.title.replaceChildren(...marked(entry.link.title, words));
      if (entry.note) entry.note.replaceChildren(...marked(entry.link.note, words));
      if (group.list.children[index] !== entry.card) group.list.insertBefore(entry.card, group.list.children[index] || null);
      first = first || entry.anchor;
      index += 1;
    }
    group.count.textContent = String(index);
    shown += index;
    if (!index) {
      group.section.remove();
    } else {
      if (groupsElement.children[place] !== group.section) groupsElement.insertBefore(group.section, groupsElement.children[place] || null);
      place += 1;
    }
  }
  $("status").textContent = words.length
    ? shown + " of " + total + " links"
    : total + (total === 1 ? " link" : " links") + " in " + groups.length + (groups.length === 1 ? " group." : " groups.");
  $("enter-hint").hidden = !(words.length && first);
  $("nothing").hidden = shown > 0 || !total;
  $("nothing-title").textContent = 'Nothing matches "' + filter.value.trim() + '"';
  for (const chip of $("tags").children) chip.setAttribute("aria-pressed", String(chip.dataset.tag.toLowerCase() === query));
}

// The tags of the file as quick filters, the most used first.
function tagChips() {
  const counts = new Map();
  for (const group of groups) {
    for (const entry of group.entries) for (const tag of entry.link.tags || []) counts.set(tag, (counts.get(tag) || 0) + 1);
  }
  const top = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 12);
  $("tags").replaceChildren(
    ...top.map(([tag]) =>
      h("button.btn.btn-xs.tl-chip.efp-press", { type: "button", dataset: { tag }, "aria-pressed": "false", onclick: () => searchFor(tag) }, kit.icon("hash", "size-3 opacity-60"), tag),
    ),
  );
}

// A tag that is already the search clears it again.
function searchFor(tag) {
  filter.value = filter.value.trim().toLowerCase() === tag.toLowerCase() ? "" : tag;
  render();
  filter.focus();
}

function showProblem(text) {
  groupsElement.replaceChildren();
  $("status").textContent = "No links to show.";
  $("problem").hidden = false;
  $("problem").replaceChildren(
    h(
      "div.alert.alert-soft.alert-error",
      { role: "alert" },
      kit.icon("circle-alert", "size-5"),
      h("span", { text }),
      h("button.btn.btn-sm.btn-ghost", { type: "button", onclick: () => location.reload() }, kit.icon("rotate-cw", "size-4"), "Reload"),
    ),
  );
}

// The links, or the sentence that says why there are none. Asked for while
// the icons load (see the end of the file), drawn once they are there.
async function fetchLinks() {
  let response;
  try {
    response = await fetch("links.json");
  } catch (error) {
    return { problem: "The links could not be loaded. Check the connection and reload the page." };
  }
  if (!response.ok) {
    // 401 from the gateway: the visitor's session has ended.
    return { problem: response.status === 401 ? "Your session has ended. Reload the page to sign in again." : "The links could not be loaded (" + response.status + ")." };
  }
  try {
    return { groups: (await response.json()).groups || [] };
  } catch (error) {
    return { problem: "links.json is not valid JSON. Check the file and deploy again." };
  }
}

filter.addEventListener("input", render);
filter.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && filter.value) {
    event.preventDefault();
    filter.value = "";
    render();
  } else if (event.key === "Enter" && first) {
    event.preventDefault();
    first.click();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && document.activeElement !== filter && !event.ctrlKey && !event.metaKey) {
    event.preventDefault();
    filter.focus();
    filter.select();
  }
});
$("clear").addEventListener("click", () => {
  filter.value = "";
  render();
  filter.focus();
});

const links = fetchLinks();
await kit.ready();
kit.themeSwitch($("theme"));
const found = await links;
if (found.problem) showProblem(found.problem);
else build(found.groups);
