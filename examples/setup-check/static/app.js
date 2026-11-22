// Every URL in this file is relative ("api/overview", not "/api/overview"):
// the app is served under /{slug}/ once deployed. The one exception is the
// EFP kit at /_efp/lib/, which every gateway serves to every app.
//
// The overview is asked for when the page opens, after a test call (so that
// the budget moves on the page as it moved in the Portal) and on Refresh.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const type = $("type");
const path = $("path");
let shown = false; // the first overview is on the page

// A harmless path to start from, per kind of connector, and an icon for each.
const FIRST_PATH = {
  jira: "/rest/api/2/serverInfo",
  confluence: "/rest/api/space?limit=5",
  github: "/rate_limit",
  jenkins: "/api/json?tree=jobs[name,color]",
  nexus: "/service/rest/v1/repositories",
  splunk: "/services/server/info?output_mode=json",
};
const LOOKS = {
  jira: ["ticket", "Jira"],
  confluence: ["book-open", "Confluence"],
  github: ["git-branch", "GitHub"],
  jenkins: ["hammer", "Jenkins"],
  nexus: ["package", "Nexus"],
  splunk: ["search", "Splunk"],
};

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function failure(response) {
  try {
    const body = await response.json();
    if (body.error && body.error.message) return { code: body.error.code || "", message: body.error.message };
  } catch (error) {
    // not JSON
  }
  return response.status === 401
    ? { code: "sign_in", message: "Your session has ended. Reload the page to sign in again." }
    : { code: "", message: "The app answered with an error (" + response.status + ")." };
}

// One request (a function that starts it); the answer's JSON, or a
// {code, message} that the page can show.
async function ask(request) {
  let response;
  try {
    response = await request();
  } catch (error) {
    throw { code: "unreachable", message: "The app cannot be reached right now." };
  }
  if (!response.ok) throw await failure(response);
  return response.json();
}

function post(url, data) {
  return () => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}

function plural(count, word) {
  return count.toLocaleString("en") + " " + word + (count === 1 ? "" : "s");
}

// --- The overview -----------------------------------------------------------------------

function check({ icon, label, ok, value, note }) {
  return h(
    "div.efp-surface.efp-lift.sc-check",
    { class: ok ? "is-ok" : "is-off" },
    h("span.sc-check-icon", { "aria-hidden": "true" }, kit.icon(icon, "size-5")),
    h("div.min-w-0", {}, h("p.text-xs.text-base-content/60", {}, label), h("p.font-semibold.truncate", {}, value), h("p.text-xs.text-base-content/60.truncate", { text: note })),
    h("span.sc-check-mark", { title: ok ? "Ready" : "Not ready" }, kit.icon(ok ? "circle-check" : "circle-alert", "size-5")),
  );
}

// A bar of what is used of a daily limit; it glides to a new value on the next drawing.
function meter(id, label, used, limit, unit) {
  let bar = $(id);
  if (!bar) {
    bar = h("div", { id }, h("div.flex.flex-wrap.justify-between.gap-x-3.text-sm.mb-1.5", {}, h("span.whitespace-nowrap", {}, label), h("span.sc-meter-text.efp-number.whitespace-nowrap")), h("div.sc-meter", {}, h("span")));
  }
  const share = limit ? Math.min(1, (used || 0) / limit) : 0;
  bar.querySelector(".sc-meter-text").textContent = limit === null || limit === undefined ? "no limit" : (used || 0).toLocaleString("en", { maximumFractionDigits: 3 }) + " of " + limit.toLocaleString("en") + " " + unit;
  bar.querySelector(".sc-meter").classList.toggle("is-high", share > 0.8);
  // Two frames: a new bar is drawn at zero first, so that it grows in.
  requestAnimationFrame(() => requestAnimationFrame(() => (bar.querySelector(".sc-meter > span").style.width = share * 100 + "%")));
  return bar;
}

