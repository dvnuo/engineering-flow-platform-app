// JSON read token by token.
//
// Two reasons not to lean on JSON.parse alone. Browsers word its errors
// differently and some give no position, so "line 3, column 5" needs a reader
// of our own. And formatting through JSON.parse + JSON.stringify quietly
// changes data: 12345678901234567890 comes back as 12345678901234567000 and
// a repeated key disappears. Here the output is built from the tokens as
// they were typed, so only the white space changes.
//
// No DOM in this file: it is tested with node (sdk/tests/test_example_dev_toolbox.py).

const MAX_DEPTH = 1000;
const MAX_WARNINGS = 20;
const NUMBER = /-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/y;
const WORD = /[A-Za-z_$][A-Za-z0-9_$]*/y;
const WORD_HINTS = {
  True: "true",
  TRUE: "true",
  False: "false",
  FALSE: "false",
  None: "null",
  NULL: "null",
  Null: "null",
  nil: "null",
};

class Problem extends Error {
  constructor(message, offset) {
    super(message);
    this.offset = offset;
  }
}

// Line and column (both from 1) of an offset in the text.
export function position(text, offset) {
  let line = 1;
  let start = 0;
  for (let at = text.indexOf("\n"); at !== -1 && at < offset; at = text.indexOf("\n", at + 1)) {
    line += 1;
    start = at + 1;
  }
  return { line, column: offset - start + 1 };
}

// The line around an offset, cut to a width that fits a status box, and
// where the caret goes under it.
export function excerpt(text, offset, width = 72) {
  const start = text.lastIndexOf("\n", offset - 1) + 1;
  let end = text.indexOf("\n", offset);
  if (end === -1) {
    end = text.length;
  }
  const from = Math.max(start, Math.min(offset - Math.floor(width / 2), end - width));
  const to = Math.min(end, from + width);
  const before = from > start ? "..." : "";
  const after = to < end ? "..." : "";
  return {
    text: before + text.slice(from, to).replace(/[\t\r]/g, " ") + after,
    caret: before.length + (offset - from),
  };
}

function shown(character) {
  if (character === undefined) {
    return "the end of the text";
  }
  const code = character.codePointAt(0);
  return code < 32 || code === 127 ? "a control character (code " + code + ")" : "'" + character + "'";
}

function readString(text, start) {
  let at = start + 1;
  for (;;) {
    if (at >= text.length) {
      throw new Problem("This string is never closed with a double quote.", start);
    }
    const code = text.charCodeAt(at);
    if (code === 34) {
      return at + 1;
    }
    if (code === 10 || code === 13) {
      throw new Problem("A string cannot run over the end of a line. Close it, or write the line break as \\n.", at);
    }
    if (code < 32) {
      const escape = code === 9 ? "\\t" : "\\u" + code.toString(16).padStart(4, "0");
      throw new Problem("A string cannot hold a raw control character. Write it as " + escape + ".", at);
    }
    if (code !== 92) {
      at += 1;
    } else if (text[at + 1] === "u") {
      if (!/^[0-9a-fA-F]{4}$/.test(text.slice(at + 2, at + 6))) {
        throw new Problem("\\u must be followed by four hex digits.", at);
      }
      at += 6;
    } else if (at + 1 < text.length && '"\\/bfnrt'.includes(text[at + 1])) {
      at += 2;
    } else {
      throw new Problem("\\" + (text[at + 1] || "") + " is not an escape JSON knows. A backslash itself is written \\\\.", at);
    }
  }
}

function readNumber(text, start) {
  NUMBER.lastIndex = start;
  const found = NUMBER.exec(text);
  if (!found) {
    throw new Problem("A minus sign must be followed by a digit.", start);
  }
  const end = start + found[0].length;
  const next = text[end];
  if (next !== undefined && /[0-9A-Za-z._+]/.test(next)) {
    if (/[0-9]/.test(next)) {
      throw new Problem("A number cannot start with extra zeros.", start);
    }
    if (next === ".") {
      throw new Problem("A decimal point must be followed by a digit.", end);
    }
    if (next === "e" || next === "E") {
      throw new Problem("An exponent needs digits after the e.", end);
    }
    throw new Problem("This is not a JSON number. Hex, octal and suffixes are not allowed.", start);
  }
  return end;
}

