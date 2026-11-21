// A relative URL ("data.json", not "/data.json"): the app is served under
// /{slug}/ once deployed, and a URL that starts with "/" would leave it.

const items = document.getElementById("items");

function show(text) {
  const row = document.createElement("li");
  row.textContent = text;
  items.appendChild(row);
}

fetch("data.json")
  .then((response) => {
    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }
    return response.json();
  })
  .then((data) => data.items.forEach(show))
  .catch(() => show("The data could not be loaded."));
