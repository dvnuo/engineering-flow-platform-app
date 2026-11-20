// Talking to the backend. Every URL is relative ("api/drawings", never
// "/api/drawings"): the app is served under /{slug}/ once deployed.

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
async function send(url, request) {
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

export const loadGallery = () => send("api/drawings", {});

// The picture is the body of the request, as it is: no form, no base64. The
// title travels in the query, encoded by URLSearchParams.
export const saveDrawing = (title, picture) =>
  send("api/drawings?" + new URLSearchParams({ title }), {
    method: "POST",
    headers: { "Content-Type": "image/png" },
    body: picture,
  });

export const deleteDrawing = (authorId, id) => send("api/drawings/" + authorId + "/" + id, { method: "DELETE" });

// Where an <img> finds a saved picture; relative, like everything else.
export const imageUrl = (authorId, id) => "api/drawings/" + authorId + "/" + id + "/image";