function unexpectedValue(text, at) {
  const character = text[at];
  if (character === "'") {
    return "Strings need double quotes, not single quotes.";
  }
  if (character === "/" && (text[at + 1] === "/" || text[at + 1] === "*")) {
    return "JSON has no comments. Remove this one.";
  }
  if (character === ",") {
    return "A value is missing before this comma.";
  }
  if (character === "." || character === "+") {
    return "A number cannot start with '" + character + "'. Write 0.5, not .5, and 1, not +1.";
  }
  WORD.lastIndex = at;
  const word = WORD.exec(text);
  if (word) {
    if (WORD_HINTS[word[0]]) {
      return word[0] + " must be written " + WORD_HINTS[word[0]] + ".";
    }
    if (["undefined", "NaN", "Infinity"].includes(word[0])) {
      return word[0] + " is not a JSON value. Use null, or leave the key out.";
    }
    return "Unexpected word " + word[0] + ". Text must be in double quotes.";
  }
  return "Expected a value, found " + shown(character) + ".";
}

// Read the whole text. {ok: true, tokens, warnings, summary} or
// {ok: false, error: {message, offset, line, column}}.
export function scan(text) {
  const tokens = [];
  const warnings = [];
  const stack = []; // one frame per open bracket: {open, at, keys, members}
  const summary = { type: "", members: 0, values: 0, depth: 0 };
  let expect = "value";
  let at = text.charCodeAt(0) === 0xfeff ? 1 : 0; // a byte order mark from an editor is not worth an error

  const push = (type, end) => {
    tokens.push({ type, text: text.slice(at, end), offset: at });
    at = end;
  };
  const valueDone = (type) => {
    summary.values += 1;
    if (stack.length) {
      stack[stack.length - 1].members += 1;
      expect = "comma";
    } else {
      summary.type = type;
      expect = "end";
    }
  };
  const open = (bracket) => {
    if (stack.length >= MAX_DEPTH) {
      throw new Problem("This is nested more than " + MAX_DEPTH + " levels deep, which this tool does not follow.", at);
    }
    stack.push({ open: bracket, at, keys: bracket === "{" ? new Set() : null, members: 0 });
    summary.depth = Math.max(summary.depth, stack.length);
    push(bracket, at + 1);
    expect = bracket === "{" ? "first-key" : "first-value";
  };
  const close = (bracket) => {
    const frame = stack.pop();
    push(bracket, at + 1);
    if (!stack.length) {
      summary.members = frame.members;
    }
    valueDone(frame.open === "{" ? "object" : "array");
  };

  try {
    for (;;) {
      while (at < text.length && " \t\n\r".includes(text[at])) {
        at += 1;
      }
      if (at >= text.length) {
        break;
      }
      const character = text[at];
      if (expect === "end") {
        throw new Problem(
          character === "{" || character === "["
            ? "A JSON text holds one value, and a second one starts here. (For JSON Lines, check one line at a time.)"
            : "The JSON value has ended, but more text follows: " + shown(character) + ".",
          at,
        );
      }
      if (expect === "value" || expect === "first-value") {
        if (character === "]" && stack.length && stack[stack.length - 1].open === "[") {
          if (expect === "value") {
            throw new Problem("Remove the comma before this ']': JSON allows no trailing comma.", at);
          }
          close("]");
        } else if (character === "{" || character === "[") {
          open(character);
        } else if (character === '"') {
          push("string", readString(text, at));
          valueDone("string");
        } else if (character === "-" || (character >= "0" && character <= "9")) {
          push("number", readNumber(text, at));
          valueDone("number");
        } else {
          WORD.lastIndex = at;
          const word = WORD.exec(text);
          if (!word || !["true", "false", "null"].includes(word[0])) {
            throw new Problem(unexpectedValue(text, at), at);
          }
          push("literal", at + word[0].length);
          valueDone(word[0] === "null" ? "null" : "true or false");
        }
      } else if (expect === "key" || expect === "first-key") {
        if (character === "}") {
          if (expect === "key") {
            throw new Problem("Remove the comma before this '}': JSON allows no trailing comma.", at);
          }
          close("}");
        } else if (character === '"') {
          const end = readString(text, at);
          const frame = stack[stack.length - 1];
          const key = JSON.parse(text.slice(at, end));
          if (frame.keys.has(key) && warnings.length < MAX_WARNINGS) {
            warnings.push({
              message: "The key " + text.slice(at, end) + " appears more than once in one object. Most programs keep only the last.",
              offset: at,
              ...position(text, at),
            });
          }
          frame.keys.add(key);
          push("key", end);
          expect = "colon";
        } else {
          WORD.lastIndex = at;
          const word = WORD.exec(text);
          throw new Problem(
            character === "'" || word
              ? "A key must be in double quotes" + (word ? ': "' + word[0] + '".' : ".")
              : "Expected a key in double quotes, found " + shown(character) + ".",
            at,
          );
        }
      } else if (expect === "colon") {
        if (character !== ":") {
          throw new Problem("Expected ':' after the key, found " + shown(character) + ".", at);
        }
        push(":", at + 1);
        expect = "value";
      } else {
        const frame = stack[stack.length - 1];
        const closer = frame.open === "{" ? "}" : "]";
        if (character === ",") {
          push(",", at + 1);
          expect = frame.open === "{" ? "key" : "value";
        } else if (character === closer) {
          close(closer);
        } else {
          throw new Problem("Expected ',' or '" + closer + "', found " + shown(character) + ". Is a comma missing?", at);
        }
      }
    }
    if (!tokens.length) {
      throw new Problem("There is no JSON here yet.", at);
    }
    if (expect !== "end") {
      const frame = stack[stack.length - 1];
      const where = position(text, frame ? frame.at : 0);
      throw new Problem(
        frame
          ? "The text ends before the '" + frame.open + "' of line " + where.line + ", column " + where.column + " is closed."
          : "The text ends where a value is expected.",
        at,
      );
    }
  } catch (problem) {
    if (!(problem instanceof Problem)) {
      throw problem;
    }
    return { ok: false, error: { message: problem.message, offset: problem.offset, ...position(text, problem.offset) } };
  }
  return { ok: true, tokens, warnings, summary };
}

