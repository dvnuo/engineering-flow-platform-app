// Five-field cron expressions: read, put into words, and the next run times.
//
// The rules are those of the classic (Vixie) cron that Linux, Kubernetes
// CronJobs and GitHub Actions follow. Dialect extras are named and refused
// rather than guessed at: Jenkins's H, Quartz's seconds field, L, W and #.
//
// No DOM in this file.

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const short = (names) => names.map((name) => name.slice(0, 3).toUpperCase());

const FIELDS = [
  { name: "minute", min: 0, max: 59, show: String },
  { name: "hour", min: 0, max: 23, show: String },
  { name: "day of the month", min: 1, max: 31, show: String },
  { name: "month", min: 1, max: 12, names: short(MONTHS), show: (value) => MONTHS[value - 1] },
  // 0 and 7 both mean Sunday. Weekdays are put into words by weekdayText(), from Monday.
  { name: "day of the week", min: 0, max: 7, names: short(DAYS) },
];
const MACROS = {
  "@yearly": "0 0 1 1 *",
  "@annually": "0 0 1 1 *",
  "@monthly": "0 0 1 * *",
  "@weekly": "0 0 * * 0",
  "@daily": "0 0 * * *",
  "@midnight": "0 0 * * *",
  "@hourly": "0 * * * *",
};

class Problem extends Error {}

function numberOf(token, field) {
  let value;
  if (/^\d+$/.test(token)) {
    value = Number(token);
  } else {
    const index = field.names ? field.names.indexOf(token.toUpperCase()) : -1;
    if (index === -1) {
      throw new Problem('"' + token + '" is not a ' + field.name + ".");
    }
    value = index + field.min;
  }
  if (value < field.min || value > field.max) {
    throw new Problem("The " + field.name + " field takes " + field.min + " to " + field.max + "; " + value + " is outside.");
  }
  return value;
}

function parseField(source, field) {
  const last = field.max === 7 ? 6 : field.max;
  const values = new Set();
  for (const part of source.split(",")) {
    if (/^H(\(\d+-\d+\))?(\/\d+)?$/.test(part)) {
      throw new Problem("H is Jenkins syntax (a time spread by job name). Put a number in its place to see the run times.");
    }
    if (/#|^\d*[LW]+(-\d+)?$/.test(part)) {
      throw new Problem('"' + part + '" is a Quartz extension. This explainer reads standard five-field cron.');
    }
    const found = /^(\*|\?|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?)(?:\/(\d+))?$/.exec(part);
    if (!found) {
      throw new Problem('"' + part + '" in the ' + field.name + " field is not something cron understands.");
    }
    const step = found[2] === undefined ? 1 : Number(found[2]);
    if (step < 1) {
      throw new Problem("A step of 0 in the " + field.name + " field would never move on.");
    }
    let from = field.min;
    let to = last;
    if (found[1] !== "*" && found[1] !== "?") {
      const [low, high] = found[1].split("-");
      from = numberOf(low, field);
      // "5/15" means "from 5 on, every 15".
      to = high === undefined ? (found[2] === undefined ? from : field.max) : numberOf(high, field);
      if (from > to) {
        throw new Problem("The range " + found[1] + " in the " + field.name + " field runs backwards.");
      }
    }
    for (let value = from; value <= to; value += step) {
      values.add(field.max === 7 && value === 7 ? 0 : value);
    }
  }
  return Array.from(values).sort((left, right) => left - right);
}

// {ok: true, expression, fields: [{name, source, values, star}]} or {ok: false, message}.
export function parseCron(text) {
  let expression = text.trim().replace(/\s+/g, " ");
  if (!expression) {
    return { ok: false, message: "Type a cron expression, for example 0 9 * * 1-5." };
  }
  if (expression.toLowerCase() === "@reboot") {
    return { ok: false, message: "@reboot runs once when the machine starts. It has no schedule to show." };
  }
  expression = MACROS[expression.toLowerCase()] || expression;
  const sources = expression.split(" ");
  if (sources.length === 6 || sources.length === 7) {
    return {
      ok: false,
      message:
        "This has " + sources.length + " fields. Standard cron has five; an extra one usually means seconds or a year (Quartz, Spring). Remove it to see the schedule.",
    };
  }
  if (sources.length !== 5) {
    return {
      ok: false,
      message: "A cron expression has five fields (minute, hour, day of the month, month, day of the week); this one has " + sources.length + ".",
    };
  }
  try {
    const fields = FIELDS.map((field, index) => ({
      name: field.name,
      source: sources[index],
      values: parseField(sources[index], field),
      star: sources[index][0] === "*" || sources[index][0] === "?",
    }));
    return { ok: true, expression, fields };
  } catch (problem) {
    if (!(problem instanceof Problem)) {
      throw problem;
    }
    return { ok: false, message: problem.message };
  }
}

function listText(items) {
  return items.length < 2 ? items.join("") : items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
}

// "1, 2, 3, 5" as "1 through 3 and 5".
function spell(values, show) {
  const items = [];
  for (let at = 0; at < values.length; ) {
    let end = at;
    while (end + 1 < values.length && values[end + 1] === values[end] + 1) {
      end += 1;
    }
    if (end - at >= 2) {
      items.push(show(values[at]) + " through " + show(values[end]));
    } else {
      for (let index = at; index <= end; index += 1) {
        items.push(show(values[index]));
      }
    }
    at = end + 1;
  }
  return listText(items);
}

// Weekdays read best from Monday: "Friday through Sunday", not "Sunday, Friday and Saturday".
function weekdayText(values) {
  const fromMonday = values.map((value) => (value + 6) % 7).sort((left, right) => left - right);
  return spell(fromMonday, (value) => DAYS[(value + 1) % 7]);
}

