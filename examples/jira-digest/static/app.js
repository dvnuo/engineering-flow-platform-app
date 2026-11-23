// The page gets its URLs from the template (url_for), which adds the /{slug}
// prefix once the app is deployed. Do not write "/issues" or "/digest" here.
// The one root-absolute URL is the EFP kit's, at /_efp/lib/, which every
// gateway serves to every app.
//
// The issues are a list kept by key between two searches of a project: a new
// search moves the issues that are still there and brings in the others
// (kit.animateList), so the eye sees what changed. Another project gets a
// list of its own, whose issues rise in one after the other. The digest is written by the model on the
// server from what Jira says then; the page shows it as text, line by line.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";

const { h } = kit;
const main = document.querySelector("main");
const issuesUrl = main.dataset.issuesUrl;
const digestUrl = main.dataset.digestUrl;

const $ = (id) => document.getElementById(id);
const form = $("search-form");
const project = $("project");
const days = $("days");
const assignee = $("assignee");
const searchButton = $("search-button");
const mineButton = $("mine-button"); // absent for a visitor who is not signed in
const digestButton = $("digest-button");
let list = $("issues"); // a new list for each project: see showIssues()
const NOT_SET_UP = ["not_configured", "connector_disabled", "connector_not_enabled", "llm_disabled"];

const items = new Map(); // issue key -> its <li>, kept between two searches
let searched = null; // the filters of the issues on the page
let digestText = "";

// Statuses, types and priorities are names a Jira chooses; these patterns
// give the common ones a colour and an icon. Anything else is neutral.
const STATUS_TONES = [
  [/done|closed|resolved|released|fixed|complete/i, "success"],
  [/block|stuck|hold|impediment/i, "error"],
  [/review|test|qa|verif/i, "secondary"],
  [/progress|doing|develop/i, "info"],
];
const TYPE_ICONS = [
  [/bug|defect|incident/i, "bug"],
  [/story/i, "bookmark"],
  [/epic/i, "zap"],
  [/sub-?task/i, "list-tree"],
  [/task|chore/i, "square-check"],
];
const PRIORITY_TONES = [
  [/highest|blocker|critical|urgent/i, "error"],
  [/high|major/i, "warning"],
  [/low|minor|trivial/i, "ghost"],
];

function pick(rules, text, fallback) {
  const found = rules.find(([pattern]) => pattern.test(text || ""));
  return found ? found[1] : fallback;
}

// The colour of a status as CSS: a colour of the theme, or a grey made from the text's.
function statusColor(status) {
  const tone = pick(STATUS_TONES, status, null);
  return tone ? `var(--color-${tone})` : "color-mix(in oklab, var(--color-base-content) 38%, var(--color-base-100))";
}

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function failure(response) {
  if (response.status === 401) {
    return { code: "sign_in", message: "Your session has ended. Reload the page to sign in again." };
  }
  try {
    const body = await response.json();
    return { code: (body.error && body.error.code) || "", message: (body.error && body.error.message) || "The app answered with an error (" + response.status + ")." };
  } catch (error) {
    return { code: "", message: "The app answered with an error (" + response.status + ")." };
  }
}

function filters() {
  return { project: project.value.trim(), days: days.value, assignee: assignee.value.trim() };
}

function say(text) {
  $("status").textContent = text;
}

function plural(count, word) {
  return count.toLocaleString("en") + " " + word + (count === 1 ? "" : "s");
}

