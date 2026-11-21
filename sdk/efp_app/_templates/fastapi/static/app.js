// Every URL in this file is relative ("api/me", not "/api/me"). The app is
// served under /{slug}/ once deployed; a URL that starts with "/" would point
// outside the app there, although it works on your own machine.

const who = document.getElementById("who");
const askForm = document.getElementById("ask-form");
const askButton = document.getElementById("ask-button");
const question = document.getElementById("question");
const answer = document.getElementById("answer");
const noteForm = document.getElementById("note-form");
const note = document.getElementById("note");
const noteStatus = document.getElementById("note-status");

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

async function showVisitor() {
  try {
    const response = await fetch("api/me");
    if (!response.ok) {
      who.textContent = await errorMessage(response);
      return;
    }
    const me = await response.json();
    who.textContent = me.signed_in
      ? "Hello, " + me.name + (me.group ? " (" + me.group + ")" : "") + "."
      : "Hello, guest. You are not signed in.";
  } catch (error) {
    who.textContent = "The app cannot be reached right now.";
  }
}

async function loadNote() {
  try {
    const response = await fetch("api/note");
    if (!response.ok) {
      noteStatus.textContent = await errorMessage(response);
      return;
    }
    note.value = (await response.json()).text;
  } catch (error) {
    noteStatus.textContent = "The note could not be loaded.";
  }
}

askForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  answer.textContent = "";
  askButton.disabled = true;
  try {
    const response = await fetch("api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: question.value }),
    });
    if (!response.ok) {
      answer.textContent = await errorMessage(response);
      return;
    }
    // The answer arrives piece by piece as plain text.
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      answer.textContent += decoder.decode(value, { stream: true });
    }
  } catch (error) {
    answer.textContent += "\n[The connection was lost.]";
  } finally {
    askButton.disabled = false;
  }
});

noteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  noteStatus.textContent = "Saving...";
  try {
    const response = await fetch("api/note", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: note.value }),
    });
    noteStatus.textContent = response.ok ? "Saved." : await errorMessage(response);
  } catch (error) {
    noteStatus.textContent = "The note could not be saved.";
  }
});

showVisitor();
loadNote();
