import { BUDGET_MS, MAX_MATCHES, MAX_TEXT, boundText, createRunner, segments } from "../lib/regex.js";
import { checkbox, copyButton, debounce, el, field, message, table, textarea } from "../ui.js";

const FLAGS = [
  ["g", "g: all matches", true],
  ["i", "i: ignore case", false],
  ["m", "m: ^ and $ per line", false],
  ["s", "s: dot matches line breaks", false],
  ["u", "u: Unicode", false],
  ["y", "y: sticky", false],
];
const MAX_ROWS = 200;

export default {
  id: "regex",
  title: "Regex",
  blurb: "Try a JavaScript regular expression on a sample: matches highlighted, groups in a table.",
  mount(root) {
    const pattern = el("input", { type: "text", class: "mono", autocomplete: "off", spellcheck: "false", placeholder: "(?<year>\\d{4})-(\\d{2})" });
    const flags = FLAGS.map(([flag, label, on]) => ({ flag, box: checkbox(label, on) }));
    const sample = textarea({ rows: 6, "aria-label": "Text to search" });
    const verdict = message();
    const marked = el("pre", { class: "output marked", tabindex: "0", "aria-label": "The text with its matches highlighted", hidden: true });
    const groups = el("div", { class: "block" });
    let matches = [];

    // The pattern runs in a worker that is ended when it takes too long: see lib/regex.js.
    // The worker's address is relative to this file, so it holds under any path prefix.
    const runner = createRunner(() => new Worker(new URL("../regex-worker.js", import.meta.url), { type: "module" }), BUDGET_MS);

    function show(result, cut) {
      matches = result.matches;
      const names = result.names;
      marked.replaceChildren();
      for (const part of segments(boundText(sample.value).text, matches)) {
        if (part.match === null) {
          marked.append(part.text);
        } else {
          // Two tints in turn, so matches that touch can be told apart.
          marked.append(el("mark", { class: (part.match % 2 ? "odd" : "even") + (part.text ? "" : " empty"), text: part.text }));
        }
      }
      marked.hidden = false;
      const notes = [];
      if (result.more) {
        notes.push("Only the first " + MAX_MATCHES.toLocaleString("en") + " are shown.");
      }
      if (result.outOfTime) {
        notes.push("The search was stopped after " + BUDGET_MS / 1000 + " second.");
      }
      if (cut) {
        notes.push("Only the first " + MAX_TEXT.toLocaleString("en") + " characters of the text were searched.");
      }
      const found = matches.length === 1 ? "1 match." : matches.length ? matches.length.toLocaleString("en") + " matches." : "No match.";
      verdict.say(found + (notes.length ? " " + notes.join(" ") : ""));
      if (matches.length) {
        const headers = ["#", "At", "Match", ...names.map((name, index) => (name ? name + " (" + (index + 1) + ")" : "Group " + (index + 1)))];
        const cell = (value) => (value === null ? el("span", { class: "muted", text: "not matched" }) : value === "" ? el("span", { class: "muted", text: "empty" }) : value);
        const rows = matches.slice(0, MAX_ROWS).map((match, index) => [String(index + 1), String(match.index), cell(match.text), ...match.groups.map(cell)]);
        groups.append(table(headers, rows, "values"));
        if (matches.length > MAX_ROWS) {
          groups.append(el("p", { class: "muted", text: "The table shows the first " + MAX_ROWS + " matches; Copy gives all of them." }));
        }
      }
    }

    async function run() {
      matches = [];
      groups.replaceChildren();
      marked.hidden = true;
      if (!pattern.value) {
        runner.stop();
        verdict.say("Type a pattern. It is tried on the text below as you type, for at most " + BUDGET_MS / 1000 + " second.");
        return;
      }
      const chosen = flags.filter((entry) => entry.box.input.checked).map((entry) => entry.flag).join("");
      const bounded = boundText(sample.value);
      verdict.say("Searching...");
      const answer = await runner.run({ pattern: pattern.value, flags: chosen, text: bounded.text });
      if (answer.ok && answer.result.ok) {
        show(answer.result, bounded.cut);
      } else if (answer.ok) {
        verdict.fail(answer.result.message);
      } else if (answer.reason === "timeout") {
        verdict.fail(
          "The pattern did not finish within " + BUDGET_MS / 1000 + " second on this text and was stopped. It probably tries an exploding number of ways to match: look for a repetition inside a repetition, such as (a+)+ or (.*)*.",
        );
      } else if (answer.reason === "unavailable") {
        verdict.fail("This browser cannot run the pattern in the background, and running it in the page could freeze the tab, so the tester stays off.");
      }
      // "replaced": a newer run is on its way and will write the result.
    }

    const later = debounce(run, 200);
    pattern.addEventListener("input", later);
    sample.addEventListener("input", later);
    for (const entry of flags) {
      entry.box.input.addEventListener("change", run);
    }
    sample.value = "Released 2026-11-20, patched 2026-12-03.";

    root.append(
      field("Pattern", pattern),
      el("div", { class: "row" }, ...flags.map((entry) => entry.box.node)),
      field("Text", sample),
      verdict.node,
      marked,
      groups,
      el("div", { class: "row" }, copyButton(() => matches.map((match) => match.text).join("\n"), "Copy matches")),
    );
    run();
  },
};
