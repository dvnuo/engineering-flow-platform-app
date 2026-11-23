import { BUDGET_MS, MAX_MATCHES, MAX_TEXT, boundText, createRunner, segments } from "../lib/regex.js";
import { checkbox, copyButton, debounce, editor, el, message, pane, row, table } from "../ui.js";

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
  icon: "regex",
  keywords: "regular expression pattern match test groups",
  blurb: "Try a JavaScript regular expression on a text of yours: matches highlighted, groups in a table.",
  mount(root) {
    const pattern = el("input", { type: "text", class: "grow", autocomplete: "off", spellcheck: "false", "aria-label": "Pattern", placeholder: "(?<year>\\d{4})-(\\d{2})" });
    const flagText = el("span", { class: "dt-flags", "aria-hidden": "true" });
    const flags = FLAGS.map(([flag, label, on]) => ({ flag, box: checkbox(label, on) }));
    const sample = editor({ rows: 6, "aria-label": "Text to search", placeholder: "The text to search: a log line, a file name, a list..." });
    const verdict = message();
    const marked = el("pre", { class: "dt-out dt-marked", tabindex: "0", "aria-label": "The text with its matches highlighted" });
    const groups = el("div");
    const found = pane("Matches", marked, copyButton(() => matches.map((match) => match.text).join("\n"), "Copy matches"));
    found.hidden = true;
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
      found.hidden = false;
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
      const said = matches.length === 1 ? "1 match." : matches.length ? matches.length.toLocaleString("en") + " matches." : "No match.";
      verdict[matches.length ? "ok" : "say"](said + (notes.length ? " " + notes.join(" ") : ""));
      if (matches.length) {
        const headers = ["#", "At", "Match", ...names.map((name, index) => (name ? name + " (" + (index + 1) + ")" : "Group " + (index + 1)))];
        const cell = (value) => (value === null ? el("span", { class: "dt-faint", text: "not matched" }) : value === "" ? el("span", { class: "dt-faint", text: "empty" }) : el("span", { class: "dt-code", text: value }));
        const rows = matches.slice(0, MAX_ROWS).map((match, index) => [String(index + 1), String(match.index), cell(match.text), ...match.groups.map(cell)]);
        groups.append(pane("Groups", table(headers, rows, "dt-values")));
        if (matches.length > MAX_ROWS) {
          groups.append(el("p", { class: "dt-note", text: "The table shows the first " + MAX_ROWS + " matches; Copy gives all of them." }));
        }
      }
    }

    async function run() {
      matches = [];
      groups.replaceChildren();
      const chosen = flags.filter((entry) => entry.box.input.checked).map((entry) => entry.flag).join("");
      flagText.textContent = chosen;
      if (!pattern.value) {
        runner.stop();
        found.hidden = true;
        verdict.say("Type a pattern, and the text to try it on. It runs as you type, for at most " + BUDGET_MS / 1000 + " second.");
        return;
      }
      const bounded = boundText(sample.value);
      const answer = await runner.run({ pattern: pattern.value, flags: chosen, text: bounded.text });
      if (answer.ok && answer.result.ok) {
        show(answer.result, bounded.cut);
      } else if (answer.ok) {
        found.hidden = true;
        verdict.fail(answer.result.message);
      } else if (answer.reason === "timeout") {
        found.hidden = true;
        verdict.fail(
          "The pattern did not finish within " + BUDGET_MS / 1000 + " second on this text and was stopped. It probably tries an exploding number of ways to match: look for a repetition inside a repetition, such as (a+)+ or (.*)*.",
        );
      } else if (answer.reason === "unavailable") {
        found.hidden = true;
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

    root.append(
      el("label", { class: "input dt-code dt-pattern w-full" }, el("span", { class: "dt-slash", text: "/" }), pattern, el("span", { class: "dt-slash", text: "/" }), flagText),
      row(...flags.map((entry) => entry.box.node)),
      pane("Text", sample),
      verdict.node,
      found,
      groups,
    );
    run();
  },
};
