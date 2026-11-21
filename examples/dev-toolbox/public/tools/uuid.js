import { MAX_COUNT, generate } from "../lib/uuid.js";
import { checkbox, copyButton, el, field, message, plural, textarea } from "../ui.js";

export default {
  id: "uuid",
  title: "UUID",
  blurb: "Random (version 4) UUIDs from the browser's cryptographic random numbers.",
  mount(root) {
    const count = el("input", { type: "number", min: "1", max: String(MAX_COUNT), value: "5", inputmode: "numeric" });
    const uppercase = checkbox("Capitals");
    const plain = checkbox("No hyphens");
    const verdict = message();
    const output = textarea({ rows: 8, readonly: true, "aria-label": "UUIDs" });

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
        verdict.say(plural(list.length, "new UUID") + ". Each press makes new ones; none is kept anywhere.");
      } catch (error) {
        verdict.fail("This browser offers no secure random numbers, so no UUID can be made here.");
      }
    }

    const again = el("button", { type: "button", class: "primary", text: "Generate" });
    again.addEventListener("click", make);
    for (const control of [count, uppercase.input, plain.input]) {
      control.addEventListener("change", make);
    }
    root.append(el("div", { class: "row" }, field("How many", count), uppercase.node, plain.node, again), verdict.node, output, el("div", { class: "row" }, copyButton(() => output.value)));
    make();
  },
};
