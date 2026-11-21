// Talking to the backend. Every URL is relative ("api/maps", never
// "/api/maps"): the app is served under /{slug}/ once deployed.

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
// that can be shown as it is. "keepalive" lets a save finish while the tab
// closes; browsers allow that for small bodies only (64 KiB, counted in
// bytes: a character outside ASCII is two or three of them).
export async function api(method, url, body, keepalive) {
  const request = { method };
  if (body !== undefined) {
    request.headers = { "Content-Type": "application/json" };
    request.body = JSON.stringify(body);
  }
  request.keepalive = Boolean(keepalive) && (!request.body || new Blob([request.body]).size < 60000);
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

export const listMaps = () => api("GET", "api/maps");
export const createMap = (title, nodes) => api("POST", "api/maps", nodes ? { title, nodes } : { title });
export const readMap = (ownerId, mapId) => api("GET", "api/maps/" + ownerId + "/" + mapId);
export const saveMap = (mapId, map, keepalive) => api("PUT", "api/maps/" + mapId, map, keepalive);
export const changeMap = (mapId, change) => api("PATCH", "api/maps/" + mapId, change);
export const deleteMap = (mapId) => api("DELETE", "api/maps/" + mapId);
