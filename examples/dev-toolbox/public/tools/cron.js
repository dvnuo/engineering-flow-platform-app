import { describeCron, explainFields, nextRuns, parseCron } from "../lib/cron.js";
import { isoUtc, isoWithOffset, localOffset, relativeTime } from "../lib/time.js";
import { copyButton, el, field, message, radios, table } from "../ui.js";

const EXAMPLES = ["*/15 * * * *", "0 9 * * 1-5", "30 2 * * 0", "0 0 1 * *", "0 8-18/2 * * MON-FRI"];
const RUNS = 5;

export default {
  id: "cron",
  title: "Cron",
  blurb: "A five-field cron expression in words, with its next " + RUNS + " run times.",
  mount(root) {
    const input = el("input", { type: "text", class: "mono", autocomplete: "off", spellcheck: "false", placeholder: "minute hour day-of-month month day-of-week" });
    const verdict = message();
    const sentence = el("p", { class: "headline" });
    const result = el("div", { class: "block" });
    let summary = "";

    function run() {
      result.replaceChildren();
      sentence.textContent = "";
      summary = "";
      const parsed = parseCron(input.value);
      if (!parsed.ok) {
        if (input.value.trim()) {
          verdict.fail(parsed.message);
        } else {
          verdict.say(parsed.message);
        }
        return;
      }
      verdict.say("");
      sentence.textContent = describeCron(parsed);
      const utc = zone.value === "utc";
      const now = Date.now();
      const runs = nextRuns(parsed, now, RUNS, { utc });
      const stamp = (ms) => (utc ? isoUtc(ms) : isoWithOffset(ms, localOffset(ms)));
      const weekday = new Intl.DateTimeFormat("en", { weekday: "long", timeZone: utc ? "UTC" : undefined });
      result.append(table(["Field", "Typed", "Allows"], explainFields(parsed).map((row) => [row.name, row.source, row.meaning]), "values"));
      result.append(el("h3", { text: "Next runs, " + (utc ? "if the scheduler runs on UTC" : "if the scheduler runs on your time zone") }));
      if (runs.length) {
        const list = el("ol", { class: "runs" });
        for (const ms of runs) {
          list.append(el("li", {}, el("span", { class: "mono", text: stamp(ms) }), " " + weekday.format(ms) + ", " + relativeTime(ms, now)));
        }
        result.append(list);
      } else {
        result.append(el("p", { class: "muted", text: "It never runs: no such date comes in the next eight years." }));
      }
      summary = [parsed.expression, sentence.textContent, ...runs.map(stamp)].join("\n");
    }

    const zone = radios("cron-zone", "Read the times in", [["local", "My time zone"], ["utc", "UTC"]], run);
    const examples = el("div", { class: "row" }, el("span", { class: "label", text: "Try" }));
    for (const example of EXAMPLES) {
      const button = el("button", { type: "button", class: "mono chip", text: example });
      button.addEventListener("click", () => {
        input.value = example;
        run();
      });
      examples.append(button);
    }
    input.addEventListener("input", run);
    input.value = EXAMPLES[1];

    root.append(field("Expression", input), examples, zone.node, verdict.node, sentence, result, el("div", { class: "row" }, copyButton(() => summary, "Copy explanation")));
    run();
  },
};
