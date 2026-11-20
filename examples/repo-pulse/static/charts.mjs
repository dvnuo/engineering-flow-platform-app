// Drawing arithmetic for the four charts: data in, numbers, SVG path strings
// and words out. Nothing here touches the document, so the same file runs in
// the browser and under node, where the tests call it.

export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function round(value) {
  return Math.round(value * 10) / 10;
}

// --- Words ---------------------------------------------------------------------

// 1284 -> "1,284", 12900 -> "12.9K": whole numbers in full below ten thousand.
export function formatCount(value) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "-";
  }
  if (value < 10000) {
    return String(Math.round(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }
  if (value < 1000000) {
    return round(value / 1000) + "K";
  }
  return round(value / 1000000) + "M";
}

// How long ago, from a number of days: "5 hours", "3 days", "2 weeks", "4 months".
export function formatAge(days) {
  const some = (count, word) => count + " " + word + (count === 1 ? "" : "s");
  if (days === null || days === undefined || !Number.isFinite(days)) {
    return "-";
  }
  if (days < 1 / 24) {
    return "under an hour";
  }
  if (days < 1) {
    return some(Math.floor(days * 24), "hour");
  }
  if (days < 14) {
    return some(Math.floor(days), "day");
  }
  if (days < 60) {
    return some(Math.floor(days / 7), "week");
  }
  if (days < 730) {
    return some(Math.floor(days / 30), "month");
  }
  return some(Math.floor(days / 365), "year");
}

// A day of GitHub's calendar (seconds since 1970, counted in UTC) as "Tue 3 Nov 2026".
export function dayName(seconds) {
  const date = new Date(seconds * 1000);
  return (
    DAYS[date.getUTCDay()].slice(0, 3) + " " + date.getUTCDate() + " " + MONTHS[date.getUTCMonth()] + " " + date.getUTCFullYear()
  );
}

export function hourName(hour) {
  const two = (value) => String(value).padStart(2, "0");
  return two(hour) + ":00-" + two((hour + 1) % 24) + ":00";
}

export function truncate(text, limit) {
  return text.length > limit ? text.slice(0, limit - 3) + "..." : text;
}

// --- Scales ---------------------------------------------------------------------

// Round steps for an axis that counts things: 0, 2, 4, 6, 8 for a largest value of 7.
export function niceTicks(max, intervals) {
  const wanted = intervals || 4;
  if (!(max > 0)) {
    return [0, 1];
  }
  const rough = max / wanted;
  const power = Math.pow(10, Math.floor(Math.log10(rough)));
  const step = Math.max(1, [1, 2, 5, 10].map((factor) => factor * power).find((candidate) => candidate >= rough));
  const ticks = [0];
  while (ticks[ticks.length - 1] < max) {
    ticks.push(ticks[ticks.length - 1] + step);
  }
  return ticks;
}

// Which of the four shades a day gets: 0 for no commit, 4 for the busiest days.
export function levelOf(count, max) {
  return count > 0 && max > 0 ? Math.min(4, Math.ceil((count / max) * 4)) : 0;
}

// A bar with a rounded end where the value is and a square end on the
// baseline. `end` is "right" for a bar that grows sideways, "top" for a column.
export function roundedBar(x, y, width, height, radius, end) {
  const r = Math.max(0, Math.min(radius, end === "top" ? height : width, (end === "top" ? width : height) / 2));
  const right = round(x + width);
  const bottom = round(y + height);
  if (end === "top") {
    return (
      "M" + x + " " + bottom + "V" + round(y + r) + "Q" + x + " " + y + " " + round(x + r) + " " + y +
      "H" + round(right - r) + "Q" + right + " " + y + " " + right + " " + round(y + r) + "V" + bottom + "Z"
    );
  }
  return (
    "M" + x + " " + y + "H" + round(right - r) + "Q" + right + " " + y + " " + right + " " + round(y + r) +
    "V" + round(bottom - r) + "Q" + right + " " + bottom + " " + round(right - r) + " " + bottom + "H" + x + "Z"
  );
}

// --- The four charts ----------------------------------------------------------------

// A year of commits: one column per week, one square per day, Sunday on top.
// `weeks` is [{ week: seconds, days: [7 counts] }], the oldest first.
export function calendar(weeks, options) {
  const size = (options && options.size) || 11;
  const gap = options && options.gap !== undefined ? options.gap : 2;
  const left = 30; // room for "Mon", "Wed", "Fri"
  const top = 16; // room for the months
  const step = size + gap;
  const max = Math.max(0, ...weeks.map((week) => Math.max(...week.days)));
  const cells = [];
  const months = [];
  weeks.forEach((week, column) => {
    week.days.forEach((count, day) => {
      cells.push({
        index: column * 7 + day,
        x: left + column * step,
        y: top + day * step,
        size: size,
        count: count,
        level: levelOf(count, max),
        day: week.week + day * 86400,
      });
    });
    // A month is named above the first week that begins in it, when there is room for the name.
    const month = new Date(week.week * 1000).getUTCMonth();
    const before = column > 0 ? new Date(weeks[column - 1].week * 1000).getUTCMonth() : -1;
    const last = months[months.length - 1];
    if (month !== before && column <= weeks.length - 2) {
      if (last && column - last.column < 3) {
        months.pop();
      }
      months.push({ column: column, x: left + column * step, label: MONTHS[month] });
    }
  });
  return {
    width: left + Math.max(1, weeks.length) * step - gap,
    height: top + 7 * step - gap,
    max: max,
    cells: cells,
    months: months,
    days: [1, 3, 5].map((day) => ({ y: top + day * step + size - 2, label: DAYS[day].slice(0, 3) })),
  };
}

// Commits by weekday and hour: a dot per hour whose area grows with the count.
// `hours` is seven rows of 24 counts, Sunday first.
export function punchCard(hours, options) {
  const step = (options && options.step) || 26;
  const left = 40; // room for the weekdays
  const top = 4;
  const largest = step / 2 - 2;
  const max = Math.max(0, ...hours.map((day) => Math.max(...day)));
  const dots = [];
  hours.forEach((day, row) => {
    day.forEach((commits, hour) => {
      dots.push({
        index: row * 24 + hour,
        x: left + hour * step,
        y: top + row * step,
        cx: left + hour * step + step / 2,
        cy: top + row * step + step / 2,
        // The area, not the radius, follows the count; a single commit still shows.
        r: commits > 0 ? Math.max(1.5, round(largest * Math.sqrt(commits / max))) : 0,
        day: row,
        hour: hour,
        commits: commits,
      });
    });
  });
  return {
    width: left + 24 * step,
    height: top + 7 * step + 18,
    step: step,
    max: max,
    dots: dots,
    days: DAYS.map((name, row) => ({ y: top + row * step + step / 2 + 4, label: name.slice(0, 3) })),
    hours: [0, 3, 6, 9, 12, 15, 18, 21].map((hour) => ({ x: left + hour * step + step / 2, label: String(hour).padStart(2, "0") })),
  };
}

// Ranked bars, one per item, the name on the left and the value at the tip.
// `items` is [{ label, value }], the largest first.
export function barRows(items, options) {
  const width = (options && options.width) || 640;
  const label = (options && options.label) || 150;
  const tip = 56; // room for the value after the bar
  const thick = 16;
  const step = 26;
  const max = Math.max(1, ...items.map((item) => item.value));
  const room = width - label - tip;
  return {
    width: width,
    height: Math.max(thick, items.length * step - (step - thick)),
    rows: items.map((item, index) => {
      const length = item.value > 0 ? Math.max(2, round((room * item.value) / max)) : 0;
      return {
        index: index,
        x: label,
        y: index * step,
        length: length,
        thick: thick,
        path: roundedBar(label, index * step, length, thick, 4, "right"),
        label: truncate(item.label, 20),
        value: item.value,
      };
    }),
  };
}

// Columns of a count per bucket, each split into "ready" and "draft".
// `buckets` is [{ label, ready, draft }] in their order.
export function histogram(buckets, options) {
  const width = (options && options.width) || 640;
  const height = (options && options.height) || 220;
  const left = 36; // room for the axis numbers
  const top = 20; // room for the count on the tallest column
  const bottom = 24; // room for the bucket names
  const thick = 24;
  const gap = 2; // surface between the two parts of a column
  const ticks = niceTicks(Math.max(0, ...buckets.map((bucket) => bucket.ready + bucket.draft)));
  const ceiling = ticks[ticks.length - 1];
  const base = height - bottom;
  const scale = (base - top) / ceiling;
  const band = (width - left) / Math.max(1, buckets.length);
  return {
    width: width,
    height: height,
    base: base,
    left: left,
    ticks: ticks.map((value) => ({ value: value, y: round(base - value * scale) })),
    columns: buckets.map((bucket, index) => {
      const x = round(left + band * index + band / 2 - thick / 2);
      const ready = round(bucket.ready * scale);
      const draft = round(bucket.draft * scale);
      const lift = ready > 0 && draft > 0 ? gap : 0;
      const parts = [];
      if (ready > 0) {
        // The lower part is square at both ends unless it is also the top of the column.
        parts.push({ kind: "ready", path: roundedBar(x, round(base - ready), thick, ready, draft > 0 ? 0 : 4, "top") });
      }
      if (draft > 0) {
        const tall = Math.max(1, draft - lift);
        parts.push({ kind: "draft", path: roundedBar(x, round(base - ready - lift - tall), thick, tall, 4, "top") });
      }
      return {
        index: index,
        x: x,
        center: round(x + thick / 2),
        top: round(base - ready - draft),
        band: { x: round(left + band * index), width: round(band) },
        label: bucket.label,
        total: bucket.ready + bucket.draft,
        ready: bucket.ready,
        draft: bucket.draft,
        parts: parts,
      };
    }),
  };
}