// "an object with 3 keys", for the status line.
export function describe(summary) {
  const count = (number, word) => number + " " + word + (number === 1 ? "" : "s");
  if (summary.type === "object") {
    return "an object with " + count(summary.members, "key");
  }
  if (summary.type === "array") {
    return "an array of " + count(summary.members, "item");
  }
  return "a single value (" + summary.type + ")";
}

// Indented JSON. ``indent`` is a number of spaces or "\t".
export function format(text, indent = 2) {
  const read = scan(text);
  if (!read.ok) {
    return read;
  }
  const unit = typeof indent === "number" ? " ".repeat(Math.max(0, Math.min(indent, 8))) : "\t";
  const out = [];
  let depth = 0;
  for (let index = 0; index < read.tokens.length; index += 1) {
    const token = read.tokens[index];
    const next = read.tokens[index + 1];
    if (token.type === "{" || token.type === "[") {
      if (next && (next.type === "}" || next.type === "]")) {
        out.push(token.text + next.text); // an empty object or array stays on one line
        index += 1;
      } else {
        depth += 1;
        out.push(token.text, "\n", unit.repeat(depth));
      }
    } else if (token.type === "}" || token.type === "]") {
      depth -= 1;
      out.push("\n", unit.repeat(depth), token.text);
    } else if (token.type === ",") {
      out.push(",\n", unit.repeat(depth));
    } else if (token.type === ":") {
      out.push(": ");
    } else {
      out.push(token.text);
    }
  }
  return { ok: true, text: out.join(""), warnings: read.warnings, summary: read.summary };
}

// The same JSON without any white space between the tokens.
export function minify(text) {
  const read = scan(text);
  if (!read.ok) {
    return read;
  }
  return { ok: true, text: read.tokens.map((token) => token.text).join(""), warnings: read.warnings, summary: read.summary };
}
