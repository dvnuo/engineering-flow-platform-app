import { digests, matchDigest } from "../lib/hash.js";
import { copyButton, debounce, el, field, message, plural, table, textarea } from "../ui.js";

// crypto.subtle wants the whole input in memory at once.
const MAX_FILE_BYTES = 200 * 1024 * 1024;

export default {
  id: "hash",
  title: "Hash",
  blurb: "SHA-1, SHA-256, SHA-384 and SHA-512 of a text or a file, and a check against a published checksum.",
  mount(root) {
    const input = textarea({ rows: 5, "aria-label": "Text to hash" });
    const file = el("input", { type: "file" });
    const expected = el("input", { type: "text", class: "mono", autocomplete: "off", spellcheck: "false", placeholder: "a checksum to compare with" });
    const verdict = message();
    const comparison = message();
    const result = el("div", { class: "block" });
    let results = [];
    let turn = 0; // a slow digest must not overwrite the answer to a newer input

    function compare() {
      const match = matchDigest(expected.value, results);
      if (match.state === "empty" || !results.length) {
        comparison.say("");
      } else if (match.state === "match") {
        comparison.say("It matches the " + match.algorithm + " digest.");
      } else if (match.state === "mismatch") {
        comparison.fail("It does NOT match: it is as long as a " + match.algorithm + " digest, but differs from the one above.");
      } else {
        comparison.fail(match.state === "not-hex" ? "A checksum is written in hex digits (0-9, a-f)." : "No SHA digest has this length.");
      }
    }

    async function show(data, what) {
      const mine = (turn += 1);
      results = [];
      result.replaceChildren();
      if (!globalThis.crypto || !globalThis.crypto.subtle) {
        verdict.fail("This page was opened over plain http, where browsers switch the hash functions off. Open it over https or on localhost.");
        return;
      }
      try {
        const found = await digests(data);
        if (mine !== turn) {
          return;
        }
        results = found;
        verdict.say(what);
        result.append(table(["Algorithm", "Digest (hex)", ""], found.map((row) => [row.algorithm, row.hex, copyButton(() => row.hex)]), "values"));
      } catch (error) {
        verdict.fail("The digest could not be computed in this browser.");
      }
      compare();
    }

    const hashText = () => {
      file.value = "";
      const bytes = new TextEncoder().encode(input.value).length;
      show(input.value, input.value ? plural(bytes, "byte") + " of text, hashed as UTF-8." : "The digests of the empty text. Type something, or choose a file.");
    };
    input.addEventListener("input", debounce(hashText));
    file.addEventListener("change", async () => {
      const chosen = file.files[0];
      if (!chosen) {
        hashText();
      } else if (chosen.size > MAX_FILE_BYTES) {
        turn += 1;
        results = [];
        result.replaceChildren();
        verdict.fail("This file is larger than 200 MB. The browser would have to hold it in memory at once; use sha256sum for it.");
      } else {
        verdict.say("Reading the file...");
        try {
          show(await chosen.arrayBuffer(), "The file " + chosen.name + " (" + plural(chosen.size, "byte") + "). It was read in this tab and not uploaded.");
        } catch (error) {
          verdict.fail("The file could not be read.");
        }
      }
    });
    expected.addEventListener("input", compare);

    root.append(input, el("div", { class: "row" }, field("Or a file", file)), verdict.node, result, field("Compare with", expected), comparison.node);
    hashText();
  },
};