function show(overview) {
  const { app, owner, model, budget, connectors } = overview;
  $("loading").hidden = true;
  $("problem").hidden = true;
  $("content").hidden = false;
  $("refresh").hidden = false;

  $("hero").replaceChildren(
    kit.avatar(owner.name || owner.username, { size: "3.25rem" }),
    h(
      "div.min-w-0.grow",
      {},
      h("h2.text-xl.font-semibold.truncate", { text: app.name || app.slug || "This app" }),
      app.url ? h("a.link.link-hover.text-sm.text-base-content/70.break-all", { href: app.url, target: "_blank", rel: "noopener noreferrer" }, app.url) : null,
      h("p.text-sm.text-base-content/60", {}, "Owned by ", h("strong", { text: owner.name || owner.username }), owner.group ? " (" + owner.group + ")" : ""),
    ),
    h(
      "div.sc-badges",
      {},
      h("span.badge.badge-soft", { class: app.deployed ? "badge-success" : "badge-info" }, kit.icon(app.deployed ? "cloud" : "laptop", "size-3.5"), app.deployed ? "On the app host" : "On a developer's machine"),
      h("span.badge.badge-soft", {}, kit.icon("database", "size-3.5"), "Storage: " + overview.storage.mode),
    ),
  );

  const left = budget.daily_credits === null || budget.daily_credits === undefined ? null : Math.max(0, budget.daily_credits - (budget.used_credits || 0));
  $("checks").replaceChildren(
    check({ icon: "bot", label: "Language model", ok: model.enabled, value: model.enabled ? "On" : "Off", note: model.enabled ? model.default || "" : "Turn it on in the Portal" }),
    check({ icon: "coins", label: "Budget left today", ok: left === null || left > 0, value: left === null ? "No limit" : left.toLocaleString("en", { maximumFractionDigits: 2 }) + " credits", note: left === null ? "" : "of " + budget.daily_credits + " credits" }),
    check({ icon: "plug", label: "Connectors", ok: connectors.length > 0, value: connectors.length ? plural(connectors.length, "connector") : "None", note: connectors.map((item) => item.type).join(", ") || "Turn one on in the Portal" }),
  );

  $("model").replaceChildren(
    h("dt", {}, "State"),
    h("dd", {}, h("span.badge.badge-sm.badge-soft", { class: model.enabled ? "badge-success" : "badge-warning" }, model.enabled ? "on" : "off")),
    h("dt", {}, "Default model"),
    h("dd.font-mono.text-sm", { text: model.default || "-" }),
    h("dt", {}, "Allowed models"),
    h("dd.flex.flex-wrap.gap-1", {}, model.models.length ? model.models.map((name) => h("span.badge.badge-sm.badge-ghost.font-mono", {}, name)) : "-"),
  );
  const meters = [meter("credits-meter", "Credits used today", budget.used_credits, budget.daily_credits, "credits"), meter("tokens-meter", "Tokens used today", budget.used_tokens, budget.daily_tokens, "tokens")];
  if (!$("credits-meter")) $("meters").replaceChildren(...meters);
  $("ping-button").disabled = !model.enabled;

  $("connectors").replaceChildren(
    ...connectors.map((item) =>
      h(
        "li.sc-connector",
        {},
        h("span.sc-connector-icon", { "aria-hidden": "true" }, kit.icon((LOOKS[item.type] || ["plug"])[0], "size-4")),
        h("div.min-w-0.grow", {}, h("p.font-medium", { text: (LOOKS[item.type] || [0, item.type])[1] }), h("p.font-mono.text-xs.text-base-content/60.truncate", { text: item.address || "" })),
        h("span.badge.badge-sm.badge-soft", { class: item.write ? "badge-warning" : "badge-ghost" }, item.write ? "read and write" : "read"),
      ),
    ),
  );
  if (!connectors.length) {
    $("connectors").replaceChildren(h("li.efp-empty", {}, h("span.efp-empty-icon", {}, kit.icon("plug-zap")), h("strong", {}, "No connector is on"), h("span.text-sm", {}, "Turn one on for this app in the Portal, then press Refresh.")));
  }
  $("try-section").hidden = connectors.length === 0;
  const before = type.value;
  type.replaceChildren(...connectors.map((item) => h("option", { value: item.type, selected: item.type === before }, item.type)));
  if (!path.value || type.value !== before) path.value = FIRST_PATH[type.value] || "/";

  if (!shown) {
    kit.rise([$("hero"), ...$("checks").children, ...document.querySelectorAll(".sc-panel")], { stagger: 60 });
    shown = true;
  }
}

