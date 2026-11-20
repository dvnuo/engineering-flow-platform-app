// How the page words what the server sent. Plain functions, so the tests
// run them with node.

export function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

// 4 for 4.0, 6.5 for 6.5, a dash when there is no number (only "?" was picked).
export function number(value) {
  return typeof value === "number" && Number.isFinite(value) ? String(Math.round(value * 10) / 10) : "-";
}

// What a member's row says. Before the reveal the page knows only whether
// somebody has picked; the card is not in what the server sent.
export function memberStatus(member, revealed) {
  if (revealed) {
    return member.picked ? member.card : "no card";
  }
  return member.picked ? "has picked" : member.online ? "is thinking" : "away";
}

// One sentence about a revealed round.
export function verdict(result) {
  if (!result || result.votes === 0) {
    return "";
  }
  if (result.consensus) {
    return "Consensus: everybody chose " + result.spread[0].card + ".";
  }
  if (result.votes === 1) {
    return "One card only. Wait for the others, or start a new round.";
  }
  const numbers = result.spread.map((row) => Number(row.card)).filter((value) => Number.isFinite(value));
  if (numbers.length > 1) {
    return (
      "No consensus: from " + Math.min(...numbers) + " to " + Math.max(...numbers) +
      ". Let the lowest and the highest explain, then play another round."
    );
  }
  return "No consensus. Talk it over, then play another round.";
}

// The spread as rows of a bar chart: each card with its count and the width
// of its bar, in percent of the longest one.
export function spreadRows(result) {
  const most = Math.max(1, ...result.spread.map((row) => row.count));
  return result.spread.map((row) => ({ card: row.card, count: row.count, width: Math.round((row.count / most) * 100) }));
}

// A finished round in one line, for the history.
export function roundLine(entry) {
  const facts = [plural(entry.result.votes, "card")];
  if (entry.result.average !== null) {
    facts.push("average " + number(entry.result.average), "median " + number(entry.result.median));
  }
  facts.push(entry.result.consensus ? "consensus" : "no consensus");
  return "Round " + entry.round + ": " + facts.join(", ");
}

export function picksLine(entry) {
  return entry.picks.map((chosen) => chosen.name + " " + chosen.card).join(", ");
}

// Only http and https addresses become something to click on. The server
// checks the same; this is the page not trusting what it is handed.
export function safeLink(link) {
  return typeof link === "string" && /^https?:\/\//i.test(link) ? link : null;
}
