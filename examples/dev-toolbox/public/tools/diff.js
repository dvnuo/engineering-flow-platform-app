import { diffLines, hunkHeader, hunks, unified } from "../lib/diff.js";
import { checkbox, copyButton, debounce, el, field, message, plural, select, textarea } from "../ui.js";

const MAX_SHOWN = 5000;

export default {
  id: "diff",
  title: "Text diff",
  blurb: "Compare two texts line by line. Copy gives a unified diff that patch and git understand.",
  mount(root) {
    const before = textarea({ rows: 8 });
    const after = textarea({ rows: 8 });
    const trailing = checkbox("Ignore spaces at line ends");
    const context = select("Unchanged lines around a change", [
      ["3", "3 lines of context"],
      ["10", "10 lines of context"],
      ["all", "The whole text"],
    ]);
    const verdict = message();
    const view = el("div", { class: "diff", role: "group", "aria-label": "Differences", hidden: true });
    let patch = "";

    function line(kind, beforeNumber, afterNumber, text) {
      const sign = kind === "add" ? "+" : kind === "del" ? "-" : " ";
      return el(
        "div",
        { class: "line " + kind },
        el("span", { class: "number", text: beforeNumber === null ? "" : String(beforeNumber) }),
        el("span", { class: "number", text: afterNumber === null ? "" : String(afterNumber) }),
        el("span", { class: "text", text: kind === "hunk" ? text : sign + " " + text }),
      );
    }

    function run() {
      patch = "";
      view.replaceChildren();
      view.hidden = true;
      if (!before.value && !after.value) {
        verdict.say("Paste the original on one side and the changed text on the other.");
        return;
      }
      const result = diffLines(before.value, after.value, { ignoreTrailingSpace: trailing.input.checked });
      if (!result.ok) {
        verdict.fail(result.message);
        return;
      }
      if (!result.added && !result.removed) {
        verdict.say("The two texts are the same" + (trailing.input.checked ? ", spaces at line ends aside." : ".") + " " + plural(result.unchanged, "line") + ".");
        return;
      }
      const around = context.value === "all" ? Infinity : Number(context.value);
      patch = unified(result.ops, { context: around });
      let shown = 0;
      for (const hunk of hunks(result.ops, around)) {
        view.append(line("hunk", null, null, hunkHeader(hunk)));
        for (const op of hunk.ops) {
          if (shown < MAX_SHOWN) {
            view.append(line(op.type, op.before, op.after, op.text));
          }
          shown += 1;
        }
      }
      view.hidden = false;
      verdict.say(
        plural(result.added, "line") + " added, " + plural(result.removed, "line") + " removed, " + plural(result.unchanged, "line") + " unchanged." +
          (shown > MAX_SHOWN ? " The first " + MAX_SHOWN.toLocaleString("en") + " lines of the diff are shown; Copy gives all of it." : ""),
      );
    }

    const later = debounce(run, 200);
    before.addEventListener("input", later);
    after.addEventListener("input", later);
    trailing.input.addEventListener("change", run);
    context.addEventListener("change", run);
    const swap = el("button", { type: "button", text: "Swap sides" });
    swap.addEventListener("click", () => {
      [before.value, after.value] = [after.value, before.value];
      run();
    });

    root.append(
      el("div", { class: "pair" }, field("Original", before), field("Changed", after)),
      el("div", { class: "row" }, trailing.node, context, swap),
      verdict.node,
      view,
      el("div", { class: "row" }, copyButton(() => patch, "Copy unified diff")),
    );
    run();
  },
};
