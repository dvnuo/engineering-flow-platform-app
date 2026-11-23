import { decodeComponent, encodeComponent, parseUrl } from "../lib/url.js";
import { button, checkbox, copyButton, editor, el, empty, message, motion, pane, plural, row, table } from "../ui.js";

export default {
  id: "url",
  title: "URL",
  icon: "link",
  keywords: "percent encode decode query parameters escape",
  blurb: "Percent-encode or decode a piece of a URL, and see the query parameters of a whole one as a table.",
  mount(root) {
    const input = editor({ rows: 4, "aria-label": "URL or text", placeholder: "https://example.com/search?q=two+words&page=2" });
    const plus = checkbox("When decoding, read + as a space (form style)", true);
    const verdict = message();
    const output = editor({ rows: 4, readonly: true, "aria-label": "Result", placeholder: "Press Encode or Decode." });
    const apart = el("div", { class: "dt-stack" });
    let parameters = [];

    const result = pane("Result", output, copyButton(() => output.value));
    function convert(action) {
      const done = action(input.value);
      output.value = done.ok ? done.text : "";
      if (!done.ok) {
        verdict.fail(done.message);
      } else if (!input.value) {
        verdict.say("Type or paste something first.");
      } else {
        verdict.say("");
        motion("flash", result);
      }
    }

    function takeApart() {
      parameters = [];
      if (!input.value.trim()) {
        apart.replaceChildren(empty("list-tree", "Nothing to take apart yet", "Paste a whole URL, or just its query string, to see its parts and parameters."));
        return;
      }
      const parsed = parseUrl(input.value);
      if (!parsed.ok) {
        apart.replaceChildren(el("p", { class: "dt-note", text: parsed.message }));
        return;
      }
      apart.replaceChildren();
      if (parsed.parts.length) {
        apart.append(pane("Parts", table(["Part", "Value"], parsed.parts.map((part) => [part.name, part.value]), "dt-values")));
      }
      const tables = [
        ["Query parameters", parsed.params],
        ["Parameters in the fragment", parsed.fragmentParams],
      ];
      for (const [title, pairs] of tables) {
        if (pairs.length) {
          const copy = pairs === parsed.params ? copyButton(() => JSON.stringify(pairs.map((pair) => [pair.key, pair.value]), null, 2), "Copy as JSON") : null;
          apart.append(pane(title + " (" + pairs.length + ")", table(["Name", "Value, decoded"], pairs.map((pair) => [pair.key, pair.value]), "dt-values"), copy));
        }
      }
      parameters = parsed.params;
      apart.append(
        el("p", {
          class: "dt-note",
          text: !parsed.params.length && !parsed.fragmentParams.length ? "This URL has no query parameters." : plural(parsed.params.length, "query parameter") + ". A name can repeat; the order is kept.",
        }),
      );
    }

    input.addEventListener("input", takeApart);
    root.append(
      pane("Text or URL", input),
      row(
        button("Encode", { icon: "lock", kind: "primary", onClick: () => convert(encodeComponent) }),
        button("Decode", { icon: "lock-open", kind: "soft", onClick: () => convert((text) => decodeComponent(text, { plusAsSpace: plus.input.checked })) }),
        plus.node,
      ),
      verdict.node,
      result,
      el("h3", { class: "dt-subhead", text: "Taken apart" }),
      apart,
    );
    takeApart();
  },
};