function showProblem(problem) {
  const looks = {
    not_configured: ["plug-zap", "Not set up yet"],
    owner_only: ["lock", "Only for the owner"],
    sign_in_required: ["log-in", "Sign in first"],
    sign_in: ["log-in", "Signed out"],
  };
  const [icon, title] = looks[problem.code] || ["cloud-off", "The Portal could not be asked"];
  $("status").textContent = title; // the sentence itself is in the panel, once
  $("loading").hidden = true;
  $("content").hidden = true;
  $("problem").hidden = false;
  $("problem").replaceChildren(
    h("div.efp-surface.efp-empty.py-16", {}, h("span.efp-empty-icon", {}, kit.icon(icon)), h("strong", {}, title), h("span.text-sm.max-w-md", { text: problem.message })),
  );
}

// pending: an overview already asked for (the first one, asked while the icons load).
async function load(button, pending) {
  try {
    const request = () => pending || ask(() => fetch("api/overview"));
    const overview = await (button ? kit.busy(button, request) : request());
    show(overview);
    $("status").textContent = "What this app may use, as the Portal reports it.";
  } catch (problem) {
    if (shown) kit.toast(problem.message, { kind: "error" });
    else showProblem(problem);
  }
}

// --- A test call to the model ---------------------------------------------------------------

$("ping-button").addEventListener("click", async (event) => {
  const result = $("ping-result");
  try {
    const answer = await kit.busy(event.currentTarget, () => ask(post("api/ping-model", {})));
    const chips = [
      [kit.icon("bot", "size-3.5"), answer.model],
      [kit.icon("arrow-up", "size-3.5"), plural(answer.input_tokens, "token") + " in"],
      [kit.icon("arrow-down", "size-3.5"), plural(answer.output_tokens, "token") + " out"],
    ];
    if (answer.credits !== null) chips.push([kit.icon("coins", "size-3.5"), answer.credits + " credits"]);
    result.replaceChildren(
      h(
        "div.sc-pong",
        {},
        h("span.sc-pong-word", { text: answer.text.trim() || "(an empty answer)" }),
        h("div.flex.flex-wrap.gap-1.5", {}, chips.map((chip) => h("span.badge.badge-sm.badge-soft", {}, chip))),
      ),
    );
    kit.appear(result.firstChild);
    load(); // the budget has moved: its bars glide to the new value
  } catch (problem) {
    result.replaceChildren(h("div.alert.alert-soft.alert-warning", { role: "alert" }, kit.icon("circle-alert", "size-5"), h("span", { text: problem.message })));
  }
});

// --- Try a path ----------------------------------------------------------------------------------

function statusTone(status) {
  return status < 300 ? "success" : status < 400 ? "info" : status < 500 ? "warning" : "error";
}

$("try-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const result = $("try-result");
  try {
    const answer = await kit.busy($("try-button"), () => ask(post("api/try", { type: type.value, path: path.value })));
    const status = h("span.badge.badge-soft.font-mono", { class: "badge-" + statusTone(answer.status) }, "Status " + answer.status);
    const copy = h("button.btn.btn-xs.btn-ghost.ml-auto", { type: "button" }, kit.icon("copy", "size-3.5"), "Copy");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(answer.body);
        kit.toast("The answer is copied.", { kind: "success", timeout: 2500 });
      } catch (error) {
        kit.toast("The browser did not allow copying. Select the text to copy it.", { kind: "warning" });
      }
    });
    result.replaceChildren(
      h(
        "div.sc-answer",
        {},
        h(
          "div.sc-answer-head",
          {},
          status,
          answer.content_type ? h("span.text-xs.font-mono.text-base-content/60", { text: answer.content_type }) : null,
          answer.cut ? h("span.badge.badge-sm.badge-soft.badge-warning", { title: "Only the start of the answer is shown." }, kit.icon("scissors", "size-3"), "cut") : null,
          copy,
        ),
        h("pre.sc-body", { text: answer.body || "(an empty body)" }),
      ),
    );
    kit.rise(result.firstChild, { distance: 8 });
    kit.pop(status);
  } catch (problem) {
    result.replaceChildren(h("div.alert.alert-soft.alert-error", { role: "alert" }, kit.icon("circle-alert", "size-5"), h("span", { text: problem.message })));
  }
});

type.addEventListener("change", () => {
  path.value = FIRST_PATH[type.value] || "/";
});
$("refresh").addEventListener("click", (event) => load(event.currentTarget));

const first = ask(() => fetch("api/overview"));
first.catch(() => {}); // load() below reports a failure
await kit.ready();
kit.themeSwitch($("theme"));
load(null, first);
