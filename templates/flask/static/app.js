// The page gets its URLs from the template (url_for), which adds the /{slug}
// prefix once the app is deployed. Do not write "/ask" or "/note" here.

const main = document.querySelector("main");
const askUrl = main.dataset.askUrl;
const noteUrl = main.dataset.noteUrl;

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

function postJson(url, data) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

askForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  answer.textContent = "";
  askButton.disabled = true;
  try {
    const response = await postJson(askUrl, { text: question.value });
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
    const response = await postJson(noteUrl, { text: note.value });
    noteStatus.textContent = response.ok ? "Saved." : await errorMessage(response);
  } catch (error) {
    noteStatus.textContent = "The note could not be saved.";
  }
});