// Jira sends the day an issue was updated; "today", "yesterday", "3 days ago" reads faster.
function dayAgo(day) {
  if (!day) return "";
  const then = new Date(day + "T00:00:00");
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const gone = Math.round((today - then) / 86400000);
  if (Number.isNaN(gone)) return day;
  if (gone <= 0) return "today";
  if (gone === 1) return "yesterday";
  return gone < 14 ? gone + " days ago" : then.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

// A problem: an empty state with what to do, in place of the results.
function showProblem(problem) {
  $("problem").hidden = !problem;
  if (!problem) {
    $("problem").replaceChildren();
    return;
  }
  const [icon, title] = NOT_SET_UP.includes(problem.code)
    ? ["plug-zap", "Not set up yet"]
    : problem.code === "bad_input"
      ? ["text-cursor-input", "Check what you typed"]
      : problem.code === "jira_refused"
        ? ["ban", "Jira did not accept this"]
        : ["cloud-alert", "Jira could not be read"];
  $("problem").replaceChildren(
    h(
      "div",
      { class: "efp-surface efp-empty" },
      h("span", { class: "efp-empty-icon" }, kit.icon(icon)),
      h("strong", { text: title }),
      h("span", { class: "text-sm max-w-md", text: problem.message }),
    ),
  );
  kit.rise($("problem").firstChild);
}

// --- The issues ---------------------------------------------------------------------------------

// Every text here came from Jira: it goes in as text, never as markup.
function fillIssue(item, issue) {
  const priority = issue.priority ? pick(PRIORITY_TONES, issue.priority, "neutral") : null;
  item.style.setProperty("--jd-tone", statusColor(issue.status));
  item.replaceChildren(
    h(
      "div",
      { class: "jd-issue-main" },
      h("span", { class: "jd-type", title: issue.type || "Issue" }, kit.icon(pick(TYPE_ICONS, issue.type, "circle-dot"), "size-4")),
      h(
        "div",
        { class: "min-w-0" },
        h(
          "div",
          { class: "jd-key-line" },
          issue.url
            ? h("a", { class: "jd-key link link-hover", href: issue.url, target: "_blank", rel: "noopener noreferrer", text: issue.key })
            : h("span", { class: "jd-key", text: issue.key }),
          priority ? h("span", { class: "jd-priority jd-priority-" + priority, title: "Priority: " + issue.priority, text: issue.priority }) : null,
        ),
        h("p", { class: "jd-summary", title: issue.summary, text: issue.summary }),
      ),
    ),
    h("span", { class: "jd-status" }, h("span", { class: "jd-status-dot", "aria-hidden": "true" }), h("span", { text: issue.status || "No status" })),
    h(
      "span",
      { class: "jd-assignee" },
      issue.assignee === "Unassigned" ? h("span", { class: "jd-nobody", "aria-hidden": "true" }, kit.icon("user-round-x", "size-3.5")) : kit.avatar(issue.assignee, { size: "1.6rem" }),
      h("span", { class: "truncate", text: issue.assignee }),
    ),
    h("time", { class: "jd-updated", datetime: issue.updated, title: issue.updated, text: dayAgo(issue.updated) }),
  );
}

// The statuses of the list as one bar, and a line under it that names them.
function showMix(issues) {
  const counts = new Map();
  for (const issue of issues) counts.set(issue.status || "No status", (counts.get(issue.status || "No status") || 0) + 1);
  const statuses = Array.from(counts).sort((a, b) => b[1] - a[1]);
  const mix = $("mix");
  mix.setAttribute("aria-label", statuses.map(([name, count]) => count + " " + name).join(", "));
  mix.replaceChildren(
    ...statuses.map(([name, count], index) => h("span", { class: "jd-mix-part", title: count + " " + name, style: { flexGrow: count, "--jd-tone": statusColor(name), "--i": index } })),
  );
  $("legend").replaceChildren(
    ...statuses.map(([name, count]) => h("li", { style: { "--jd-tone": statusColor(name) } }, h("span", { class: "jd-status-dot" }), h("span", { text: name }), h("span", { class: "efp-number text-base-content/50", text: String(count) }))),
  );
}

function showIssues(found, wanted) {
  const fresh = !searched || searched.project !== wanted.project;
  searched = wanted;
  $("start").hidden = true;
  $("loading").hidden = true;
  $("results").hidden = false;
  showProblem(null);

  if (fresh) {
    const made = h("ul", { id: "issues", class: "jd-issues", "aria-label": "Issues" });
    list.replaceWith(made);
    list = made;
    items.clear();
  }
  const keep = new Set(found.issues.map((issue) => issue.key));
  for (const [key, item] of items) {
    if (!keep.has(key)) {
      items.delete(key);
      item.remove();
    }
  }
  const added = [];
  found.issues.forEach((issue, index) => {
    let item = items.get(issue.key);
    if (!item) {
      item = h("li", { class: "jd-issue", dataset: { key: issue.key } });
      items.set(issue.key, item);
      added.push(item);
    }
    fillIssue(item, issue);
    const after = index === 0 ? list.firstChild : items.get(found.issues[index - 1].key).nextSibling;
    if (item !== after) list.insertBefore(item, after);
  });
  if (fresh) {
    kit.rise(added.slice(0, 20), { stagger: 35 });
    kit.animateList(list);
  }

  const shown = found.issues.length;
  kit.countUp($("count"), shown, { duration: 700 });
  $("count-word").textContent = shown === 1 ? "issue" : "issues";
  $("results-note").textContent =
    "Updated in the last " + plural(Number(wanted.days), "day") + " in " + wanted.project + (wanted.assignee ? ", assigned to " + wanted.assignee : "") +
    (shown < found.total ? ". The " + shown + " most recently updated of " + found.total + "." : ".");
  showMix(found.issues);
  $("none").hidden = shown > 0;
  $("none").replaceChildren(
    shown
      ? ""
      : h(
          "div",
          { class: "efp-empty" },
          h("span", { class: "efp-empty-icon" }, kit.icon("coffee")),
          h("strong", { text: "Nothing was updated in this period" }),
          h("span", { class: "text-sm", text: Number(wanted.days) < 30 ? "Try more days back." : "A quiet month: nothing to sum up." }),
        ),
  );
  digestButton.disabled = shown === 0;
  say(wanted.project + ": " + plural(shown, "issue") + " updated in the last " + plural(Number(wanted.days), "day"));
}

async function search() {
  const wanted = filters();
  if (!form.reportValidity()) return;
  hideDigest();
  digestButton.disabled = true;
  if (!$("results").hidden) {
    list.classList.add("is-stale"); // the old list stays in place, dimmed, until the new one is there
  } else {
    $("start").hidden = true;
    $("problem").hidden = true;
    $("loading").hidden = false;
  }
  say("Asking Jira...");
  const work = async () => {
    let response;
    try {
      response = await fetch(issuesUrl + "?" + new URLSearchParams(wanted));
    } catch (error) {
      throw { code: "unreachable", message: "The app cannot be reached right now." };
    }
    if (!response.ok) throw await failure(response);
    showIssues(await response.json(), wanted);
  };
  try {
    await kit.busy(searchButton, work);
  } catch (problem) {
    say(problem.message || "Something went wrong.");
    $("start").hidden = true;
    $("loading").hidden = true;
    $("results").hidden = true;
    searched = null;
    showProblem(problem);
  } finally {
    list.classList.remove("is-stale");
  }
}

// --- The digest -------------------------------------------------------------------------------------

function hideDigest() {
  $("digest-card").hidden = true;
  $("copy").hidden = true;
  digestText = "";
}

// What the model wrote, as text: a line that starts with "-", "*" or a bullet
// becomes an item of a list, any other line a paragraph.
function digestNodes(text) {
  const nodes = [];
  let bullets = null;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const bullet = /^([-*\u2022]|\d+[.)])\s+/.exec(line);
    if (bullet) {
      if (!bullets) nodes.push((bullets = h("ul", {})));
      bullets.append(h("li", { text: line.slice(bullet[0].length) }));
    } else {
      bullets = null;
      nodes.push(h("p", { text: line }));
    }
  }
  return nodes;
}

