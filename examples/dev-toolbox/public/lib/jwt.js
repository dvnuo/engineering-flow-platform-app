// A JSON Web Token taken apart. Decoding is all this does: the signature is
// NOT verified (that needs the issuer's key, which a page must never hold),
// so nothing here says whether a token is genuine.
//
// No DOM in this file.

import { base64ToBytes, utf8 } from "./base64.js";
import { format } from "./json.js";
import { isoUtc, relativeTime } from "./time.js";

const TIME_CLAIMS = [
  ["exp", "Expires"],
  ["nbf", "Not valid before"],
  ["iat", "Issued at"],
  ["auth_time", "Signed in at"],
];

function readPart(part, name) {
  const decoded = base64ToBytes(part);
  if (!decoded.ok) {
    return { ok: false, message: "The " + name + " is not Base64URL: " + decoded.message };
  }
  const text = utf8(decoded.bytes);
  let value;
  try {
    value = JSON.parse(text === null ? "" : text);
  } catch (error) {
    return { ok: false, message: "The " + name + " does not decode to JSON, so this is not a JWT." };
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return { ok: false, message: "The " + name + " is JSON but not an object, so this is not a JWT." };
  }
  // Pretty-printed from the text itself, so large numbers stay as they were issued.
  return { ok: true, value, text: format(text).text || JSON.stringify(value, null, 2) };
}

function timeOf(claim, label, value, nowMs) {
  // The standard counts seconds. Thirteen digits are milliseconds, a common mistake of issuers.
  const milliseconds = Math.abs(value) >= 1e11;
  const ms = Math.round(milliseconds ? value : value * 1000);
  if (!Number.isFinite(ms) || Math.abs(ms) > 8.64e15) {
    return { claim, label, value, ms: null, iso: "not a time", relative: "", note: "" };
  }
  return {
    claim,
    label,
    value,
    ms,
    iso: isoUtc(ms),
    relative: relativeTime(ms, nowMs),
    note: milliseconds ? "This looks like milliseconds; the standard asks for seconds." : "",
  };
}

// {ok: true, header, payload, headerText, payloadText, algorithm, signatureBytes, times, validity}
// or {ok: false, message}.
export function decodeJwt(token, nowMs) {
  const compact = token.trim().replace(/^Bearer\s+/i, "").replace(/\s+/g, "");
  if (!compact) {
    return { ok: false, message: "Paste a token." };
  }
  const parts = compact.split(".");
  if (parts.length === 5) {
    return { ok: false, message: "This token has five parts: it is encrypted (JWE). Its content cannot be read without the key." };
  }
  if (parts.length !== 3) {
    return {
      ok: false,
      message: "A JWT has three parts separated by dots (header.payload.signature); this text has " + parts.length + ".",
    };
  }
  const header = readPart(parts[0], "header");
  if (!header.ok) {
    return header;
  }
  const payload = readPart(parts[1], "payload");
  if (!payload.ok) {
    return payload;
  }
  const signature = base64ToBytes(parts[2]);
  const times = TIME_CLAIMS.filter(([claim]) => typeof payload.value[claim] === "number").map(([claim, label]) =>
    timeOf(claim, label, payload.value[claim], nowMs),
  );
  const at = (claim) => {
    const found = times.find((time) => time.claim === claim);
    return found && found.ms !== null ? found : null;
  };
  const expires = at("exp");
  const starts = at("nbf");
  let validity;
  if (expires && nowMs >= expires.ms) {
    validity = { state: "expired", sentence: "Expired " + expires.relative + "." };
  } else if (starts && nowMs < starts.ms) {
    validity = { state: "not-yet-valid", sentence: "Not valid yet: its time starts " + starts.relative + "." };
  } else if (expires) {
    validity = { state: "current", sentence: "Inside its time window: it expires " + expires.relative + "." };
  } else {
    validity = { state: "no-expiry", sentence: "It carries no expiry time (no exp claim)." };
  }
  return {
    ok: true,
    header: header.value,
    payload: payload.value,
    headerText: header.text,
    payloadText: payload.text,
    algorithm: typeof header.value.alg === "string" ? header.value.alg : "",
    signatureBytes: signature.ok ? signature.bytes.length : 0,
    times,
    validity,
  };
}
