import { decodeText, encodeText, hexDump } from "../lib/base64.js";
import { button, checkbox, copyButton, editor, el, message, motion, pane, plural, radios, row } from "../ui.js";

export default {
  id: "base64",
  title: "Base64",
  icon: "binary",
  keywords: "encode decode bytes hex dump",
  blurb: "Encode text or decode Base64. Text goes through UTF-8, so accents, CJK and emoji survive the round trip.",
  mount(root) {
    const input = editor({ rows: 10, "aria-label": "Text or Base64" });
    const output = editor({ rows: 10, readonly: true, "aria-label": "Result", placeholder: "The result appears here as you type." });
    const verdict = message();
    const urlSafe = checkbox("URL-safe alphabet (- and _, no padding)");
    let isText = false; // a hex dump is not something to swap back in

    function run() {
      const encoding = mode.value === "encode";
      urlSafe.input.disabled = !encoding;
      input.placeholder = encoding ? "Text to encode" : "Base64 to decode";
      output.value = "";
      isText = false;
      if (!input.value) {
        verdict.say(encoding ? "Type text to encode." : "Paste Base64 to decode. Both alphabets are read, with or without padding.");
        return;
      }
      if (encoding) {
        output.value = encodeText(input.value, { urlSafe: urlSafe.input.checked });
        isText = true;
        const bytes = new TextEncoder().encode(input.value).length;
        verdict.say(plural(bytes, "byte") + " of UTF-8 became " + plural(output.value.length, "character") + ".");
        return;
      }
      const decoded = decodeText(input.value);
      if (!decoded.ok) {
        verdict.fail(decoded.message);
      } else if (decoded.text === null) {
        output.value = hexDump(decoded.bytes);
        verdict.warn(plural(decoded.bytes.length, "byte") + ", not UTF-8 text (a file or a key, perhaps), shown as a hex dump.");
      } else {
        output.value = decoded.text;
        isText = true;
        verdict.ok(plural(decoded.bytes.length, "byte") + " of UTF-8 text, from the " + (decoded.urlSafe ? "URL-safe" : "standard") + " alphabet.");
      }
    }

    const mode = radios("base64-mode", "Direction", [["encode", "Encode text"], ["decode", "Decode Base64"]], run);
    const result = pane(
      "Result",
      output,
      button("Use as input", {
        icon: "arrow-left-right",
        title: "Use the result as the input and turn the direction around",
        onClick: () => {
          if (isText) {
            input.value = output.value;
            mode.value = mode.value === "encode" ? "decode" : "encode";
            run();
            motion("flash", result);
          }
        },
      }),
      copyButton(() => output.value),
    );
    input.addEventListener("input", run);
    urlSafe.input.addEventListener("change", run);

    root.append(row(mode.node, urlSafe.node), el("div", { class: "dt-split" }, pane("Input", input), result), verdict.node);
    run();
  },
};
