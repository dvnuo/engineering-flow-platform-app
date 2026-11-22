import { digests, matchDigest } from "../lib/hash.js";
import { copyButton, debounce, editor, el, empty, field, input, message, motion, pane, plural } from "../ui.js";

// crypto.subtle wants the whole input in memory at once.
const MAX_FILE_BYTES = 200 * 1024 * 1024;

export default {
  id: "hash",
  title: "Hash",
  icon: "hash",
  keywords: "sha sha256 sha1 sha512 checksum digest verify download",
  blurb: "SHA-1, SHA-256, SHA-384 and SHA-512 of a text or a file, and a check against a published checksum.",
  mount(root) {
    const text = editor({ rows: 5, "aria-label": "Text to hash", placeholder: "Text to hash, as UTF-8" });
    const file = el("input", { type: "file", class: "file-input file-input-sm", "aria-label": "A file to hash" });
    const expected = input({ placeholder: "A checksum to compare with, for example from a download page" });
    const verdict = message();
    const comparison = message();
    const result = el("div");
    let results = [];
    let rows = new Map(); // algorithm -> its row, to light up the one that matches
    let turn = 0; // a slow digest must not overwrite the answer to a newer input

    function compare() {
      const match = matchDigest(expected.value, results);
      for (const line of rows.values()) line.classList.remove("is-match");
      if (match.state === "empty" || !results.length) {
        comparison.say("");
      } else if (match.state === "match") {
        comparison.ok("It matches the " + match.algorithm + " digest.");
        const line = rows.get(match.algorithm);
        line.classList.add("is-match");
        motion("flash", line, "var(--color-success)");
      } else if (match.state === "mismatch") {
        comparison.fail("It does NOT match: it is as long as a " + match.algorithm + " digest, but differs from the one above.");
      } else {
        comparison.fail(match.state === "not-hex" ? "A checksum is written in hex digits (0-9, a-f)." : "No SHA digest has this length.");
      }
    }

    function nothing() {
      turn += 1;
      results = [];
      rows = new Map();
      verdict.say("");
      result.replaceChildren(empty("hash", "Nothing hashed yet", "Type or paste text above, or choose a file. A file is read in this tab and not uploaded."));
      compare();
    }

    async function show(data, what) {
      const mine = (turn += 1);
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
        rows = new Map();
        const body = el("tbody");
        for (const row of found) {
          const line = el("tr", {}, el("th", { scope: "row", text: row.algorithm }), el("td", {}, el("span", { class: "dt-code dt-digest", text: row.hex })), el("td", {}, copyButton(() => row.hex)));
          rows.set(row.algorithm, line);
          body.append(line);
        }
        verdict.ok(what);
        result.replaceChildren(el("div", { class: "dt-pane" }, el("div", { class: "dt-table" }, el("table", { class: "table table-sm dt-values" }, body))));
      } catch (error) {
        verdict.fail("The digest could not be computed in this browser.");
      }
      compare();
    }

    const hashText = () => {
      file.value = "";
      if (!text.value) {
        nothing();
        return;
      }
      const bytes = new TextEncoder().encode(text.value).length;
      show(text.value, plural(bytes, "byte") + " of text, hashed as UTF-8.");
    };
    text.addEventListener("input", debounce(hashText));
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

    root.append(pane("Text", text), field("Or a file", file), verdict.node, result, field("Compare with", expected), comparison.node);
    nothing();
  },
};
