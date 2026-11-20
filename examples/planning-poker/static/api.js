// The two plain requests of the lobby. Every URL is relative ("api/rooms",
// never "/api/rooms"): the app is served under /{slug}/ once deployed.

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

export const listRooms = () => api("GET", "api/rooms");
export const createRoom = (title, link) => api("POST", "api/rooms", { title, link: link || null });
