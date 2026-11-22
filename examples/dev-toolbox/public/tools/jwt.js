import { decodeJwt } from "../lib/jwt.js";
import { copyButton, debounce, editor, el, empty, message, pane, table } from "../ui.js";

// How the validity of a token is said: a token that is used now is fine, one that expired is not.
const TONES = { current: "ok", expired: "fail", "not-yet-valid": "warn", "no-expiry": "warn" };

export default {
  id: "jwt",
  title: "JWT",
  icon: "key-round",
  keywords: "token bearer claims decode oauth oidc",
  blurb: "Read the header and the payload of a JSON Web Token, with its times as dates.",
  mount(root) {
    const input = editor({ rows: 5, "aria-label": "Token", placeholder: 'eyJhbGciOi... (a leading "Bearer " is fine)' });
    const verdict = message();
    const result = el("div", { class: "dt-stack" });
    let headerText = "";
    let payloadText = "";

    function run() {
      headerText = "";
      payloadText = "";
      if (!input.value.trim()) {
        verdict.say("");
        result.replaceChildren(empty("key-round", "Paste a token", "Its header, its payload and its times appear here. It is decoded in this tab and goes nowhere."));
        return;
      }
      const decoded = decodeJwt(input.value, Date.now());
      if (!decoded.ok) {
        verdict.fail(decoded.message);
        result.replaceChildren();
        return;
      }
      headerText = decoded.headerText;
      payloadText = decoded.payloadText;
      const unsigned = decoded.algorithm.toLowerCase() === "none";
      verdict[TONES[decoded.validity.state] || "say"](
        decoded.validity.sentence +
          (unsigned ? " Its alg is none: it is not signed at all." : " Algorithm " + (decoded.algorithm || "not stated") + ", signature of " + decoded.signatureBytes + " bytes (not checked)."),
      );
      result.replaceChildren(
        el(
          "div",
          { class: "dt-split" },
          pane("Header", el("pre", { class: "dt-out", tabindex: "0", text: headerText }), copyButton(() => headerText)),
          pane("Payload", el("pre", { class: "dt-out", tabindex: "0", text: payloadText }), copyButton(() => payloadText)),
        ),
      );
      if (decoded.times.length) {
        const rows = decoded.times.map((time) => [time.label + " (" + time.claim + ")", String(time.value), time.iso, time.relative + (time.note ? ". " + time.note : "")]);
        result.append(pane("Times", table(["Claim", "Value", "UTC", "From now"], rows, "dt-values")));
      }
    }

    input.addEventListener("input", debounce(run));
    root.append(
      el(
        "div",
        { class: "alert alert-soft alert-warning text-sm dt-notice", role: "note" },
        el(
          "span",
          {},
          el("strong", { text: "The signature is NOT verified." }),
          " Anyone can write a token that decodes like this. Whether it is genuine is decided by the service that holds the key, never by reading it.",
        ),
      ),
      pane("Token", input),
      verdict.node,
      result,
    );
    run();
  },
};
