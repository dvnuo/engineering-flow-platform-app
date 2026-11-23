import { describeCron, explainFields, nextRuns, parseCron } from "../lib/cron.js";
import { isoUtc, isoWithOffset, localOffset, relativeTime } from "../lib/time.js";
import { copyButton, el, empty, input, message, motion, pane, radios, row, table } from "../ui.js";

// Shapes to start from, not data: one press puts the expression in the field.
const EXAMPLES = ["*/15 * * * *", "0 9 * * 1-5", "30 2 * * 0", "0 0 1 * *", "0 8-18/2 * * MON-FRI"];
const RUNS = 5;

export default {
  id: "cron",
  title: "Cron",
  icon: "calendar-clock",
  keywords: "schedule crontab jenkins timer job",
  blurb: "A five-field cron expression in words, with its next " + RUNS + " run times.",
  mount(root) {
    const expression = input({ class: "input-lg dt-cron", "aria-label": "Cron expression", placeholder: "minute hour day-of-month month day-of-week" });
    const verdict = message();
    const sentence = el("p", { class: "dt-headline", "aria-live": "polite" });
    const result = el("div", { class: "dt-stack" });
    let summary = "";
    let drawn = ""; // the expression whose runs are on the page: only a new one makes them rise in

    function run() {
      summary = "";
      const parsed = parseCron(expression.value);
      if (!parsed.ok) {
        sentence.textContent = "";
        if (expression.value.trim()) {
          verdict.fail(parsed.message);
          result.replaceChildren();
        } else {
          verdict.say("");
          result.replaceChildren(empty("calendar-clock", "Type a schedule", "Five fields (minute, hour, day of the month, month, day of the week), a macro such as @daily, or one of the shapes above."));
        }
        drawn = "";
        return;
      }
      verdict.say("");
      sentence.textContent = describeCron(parsed);
      const utc = zone.value === "utc";
      const now = Date.now();
      const runs = nextRuns(parsed, now, RUNS, { utc });
      const stamp = (ms) => (utc ? isoUtc(ms) : isoWithOffset(ms, localOffset(ms)));
      const weekday = new Intl.DateTimeFormat("en", { weekday: "long", timeZone: utc ? "UTC" : undefined });
      const list = el("ol", { class: "dt-runs" });
      for (const ms of runs) {
        list.append(el("li", {}, el("span", { class: "dt-code", text: stamp(ms) }), el("span", { class: "dt-faint", text: weekday.format(ms) + ", " + relativeTime(ms, now) })));
      }
      result.replaceChildren(
        pane("Next runs, " + (utc ? "if the scheduler runs on UTC" : "if the scheduler runs on your time zone"), runs.length ? list : el("p", { class: "dt-note dt-pad", text: "It never runs: no such date comes in the next eight years." })),
        pane("The five fields", table(["Field", "Typed", "Allows"], explainFields(parsed).map((field) => [field.name, field.source, field.meaning]), "dt-values")),
      );
      const key = parsed.expression + " " + zone.value;
      if (key !== drawn) motion("rise", list.children, { stagger: 50, distance: 8 }); // a new schedule: its runs come in one after the other
      drawn = key;
      summary = [parsed.expression, sentence.textContent, ...runs.map(stamp)].join("\n");
    }

    const zone = radios("cron-zone", "Read the times in", [["local", "My time zone"], ["utc", "UTC"]], run);
    const shapes = EXAMPLES.map((example) =>
      el("button", {
        type: "button",
        class: "btn btn-xs btn-ghost dt-chip",
        text: example,
        onclick: () => {
          expression.value = example;
          run();
          expression.focus();
        },
      }),
    );
    expression.addEventListener("input", run);

    root.append(
      expression,
      row(el("span", { class: "dt-label", text: "Try" }), ...shapes),
      row(zone.node, copyButton(() => summary, "Copy explanation")),
      verdict.node,
      sentence,
      result,
    );
    run();
  },
};