digestButton.addEventListener("click", async () => {
  if (!searched) return;
  const card = $("digest-card");
  const body = $("digest");
  const wasHidden = card.hidden;
  card.hidden = false;
  card.classList.add("is-writing");
  $("copy").hidden = true;
  $("digest-note").textContent = "The model is reading the issues...";
  // Lines of a skeleton where the text will be: the card keeps its place while the model writes.
  body.replaceChildren(...[92, 100, 84, 96, 70].map((width) => h("div", { class: "skeleton h-3.5 rounded", style: { width: width + "%" } })));
  if (wasHidden) kit.rise(card);
  say("Writing a digest...");
  try {
    const answer = await kit.busy(digestButton, async () => {
      let response;
      try {
        response = await fetch(digestUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(searched) });
      } catch (error) {
        throw { message: "The app cannot be reached right now." };
      }
      if (!response.ok) throw await failure(response);
      return response.json();
    });
    digestText = answer.text;
    body.replaceChildren(...digestNodes(answer.text));
    $("digest-note").textContent = answer.issues ? "From " + plural(answer.issues, "issue") + " of " + searched.project + ", as Jira had them just now" : "Nothing to sum up";
    $("copy").hidden = !answer.issues;
    kit.rise(body.querySelectorAll("p, li"), { stagger: 70, distance: 8 });
    kit.pop(card.querySelector(".jd-digest-icon"));
    say("The digest is written");
  } catch (problem) {
    body.replaceChildren(h("div", { class: "alert alert-soft alert-error text-sm" }, kit.icon("circle-alert", "size-4"), h("span", { text: problem.message || "The digest could not be written." })));
    $("digest-note").textContent = "";
    say(problem.message || "The digest could not be written.");
  } finally {
    card.classList.remove("is-writing");
  }
});

$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(digestText);
    kit.toast("The digest is copied", { kind: "success", timeout: 2500 });
  } catch (error) {
    kit.toast("The browser did not allow copying. Select the text instead.", { kind: "warning" });
  }
});

// --- The form -----------------------------------------------------------------------------------------

function showDays() {
  $("days-out").textContent = plural(Number(days.value), "day");
}

days.addEventListener("input", showDays);
days.addEventListener("change", () => kit.pop($("days-out"), { scale: 1.12 }));
project.addEventListener("input", () => {
  // Jira's keys are upper case; the server checks the pattern again.
  const at = project.selectionStart;
  project.value = project.value.toUpperCase();
  project.setSelectionRange(at, at);
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});

if (mineButton) {
  $("me").append(kit.avatar(mineButton.dataset.username, { size: "2rem" }));
  mineButton.addEventListener("click", () => {
    assignee.value = mineButton.dataset.username;
    kit.flash(assignee.closest(".input"));
    search();
  });
}

await kit.ready();
kit.themeSwitch($("theme"));
showDays();
project.focus();
