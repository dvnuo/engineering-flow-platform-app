// Every URL in this file is relative ("api/notes", not "/api/notes"): the
// app is served under /{slug}/ once deployed.

const form = document.getElementById("notes-form");
const repository = document.getElementById("repository");
const base = document.getElementById("base");
const head = document.getElementById("head");
const tags = document.getElementById("tags");
const draftButton = document.getElementById("draft-button");
const againButton = document.getElementById("again-button");
const copyButton = document.getElementById("copy-button");
const status = document.getElementById("status");
const notes = document.getElementById("notes");

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

function say(text, isProblem) {
  status.textContent = text;
  status.classList.toggle("problem", Boolean(isProblem));
}

// Suggest the repository's tags in the two fields. A convenience: when it
// fails, the fields still take what is typed.
repository.addEventListener("change", async () => {
  tags.replaceChildren();
  if (!repository.value.trim()) {
    return;
  }
  try {
    const response = await fetch("api/tags?" + new URLSearchParams({ repository: repository.value.trim() }));
    if (!response.ok) {
      say(await errorMessage(response), true);
      return;
    }
    say("");
    for (const name of (await response.json()).tags) {
      const option = document.createElement("option");
      option.value = name;
      tags.append(option);
    }
  } catch (error) {
    // The suggestions are optional.
  }
});

async function draft(again) {
  draftButton.disabled = true;
  againButton.hidden = true;
  copyButton.hidden = true;
  notes.textContent = "";
  say(again ? "Writing a new draft..." : "Reading the commits...");
  try {
    const response = await fetch("api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repository: repository.value.trim(),
        base: base.value.trim(),
        head: head.value.trim(),
        again: again,
      }),
    });
    if (!response.ok) {
      say(await errorMessage(response), true);
      return;
    }
    const found = await response.json();
    if (!found.notes) {
      say(found.message || "There is nothing to write about.");
      return;
    }
    notes.textContent = found.notes;
    say(
      found.commits + (found.commits === 1 ? " commit. " : " commits. ") +
        (found.cached ? "This draft was written earlier and kept; nothing was spent." : "A new draft.")
    );
    againButton.hidden = false;
    copyButton.hidden = false;
  } catch (error) {
    say("The app cannot be reached right now.", true);
  } finally {
    draftButton.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  draft(false);
});

againButton.addEventListener("click", () => {
  if (form.reportValidity()) {
    draft(true);
  }
});

copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(notes.textContent);
    copyButton.textContent = "Copied";
  } catch (error) {
    copyButton.textContent = "Select the text to copy it";
  }
  setTimeout(() => {
    copyButton.textContent = "Copy";
  }, 2000);
});
