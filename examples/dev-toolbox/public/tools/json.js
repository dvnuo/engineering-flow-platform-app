import { describe, excerpt, format, minify, scan } from "../lib/json.js";
import { copyButton, debounce, el, message, plural, select, textarea } from "../ui.js";

export default {
  id: "json",
  title: "JSON",
  blurb: "Format, minify and validate. Only the white space changes: numbers and repeated keys stay exactly as typed.",
  mount(root) {
    const input = textarea({ rows: 10, "aria-label": "JSON to check", placeholder: '{"paste": "JSON here"}' });
    const indent = select("Indent", [
      ["2", "2 spaces"],
      ["4", "4 spaces"],
      ["tab", "Tabs"],
    ]);
    const verdict = message();
    const pointer = el("pre", { class: "pointer", hidden: true });
    const jump = el("button", { type: "button", hidden: true, text: "Go to the mistake" });
    const notes = el("ul", { class: "notes" });
    const output = textarea({ rows: 10, readonly: true, "aria-label": "Result" });
    let mistake = null;

    // Checks the input and says what it is; the scan result, or null when there is nothing valid.
    function check() {
      const text = input.value;
      mistake = null;
      pointer.hidden = true;
      jump.hidden = true;
      notes.replaceChildren();
      if (!text.trim()) {
        verdict.say("Paste JSON above. It is checked as you type.");
        return null;
      }
      const read = scan(text);
      if (!read.ok) {
        mistake = read.error;
        verdict.fail("Line " + mistake.line + ", column " + mistake.column + ": " + mistake.message);
        const shown = excerpt(text, mistake.offset);
        pointer.textContent = shown.text + "\n" + " ".repeat(shown.caret) + "^";
        pointer.hidden = false;
        jump.hidden = false;
        return null;
      }
      const summary = read.summary;
      verdict.say(
        "Valid JSON: " + describe(summary) + (summary.depth ? ", " + plural(summary.values, "value") + " in all, nested " + plural(summary.depth, "level") + " deep." : "."),
      );
      for (const warning of read.warnings) {
        notes.append(el("li", { text: "Line " + warning.line + ": " + warning.message }));
      }
      return read;
    }

    function write(make) {
      output.value = check() ? make(input.value).text : "";
    }
    const pretty = () => write((text) => format(text, indent.value === "tab" ? "\t" : Number(indent.value)));

    input.addEventListener("input", debounce(check));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        pretty();
      }
    });
    jump.addEventListener("click", () => {
      input.focus();
      input.setSelectionRange(mistake.offset, Math.min(mistake.offset + 1, input.value.length));
    });
    const formatButton = el("button", { type: "button", class: "primary", title: "Ctrl+Enter", text: "Format" });
    formatButton.addEventListener("click", pretty);
    const minifyButton = el("button", { type: "button", text: "Minify" });
    minifyButton.addEventListener("click", () => write(minify));

    root.append(
      input,
      el("div", { class: "row" }, formatButton, indent, minifyButton, jump),
      verdict.node,
      pointer,
      notes,
      output,
      el("div", { class: "row" }, copyButton(() => output.value)),
    );
    check();
  },
};
