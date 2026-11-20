// Random (version 4) UUIDs.
//
// crypto.randomUUID() exists only on HTTPS pages; crypto.getRandomValues()
// is everywhere, so the 16 bytes come from there. The source of the bytes is
// an argument, which is also what lets node test this file. No DOM here.

export const MAX_COUNT = 100;

function systemRandom(length) {
  return globalThis.crypto.getRandomValues(new Uint8Array(length));
}

// Sixteen bytes as a UUID, with the version and variant bits of RFC 9562 set.
export function uuidFromBytes(bytes) {
  const copy = Uint8Array.from(bytes);
  copy[6] = (copy[6] & 0x0f) | 0x40; // version 4
  copy[8] = (copy[8] & 0x3f) | 0x80; // variant 10xx
  const hex = Array.from(copy, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
}

// ``count`` UUIDs (1 to MAX_COUNT), optionally in capitals or without hyphens.
export function generate(count, { uppercase = false, hyphens = true } = {}, random = systemRandom) {
  const wanted = Math.max(1, Math.min(MAX_COUNT, Math.floor(Number(count)) || 1));
  const list = [];
  for (let made = 0; made < wanted; made += 1) {
    let uuid = uuidFromBytes(random(16));
    uuid = hyphens ? uuid : uuid.replace(/-/g, "");
    list.push(uppercase ? uuid.toUpperCase() : uuid);
  }
  return list;
}
