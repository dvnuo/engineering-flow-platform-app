// Every URL in this file is relative ("api/overview", not "/api/overview"):
// the app is served under /{slug}/ once deployed.

const status = document.getElementById("status");
const content = document.getElementById("content");
const facts = document.getElementById("facts");
const model = document.getElementById("model");
const pingButton = document.getElementById("ping-button");
const pingResult = document.getElementById("ping-result");
const connectorRows = document.querySelector("#connectors tbody");
const tryForm = document.getElementById("try-form");
const type = document.getElementById("type");
const path = document.getElementById("path");
const tryButton = document.getElementById("try-button");
const tryStatus = document.getElementById("try-status");
const tryBody = document.getElementById("try-body");

// A harmless path to start from, per kind of connector.
const FIRST_PATH = {
  jira: "/rest/api/2/serverInfo",
  confluence: "/rest/api/space?limit=5",
  github: "/rate_limit",
  jenkins: "/api/json?tree=jobs[name,color]",
  nexus: "/service/rest/v1/repositories",
  splunk: "/services/server/info?output_mode=json",
};

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function errorMessage(response) {
  try {
    const body = await response.json();
    if (body.error && body.error.message) {
      return body.error.message;
    }
  } catch (error) {
    // not JSON
  }
  return response.status === 401
    ? "Your session has ended. Reload the page to sign in again."
    : "The app answered with an error (" + response.status + ").";
}

function postJson(url, data) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {}),
  });
}

function fact(table, name, value) {
  const row = document.createElement("tr");
  const head = document.createElement("th");
  head.textContent = name;
  const cell = document.createElement("td");
  cell.textContent = value;
  row.append(head, cell);
  table.append(row);
}

function used(spent, limit, unit) {
  if (limit === null || limit === undefined) {
    return "no limit";
  }
  return (spent || 0) + " of " + limit + " " + unit + " used today";
}

function show(overview) {
  facts.replaceChildren();
  fact(facts, "Name", overview.app.name || "");
  fact(facts, "Address", overview.app.url || "");
  fact(facts, "Owner", (overview.owner.name || overview.owner.username || "") + (overview.owner.group ? " (" + overview.owner.group + ")" : ""));
  fact(facts, "Running", overview.app.deployed ? "on the app host" : "on a developer's machine");
  fact(facts, "Storage", overview.storage.mode);

  model.replaceChildren();
  fact(model, "State", overview.model.enabled ? "on" : "off: turn it on for this app in the Portal");
  fact(model, "Default model", overview.model.default || "");
  fact(model, "Allowed models", overview.model.models.join(", "));
  fact(model, "Credits", used(overview.budget.used_credits, overview.budget.daily_credits, "credits"));
  fact(model, "Tokens", used(overview.budget.used_tokens, overview.budget.daily_tokens, "tokens"));
  pingButton.disabled = !overview.model.enabled;

  connectorRows.replaceChildren();
  type.replaceChildren();
  for (const connector of overview.connectors) {
    const row = document.createElement("tr");
    for (const text of [connector.type, connector.address || "", connector.write ? "read and write" : "read"]) {
      const cell = document.createElement("td");
      cell.textContent = text;
      row.append(cell);
    }
    connectorRows.append(row);
    const option = document.createElement("option");
    option.value = connector.type;
    option.textContent = connector.type;
    type.append(option);
  }
  if (overview.connectors.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 3;
    cell.textContent = "No connector is turned on for this app. Turn one on in the Portal.";
    row.append(cell);
    connectorRows.append(row);
  }
  tryForm.hidden = overview.connectors.length === 0;
  path.value = FIRST_PATH[type.value] || "/";
}

async function load() {
  try {
    const response = await fetch("api/overview");
    if (!response.ok) {
      status.classList.add("problem");
      status.textContent = await errorMessage(response);
      return;
    }
    show(await response.json());
    status.textContent = "What this app may use, as the Portal reports it.";
    content.hidden = false;
  } catch (error) {
    status.textContent = "The app cannot be reached right now.";
  }
}

type.addEventListener("change", () => {
  path.value = FIRST_PATH[type.value] || "/";
});

tryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  tryButton.disabled = true;
  tryStatus.textContent = "Asking...";
  tryBody.textContent = "";
  try {
    const response = await postJson("api/try", { type: type.value, path: path.value });
    if (!response.ok) {
      tryStatus.textContent = await errorMessage(response);
      return;
    }
    const answer = await response.json();
    tryStatus.textContent =
      "Status " + answer.status + (answer.content_type ? ", " + answer.content_type : "") + (answer.cut ? " (cut)" : "");
    tryBody.textContent = answer.body;
  } catch (error) {
    tryStatus.textContent = "The app cannot be reached right now.";
  } finally {
    tryButton.disabled = false;
  }
});

pingButton.addEventListener("click", async () => {
  pingButton.disabled = true;
  pingResult.textContent = "Calling...";
  try {
    const response = await postJson("api/ping-model");
    if (!response.ok) {
      pingResult.textContent = await errorMessage(response);
      return;
    }
    const answer = await response.json();
    pingResult.textContent =
      '"' + answer.text.trim() + '" from ' + answer.model + ": " + answer.input_tokens + " tokens in, " +
      answer.output_tokens + " out" + (answer.credits === null ? "" : ", " + answer.credits + " credits") + ".";
    load();
  } catch (error) {
    pingResult.textContent = "The app cannot be reached right now.";
  } finally {
    pingButton.disabled = false;
  }
});

load();
