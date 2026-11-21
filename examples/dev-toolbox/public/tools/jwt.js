import { decodeJwt } from "../lib/jwt.js";
import { copyButton, debounce, el, message, table, textarea } from "../ui.js";

export default {
  id: "jwt",
  title: "JWT",
  blurb: "Read the header and the payload of a JSON Web Token, with its times as dates.",
  mount(root) {
    const input = textarea({ rows: 5, "aria-label": "Token", placeholder: "eyJhbGciOi... (a leading \"Bearer \" is fine)" });
    const verdict = message();
    const result = el("div", { class: "block" });
    let headerText = "";
    let payloadText = "";

    function part(title, text) {
      return [el("h3", { text: title }), el("pre", { class: "output", tabindex: "0", text })];
    }

    function run() {
      result.replaceChildren();
      headerText = "";
      payloadText = "";
      if (!input.value.trim()) {
        verdict.say("Paste a token. It is decoded here, in this tab, and goes nowhere.");
        return;
      }
      const decoded = decodeJwt(input.value, Date.now());
      if (!decoded.ok) {
        verdict.fail(decoded.message);
        return;
      }
      headerText = decoded.headerText;
      payloadText = decoded.payloadText;
      const unsigned = decoded.algorithm.toLowerCase() === "none";
      verdict.say(
        decoded.validity.sentence +
          (unsigned ? " Its alg is none: it is not signed at all." : " Algorithm " + (decoded.algorithm || "not stated") + ", signature of " + decoded.signatureBytes + " bytes (not checked)."),
      );
      result.append(...part("Header", headerText), ...part("Payload", payloadText));
      if (decoded.times.length) {
        const rows = decoded.times.map((time) => [time.label + " (" + time.claim + ")", String(time.value), time.iso, time.relative + (time.note ? ". " + time.note : "")]);
        result.append(el("h3", { text: "Times" }), table(["Claim", "Value", "UTC", "From now"], rows, "values"));
      }
    }

    input.addEventListener("input", debounce(run));
    root.append(
      el(
        "p",
        { class: "notice" },
        el("strong", { text: "The signature is NOT verified." }),
        " Anyone can write a token that decodes like this. Whether it is genuine is decided by the service that holds the key, never by reading it.",
      ),
      input,
      verdict.node,
      result,
      el("div", { class: "row" }, copyButton(() => payloadText, "Copy payload"), copyButton(() => headerText, "Copy header")),
    );
    run();
  },
};
