// Talking to the backend. Every URL is relative ("api/board", never
// "/api/board"): the app is served under /{slug}/ once deployed.

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// The backend and the gateway both answer errors as {"error": {"code", "message"}}.
async function failure(response) {
  let code = "";
  let message = "";
  try {
    const body = await response.json();
    code = (body.error && body.error.code) || "";
    message = (body.error && body.error.message) || "";
  } catch (error) {
    // not JSON
  }
  if (response.status === 401) {
    message = "Your session has ended. Reload the page to sign in again.";
  }
  return new ApiError(message || "The app answered with an error (" + response.status + ").", response.status, code);
}

// One request; the answer's JSON, or an ApiError whose message is a sentence
// that can be shown as it is.
async function api(method, url, body) {
  const request = { method };
  if (body !== undefined) {
    request.headers = { "Content-Type": "application/json" };
    request.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, request);
  } catch (error) {
    throw new ApiError("The app cannot be reached right now.", 0, "unreachable");
  }
  if (!response.ok) {
    throw await failure(response);
  }
  return response.json();
}

// Every call answers with the whole board as this visitor may see it.
export const readBoard = (pastId) => api("GET", pastId ? "api/board?" + new URLSearchParams({ board: pastId }) : "api/board");
export const addCard = (column, text) => api("POST", "api/cards", { column, text });
export const changeCard = (id, text, column) => api("PUT", "api/cards/" + id, { text, column });
export const deleteCard = (id) => api("DELETE", "api/cards/" + id);
export const giveVote = (id) => api("PUT", "api/cards/" + id + "/vote");
export const takeVoteBack = (id) => api("DELETE", "api/cards/" + id + "/vote");
export const changeState = (change) => api("POST", "api/board/state", change);