function ordinal(number) {
  const tens = number % 100;
  const suffix = tens >= 11 && tens <= 13 ? "th" : ["th", "st", "nd", "rd"][number % 10] || "th";
  return number + suffix;
}

const stepOf = (field) => {
  const found = /^\*\/(\d+)$/.exec(field.source);
  return found && Number(found[1]) > 1 ? Number(found[1]) : 0;
};
const full = (field, index) => field.values.length === (index === 4 ? 7 : FIELDS[index].max - FIELDS[index].min + 1);
const clock = (hour, minute) => String(hour).padStart(2, "0") + ":" + String(minute).padStart(2, "0");

function timePhrase(minute, hour) {
  const anyMinute = full(minute, 0);
  const anyHour = full(hour, 1);
  let hours = "every hour";
  if (!anyHour) {
    hours = stepOf(hour)
      ? "every " + ordinal(stepOf(hour)) + " hour"
      : hour.values.length === 1
        ? "the hour from " + clock(hour.values[0], 0)
        : "the hours " + spell(hour.values, String);
  }
  if (anyMinute) {
    return anyHour ? "Every minute" : "Every minute of " + hours;
  }
  if (stepOf(minute)) {
    return "Every " + stepOf(minute) + " minutes" + (anyHour ? "" : " during " + hours);
  }
  if (!anyHour && !stepOf(hour) && minute.values.length * hour.values.length <= 6) {
    const times = [];
    for (const hourValue of hour.values) {
      for (const minuteValue of minute.values) {
        times.push(clock(hourValue, minuteValue));
      }
    }
    return "At " + listText(times);
  }
  if (minute.values.length === 1) {
    return minute.values[0] === 0 && anyHour ? "At the start of every hour" : "At minute " + minute.values[0] + " of " + hours;
  }
  return "At minutes " + spell(minute.values, String) + " of " + hours;
}

function dayPhrase(dayOfMonth, dayOfWeek) {
  const anyDate = full(dayOfMonth, 2);
  const anyWeekday = full(dayOfWeek, 4);
  const dates = stepOf(dayOfMonth)
    ? "every " + ordinal(stepOf(dayOfMonth)) + " day of the month"
    : "day" + (dayOfMonth.values.length === 1 ? " " : "s ") + spell(dayOfMonth.values, String) + " of the month";
  const weekdays = weekdayText(dayOfWeek.values);
  if (anyDate && anyWeekday) {
    return "";
  }
  if (anyDate) {
    return ", on " + weekdays;
  }
  if (anyWeekday) {
    return ", on " + dates;
  }
  // Cron's oddest rule: two restricted day fields are joined by OR, unless one of them starts with *.
  return dayOfMonth.star || dayOfWeek.star
    ? ", on " + dates + ", and only on " + weekdays
    : ", on " + dates + " or on " + weekdays + " (cron runs it when either one matches)";
}

// The schedule as one sentence.
export function describeCron(parsed) {
  const [minute, hour, dayOfMonth, month, dayOfWeek] = parsed.fields;
  const months = full(month, 3) ? "" : ", in " + spell(month.values, FIELDS[3].show);
  return timePhrase(minute, hour) + dayPhrase(dayOfMonth, dayOfWeek) + months + ".";
}

// What each field allows, for a table: [{name, source, meaning}].
export function explainFields(parsed) {
  return parsed.fields.map((field, index) => ({
    name: field.name,
    source: field.source,
    meaning: full(field, index) ? "every " + field.name : index === 4 ? weekdayText(field.values) : spell(field.values, FIELDS[index].show),
  }));
}

// The next ``count`` run times after ``fromMs``, as epoch milliseconds, read
// in UTC or in the engine's local time zone. Fewer than ``count`` (or none)
// when the schedule names a date that does not come, such as 30 February.
export function nextRuns(parsed, fromMs, count = 5, { utc = false } = {}) {
  const [minute, hour, dayOfMonth, month, dayOfWeek] = parsed.fields.map((field) => ({ ...field, set: new Set(field.values) }));
  const runs = [];
  const from = new Date(fromMs);
  const year = utc ? from.getUTCFullYear() : from.getFullYear();
  const monthIndex = utc ? from.getUTCMonth() : from.getMonth();
  const date = utc ? from.getUTCDate() : from.getDate();
  // Eight years of days reach every leap day; a day is cheap to test.
  for (let ahead = 0; ahead < 366 * 8 && runs.length < count; ahead += 1) {
    const day = utc ? new Date(Date.UTC(year, monthIndex, date + ahead)) : new Date(year, monthIndex, date + ahead);
    const [y, m, d, weekday] = utc
      ? [day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), day.getUTCDay()]
      : [day.getFullYear(), day.getMonth(), day.getDate(), day.getDay()];
    const dateFits = dayOfMonth.set.has(d);
    const weekdayFits = dayOfWeek.set.has(weekday);
    const dayFits = dayOfMonth.star || dayOfWeek.star ? dateFits && weekdayFits : dateFits || weekdayFits;
    if (!month.set.has(m + 1) || !dayFits) {
      continue;
    }
    for (const h of hour.values) {
      for (const min of minute.values) {
        const run = utc ? new Date(Date.UTC(y, m, d, h, min)) : new Date(y, m, d, h, min);
        // A local time that daylight saving skips does not exist that day.
        const exists = utc || (run.getHours() === h && run.getMinutes() === min);
        if (exists && run.getTime() > fromMs && runs.length < count) {
          runs.push(run.getTime());
        }
      }
    }
  }
  return runs;
}
