import { decodeComponent, encodeComponent, parseUrl } from "../lib/url.js";
import { checkbox, copyButton, el, message, plural, table, textarea } from "../ui.js";

export default {
  id: "url",
  title: "URL",
  blurb: "Percent-encode or decode a piece of a URL, and see the query parameters of a whole one as a table.",
  mount(root) {
    const input = textarea({ rows: 4, "aria-label": "URL or text", placeholder: "https://example.com/search?q=two+words&page=2" });
    const plus = checkbox("When decoding, read + as a space (form style)", true);
    const verdict = message();
    const output = textarea({ rows: 4, readonly: true, "aria-label": "Result" });
    const apart = el("div", { class: "block" });
    let parameters = [];

    function convert(action) {
      const done = action(input.value);
      output.value = done.ok ? done.text : "";
      if (done.ok) {
        verdict.say(input.value ? "" : "Type or paste something first.");
      } else {
        verdict.fail(done.message);
      }
    }

    function takeApart() {
      apart.replaceChildren(el("h3", { text: "Taken apart" }));
      parameters = [];
      if (!input.value.trim()) {
        apart.append(el("p", { class: "muted", text: "Paste a whole URL (or just its query string) to see its parts." }));
        return;
      }
      const parsed = parseUrl(input.value);
      if (!parsed.ok) {
        apart.append(el("p", { class: "muted", text: parsed.message }));
        return;
      }
      if (parsed.parts.length) {
        apart.append(table(["Part", "Value"], parsed.parts.map((part) => [part.name, part.value]), "values"));
      }
      const tables = [
        ["Query parameters", parsed.params],
        ["Parameters in the fragment", parsed.fragmentParams],
      ];
      for (const [title, pairs] of tables) {
        if (pairs.length) {
          apart.append(el("h3", { text: title + " (" + pairs.length + ")" }), table(["Name", "Value, decoded"], pairs.map((pair) => [pair.key, pair.value]), "values"));
        }
      }
      parameters = parsed.params;
      if (!parsed.params.length && !parsed.fragmentParams.length) {
        apart.append(el("p", { class: "muted", text: "This URL has no query parameters." }));
      } else {
        apart.append(el("p", { class: "muted", text: plural(parsed.params.length, "query parameter") + ". A name can repeat; the order is kept." }));
      }
    }

    const encode = el("button", { type: "button", class: "primary", text: "Encode" });
    encode.addEventListener("click", () => convert(encodeComponent));
    const decode = el("button", { type: "button", text: "Decode" });
    decode.addEventListener("click", () => convert((text) => decodeComponent(text, { plusAsSpace: plus.input.checked })));
    input.addEventListener("input", takeApart);

    root.append(
      input,
      el("div", { class: "row" }, encode, decode, plus.node),
      verdict.node,
      output,
      el("div", { class: "row" }, copyButton(() => output.value)),
      apart,
      el("div", { class: "row" }, copyButton(() => (parameters.length ? JSON.stringify(parameters.map((pair) => [pair.key, pair.value]), null, 2) : ""), "Copy parameters as JSON")),
    );
    takeApart();
  },
};
