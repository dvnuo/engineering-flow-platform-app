// The page gets its URLs from the template (url_for), which adds the /{slug}
// prefix once the app is deployed. Do not write "/issues" or "/digest" here.

const main = document.querySelector("main");
const issuesUrl = main.dataset.issuesUrl;
const digestUrl = main.dataset.digestUrl;

const form = document.getElementById("search-form");
const project = document.getElementById("project");
const days = document.getElementById("days");
const assignee = document.getElementById("assignee");
const searchButton = document.getElementById("search-button");
const mineButton = document.getElementById("mine-button");
const digestButton = document.getElementById("digest-button");
const status = document.getElementById("status");
const digest = document.getElementById("digest");
const table = document.getElementById("issues");
const rows = table.querySelector("tbody");

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function errorMessage(response) {
  if (response.status === 401) {
    return "Your session has ended. Reload the page to sign in again.";
  }
  try {
    const body = await response.json();
    return (body.error && body.error.message) || "The app answered with an error (" + response.status + ").";
  } catch (error) {
    return "The app answered with an error (" + response.status + ").";
  }
}

function filters() {
  return { project: project.value.trim(), days: days.value, assignee: assignee.value.trim() };
}

function say(text, isProblem) {
  status.textContent = text;
  status.classList.toggle("problem", Boolean(isProblem));
}

function cell(text) {
  const element = document.createElement("td");
  element.textContent = text;
  return element;
}

function show(found) {
  rows.replaceChildren();
  for (const issue of found.issues) {
    const row = document.createElement("tr");
    const key = document.createElement("td");
    if (issue.url) {
      const link = document.createElement("a");
      link.href = issue.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = issue.key;
      key.append(link);
    } else {
      key.textContent = issue.key;
    }
    row.append(key, cell(issue.summary), cell(issue.status), cell(issue.assignee), cell(issue.updated));
    rows.append(row);
  }
  table.hidden = found.issues.length === 0;
  digestButton.disabled = found.issues.length === 0;
  say(
    found.issues.length === 0
      ? "Nothing was updated in this period."
      : found.issues.length < found.total
        ? "Showing the " + found.issues.length + " most recently updated of " + found.total + " issues."
        : found.issues.length + (found.issues.length === 1 ? " issue." : " issues.")
  );
}

async function search() {
  digest.textContent = "";
  digestButton.disabled = true;
  searchButton.disabled = true;
  say("Asking Jira...");
  try {
    const response = await fetch(issuesUrl + "?" + new URLSearchParams(filters()));
    if (!response.ok) {
      table.hidden = true;
      say(await errorMessage(response), true);
      return;
    }
    show(await response.json());
  } catch (error) {
    say("The app cannot be reached right now.", true);
  } finally {
    searchButton.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});

if (mineButton) {
  mineButton.addEventListener("click", () => {
    assignee.value = mineButton.dataset.username;
    if (form.reportValidity()) {
      search();
    }
  });
}

digestButton.addEventListener("click", async () => {
  digestButton.disabled = true;
  digest.textContent = "Writing...";
  try {
    const response = await fetch(digestUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(filters()),
    });
    digest.textContent = response.ok ? (await response.json()).text : await errorMessage(response);
  } catch (error) {
    digest.textContent = "The app cannot be reached right now.";
  } finally {
    digestButton.disabled = false;
  }
});
