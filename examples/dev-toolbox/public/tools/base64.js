import { decodeText, encodeText, hexDump } from "../lib/base64.js";
import { checkbox, copyButton, el, message, plural, radios, textarea } from "../ui.js";

export default {
  id: "base64",
  title: "Base64",
  blurb: "Encode text or decode Base64. Text goes through UTF-8, so accents, CJK and emoji survive the round trip.",
  mount(root) {
    const input = textarea({ "aria-label": "Text or Base64" });
    const verdict = message();
    const output = textarea({ readonly: true, "aria-label": "Result" });
    const urlSafe = checkbox("URL-safe alphabet (- and _, no padding)");
    let isText = false; // a hex dump is not something to swap back in

    function run() {
      const encoding = mode.value === "encode";
      urlSafe.input.disabled = !encoding;
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
        verdict.say(plural(decoded.bytes.length, "byte") + ", not UTF-8 text (a file or a key, perhaps), shown as a hex dump.");
      } else {
        output.value = decoded.text;
        isText = true;
        verdict.say(plural(decoded.bytes.length, "byte") + " of UTF-8 text, from the " + (decoded.urlSafe ? "URL-safe" : "standard") + " alphabet.");
      }
    }

    const mode = radios("base64-mode", "Direction", [["encode", "Encode text"], ["decode", "Decode Base64"]], run);
    const swap = el("button", { type: "button", title: "Use the result as the input and turn the direction around", text: "Swap" });
    swap.addEventListener("click", () => {
      if (isText) {
        input.value = output.value;
        mode.value = mode.value === "encode" ? "decode" : "encode";
        run();
      }
    });
    input.addEventListener("input", run);
    urlSafe.input.addEventListener("change", run);

    root.append(mode.node, input, el("div", { class: "row" }, urlSafe.node), verdict.node, output, el("div", { class: "row" }, copyButton(() => output.value), swap));
    run();
  },
};
