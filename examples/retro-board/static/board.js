// What the page does with a board once it has it: the order of the cards,
// the columns, the Markdown export. Plain functions of the data the server
// sent, so the tests run them with node.
//
// They can only use what the server sent. A card of somebody else has no
// author in it until the board is revealed, so nothing here could show one.

export function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

// "votes": the most votes first, and the older card first among equals.
// "time": in the order the cards were written.
export function sortCards(cards, mode) {
  const byTime = (first, second) =>
    String(first.created_at).localeCompare(String(second.created_at)) || String(first.id).localeCompare(String(second.id));
  const sorted = cards.slice().sort(byTime);
  if (mode === "votes") {
    sorted.sort((first, second) => second.votes - first.votes); // a stable sort: equal votes stay in time order
  }
  return sorted;
}

// The columns of the board, each with its cards in the wanted order.
export function byColumn(view, mode) {
  return view.board.columns.map((column) => ({
    column,
    cards: sortCards(view.cards.filter((card) => card.column === column.id), mode),
  }));
}

export function totals(view) {
  const votes = view.cards.reduce((sum, card) => sum + card.votes, 0);
  return plural(view.cards.length, "card") + ", " + plural(votes, "vote");
}

// The board as a Markdown document: a heading per column, a list item per
// card, the most voted first. Names are written only when the board has been
// revealed. Before that the page knows the visitor's own cards, and a file
// that marked them would give the visitor away to whoever gets the file.
export function toMarkdown(view) {
  const lines = ["# " + view.board.title, ""];
  for (const { column, cards } of byColumn(view, "votes")) {
    lines.push("## " + column.title, "");
    for (const card of cards) {
      const facts = [];
      if (card.votes) {
        facts.push(plural(card.votes, "vote"));
      }
      if (view.revealed && card.author) {
        facts.push(card.author);
      }
      const text = card.text.split("\n");
      text[text.length - 1] += facts.length ? " (" + facts.join(", ") + ")" : "";
      lines.push("- " + text[0], ...text.slice(1).map((line) => "  " + line));
    }
    if (!cards.length) {
      lines.push("Nothing.");
    }
    lines.push("");
  }
  return lines.join("\n").trimEnd() + "\n";
}

export function fileName(view) {
  const stem = String(view.board.id).replace(/[^A-Za-z0-9_-]+/g, "-");
  return (stem || "retro") + ".md";
}
