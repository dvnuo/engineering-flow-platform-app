// Every URL in this file is relative ("api/explain", not "/api/explain"):
// the app is served under /{slug}/ once deployed.

const form = document.getElementById("explain-form");
const button = document.getElementById("explain-button");
const log = document.getElementById("log");
const answer = document.getElementById("answer");

// The backend cuts the log anyway; sending only its end keeps the request small.
const MAX_SENT = 100000;

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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  answer.textContent = "";
  answer.classList.remove("problem");
  button.disabled = true;
  try {
    const response = await fetch("api/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: log.value.slice(-MAX_SENT) }),
    });
    if (!response.ok) {
      answer.classList.add("problem");
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
    button.disabled = false;
  }
});
