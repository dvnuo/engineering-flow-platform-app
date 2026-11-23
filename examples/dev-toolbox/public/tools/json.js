import { describe, excerpt, format, minify, scan } from "../lib/json.js";
import { button, copyButton, debounce, editor, el, message, motion, pane, plural, select } from "../ui.js";

export default {
  id: "json",
  title: "JSON",
  icon: "braces",
  keywords: "format pretty print minify validate lint",
  blurb: "Format, minify and validate. Only the white space changes: numbers and repeated keys stay exactly as typed.",
  mount(root) {
    const input = editor({ rows: 14, "aria-label": "JSON to check", placeholder: '{"paste": "JSON here"}' });
    const output = editor({ rows: 14, readonly: true, "aria-label": "Result", placeholder: "The formatted JSON appears here: press Format, or Ctrl+Enter in the input." });
    const indent = select("Indent", [
      ["2", "2 spaces"],
      ["4", "4 spaces"],
      ["tab", "Tabs"],
    ]);
    const verdict = message();
    const notes = el("div", { class: "dt-notes" });
    let mistake = null;

    function jump() {
      input.focus();
      input.setSelectionRange(mistake.offset, Math.min(mistake.offset + 1, input.value.length));
    }

    // Checks the input and says what it is; the scan result, or null when there is nothing valid.
    function check() {
      const text = input.value;
      mistake = null;
      notes.replaceChildren();
      if (!text.trim()) {
        verdict.say("Paste JSON into the input. It is checked as you type.");
        return null;
      }
      const read = scan(text);
      if (!read.ok) {
        mistake = read.error;
        const shown = excerpt(text, mistake.offset);
        verdict.fail(
          "Line " + mistake.line + ", column " + mistake.column + ": " + mistake.message,
          el("pre", { class: "dt-pointer", text: shown.text + "\n" + " ".repeat(shown.caret) + "^" }),
          el("div", {}, button("Go to the mistake", { icon: "locate-fixed", onClick: jump })),
        );
        return null;
      }
      const summary = read.summary;
      verdict.ok("Valid JSON: " + describe(summary) + (summary.depth ? ", " + plural(summary.values, "value") + " in all, nested " + plural(summary.depth, "level") + " deep." : "."));
      for (const warning of read.warnings) {
        notes.append(el("div", { class: "alert alert-soft alert-warning text-sm py-2" }, el("span", { text: "Line " + warning.line + ": " + warning.message })));
      }
      return read;
    }

    const result = pane("Result", output, copyButton(() => output.value));
    function write(make) {
      output.value = check() ? make(input.value).text : "";
      if (output.value) motion("flash", result); // the result is new
    }
    const pretty = () => write((text) => format(text, indent.value === "tab" ? "\t" : Number(indent.value)));

    input.addEventListener("input", debounce(check));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        pretty();
      }
    });
    indent.addEventListener("change", () => output.value && pretty());

    root.append(
      el(
        "div",
        { class: "dt-split" },
        pane(
          "Input",
          input,
          indent,
          button("Minify", { icon: "fold-horizontal", onClick: () => write(minify) }),
          button("Format", { icon: "wand-sparkles", kind: "primary", title: "Ctrl+Enter", onClick: pretty }),
        ),
        result,
      ),
      verdict.node,
      notes,
    );
    check();
  },
};
