import { diffLines, hunkHeader, hunks, unified } from "../lib/diff.js";
import { button, checkbox, copyButton, debounce, editor, el, message, pane, plural, row, select } from "../ui.js";

const MAX_SHOWN = 5000;

export default {
  id: "diff",
  title: "Text diff",
  icon: "git-compare",
  keywords: "compare text patch changes unified",
  blurb: "Compare two texts line by line. Copy gives a unified diff that patch and git understand.",
  mount(root) {
    const before = editor({ rows: 10, "aria-label": "Original", placeholder: "The original text" });
    const after = editor({ rows: 10, "aria-label": "Changed", placeholder: "The changed text" });
    const trailing = checkbox("Ignore spaces at line ends");
    const context = select("Unchanged lines around a change", [
      ["3", "3 lines of context"],
      ["10", "10 lines of context"],
      ["all", "The whole text"],
    ]);
    const verdict = message();
    const view = el("div", { class: "dt-diff", role: "group", "aria-label": "Differences" });
    const shown = pane("Differences", view, copyButton(() => patch, "Copy unified diff"));
    shown.hidden = true;
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
      shown.hidden = true;
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
        verdict.ok("The two texts are the same" + (trailing.input.checked ? ", spaces at line ends aside." : ".") + " " + plural(result.unchanged, "line") + ".");
        return;
      }
      const around = context.value === "all" ? Infinity : Number(context.value);
      patch = unified(result.ops, { context: around });
      let count = 0;
      for (const hunk of hunks(result.ops, around)) {
        view.append(line("hunk", null, null, hunkHeader(hunk)));
        for (const op of hunk.ops) {
          if (count < MAX_SHOWN) {
            view.append(line(op.type, op.before, op.after, op.text));
          }
          count += 1;
        }
      }
      shown.hidden = false;
      verdict.say(
        plural(result.added, "line") + " added, " + plural(result.removed, "line") + " removed, " + plural(result.unchanged, "line") + " unchanged." +
          (count > MAX_SHOWN ? " The first " + MAX_SHOWN.toLocaleString("en") + " lines of the diff are shown; Copy gives all of it." : ""),
      );
    }

    const later = debounce(run, 200);
    before.addEventListener("input", later);
    after.addEventListener("input", later);
    trailing.input.addEventListener("change", run);
    context.addEventListener("change", run);

    root.append(
      el("div", { class: "dt-split" }, pane("Original", before), pane("Changed", after)),
      row(
        trailing.node,
        context,
        button("Swap sides", {
          icon: "arrow-left-right",
          onClick: () => {
            [before.value, after.value] = [after.value, before.value];
            run();
          },
        }),
      ),
      verdict.node,
      shown,
    );
    run();
  },
};
