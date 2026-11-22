// Every URL in this file is relative ("api/notes", not "/api/notes"): the
// app is served under /{slug}/ once deployed. The one exception is the EFP
// kit at /_efp/lib/, which every gateway serves to every app.
//
// The draft comes back as Markdown. The page draws the little of it that
// release notes use (headings, lists, **bold**, `code`, #123) as elements
// with kit.h, so nothing the model wrote is ever parsed as HTML; Copy hands
// over the Markdown itself.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const form = $("notes-form");
const repository = $("repository");
const base = $("base");
const head = $("head");

let markdown = ""; // the draft on the page, for Copy

// An icon for the headings the system prompt asks for; others get a list.
const HEADINGS = { features: "sparkles", fixes: "bug", other: "package" };

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

// --- The tags of the repository, as suggestions ---------------------------------------

// A convenience: when it fails, the fields still take what is typed.
repository.addEventListener("change", async () => {
  const hint = $("tags-hint");
  $("tags").replaceChildren();
  hint.replaceChildren();
  const name = repository.value.trim();
  if (!name) return;
  hint.replaceChildren(h("span.loading.loading-spinner.loading-xs"), "Looking for its tags...");
  try {
    const response = await fetch("api/tags?" + new URLSearchParams({ repository: name }));
    if (!response.ok) {
      // A setup problem is said once, where the draft would be; GitHub's own "no" is said here.
      const problem = await failure(response);
      const quiet = ["not_configured", "connector_disabled", "connector_not_enabled"].includes(problem.code);
      hint.replaceChildren(...(quiet ? [] : [kit.icon("circle-alert", "size-3.5 text-warning"), h("span.flex-1.min-w-0", { text: problem.message })]));
      return;
    }
    const found = (await response.json()).tags;
    $("tags").replaceChildren(...found.map((tag) => h("option", { value: tag })));
    if (found.length < 2) {
      hint.textContent = found.length ? "One tag: " + found[0] + "." : "No tags yet: a branch name works too.";
      return;
    }
    // The newest two tags are the usual range: one click fills both fields.
    const fill = h("button.btn.btn-xs.btn-soft.btn-primary.efp-press", { type: "button" }, kit.icon("wand-sparkles", "size-3"), found[1] + " to " + found[0]);
    fill.addEventListener("click", () => {
      base.value = found[1];
      head.value = found[0];
      kit.flash([base.parentElement, head.parentElement]);
    });
    hint.replaceChildren(h("span", {}, found.length + " tags found."), fill);
    kit.appear(fill);
  } catch (error) {
    hint.replaceChildren(); // the suggestions are optional
  }
});

// --- The draft ----------------------------------------------------------------------------

// **bold**, `code` and (#123) inside a line; the rest stays text.
function inline(text, pullBase) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\(#\d+\)|#\d+\b)/).map((part, index) => {
    if (index % 2 === 0) return part;
    if (part.startsWith("**")) return h("strong", {}, part.slice(2, -2));
    if (part.startsWith("`")) return h("code.rn-code", {}, part.slice(1, -1));
    const number = part.replace(/[^0-9]/g, ""); // the chip takes the place of the brackets
    return pullBase
      ? h("a.rn-ref", { href: pullBase + "/pull/" + number, target: "_blank", rel: "noopener noreferrer", title: "Pull request " + number }, "#" + number)
      : h("span.rn-ref", {}, "#" + number);
  });
}

// The Markdown of the draft as elements: headings, lists and paragraphs.
function draw(text, pullBase) {
  const blocks = [];
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    const item = line.match(/^(?:[-*+]|\d+[.)])\s+(.*)$/);
    if (item) {
      if (!list) blocks.push((list = h("ul.rn-list")));
      list.append(h("li", {}, inline(item[1], pullBase)));
      continue;
    }
    list = null;
    if (heading) {
      const title = heading[1].replace(/\*\*/g, "");
      const icon = HEADINGS[title.toLowerCase().replace(/[^a-z]/g, "")] || "list";
      blocks.push(h("h3.rn-heading", { dataset: { kind: icon } }, h("span.rn-heading-icon", { "aria-hidden": "true" }, kit.icon(icon, "size-4")), title));
    } else if (line) {
      blocks.push(h("p", {}, inline(line, pullBase)));
    }
  }
  return blocks;
}

