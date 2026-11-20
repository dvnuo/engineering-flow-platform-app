// Epoch numbers and ISO 8601 text, both ways.
//
// No DOM in this file. Local time is whatever the JavaScript engine says it
// is; the functions that need an offset take it as an argument so they can
// be tested.

const MAX_MS = 8.64e15; // the range of a JavaScript Date
const ISO =
  /^(\d{4})-(\d{2})-(\d{2})(?:[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?\s?([Zz]|[+-]\d{2}(?::?\d{2})?)?)?$/;
const UNITS = { seconds: 1000, milliseconds: 1, microseconds: 0.001, nanoseconds: 0.000001 };
const RANGE = "That is outside the range a date can hold (about 271,821 BC to AD 275,760).";

function daysIn(year, month) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

// Which unit a bare number most likely is: 1.7e9 is seconds, 1.7e12 milliseconds.
export function guessUnit(value) {
  const size = Math.abs(value);
  if (size < 1e11) {
    return "seconds";
  }
  if (size < 1e14) {
    return "milliseconds";
  }
  return size < 1e17 ? "microseconds" : "nanoseconds";
}

// A moment typed as an epoch number or as ISO 8601:
// {ok: true, ms, readAs} or {ok: false, message}.
export function parseInstant(text, unit = "auto") {
  const input = text.trim();
  if (!input) {
    return { ok: false, message: "Type an epoch number or an ISO 8601 date." };
  }
  if (/^[+-]?\d+(\.\d+)?$/.test(input)) {
    const value = Number(input);
    const chosen = unit === "auto" ? guessUnit(value) : unit;
    const ms = value * UNITS[chosen];
    if (!(Math.abs(ms) <= MAX_MS)) {
      return { ok: false, message: RANGE };
    }
    return { ok: true, ms: Math.round(ms), readAs: "epoch " + chosen + (unit === "auto" ? " (guessed from the size of the number)" : "") };
  }
  const found = ISO.exec(input);
  if (!found) {
    return {
      ok: false,
      message: "This is neither a number nor ISO 8601. Examples: 1767225600, 2026-01-01T00:00:00Z, 2026-01-01 08:00:00+08:00.",
    };
  }
  const [year, month, day] = [Number(found[1]), Number(found[2]), Number(found[3])];
  const [hour, minute, second] = [Number(found[4] || 0), Number(found[5] || 0), Number(found[6] || 0)];
  const fraction = Number((found[7] || "").padEnd(3, "0").slice(0, 3)); // milliseconds, cut from the digits: no rounding surprises
  if (year < 100) {
    return { ok: false, message: "Years before 0100 are not supported here." }; // Date.UTC reads 0-99 as 1900-1999
  }
  if (month < 1 || month > 12 || day < 1 || day > daysIn(year, month)) {
    return { ok: false, message: "There is no such day: " + found[1] + "-" + found[2] + "-" + found[3] + "." };
  }
  if (hour > 23 || minute > 59 || second > 59) {
    return { ok: false, message: "There is no such time of day: hours go to 23, minutes and seconds to 59." };
  }
  const zone = found[8];
  let ms;
  let readAs;
  if (found[4] === undefined) {
    ms = Date.UTC(year, month - 1, day);
    readAs = "a date, taken as 00:00 UTC";
  } else if (zone === undefined) {
    ms = new Date(year, month - 1, day, hour, minute, second, fraction).getTime();
    readAs = "ISO 8601 without an offset, taken as your local time";
  } else {
    ms = Date.UTC(year, month - 1, day, hour, minute, second, fraction);
    readAs = "ISO 8601 in UTC";
    if (zone !== "Z" && zone !== "z") {
      const digits = zone.replace(":", "");
      const offset = Number(digits.slice(1, 3)) * 60 + Number(digits.slice(3, 5) || 0);
      ms -= (digits[0] === "-" ? -offset : offset) * 60000;
      readAs = "ISO 8601 with the offset " + zone;
    }
  }
  return Number.isNaN(ms) || Math.abs(ms) > MAX_MS ? { ok: false, message: RANGE } : { ok: true, ms, readAs };
}

// "3 hours ago", "in 2 days": the distance from ``nowMs`` in the largest unit that fits.
export function relativeTime(ms, nowMs) {
  const seconds = Math.floor(Math.abs(ms - nowMs) / 1000);
  if (seconds < 5) {
    return "now";
  }
  const steps = [
    [60, 1, "second"],
    [3600, 60, "minute"],
    [86400, 3600, "hour"],
    [86400 * 30, 86400, "day"],
    [86400 * 365, 86400 * 30.4375, "month"],
    [Infinity, 86400 * 365.25, "year"],
  ];
  const [, size, word] = steps.find(([below]) => seconds < below);
  const count = Math.max(1, Math.floor(seconds / size));
  const phrase = count + " " + word + (count === 1 ? "" : "s");
  return ms < nowMs ? phrase + " ago" : "in " + phrase;
}

function trimZeroMillis(iso) {
  return iso.replace(".000", "");
}

export function isoUtc(ms) {
  return trimZeroMillis(new Date(ms).toISOString());
}

// "+08:00" for 480, "-03:30" for -210 (minutes east of UTC).
export function offsetText(offsetMinutes) {
  const size = Math.abs(offsetMinutes);
  const two = (number) => String(number).padStart(2, "0");
  return (offsetMinutes < 0 ? "-" : "+") + two(Math.floor(size / 60)) + ":" + two(size % 60);
}

// The wall-clock time at an offset, as ISO 8601: 2026-01-01T08:00:00+08:00.
export function isoWithOffset(ms, offsetMinutes) {
  return trimZeroMillis(new Date(ms + offsetMinutes * 60000).toISOString()).replace("Z", offsetText(offsetMinutes));
}

// Minutes east of UTC that the engine's local zone had (or will have) at that moment.
export function localOffset(ms) {
  return -new Date(ms).getTimezoneOffset();
}

// Every way of writing one moment, as rows for a table.
export function describeInstant(ms, nowMs, offsetMinutes = localOffset(ms)) {
  const seconds = ms / 1000;
  return [
    { label: "Epoch seconds", value: Number.isInteger(seconds) ? String(seconds) : seconds.toFixed(3) },
    { label: "Epoch milliseconds", value: String(ms) },
    { label: "ISO 8601, UTC", value: isoUtc(ms) },
    { label: "ISO 8601, local (UTC" + offsetText(offsetMinutes) + ")", value: isoWithOffset(ms, offsetMinutes) },
    { label: "Relative to now", value: relativeTime(ms, nowMs) },
  ];
}
