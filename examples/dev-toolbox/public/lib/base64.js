// Base64 over bytes, with text going through UTF-8.
//
// btoa("\u00e9") gives a Latin-1 byte and btoa("\u20ac") throws: the browser's own
// pair works on "binary strings", not on text. So text is turned into UTF-8
// bytes first, and decoded bytes are only shown as text when they are UTF-8.
//
// No DOM in this file.

const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const VALUES = new Map(Array.from(ALPHABET, (character, index) => [character, index]));
VALUES.set("-", 62); // the URL-safe alphabet (RFC 4648, section 5)
VALUES.set("_", 63);

export function bytesToBase64(bytes, { urlSafe = false } = {}) {
  const out = [];
  for (let at = 0; at < bytes.length; at += 3) {
    const left = bytes.length - at;
    const chunk = (bytes[at] << 16) | ((left > 1 ? bytes[at + 1] : 0) << 8) | (left > 2 ? bytes[at + 2] : 0);
    out.push(
      ALPHABET[chunk >> 18],
      ALPHABET[(chunk >> 12) & 63],
      left > 1 ? ALPHABET[(chunk >> 6) & 63] : "=",
      left > 2 ? ALPHABET[chunk & 63] : "=",
    );
  }
  const text = out.join("");
  // URL-safe output drops the padding, as JWTs and most query strings do.
  return urlSafe ? text.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "") : text;
}

// {ok: true, bytes, urlSafe} or {ok: false, message}. Both alphabets are
// read, white space is skipped and missing padding is forgiven: pasted
// Base64 is rarely tidy.
export function base64ToBytes(text) {
  const symbols = [];
  let padding = 0;
  let urlSafe = false;
  for (let at = 0; at < text.length; at += 1) {
    const character = text[at];
    if (" \t\r\n".includes(character)) {
      continue;
    }
    if (character === "=") {
      padding += 1;
      continue;
    }
    const value = VALUES.get(character);
    if (value === undefined) {
      const point = String.fromCodePoint(text.codePointAt(at));
      return { ok: false, message: "'" + point + "' (position " + (at + 1) + ") is not a Base64 character." };
    }
    if (padding) {
      return { ok: false, message: "'=' may only stand at the end, but more Base64 follows it (position " + (at + 1) + ")." };
    }
    urlSafe = urlSafe || character === "-" || character === "_";
    symbols.push(value);
  }
  if (padding > 2) {
    return { ok: false, message: "Base64 ends with at most two '=' signs; this text has " + padding + "." };
  }
  if (symbols.length % 4 === 1) {
    return { ok: false, message: "The text is cut off: " + symbols.length + " Base64 characters cannot be a whole number of bytes." };
  }
  const bytes = new Uint8Array(Math.floor((symbols.length * 3) / 4));
  let written = 0;
  for (let at = 0; at < symbols.length; at += 4) {
    const have = Math.min(4, symbols.length - at);
    const chunk =
      (symbols[at] << 18) | (symbols[at + 1] << 12) | ((have > 2 ? symbols[at + 2] : 0) << 6) | (have > 3 ? symbols[at + 3] : 0);
    bytes[written++] = chunk >> 16;
    if (have > 2) {
      bytes[written++] = (chunk >> 8) & 255;
    }
    if (have > 3) {
      bytes[written++] = chunk & 255;
    }
  }
  return { ok: true, bytes, urlSafe };
}

export function encodeText(text, options) {
  return bytesToBase64(new TextEncoder().encode(text), options);
}

// The bytes as text when they are valid UTF-8, else null.
export function utf8(bytes) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error) {
    return null;
  }
}

// {ok: true, bytes, text, urlSafe}; text is null when the bytes are not UTF-8.
export function decodeText(base64) {
  const decoded = base64ToBytes(base64);
  if (!decoded.ok) {
    return decoded;
  }
  return { ok: true, bytes: decoded.bytes, text: utf8(decoded.bytes), urlSafe: decoded.urlSafe };
}

// A classic hex dump (offset, sixteen bytes, their ASCII) for bytes that are not text.
export function hexDump(bytes, limit = 4096) {
  const lines = [];
  const end = Math.min(bytes.length, limit);
  for (let at = 0; at < end; at += 16) {
    const row = Array.from(bytes.subarray(at, Math.min(at + 16, end)));
    const hex = row.map((byte) => byte.toString(16).padStart(2, "0")).join(" ");
    const ascii = row.map((byte) => (byte >= 32 && byte < 127 ? String.fromCharCode(byte) : ".")).join("");
    lines.push(at.toString(16).padStart(8, "0") + "  " + hex.padEnd(47) + "  " + ascii);
  }
  if (bytes.length > end) {
    lines.push("... " + (bytes.length - end) + " more bytes");
  }
  return lines.join("\n");
}
