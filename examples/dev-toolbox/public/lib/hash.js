// SHA digests with the browser's own crypto.subtle: no library, and the
// text or file never leaves the tab.
//
// crypto.subtle exists only on HTTPS pages and on localhost; the page says
// so when it is missing. ``subtle`` is an argument so node can test this
// file. No DOM here.

export const ALGORITHMS = ["SHA-1", "SHA-256", "SHA-384", "SHA-512"];

export function toHex(buffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

// [{algorithm, hex}] for a text (hashed as UTF-8) or for bytes.
export async function digests(data, subtle = globalThis.crypto && globalThis.crypto.subtle) {
  if (!subtle) {
    throw new Error("crypto.subtle is not available");
  }
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
  const results = [];
  for (const algorithm of ALGORITHMS) {
    results.push({ algorithm, hex: toHex(await subtle.digest(algorithm, bytes)) });
  }
  return results;
}

// Compare a digest someone published with the computed ones.
// {state: "empty" | "not-hex" | "match" | "mismatch" | "unknown-length", algorithm}.
export function matchDigest(expected, results) {
  // "sha256:ab12..." and "ab12...  file.zip" (sha256sum output) are both common.
  const words = expected.trim().replace(/^sha-?(1|256|384|512)[:=]\s*/i, "").split(/\s+/);
  const wanted = words[0].toLowerCase();
  if (!wanted) {
    return { state: "empty", algorithm: "" };
  }
  if (!/^[0-9a-f]+$/.test(wanted)) {
    return { state: "not-hex", algorithm: "" };
  }
  const same = results.find((result) => result.hex === wanted);
  if (same) {
    return { state: "match", algorithm: same.algorithm };
  }
  const sameLength = results.find((result) => result.hex.length === wanted.length);
  return sameLength ? { state: "mismatch", algorithm: sameLength.algorithm } : { state: "unknown-length", algorithm: "" };
}
