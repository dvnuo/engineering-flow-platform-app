import { MAX_COUNT, generate } from "../lib/uuid.js";
import { button, checkbox, copyButton, editor, el, field, message, motion, pane, plural, row } from "../ui.js";

export default {
  id: "uuid",
  title: "UUID",
  icon: "fingerprint",
  keywords: "guid random generate identifier",
  blurb: "Random (version 4) UUIDs from the browser's cryptographic random numbers.",
  mount(root) {
    const count = el("input", { type: "number", min: "1", max: String(MAX_COUNT), value: "5", inputmode: "numeric", class: "input input-sm dt-number" });
    const uppercase = checkbox("Capitals");
    const plain = checkbox("No hyphens");
    const verdict = message();
    const output = editor({ rows: 8, readonly: true, "aria-label": "UUIDs" });
    const result = pane("UUIDs", output, copyButton(() => output.value, "Copy all"));

    function make() {
      const wanted = Number(count.value);
      if (!Number.isInteger(wanted) || wanted < 1 || wanted > MAX_COUNT) {
        output.value = "";
        verdict.fail("Ask for a whole number of UUIDs from 1 to " + MAX_COUNT + ".");
        return;
      }
      try {
        const list = generate(wanted, { uppercase: uppercase.input.checked, hyphens: !plain.input.checked });
        output.value = list.join("\n");
        output.rows = Math.min(16, Math.max(4, list.length));
        verdict.say(plural(list.length, "new UUID") + ". Each press makes new ones; none is kept anywhere.");
        motion("flash", result); // new ones, not the same again
      } catch (error) {
        verdict.fail("This browser offers no secure random numbers, so no UUID can be made here.");
      }
    }

    for (const control of [count, uppercase.input, plain.input]) {
      control.addEventListener("change", make);
    }
    root.append(row(field("How many", count), uppercase.node, plain.node, button("Generate", { icon: "refresh-cw", kind: "primary", onClick: make })), result, verdict.node);
    make();
  },
};
