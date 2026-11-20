import { describeInstant, parseInstant } from "../lib/time.js";
import { copyButton, el, message, select, table } from "../ui.js";

export default {
  id: "time",
  title: "Timestamp",
  blurb: "Epoch seconds or milliseconds to ISO 8601 and back, in UTC and in your local time.",
  mount(root) {
    const input = el("input", { type: "text", class: "mono", "aria-label": "Epoch number or ISO 8601 date", placeholder: "1767225600 or 2026-01-01T00:00:00Z", autocomplete: "off", spellcheck: "false" });
    const unit = select("Unit of a number", [
      ["auto", "Guess the unit"],
      ["seconds", "Seconds"],
      ["milliseconds", "Milliseconds"],
    ]);
    const verdict = message();
    const result = el("div", { class: "block" });

    function run() {
      result.replaceChildren();
      if (!input.value.trim()) {
        verdict.say("Type a moment, or press Now. Nothing ticks here: \"relative to now\" is worked out when you type.");
        return;
      }
      const parsed = parseInstant(input.value, unit.value);
      if (!parsed.ok) {
        verdict.fail(parsed.message);
        return;
      }
      verdict.say("Read as " + parsed.readAs + ".");
      const rows = describeInstant(parsed.ms, Date.now());
      rows.push({
        label: "In your browser's words",
        value: new Date(parsed.ms).toLocaleString(undefined, { dateStyle: "full", timeStyle: "long" }),
      });
      result.append(
        table(
          ["", "Value", ""],
          rows.map((row) => [row.label, row.value, copyButton(() => row.value)]),
          "values",
        ),
      );
    }

    const now = el("button", { type: "button", text: "Now" });
    now.addEventListener("click", () => {
      input.value = String(Math.floor(Date.now() / 1000));
      unit.value = "auto";
      run();
    });
    input.addEventListener("input", run);
    unit.addEventListener("change", run);

    root.append(el("div", { class: "row" }, input, unit, now), verdict.node, result);
    run();
  },
};
