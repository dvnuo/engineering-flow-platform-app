// A static app: this script and links.json are all there is. Every URL is
// relative ("links.json", not "/links.json"), because the app is served under
// /{slug}/ once deployed.

const filter = document.getElementById("filter");
const status = document.getElementById("status");
const groups = document.getElementById("groups");
let data = [];

// Only web addresses become links. Anything else in the data file (a
// "javascript:" address, for example) is shown as plain text.
function webAddress(url) {
  try {
    const parsed = new URL(url, document.baseURI);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch (error) {
    return null;
  }
}

function matches(link, words) {
  const text = [link.title, link.note, (link.tags || []).join(" ")].join(" ").toLowerCase();
  return words.every((word) => text.includes(word));
}

function row(link) {
  const item = document.createElement("li");
  const address = webAddress(link.url);
  const title = document.createElement(address ? "a" : "span");
  title.textContent = link.title;
  if (address) {
    title.href = address;
    title.target = "_blank";
    title.rel = "noopener noreferrer";
  }
  item.append(title);
  if (link.note) {
    const note = document.createElement("span");
    note.className = "muted";
    note.textContent = " - " + link.note;
    item.append(note);
  }
  for (const tag of link.tags || []) {
    const chip = document.createElement("span");
    chip.className = "tag";
    chip.textContent = tag;
    item.append(chip);
  }
  return item;
}

function render() {
  const words = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
  let shown = 0;
  groups.replaceChildren();
  for (const group of data) {
    const links = (group.links || []).filter((link) => matches(link, words));
    if (!links.length) {
      continue;
    }
    const section = document.createElement("section");
    const heading = document.createElement("h2");
    heading.textContent = group.name;
    const list = document.createElement("ul");
    list.append(...links.map(row));
    section.append(heading, list);
    groups.append(section);
    shown += links.length;
  }
  status.textContent = shown ? shown + (shown === 1 ? " link" : " links") : "Nothing matches.";
}

async function load() {
  try {
    const response = await fetch("links.json");
    if (!response.ok) {
      // 401 from the gateway: the visitor's session has ended.
      status.textContent = "The links could not be loaded. Reload the page.";
      return;
    }
    data = (await response.json()).groups || [];
    render();
  } catch (error) {
    status.textContent = "The links could not be loaded.";
  }
}

filter.addEventListener("input", render);
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && document.activeElement !== filter) {
    event.preventDefault();
    filter.focus();
  }
});

load();
