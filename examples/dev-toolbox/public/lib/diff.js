// A line diff by longest common subsequence (LCS): the lines both texts
// share, in order, stay; everything else is removed or added.
//
// The table of the textbook algorithm has (lines before) x (lines after)
// cells, so the common start and end are set aside first (most edits touch
// a small part of a text) and what is left must fit MAX_CELLS.
//
// No DOM in this file.

export const MAX_CELLS = 4000000;

// Lines of a text; "\r\n" counts as one break and a final break adds no empty line.
export function splitLines(text) {
  if (text === "") {
    return [];
  }
  const lines = text.split(/\r\n|\r|\n/);
  if (lines[lines.length - 1] === "") {
    lines.pop();
  }
  return lines;
}

// {ok: true, ops, added, removed, unchanged} or {ok: false, message}.
// An op is {type: "same" | "del" | "add", text, before, after} with the
// line numbers (from 1) on each side, null on the side it is missing from.
export function diffLines(beforeText, afterText, { ignoreTrailingSpace = false } = {}) {
  const before = splitLines(beforeText);
  const after = splitLines(afterText);
  const key = ignoreTrailingSpace ? (line) => line.replace(/[ \t]+$/, "") : (line) => line;

  // Lines become numbers once, so the table below compares integers.
  const ids = new Map();
  const idOf = (line) => {
    const wanted = key(line);
    if (!ids.has(wanted)) {
      ids.set(wanted, ids.size);
    }
    return ids.get(wanted);
  };
  const a = before.map(idOf);
  const b = after.map(idOf);

  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) {
    start += 1;
  }
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1;
    endB -= 1;
  }
  const rows = endA - start;
  const columns = endB - start;
  if (rows * columns > MAX_CELLS) {
    return {
      ok: false,
      message:
        "These texts are too long and too different to compare in the browser: " +
        rows +
        " by " +
        columns +
        " lines differ after their common start and end. Compare a smaller part.",
    };
  }

  // lengths[i][j]: how many lines a[start + i ..] and b[start + j ..] have in common, in order.
  const width = columns + 1;
  const lengths = new Uint32Array((rows + 1) * width);
  for (let i = rows - 1; i >= 0; i -= 1) {
    for (let j = columns - 1; j >= 0; j -= 1) {
      lengths[i * width + j] =
        a[start + i] === b[start + j]
          ? lengths[(i + 1) * width + j + 1] + 1
          : Math.max(lengths[(i + 1) * width + j], lengths[i * width + j + 1]);
    }
  }

  const ops = [];
  const same = (i, j) => ops.push({ type: "same", text: after[j], before: i + 1, after: j + 1 });
  for (let line = 0; line < start; line += 1) {
    same(line, line);
  }
  let i = 0;
  let j = 0;
  while (i < rows || j < columns) {
    if (i < rows && j < columns && a[start + i] === b[start + j]) {
      same(start + i, start + j);
      i += 1;
      j += 1;
    } else if (i < rows && (j === columns || lengths[(i + 1) * width + j] >= lengths[i * width + j + 1])) {
      ops.push({ type: "del", text: before[start + i], before: start + i + 1, after: null });
      i += 1;
    } else {
      ops.push({ type: "add", text: after[start + j], before: null, after: start + j + 1 });
      j += 1;
    }
  }
  for (let line = 0; endA + line < a.length; line += 1) {
    same(endA + line, endB + line);
  }
  const count = (type) => ops.reduce((total, op) => total + (op.type === type ? 1 : 0), 0);
  return { ok: true, ops, added: count("add"), removed: count("del"), unchanged: count("same") };
}

// The changes grouped with ``context`` unchanged lines around them:
// [{beforeStart, beforeCount, afterStart, afterCount, ops}].
export function hunks(ops, context = 3) {
  const found = [];
  let current = null;
  let lastChange = -Infinity;
  let beforeLine = 0; // lines seen so far on each side
  let afterLine = 0;
  ops.forEach((op, index) => {
    if (op.type !== "same") {
      if (current === null || index - lastChange > 2 * context + 1) {
        if (current) {
          found.push(current);
        }
        const lead = ops.slice(Math.max(0, index - context), index).filter((earlier) => earlier.type === "same");
        current = { beforeStart: beforeLine - lead.length + 1, afterStart: afterLine - lead.length + 1, ops: lead };
      } else {
        for (let between = lastChange + 1; between < index; between += 1) {
          current.ops.push(ops[between]);
        }
      }
      current.ops.push(op);
      current.end = index;
      lastChange = index;
    }
    beforeLine += op.type === "add" ? 0 : 1;
    afterLine += op.type === "del" ? 0 : 1;
  });
  if (current) {
    found.push(current);
  }
  for (const hunk of found) {
    hunk.ops = hunk.ops.concat(ops.slice(hunk.end + 1, hunk.end + 1 + context));
    delete hunk.end;
    hunk.beforeCount = hunk.ops.filter((op) => op.type !== "add").length;
    hunk.afterCount = hunk.ops.filter((op) => op.type !== "del").length;
    // By convention an empty side is addressed by the line before it.
    hunk.beforeStart -= hunk.beforeCount === 0 ? 1 : 0;
    hunk.afterStart -= hunk.afterCount === 0 ? 1 : 0;
  }
  return found;
}

export function hunkHeader(hunk) {
  return "@@ -" + hunk.beforeStart + "," + hunk.beforeCount + " +" + hunk.afterStart + "," + hunk.afterCount + " @@";
}

// The diff as text in the unified format that patch and git understand; "" when nothing changed.
export function unified(ops, { context = 3, beforeName = "original", afterName = "changed" } = {}) {
  const found = hunks(ops, context);
  if (!found.length) {
    return "";
  }
  const lines = ["--- " + beforeName, "+++ " + afterName];
  for (const hunk of found) {
    lines.push(hunkHeader(hunk));
    for (const op of hunk.ops) {
      lines.push((op.type === "add" ? "+" : op.type === "del" ? "-" : " ") + op.text);
    }
  }
  return lines.join("\n") + "\n";
}
