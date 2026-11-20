// Arithmetic and wording for the matrix: data in, numbers, SVG path strings
// and sentences out. Nothing here touches the document, so the same file runs
// in the browser and under node, where the tests call it.

// One letter per build, as the server sends a row.
export const STATES = {
  p: "passed",
  f: "failed",
  s: "skipped",
  "-": "not in this build",
  " ": "no report",
};

function round(value) {
  return Math.round(value * 10) / 10;
}

// "com.acme.auth.LoginTest" -> the package and the class on their own.
export function splitClass(className) {
  const dot = className.lastIndexOf(".");
  return dot < 0 ? { package: "", short: className } : { package: className.slice(0, dot), short: className.slice(dot + 1) };
}

// The packages of the rows with the number of rows in each, by name.
export function packagesOf(rows) {
  const counts = new Map();
  for (const row of rows) {
    const name = splitClass(row.class).package;
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return Array.from(counts, ([name, count]) => ({ name: name, rows: count })).sort((a, b) =>
    a.name < b.name ? -1 : a.name > b.name ? 1 : 0
  );
}

// The rows a visitor asked for: of one package (and the packages below it),
// and with every typed word somewhere in the class or the test name. Without
// a package (null) every package is meant; "" means the classes that have none.
export function filterRows(rows, wanted) {
  const inPackage = wanted && typeof wanted.package === "string" ? wanted.package : null;
  const words = ((wanted && wanted.text) || "").toLowerCase().split(/\s+/).filter(Boolean);
  return rows.filter((row) => {
    const own = splitClass(row.class).package;
    if (inPackage !== null && own !== inPackage && !own.startsWith(inPackage + ".")) {
      return false;
    }
    const text = (row.class + " " + row.name).toLowerCase();
    return words.every((word) => text.includes(word));
  });
}

// One row of the heatmap. A failed build is a full square with a cross and a
// passed one a full square, so the two differ in more than colour; a skipped
// build is a small square, and an absent one a dot. `crosses` is one path for
// all the failed squares of the row.
export function rowCells(cells, options) {
  const size = (options && options.size) || 14;
  const gap = options && options.gap !== undefined ? options.gap : 2;
  const squares = [];
  let crosses = "";
  Array.from(cells).forEach((state, index) => {
    const x = index * (size + gap);
    if (state === "p" || state === "f") {
      squares.push({ index: index, state: state, x: x, y: 0, size: size });
    } else if (state === "s") {
      squares.push({ index: index, state: state, x: round(x + size * 0.25), y: round(size * 0.25), size: round(size * 0.5) });
    } else if (state === "-") {
      squares.push({ index: index, state: state, x: round(x + size * 0.5 - 2), y: round(size * 0.5 - 2), size: 4 });
    }
    if (state === "f") {
      const left = round(x + size * 0.3);
      const right = round(x + size * 0.7);
      const top = round(size * 0.3);
      const bottom = round(size * 0.7);
      crosses += "M" + left + " " + top + "L" + right + " " + bottom + "M" + right + " " + top + "L" + left + " " + bottom;
    }
  });
  return { width: Math.max(0, cells.length * (size + gap) - gap), height: size, squares: squares, crosses: crosses };
}

// Which column of a row a pointer at `x` (in the units of the drawing) is over.
export function columnAt(x, count, options) {
  const size = (options && options.size) || 14;
  const gap = options && options.gap !== undefined ? options.gap : 2;
  return Math.max(0, Math.min(count - 1, Math.floor((x + gap / 2) / (size + gap))));
}

// Which builds get their number written above the matrix: the first, the
// last, and every fifth in between when it is not too close to an end.
export function columnLabels(builds) {
  return builds
    .map((build, index) => ({ index: index, number: build.number }))
    .filter(
      (label) =>
        label.index === 0 ||
        label.index === builds.length - 1 ||
        (label.index % 5 === 0 && label.index < builds.length - 3)
    );
}

// 0.412 -> "0.41 s", 12.34 -> "12.3 s", 125 -> "2 min 5 s".
export function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "";
  }
  if (seconds < 10) {
    return (Math.round(seconds * 100) / 100).toString() + " s";
  }
  if (seconds < 60) {
    return (Math.round(seconds * 10) / 10).toString() + " s";
  }
  const whole = Math.round(seconds);
  return Math.floor(whole / 60) + " min" + (whole % 60 ? " " + (whole % 60) + " s" : "");
}

// The share of its verdicts a test passed, as "7 of 10 passed".
export function passCount(cells) {
  const verdicts = Array.from(cells).filter((cell) => cell === "p" || cell === "f");
  return verdicts.filter((cell) => cell === "p").length + " of " + verdicts.length + " passed";
}

// What one cell says: which test, which build, what happened and how long it took.
export function describeCell(row, builds, index) {
  const build = builds[index];
  const state = row.cells[index];
  const seconds = row.seconds ? row.seconds[index] : null;
  const took = (state === "p" || state === "f") && seconds ? ", " + formatSeconds(seconds) : "";
  return {
    test: splitClass(row.class).short + ": " + row.name,
    build: "#" + build.number,
    what: (state === " " ? describeBuild(build) : STATES[state] || "unknown") + took,
  };
}

// What a build says about itself: how many tests it ran, or why it has no column of results.
export function describeBuild(build) {
  if (build.state === "ok") {
    return build.tests + (build.tests === 1 ? " test, " : " tests, ") + build.failed + " failed";
  }
  if (build.state === "too_large") {
    return "the report is too large to read";
  }
  if (build.state === "unavailable") {
    return "Jenkins did not hand out the report this time";
  }
  return "no test report";
}
