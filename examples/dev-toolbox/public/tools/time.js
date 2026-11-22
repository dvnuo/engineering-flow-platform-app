import { describeInstant, parseInstant } from "../lib/time.js";
import { button, copyButton, el, empty, input, message, motion, row, select, table } from "../ui.js";

export default {
  id: "time",
  title: "Timestamp",
  icon: "clock",
  keywords: "epoch unix iso 8601 date utc time zone",
  blurb: "Epoch seconds or milliseconds to ISO 8601 and back, in UTC and in your local time.",
  mount(root) {
    const moment = input({ class: "dt-wide", "aria-label": "Epoch number or ISO 8601 date", placeholder: "1767225600 or 2026-01-01T00:00:00Z" });
    const unit = select("Unit of a number", [
      ["auto", "Guess the unit"],
      ["seconds", "Seconds"],
      ["milliseconds", "Milliseconds"],
    ]);
    const verdict = message();
    const result = el("div");

    function run() {
      if (!moment.value.trim()) {
        verdict.say("");
        result.replaceChildren(empty("clock", "Type a moment", 'Epoch seconds or milliseconds, or an ISO 8601 date, or press Now. Nothing ticks here: "relative to now" is worked out when you type.'));
        return;
      }
      const parsed = parseInstant(moment.value, unit.value);
      if (!parsed.ok) {
        verdict.fail(parsed.message);
        result.replaceChildren();
        return;
      }
      verdict.ok("Read as " + parsed.readAs + ".");
      const rows = describeInstant(parsed.ms, Date.now());
      rows.push({
        label: "In your browser's words",
        value: new Date(parsed.ms).toLocaleString(undefined, { dateStyle: "full", timeStyle: "long" }),
      });
      result.replaceChildren(
        el(
          "div",
          { class: "dt-pane" },
          table(
            ["", "Value", ""],
            rows.map((line) => [line.label, el("span", { class: "dt-code", text: line.value }), copyButton(() => line.value)]),
            "dt-values",
          ),
        ),
      );
    }

    const now = button("Now", {
      icon: "timer-reset",
      kind: "soft",
      onClick: () => {
        moment.value = String(Math.floor(Date.now() / 1000));
        unit.value = "auto";
        run();
        motion("flash", result.firstChild);
      },
    });
    moment.addEventListener("input", run);
    unit.addEventListener("change", run);

    root.append(row(moment, unit, now), verdict.node, result);
    run();
  },
};
