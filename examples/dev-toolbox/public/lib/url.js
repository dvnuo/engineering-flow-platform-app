// Percent-encoding, and a URL taken apart.
//
// No DOM in this file (URL and TextDecoder exist in node as well).

export function encodeComponent(text) {
  try {
    return { ok: true, text: encodeURIComponent(text) };
  } catch (error) {
    // The only way encodeURIComponent fails: half of a surrogate pair.
    return { ok: false, message: "The text holds half of a character (a lone surrogate) and cannot be encoded." };
  }
}

// {ok: true, text} or {ok: false, message}. ``plusAsSpace`` is how HTML
// forms and most query strings write a space.
export function decodeComponent(text, { plusAsSpace = false } = {}) {
  const source = plusAsSpace ? text.replace(/\+/g, " ") : text;
  const broken = /%(?![0-9a-fA-F]{2})/.exec(source);
  if (broken) {
    const found = source.slice(broken.index, broken.index + 3);
    return {
      ok: false,
      message: 'A % must be followed by two hex digits; "' + found + '" at position ' + (broken.index + 1) + " is not. A literal % is written %25.",
    };
  }
  try {
    return { ok: true, text: decodeURIComponent(source) };
  } catch (error) {
    return { ok: false, message: "The percent-encoded bytes are not UTF-8 text." };
  }
}

// "a=1&b=two+words" as [{key, value, raw}], in order and with repeats kept.
// A pair that cannot be decoded is shown as typed.
export function queryPairs(query) {
  const pairs = [];
  for (const raw of query.replace(/^[?#]/, "").split("&")) {
    if (!raw) {
      continue;
    }
    const cut = raw.indexOf("=");
    const read = (part) => {
      const decoded = decodeComponent(part, { plusAsSpace: true });
      return decoded.ok ? decoded.text : part;
    };
    pairs.push({ key: read(cut === -1 ? raw : raw.slice(0, cut)), value: cut === -1 ? "" : read(raw.slice(cut + 1)), raw });
  }
  return pairs;
}

// A URL, or a bare query string, taken apart:
// {ok: true, kind: "url" | "query", parts: [{name, value}], params, fragmentParams}.
export function parseUrl(text) {
  const input = text.trim();
  if (!input) {
    return { ok: false, message: "Paste a URL or a query string." };
  }
  let url = null;
  try {
    url = new URL(input);
  } catch (error) {
    url = null;
  }
  if (url === null) {
    if (/^[?]?[^\s?#=&]+=/.test(input) && !/\s/.test(input)) {
      return { ok: true, kind: "query", parts: [], params: queryPairs(input), fragmentParams: [] };
    }
    return {
      ok: false,
      message: "This is not a full URL. Start it with a scheme such as https://, or paste only the query string (a=1&b=2).",
    };
  }
  const parts = [
    ["Scheme", url.protocol.replace(/:$/, "")],
    ["User", url.username],
    ["Password", url.password],
    ["Host", url.hostname],
    ["Port", url.port],
    ["Path", url.pathname],
    ["Query", url.search],
    ["Fragment", url.hash],
  ];
  return {
    ok: true,
    kind: "url",
    parts: parts.filter(([, value]) => value).map(([name, value]) => ({ name, value })),
    params: queryPairs(url.search),
    // Single-page apps and OAuth redirects put parameters behind the #.
    fragmentParams: url.hash.includes("=") ? queryPairs(url.hash) : [],
  };
}