// Exactly one of the four places below the head of the panel is shown.
function only(id) {
  for (const name of ["empty", "loading", "problem", "notes"]) $(name).hidden = name !== id;
}

function showProblem(problem) {
  const notSetUp = ["not_configured", "connector_disabled", "connector_not_enabled", "llm_disabled"].includes(problem.code);
  const quiet = notSetUp || problem.code === "nothing";
  $("problem").replaceChildren(
    quiet
      ? h(
          "div.efp-empty",
          {},
          h("span.efp-empty-icon", {}, kit.icon(notSetUp ? "plug-zap" : "git-commit-horizontal")),
          h("strong", {}, notSetUp ? "Not set up yet" : "Nothing to write about"),
          h("span.text-sm.max-w-sm", { text: problem.message }),
        )
      : h(
          "div.alert.alert-soft",
          { class: problem.code === "budget_exceeded" || problem.code === "github_refused" ? "alert-warning" : "alert-error", role: "alert" },
          kit.icon(problem.code === "budget_exceeded" ? "coins" : "circle-alert", "size-5"),
          h("span", { text: problem.message }),
        ),
  );
  only("problem");
}

function showDraft(found) {
  markdown = found.notes;
  const compare = /^https:\/\//.test(found.compare_url || "") ? found.compare_url : "";
  const pullBase = compare.includes("/compare/") ? compare.split("/compare/")[0] : "";
  $("notes").replaceChildren(...draw(found.notes, pullBase));
  only("notes");
  kit.rise($("notes").querySelectorAll(".rn-heading, li, p"), { stagger: 40, distance: 10 });

  const commits = h("span.efp-number", {}, "0");
  $("meta").replaceChildren(
    h("span.badge.badge-soft", {}, kit.icon("git-commit-horizontal", "size-3.5"), commits, found.commits === 1 ? "commit" : "commits"),
    found.cached
      ? h("span.badge.badge-soft.badge-success", { title: "Written " + kit.timeAgo(found.written_at) + "; this answer cost no model call." }, kit.icon("piggy-bank", "size-3.5"), "Kept: nothing spent")
      : h("span.badge.badge-soft.badge-primary", {}, kit.icon("sparkles", "size-3.5"), "New draft"),
    compare ? h("a.link.link-hover.text-xs.inline-flex.items-center.gap-1", { href: compare, target: "_blank", rel: "noopener noreferrer" }, "Compare on GitHub", kit.icon("arrow-up-right", "size-3")) : null,
  );
  kit.countUp(commits, found.commits, { from: 0 });
  kit.appear($("meta").children, { stagger: 70 });
  $("actions").hidden = false;
}

async function draft(again, button) {
  const wanted = { repository: repository.value.trim(), base: base.value.trim(), head: head.value.trim(), again };
  only("loading");
  $("meta").replaceChildren(h("span.text-xs.text-base-content/60", {}, again ? "Writing a new draft..." : "Reading the commits and writing the draft..."));
  $("actions").hidden = true;
  try {
    await kit.busy(button, async () => {
      let response;
      try {
        response = await fetch("api/notes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(wanted),
        });
      } catch (error) {
        throw { code: "unreachable", message: "The app cannot be reached right now." };
      }
      if (!response.ok) throw await failure(response);
      const found = await response.json();
      if (!found.notes) throw { code: "nothing", message: found.message || "There is nothing to write about." };
      showDraft(found);
    });
  } catch (problem) {
    $("meta").replaceChildren();
    showProblem(problem);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  draft(false, $("draft-button"));
});

$("again-button").addEventListener("click", (event) => {
  if (form.reportValidity()) draft(true, event.currentTarget);
});

$("copy-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  try {
    await navigator.clipboard.writeText(markdown);
    button.replaceChildren(kit.icon("check", "size-4"), "Copied");
    kit.pop(button.firstChild);
    setTimeout(() => button.replaceChildren(kit.icon("copy", "size-4"), "Copy"), 1800);
    kit.toast("The draft is copied as Markdown.", { kind: "success", timeout: 2500 });
  } catch (error) {
    kit.toast("The browser did not allow copying. Select the text to copy it.", { kind: "warning" });
  }
});

await kit.ready();
kit.themeSwitch($("theme"));
